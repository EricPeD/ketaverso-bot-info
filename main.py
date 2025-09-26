import os
import json
import difflib
import discord
from discord import app_commands
import aiohttp
from dotenv import load_dotenv
from deep_translator import GoogleTranslator

# Cargar alias
with open("alias.json", "r", encoding="utf-8") as f:
    ALIASES = json.load(f)

# Lista de sustancias válidas (para sugerencias)
VALID_SUBSTANCES = list(ALIASES.values())

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = 1275929248170901535 #int(os.getenv("GUILD_ID"))

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
guild = discord.Object(id=GUILD_ID)


@client.event
async def on_ready():
    await tree.sync(guild=guild)
    print(f"✅ Bot conectado como {client.user}")

def safe_add_field(embed, *, name, value, inline=False):
    """Agrega campos al embed de forma segura y truncada."""
    value = value.strip()
    if not value:
        return
    if len(value) > 1024:
        value = value[:1021] + "..."
    embed.add_field(name=name, value=value, inline=inline)

def crear_embed_v2(info: dict) -> discord.Embed:
    """Crea un embed con formato visual moderno y organizado, usando emojis y mejor estructura."""
    name = info.get("name", "Desconocido")
    summary = info.get("summary", "Sin resumen disponible.")[:1024]
    common_names = info.get("commonNames", [])
    effects = info.get("effects", [])
    roas = info.get("roas", [])

    embed = discord.Embed(
        title=f"🔍 {name}",
        description=summary,
        color=0x8e44ad
    )

    # 🧪 Alias / nombres comunes
    if common_names:
        safe_add_field(embed, name="🔹 También llamado", value=", ".join(common_names), inline=False)

    # 🎯 Efectos
    if effects:
        limit = 10
        lista = ", ".join(effect["name"] for effect in effects[:limit])
        if len(effects) > limit:
            lista += f"\n[Ver todos los efectos](https://psychonautwiki.org/wiki/{name.replace(' ', '_')}#Effects)"
        safe_add_field(embed, name="🎯 Efectos", value=lista, inline=False)

    # 💊 Dosis + ⏳ Duración + 📈 Biodisponibilidad (por ROA)
    def fmt_range(d: dict, units: str = "") -> str:
        if d and "min" in d and "max" in d:
            return f"{d['min']}–{d['max']} {units}".strip()
        return "–"

    for roa in roas[:3]:  # Limitar a 3 ROAs
        roa_name = roa.get("name", "Desconocido").capitalize()
        dose = roa.get("dose", {})
        duration = roa.get("duration", {})
        units = dose.get("units", "")

        # 💊 Dosis
        dosis_txt = []
        if dose.get("threshold") is not None:
            dosis_txt.append(f"Light: {dose['threshold']} {units}")
        for label, key in [("Normal", "common"), ("Fuerte", "strong")]:
            val = fmt_range(dose.get(key), units)
            if val != "–":
                dosis_txt.append(f"{label}: {val}")
        if dose.get("heavy") is not None:
            dosis_txt.append(f"Alta: {dose['heavy']} {units}")
        if dosis_txt:
            safe_add_field(embed, name=f"💊 Dosis - {roa_name}", value="\n".join(dosis_txt), inline=False)

        # ⏳ Duración
        duracion_txt = []
        for label, key in [
            ("Inicio", "onset"),
            ("Subida", "comeup"),
            ("Pico", "peak"),
            ("Descenso", "offset"),
            ("Postefectos", "afterglow"),
            ("Total", "total")
        ]:
            d = duration.get(key)
            dur = fmt_range(d, d.get("units", "") if d else "")
            if dur != "–":
                duracion_txt.append(f"{label}: {dur}")
        if duracion_txt:
            safe_add_field(embed, name=f"⏳ Duración - {roa_name}", value="\n".join(duracion_txt), inline=False)

        # 📈 Biodisponibilidad
        bio = roa.get("bioavailability")
        if bio and "min" in bio and "max" in bio:
            bio_val = f"{bio['min']}–{bio['max']}%"
            safe_add_field(embed, name=f"📈 Biodisponibilidad - {roa_name}", value=f"*{bio_val}*", inline=False)

    return embed

def sugerir_sustancias(nombre: str, n=3) -> list[str]:
    """Devuelve una lista de posibles sustancias similares usando difflib."""
    return difflib.get_close_matches(nombre, VALID_SUBSTANCES, n=n, cutoff=0.6)


@tree.command(
    name="info",
    description="Obtiene información de una sustancia desde PsychonautWiki",
    guild=guild
)
@app_commands.describe(sustancia="Nombre de la sustancia a buscar")
async def info(interaction: discord.Interaction, sustancia: str):
    await interaction.response.defer()

    graphql_endpoint = "https://api.psychonautwiki.org/graphql"
    with open("query.graphql", "r", encoding="utf-8") as f:
        query = f.read()

    headers = {
        "User-Agent": "KetaversoBot/1.0 (https://github.com/Triskis777/ketaverso-bot-info)",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    sustancia_input = sustancia.lower().strip()
    sustancia_normalizada = ALIASES.get(sustancia_input, sustancia_input)
    variables = {"name": sustancia_normalizada}

    # Consulta inicial a la API
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.post(graphql_endpoint, json={"query": query, "variables": variables}) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"❌ [ERROR] GraphQL ({resp.status}): {text}")
                return await interaction.followup.send(
                    f"❌ Error al consultar la API (código {resp.status}).",
                    ephemeral=True
                )
            result = await resp.json()

    substances = result.get("data", {}).get("substances", [])

    # Segundo intento: traducir al inglés y volver a consultar
    if not substances:
        try:
            traducido = GoogleTranslator(source='auto', target='en').translate(sustancia_input)
            if traducido.lower() != sustancia_input:
                print(f"🔄 [INFO] Traducción: '{sustancia_input}' → '{traducido}'")
                variables["name"] = traducido
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.post(graphql_endpoint, json={"query": query, "variables": variables}) as retry_resp:
                        retry_data = await retry_resp.json()
                        substances = retry_data.get("data", {}).get("substances", [])
        except Exception as e:
            print(f"⚠️ [ERROR] Traducción fallida: {e}")

    # No se encontró: sugerencias
    if not substances:
        sugerencias = sugerir_sustancias(sustancia_normalizada)
        sugerencia_msg = "No se encontraron sugerencias." if not sugerencias else f"🔎 ¿Quisiste decir: {', '.join(sugerencias)}?"
        embed = discord.Embed(
            title="❌ Sustancia no encontrada",
            description=sugerencia_msg,
            color=0x8e44ad
        )
        return await interaction.followup.send(embed=embed, ephemeral=True)

    # ✅ NUEVA forma: usar embed moderno
    info = substances[0]
    roas = info.get("roas", [])
    if len(roas) > 1:
        await mostrar_info_por_roa(interaction, info)
    else:
        embed = crear_embed_v2(info)
        await interaction.followup.send(embed=embed)

def generar_embed_por_roa(info: dict, index: int) -> discord.Embed:
    """Genera un embed visual con datos de un único ROA, usando emojis y formato claro, incluyendo campos globales."""
    name = info.get("name", "Desconocido")
    summary = info.get("summary", "")
    common_names = info.get("commonNames", [])
    effects = info.get("effects", [])
    roas = info.get("roas", [])
    roa = roas[index]
    roa_name = roa.get("name", "Desconocido").capitalize()
    dose = roa.get("dose", {})
    duration = roa.get("duration", {})
    bio = roa.get("bioavailability", {})

    def fmt_range(val: dict, units="") -> str:
        if val and "min" in val and "max" in val:
            return f"{val['min']}–{val['max']} {units}".strip()
        return "–"

    embed = discord.Embed(
        title=f"💡 {name} - {roa_name}",
        description=summary[:1024],
        color=0x8e44ad
    )

    # 🧪 Alias / nombres comunes
    if common_names:
        safe_add_field(embed, name="🔹 También llamado", value=", ".join(common_names), inline=False)

    # 🎯 Efectos
    if effects:
        limit = 10
        lista = ", ".join(effect["name"] for effect in effects[:limit])
        if len(effects) > limit:
            lista += f"\n[Ver todos los efectos](https://psychonautwiki.org/wiki/{name.replace(' ', '_')}#Effects)"
        safe_add_field(embed, name="🎯 Efectos", value=lista, inline=False)

    # 💊 Dosis
    dosis_txt = []
    units = dose.get("units", "")
    mapping = {
        "threshold": "Light",
        "light": "Baja",
        "common": "Normal",
        "strong": "Alta",
        "heavy": "Muy alta"
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

    # ⏳ Duración
    duracion_txt = []
    labels = {
        "onset": "Inicio",
        "comeup": "Subida",
        "peak": "Pico",
        "offset": "Bajada",
        "afterglow": "Afterglow",
        "total": "Total"
    }
    for key, label in labels.items():
        val = duration.get(key)
        if val:
            duracion_txt.append(f"__**{label}**__: {fmt_range(val, val.get('units', ''))}")
    if duracion_txt:
        embed.add_field(name="⏳ **Duración**", value="\n".join(duracion_txt), inline=False)

    # 📈 Biodisponibilidad
    if bio and "min" in bio and "max" in bio:
        bio_val = f"{bio['min']}–{bio['max']}%"
        safe_add_field(embed, name="📈 Biodisponibilidad", value=f"*{bio_val}*", inline=False)

    embed.set_footer(text=f"ROA {index + 1} de {len(roas)}")

    return embed

class ROAView(discord.ui.View):
    def __init__(self, info: dict, current: int):
        super().__init__(timeout=None)
        self.info = info
        self.current = current
        roas = info.get("roas", [])
        for i, roa in enumerate(roas):
            label = roa.get("name", f"ROA {i+1}").capitalize()
            emoji = "✅" if i == current else "💊"
            self.add_item(ROAButton(info, i, label, emoji))

class ROAButton(discord.ui.Button):
    def __init__(self, info, index: int, label: str, emoji: str):
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


    # 🔁 ANTIGUO - Esto era duplicado, ya está incluido en crear_embed_v2()
    """
    name = info.get("name", "Desconocido")
    summary = info.get("summary", "Sin resumen disponible.")
    common_names = info.get("commonNames", [])
    effects = info.get("effects", [])
    roas_data = info.get("roas", [])
    roas_utiles = [roa for roa in roas_data if roa.get("dose") or roa.get("duration")]
    # Aquí antes se reconstruían campos que ya están en crear_embed_v2()
    """

if __name__ == "__main__":
    client.run(TOKEN)
