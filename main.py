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

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

def normalizar_texto(texto: str) -> str:
    """Normaliza un texto eliminando tildes y transformando caracteres especiales."""
    nfkd_form = unicodedata.normalize('NFKD', texto)
    without_accents = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
    normalized = without_accents.replace('ñ', 'n').replace('Ñ', 'N')
    return normalized.lower()

# Mapeo de ROA a emoji (ámbito global)
ROA_EMOJIS = {
    "oral": "💊",
    "insufflated": "👃",
    "smoked": "🚬💨",
    "intravenous": "💉🩸",
    "intramuscular": "💉💪",
    "sublingual": "👅"
}

@client.event
async def on_ready():
    """Sincroniza los comandos globales al iniciar."""
    try:
        await tree.sync()
        logger.info(f"✅ Comandos globales sincronizados.")
        logger.info(f"✅ Bot conectado como {client.user}")
    except Exception as e:
        logger.error(f"❌ Error en on_ready: {e}", exc_info=True)

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
            description=sugerencia_msg,
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
