import os
import json
import difflib
import logging
import unicodedata
import discord
from discord import app_commands
import aiohttp
from dotenv import load_dotenv
from deep_translator import GoogleTranslator

# --- Configuración del Logger ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logs/bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Asegurarse de que el directorio 'logs' exista
os.makedirs("logs", exist_ok=True)

# Cargar alias
with open("alias.json", "r", encoding="utf-8") as f:
    ALIASES = json.load(f)

# Lista de sustancias válidas (para sugerencias)
VALID_SUBSTANCES = list(ALIASES.values())

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

REPORT_CHANNEL_ID = os.getenv("REPORT_CHANNEL_ID")
if REPORT_CHANNEL_ID:
    try:
        REPORT_CHANNEL_ID = int(REPORT_CHANNEL_ID)
        logger.info(f"✅ ID del canal de reportes cargado: {REPORT_CHANNEL_ID}")
    except ValueError:
        logger.error("❌ REPORT_CHANNEL_ID en .env no es un número válido. Por favor, revisa el formato.")
else:
    logger.warning("⚠️ Variable REPORT_CHANNEL_ID no encontrada en .env. Los reportes no se enviarán a un canal.")

BOT_ADMIN_USER_IDS_STR = os.getenv("BOT_ADMIN_USER_IDS")
BOT_ADMIN_USER_IDS = []
if BOT_ADMIN_USER_IDS_STR:
    try:
        BOT_ADMIN_USER_IDS = [int(uid.strip()) for uid in BOT_ADMIN_USER_IDS_STR.split(',')]
        logger.info(f"✅ IDs de administradores del bot cargados: {BOT_ADMIN_USER_IDS}")
    except ValueError:
        logger.error("❌ BOT_ADMIN_USER_IDS en .env no contiene solo números separados por comas. Por favor, revisa el formato.")
else:
    logger.warning("⚠️ Variable BOT_ADMIN_USER_IDS no encontrada en .env. El comando /alias no estará restringido.")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

def normalizar_texto(texto: str) -> str:
    """Normaliza un texto eliminando tildes y transformando caracteres especiales."""
    nfkd_form = unicodedata.normalize('NFKD', texto)
    without_accents = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
    normalized = without_accents.replace('ñ', 'n').replace('Ñ', 'N')
    return normalized.lower()

def is_bot_admin_check():
    """Verifica si el usuario que invoca el comando es un administrador del bot."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id not in BOT_ADMIN_USER_IDS:
            logger.warning(f"Intento de usar comando de admin por no-admin: {interaction.user.name} ({interaction.user.id})")
            await interaction.response.send_message("❌ No tienes permiso para usar este comando.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

# Mapeo de ROA a emoji (ámbito global)
ROA_EMOJIS = {
    "oral": "💊",
    "insufflated": "👃",
    "smoked": "🚬💨",
    "intravenous": "💉🩸",
    "intramuscular": "💉💪",
    "sublingual": "👅",
    "rectal": "🎯 💩"
}

@client.event
async def on_ready():
    """Sincroniza los comandos del bot al iniciar."""
    try:
        # Sincronización global de comandos
        await tree.sync()
        logger.info("✅ Comandos sincronizados globalmente.")
        logger.info(f"✅ Bot conectado como {client.user}")
    except Exception as e:
        logger.error(f"❌ Error en on_ready durante la sincronización global: {e}", exc_info=True)

def safe_add_field(embed, *, name, value, inline=False):
    """Agrega campos al embed de forma segura y truncada."""
    value = value.strip()
    if not value:
        return
    if len(value) > 1024:
        value = value[:1021] + "..."
    embed.add_field(name=name, value=value, inline=inline)

def crear_embed_base(info: dict) -> discord.Embed:
    """Crea la base de un embed con la información principal de la sustancia."""
    name = info.get("name", "Desconocido")
    # summary = info.get("summary", "Sin resumen disponible.")[:1024]

    embed = discord.Embed(
        title=f"🔍 {name}",
        # description=summary,
        color=0x8e44ad
    )

    common_names = info.get("commonNames", [])
    if common_names:
        safe_add_field(embed, name="🔹 También llamado", value=", ".join(common_names), inline=False)

    effects = info.get("effects", [])
    if effects:
        limit = 10
        lista = ", ".join(effect["name"] for effect in effects[:limit])
        if len(effects) > limit:
            lista += f"\n[Ver todos los efectos](https://psychonautwiki.org/wiki/{name.replace(' ', '_')}#Effects)"
        safe_add_field(embed, name="🎯 Efectos", value=lista, inline=False)
    
    return embed

@tree.command(name="info", description="Obtiene información de una sustancia desde PsychonautWiki")
@app_commands.describe(sustancia="Nombre de la sustancia a buscar")
async def info(interaction: discord.Interaction, sustancia: str):
    logger.info(f"Comando /info utilizado por {interaction.user.name} ({interaction.user.id}) para sustancia: '{sustancia}'")
    await interaction.response.defer()

    graphql_endpoint = "https://api.psychonautwiki.org/"
    with open("query.graphql", "r", encoding="utf-8") as f:
        query = f.read()

    headers = {
        "User-Agent": "KetaversoBot/1.0 (https://github.com/Triskis777/ketaverso-bot-info)",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://api.psychonautwiki.org"
    }

    sustancia_input_original = sustancia.lower().strip()
    sustancia_input_normalizada = normalizar_texto(sustancia_input_original)
    
    sustancia_para_api = ALIASES.get(sustancia_input_normalizada, sustancia_input_normalizada)
    variables = {"name": sustancia_para_api}
    substances = []

    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.post(graphql_endpoint, json={"query": query, "variables": variables}) as resp:
                raw_text = await resp.text()
                logger.info(f"Respuesta de la API (Status: {resp.status}): {raw_text[:500]}")
                logger.info(f"Cabeceras de respuesta de la API: {resp.headers}")

                if resp.status != 200:
                    logger.error(f"Error en la consulta a GraphQL ({resp.status}): {raw_text}")
                    await interaction.followup.send(f"❌ Error al consultar la API (código {resp.status}).", ephemeral=True)
                    return
                
                result = json.loads(raw_text) if raw_text else None
                
                if not result or result.get("errors") or not result.get("data"):
                    api_error = result.get("errors", "Respuesta vacía o sin datos.") if result else "Respuesta vacía."
                    logger.error(f"La API de GraphQL devolvió un error: {api_error}")
                    await interaction.followup.send("❌ La API de PsychonautWiki está experimentando problemas o no encontró la sustancia. Inténtalo de nuevo más tarde.", ephemeral=True)
                    return

                substances = result.get("data", {}).get("substances", [])
        
        except json.JSONDecodeError as e:
            logger.error(f"Falla de decodificación JSON. Raw text: '{raw_text[:500]}'. Error: {e}", exc_info=True)
            await interaction.followup.send("❌ Error al procesar la respuesta de la API (JSON inválido).", ephemeral=True)
            return
        except aiohttp.ClientError as e:
            logger.error(f"Error de cliente aiohttp: {e}", exc_info=True)
            await interaction.followup.send("❌ Error de conexión con la API.", ephemeral=True)
            return

        if not substances:
            try:
                traducido = GoogleTranslator(source='auto', target='en').translate(sustancia_input_original)
                if traducido and traducido.lower() != sustancia_input_original:
                    logger.info(f"Traducción: '{sustancia_input_original}' → '{traducido}'")
                    variables["name"] = traducido.lower()
                    async with session.post(graphql_endpoint, json={"query": query, "variables": variables}) as retry_resp:
                        if retry_resp.status == 200:
                            retry_raw_text = await retry_resp.text()
                            retry_data = json.loads(retry_raw_text) if retry_raw_text else None
                            if retry_data and retry_data.get("data"):
                                substances = retry_data.get("data", {}).get("substances", [])
            except Exception as e:
                logger.warning(f"Traducción o segundo intento fallido: {e}", exc_info=True)

    if not substances:
        sugerencias = difflib.get_close_matches(sustancia_input_normalizada, VALID_SUBSTANCES, n=3, cutoff=0.6)
        sugerencia_msg = "No se encontraron sugerencias." if not sugerencias else f"🔎 ¿Quisiste decir: {', '.join(sugerencias)}?"
        embed = discord.Embed(
            title="❌ Sustancia no encontrada",
            description=f"{sugerencia_msg}\nSi crees que esto es un error o que falta un alias, puedes informarnos usando el comando `/report`.",
            color=0x8e44ad
        )
        return await interaction.followup.send(embed=embed, ephemeral=True)

    info_data = substances[0]
    roas = info_data.get("roas", [])
    if len(roas) > 1:
        await mostrar_info_por_roa(interaction, info_data)
    else:
        embed = generar_embed_por_roa(info_data, 0)
        await interaction.followup.send(embed=embed)

@tree.command(name="report", description="Envía un reporte a los administradores del bot sobre un alias que falta o un error.")
@app_commands.describe(
    termino_buscado="El término de búsqueda que no funcionó (ej: metanfetamina)",
    sugerencia_alias="El alias correcto en inglés si lo conoces (ej: methamphetamine)",
    notas_adicionales="Cualquier comentario adicional para los administradores"
)
async def report(
    interaction: discord.Interaction,
    termino_buscado: str,
    sugerencia_alias: str = None,
    notas_adicionales: str = None
):
    logger.info(f"Comando /report utilizado por {interaction.user.name} ({interaction.user.id}) para: '{termino_buscado}'")
    await interaction.response.defer(ephemeral=True) # Defer an ephemeral response

    # 1. Confirmación para el usuario
    await interaction.followup.send("✅ Tu reporte ha sido enviado a los administradores. ¡Gracias por tu contribución!", ephemeral=True)

    if not REPORT_CHANNEL_ID:
        logger.warning("No se envió el reporte. No hay REPORT_CHANNEL_ID configurado.")
        return

    # 2. Construir el embed para el canal de reportes
    report_embed = discord.Embed(
        title="🚨 Nuevo Reporte de Alias/Error 🚨",
        description="Un usuario ha reportado un posible alias faltante o un error.",
        color=discord.Color.red()
    )
    report_embed.add_field(name="👤 Usuario", value=interaction.user.mention, inline=False)
    report_embed.add_field(name="🔍 Término Buscado", value=f"`{termino_buscado}`", inline=False)
    if sugerencia_alias:
        report_embed.add_field(name="💡 Sugerencia de Alias", value=f"`{sugerencia_alias}`", inline=False)
    if notas_adicionales:
        report_embed.add_field(name="📝 Notas Adicionales", value=notas_adicionales, inline=False)
    
    # Añadir contexto del servidor/canal desde donde se hizo el reporte
    if interaction.guild:
        report_embed.add_field(name="🏠 Servidor (Reporte)", value=f"{interaction.guild.name} (`{interaction.guild.id}`)", inline=True)
    if interaction.channel:
        report_embed.add_field(name="💬 Canal (Reporte)", value=f"{interaction.channel.mention} (`{interaction.channel.id}`)", inline=True)
    
    report_embed.set_footer(text=f"Reporte generado el {discord.utils.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")

    # 3. Enviar el embed al canal de reportes
    try:
        report_channel = client.get_channel(REPORT_CHANNEL_ID)
        if report_channel:
            await report_channel.send(embed=report_embed)
            logger.info(f"Reporte enviado al canal #{report_channel.name} ({REPORT_CHANNEL_ID})")
        else:
            logger.error(f"❌ No se encontró el canal con ID {REPORT_CHANNEL_ID}. Asegúrate de que el bot tenga acceso a él y los permisos necesarios.")
    except Exception as e:
        logger.error(f"❌ Error al enviar el reporte al canal {REPORT_CHANNEL_ID}: {e}", exc_info=True)

class ConfirmAliasView(discord.ui.View):
    def __init__(self, alias_name: str, target_name: str, normalized_alias: str, normalized_target: str):
        super().__init__(timeout=180)
        self.alias_name = alias_name
        self.target_name = target_name
        self.normalized_alias = normalized_alias
        self.normalized_target = normalized_target

    async def disable_buttons(self, interaction: discord.Interaction):
        """Deshabilita todos los botones y actualiza el mensaje."""
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(view=self)

    @discord.ui.button(label="Confirmar", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        global ALIASES, VALID_SUBSTANCES

        ALIASES[self.normalized_alias] = self.normalized_target
        if self.normalized_target not in VALID_SUBSTANCES:
            VALID_SUBSTANCES.append(self.normalized_target)

        try:
            with open("alias.json", "w", encoding="utf-8") as f:
                json.dump(ALIASES, f, ensure_ascii=False, indent=4)
            VALID_SUBSTANCES = list(set(ALIASES.values()))
            logger.info(f"Admin {interaction.user.name} confirmó y guardó el alias '{self.alias_name}' -> '{self.target_name}'.")
            await interaction.response.send_message(f"✅ Alias '{self.alias_name}' ahora apunta a '{self.target_name}'.", ephemeral=True)
        except IOError as e:
            logger.error(f"Error al guardar alias.json: {e}", exc_info=True)
            await interaction.response.send_message("❌ Error al guardar el alias en el fichero.", ephemeral=True)
        
        await self.disable_buttons(interaction)
        self.stop()

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        logger.info(f"Admin {interaction.user.name} canceló la creación del alias '{self.alias_name}'.")
        await interaction.response.send_message("❌ Operación cancelada.", ephemeral=True)
        await self.disable_buttons(interaction)
        self.stop()

@tree.command(name="alias", description="Añade o actualiza un alias para una sustancia (solo admins).")
@app_commands.describe(
    alias_name="El nombre común o alias (ej: 'metanfetamina', 'keta')",
    target_name="El nombre canónico de la sustancia en la API (ej: 'Methamphetamine', 'Ketamine')"
)
@is_bot_admin_check()
async def alias(
    interaction: discord.Interaction,
    alias_name: str,
    target_name: str
):
    logger.info(f"Comando /alias iniciado por admin {interaction.user.name} para '{alias_name}' -> '{target_name}'")
    
    normalized_alias = normalizar_texto(alias_name)
    normalized_target = target_name.lower().strip()

    embed = discord.Embed(
        title="Confirmación de Alias",
        description=f"¿Estás seguro de que quieres que el alias `{alias_name}` (normalizado: `{normalized_alias}`) apunte a `{target_name}`?",
        color=discord.Color.orange()
    )
    
    view = ConfirmAliasView(alias_name, target_name, normalized_alias, normalized_target)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@tree.command(name="aliases", description="Muestra todos los alias configurados (solo admins).")
@is_bot_admin_check()
async def aliases(interaction: discord.Interaction):
    logger.info(f"Comando /aliases utilizado por admin {interaction.user.name} ({interaction.user.id})")
    await interaction.response.defer(ephemeral=True)

    if not ALIASES:
        embed = discord.Embed(
            title="📚 Lista de Alias",
            description="No hay alias configurados actualmente.",
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    aliases_list = [f"`{alias}` -> `{target}`" for alias, target in ALIASES.items()]
    
    # Dividir la lista de alias en chunks que quepan en un embed.
    # Cada embed.description tiene un límite de 4096 caracteres.
    # Cada embed.field.value tiene un límite de 1024 caracteres.
    # Vamos a usar la descripción para la lista, dividiéndola en embeds si es necesario.
    
    current_description = ""
    embeds_to_send = []
    
    for item in aliases_list:
        if len(current_description) + len(item) + 1 > 4000: # Dejar espacio para el footer, etc.
            embed = discord.Embed(
                title="📚 Lista de Alias (continuación)",
                description=current_description,
                color=discord.Color.blue()
            )
            embeds_to_send.append(embed)
            current_description = item + "\n"
        else:
            current_description += item + "\n"
    
    if current_description: # Añadir el último embed
        embed = discord.Embed(
            title="📚 Lista de Alias" if not embeds_to_send else "📚 Lista de Alias (continuación)",
            description=current_description,
            color=discord.Color.blue()
        )
        embeds_to_send.append(embed)

    for i, embed_item in enumerate(embeds_to_send):
        embed_item.set_footer(text=f"Página {i+1} de {len(embeds_to_send)}")
        if i == 0:
            await interaction.followup.send(embed=embed_item, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed_item, ephemeral=True)

def generar_embed_por_roa(info: dict, index: int) -> discord.Embed:
    """Genera un embed visual con datos de un único ROA, usando la base y añadiendo detalles."""
    embed = crear_embed_base(info)
    roas = info.get("roas", [])
    if index >= len(roas):
        return embed

    roa = roas[index]
    roa_name_api = roa.get("name", "Desconocido")
    
    roa_emoji_char = ROA_EMOJIS.get(roa_name_api.lower(), '❓')
    roa_display_name = f"{roa_emoji_char} {roa_name_api.capitalize()}"
    
    embed.title = f"💡 {info.get('name', 'Desconocido')} - {roa_display_name}"

    dose = roa.get("dose") or {}
    duration = roa.get("duration") or {}
    bio = roa.get("bioavailability", {})

    def fmt_range(val: dict, units="") -> str:
        if val and "min" in val and "max" in val:
            return f"{val['min']}–{val['max']} {units}".strip()
        return "–"
    
    dosis_txt = []
    units = dose.get("units", "")
    mapping = {
        "threshold": "Light", "light": "Baja", "common": "Normal",
        "strong": "Alta", "heavy": "Muy alta"
    }
    for key, label in mapping.items():
        val = dose.get(key)
        if isinstance(val, dict):
            val_str = fmt_range(val, units)
        elif isinstance(val, (int, float)):
            val_str = f"{val} {units}"
        else:
            continue
        dosis_txt.append(f"__**{label}**__: {val_str}")

    if dosis_txt:
        embed.add_field(name="💊 **Dosis**", value="\n".join(dosis_txt), inline=False)

    duracion_txt = []
    labels = {
        "onset": "Inicio", "comeup": "Subida", "peak": "Pico",
        "offset": "Bajada", "afterglow": "Afterglow", "total": "Total"
    }
    for key, label in labels.items():
        val = duration.get(key)
        if val:
            duracion_txt.append(f"__**{label}**__: {fmt_range(val, val.get('units', ''))}")
    if duracion_txt:
        embed.add_field(name="⏳ **Duración**", value="\n".join(duracion_txt), inline=False)

    if bio and "min" in bio and "max" in bio:
        bio_val = f"{bio['min']}–{bio['max']}%"
        safe_add_field(embed, name="📈 Biodisponibilidad", value=f"*{bio_val}*", inline=False)

    embed.set_footer(text=f"ROA {index + 1} de {len(roas)}")
    return embed

class ROAView(discord.ui.View):
    def __init__(self, info: dict, current: int):
        super().__init__(timeout=300)
        self.info = info
        self.current = current
        roas = info.get("roas", [])
        for i, roa in enumerate(roas):
            roa_name_api = roa.get("name", f"ROA {i+1}")
            
            # Construir el label con el emoji
            roa_emoji_char = ROA_EMOJIS.get(roa_name_api.lower(), "❓")
            label = f"{roa_emoji_char} {roa_name_api.capitalize()}"

            # El emoji del botón es solo el checkmark
            emoji = "✅" if i == current else None
            
            self.add_item(ROAButton(info, i, label, emoji))

class ROAButton(discord.ui.Button):
    def __init__(self, info, index: int, label: str, emoji: str | None):
        super().__init__(style=discord.ButtonStyle.primary, label=label, emoji=emoji)
        self.info = info
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        embed = generar_embed_por_roa(self.info, self.index)
        await interaction.response.edit_message(embed=embed, view=ROAView(self.info, self.index))

async def mostrar_info_por_roa(interaction: discord.Interaction, info: dict):
    """Muestra un embed con botones para navegar entre diferentes ROAs."""
    embed = generar_embed_por_roa(info, 0)
    view = ROAView(info, 0)
    await interaction.followup.send(embed=embed, view=view)

if __name__ == "__main__":
    client.run(TOKEN)
