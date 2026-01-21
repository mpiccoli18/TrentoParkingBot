import asyncio
import os, json, math, re
import httpx
from datetime import datetime
from dotenv import load_dotenv
from redis import asyncio as aioredis
from Adapter import RedisAdapter as RedisAda
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

CITY_TO_KEY = {
    "trento": "parking_data_trento",
    "bolzano": "parking_data_bolzano",
}

# ----------------- helpers -----------------

def ask_location_markup():
    kb = [[KeyboardButton("Share location 📍", request_location=True)]]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True)

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlmb/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def parse_coords_from_gmaps(url: str):
    """
    Try to extract lat,lon from common Google Maps link patterns.
    Works for links like:
    - ...q=46.07,11.12
    - ...@46.07,11.12,17z
    """
    if not url:
        return None, None

    m = re.search(r"[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)", url)
    if m:
        return float(m.group(1)), float(m.group(2))

    m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url)
    if m:
        return float(m.group(1)), float(m.group(2))

    return None, None

def parse_coords_from_geom(geom: str):
    # Trento gives "POINT(lon lat)"
    if not geom:
        return None, None
    m = re.search(r"POINT\(([-\d.]+)\s+([-\d.]+)\)", geom)
    if not m:
        return None, None
    lon = float(m.group(1))
    lat = float(m.group(2))
    return lat, lon

def normalize_url(u: str) -> str:
    if not u:
        return ""
    u = u.strip()
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return "https://" + u


# ----------------- parsing Trento -----------------

def parse_trento_parks(raw_json_str: str):
    parks = json.loads(raw_json_str)  # list
    out = []

    for p in parks:
        # Optional: keep only car park parkings (skip bike etc.)
        # If you want to keep everything, comment the next 2 lines:
        if p.get("type") != "park":
            continue

        name = p.get("name", "Unknown")
        free = p.get("freeslots")
        cap = p.get("capacity")
        updated = p.get("updated_at") or p.get("update") or ""
        gmaps = p.get("gmaps") or ""
        website = p.get("link") or ""
        regulation = (p.get("regulation") or "").lower()
        address = p.get("address") or ""

        # Fee label (simple heuristic like in your adapter formatting)
        if "gratuito" in regulation or "free" in regulation:
            fee_type = "🆓 Free"
        elif "disco orario" in regulation:
            fee_type = "🕒 Limited time"
        else:
            fee_type = "💰 Paid"

        # coords: try explicit fields, then gmaps, then geom "POINT(lon lat)"
        lat = p.get("lat") or p.get("latitude")
        lon = p.get("lon") or p.get("longitude")

        if lat is None or lon is None:
            lat, lon = parse_coords_from_gmaps(gmaps)

        if lat is None or lon is None:
            lat, lon = parse_coords_from_geom(p.get("geom"))

        # if no gmaps, build a Google Maps link from coords
        link = gmaps
        if not link and lat is not None and lon is not None:
            link = f"https://www.google.com/maps?q={lat},{lon}"

        out.append({
            "name": name,
            "free": free,
            "cap": cap,
            "updated": updated,
            "lat": lat,
            "lon": lon,
            "link": link,
            "fee_type": fee_type,
            "website": website,
            "note": address,
        })

    return out


# ----------------- parsing Bolzano (ODH) -----------------

def parse_bolzano_parks(raw_json_str: str):
    data = json.loads(raw_json_str)  # dict with "data":[...]
    items = data.get("data", [])

    by_code = {}

    for it in items:
        # keep only "free" measurements
        if (it.get("tname") or "").lower() != "free" and (it.get("tdescription") or "").lower() != "free":
            continue

        scode = it.get("scode") or it.get("pcode") or ""
        scode = str(scode)

        # normalize pcode like "105_facility" -> "105"
        if scode.endswith("_facility"):
            scode = scode.replace("_facility", "")

        if not scode:
            continue

        # coordinates (prefer pcoordinate)
        coord = it.get("pcoordinate") or it.get("scoordinate") or {}
        lat = coord.get("y")
        lon = coord.get("x")

        pmeta = it.get("pmetadata") or {}
        smeta = it.get("smetadata") or {}

        api_name = (
            pmeta.get("standard_name")
            or smeta.get("standard_name")
            or it.get("pname")
            or it.get("sname")
            or pmeta.get("name_it")
            or smeta.get("name_it")
            or scode
        )

        free = it.get("mvalue")
        updated = it.get("mvalidtime") or it.get("_timestamp") or it.get("mtransactiontime") or ""

        # default map link from coords
        link = ""
        if lat is not None and lon is not None:
            link = f"https://www.google.com/maps?q={lat},{lon}"

        cap = pmeta.get("capacity") or smeta.get("capacity")

        # ---- ENRICH using adapter map (your static pricing/links) ----
        pricing = RedisAda.BZ_PRICING_MAP.get(scode, {})

        name = pricing.get("name") or api_name
        fee_type = pricing.get("fee_type") or ""
        day_rate = pricing.get("day_rate") or ""
        night_rate = pricing.get("night_rate") or ""
        max_24h = pricing.get("max_24h") or ""
        website = pricing.get("website") or ""
        note = pricing.get("note") or ""

        # prefer the curated Google Maps link from pricing map if present
        if pricing.get("maps"):
            link = pricing["maps"]

        by_code[scode] = {
            "scode": scode,
            "name": name,
            "free": free,
            "cap": cap,
            "updated": updated,
            "lat": lat,
            "lon": lon,
            "link": link,
            "fee_type": fee_type,
            "day_rate": day_rate,
            "night_rate": night_rate,
            "max_24h": max_24h,
            "website": website,
            "note": note,
        }

    return list(by_code.values())


# ----------------- formatting -----------------

def pretty_updated(val):
    if val is None or val == "":
        return "unknown"

    # unix timestamp like 1768491063
    if isinstance(val, (int, float)) or (isinstance(val, str) and val.isdigit()):
        try:
            ts = int(val)
            # if milliseconds, convert to seconds
            if ts > 10_000_000_000:
                ts //= 1000
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    
    if isinstance(val, str):
        s = val.replace("T", " ")
        s = s.split(".")[0]           # remove .000
        s = s.replace("+0000", "").replace("Z", "")    # remove timezone suffix
        return s.strip()
    # otherwise keep original (Bolzano often gives a readable string already)
    return str(val)

def format_results(title, results, user_lat, user_lon, limit=10):
    lines = [f"<b>{title}</b>"]

    for r in results[:limit]:
        name = r.get("name", "Unknown")
        free = r.get("free")
        cap = r.get("cap")
        updated = r.get("updated", "")
        link = r.get("link", "")

        lat, lon = r.get("lat"), r.get("lon")
        dist_txt = ""
        if lat is not None and lon is not None:
            d = haversine_km(user_lat, user_lon, float(lat), float(lon))
            dist_txt = f" — <i>{d:.2f} km</i>"

        # traffic-light icon based on free spots
        if free is None:
            icon = "⚪"
        else:
            try:
                f = float(free)
                icon = "🟢" if f > 30 else ("🟠" if f > 0 else "🔴")
            except Exception:
                icon = "⚪"

        free_txt = "?" if free is None else str(int(free)) if isinstance(free, (int, float)) else str(free)
        cap_txt = "" if cap in (None, "", "??") else f" of {cap}"

        # Build detail rows (we’ll make the last one start with └)
        detail = []
        detail.append(f"Free spots: <b>{free_txt}</b>{cap_txt}")
        if r.get("fee_type"):
            detail.append(f"Type: {r['fee_type']}")
        if r.get("day_rate"):
            detail.append(f"Day rate: {r['day_rate']}")
        if r.get("night_rate"):
            detail.append(f"Night rate: {r['night_rate']}")
        if r.get("max_24h"):
            detail.append(f"Max 24h: {r['max_24h']}")
        detail.append(f"Updated: {pretty_updated(updated)}")

        if r.get("website"):
            w = r["website"]
            detail.append(f"Website: <a href='{normalize_url(w)}'>{w}</a>")

        if r.get("note"):
            detail.append(f"Info: {r['note']}")

        if link:
            detail.append(f"Location: <a href='{normalize_url(link)}'>click here!</a>")

        # render with ├ / └
        rendered = []
        for i, row in enumerate(detail):
            prefix = "└" if i == len(detail) - 1 else "├"
            rendered.append(f"{prefix} {row}")

        lines.append(f"\n{icon} <b>{name}</b>{dist_txt}\n" + "\n".join(rendered))

    return "\n".join(lines)


# ----------------- telegram handlers -----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hello, I am your personal Parking Bot!👋\n"
        "I can find the nearest parking spots in the province of Trento or Bolzano. Cool right?\n"
        "To start searching the perfect spot, use the following commands:\n"
        "- /findparkingTN to search the nearest parking spots in Trento\n"
        "- /findparkingBZ to search the nearest parking spots in Bolzano - Bozen\n\n"
        "Then share your location 📍 and wait for my response."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Here's the list of all available commands you can use:\n"
        "/start - Start the bot\n"
        "/help - Learn about what the commands do\n"
        "/findparkingTN - Will present the nearest parking spots to you, in Trento, including information like distance, google maps direction, price, number of free spots, and when available, the website\n"
        "/findparkingBZ - Will present the nearest parking spots to you, in Bolzano, including information like distance, google maps direction, price, number of free spots, and when available, the website\n"
        "/stop - Stop the bot"
    )

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bye! 👋")

async def findparking_tn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["city"] = "trento"
    await update.message.reply_text(
        "Send your location to find nearby parking in Trento:",
        reply_markup=ask_location_markup()
    )

async def findparking_bz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["city"] = "bolzano"
    await update.message.reply_text(
        "Send your location to find nearby parking in Bolzano - Bozen:",
        reply_markup=ask_location_markup()
    )


async def fetch_live_raw(city: str) -> str:
    # reuse the URLs already defined in RedisAdapter.py
    if city == "trento":
        url = RedisAda.TRENTO_API_URL
    else:
        url = RedisAda.BOLZANO_API_URL

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text


async def on_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = context.user_data.get("city")
    if not city:
        await update.message.reply_text("Use /findparkingtn or /findparkingbz first 🙂")
        return

    redis = context.application.bot_data["redis"]
    key = CITY_TO_KEY[city]

    # 1) Try LIVE API first (primary)
    try:
        raw = await fetch_live_raw(city)
        # write-through cache (backup for later)
        await redis.set(key, raw)
        source = "live"
    except Exception:
        # 2) If API fails, fallback to Redis cache
        raw = await redis.get(key)
        source = "cache" if raw else "none"

    if source == "none":
        await update.message.reply_text("Live API is down and no cached data is available yet.")
        return

    user_lat = update.message.location.latitude
    user_lon = update.message.location.longitude
    suffix = "" if source == "live" else " (cached — API offline)"

    if city == "trento":
        parks = parse_trento_parks(raw)
        # keep only those with coords so distance works (optional)
        parks = [p for p in parks if p["lat"] is not None and p["lon"] is not None]
        parks.sort(key=lambda p: haversine_km(user_lat, user_lon, float(p["lat"]), float(p["lon"])))
        msg = format_results("🅿️ TRENTO - LIVE PARKING STATUS" + suffix, parks, user_lat, user_lon)
    else:
        parks = parse_bolzano_parks(raw)
        parks = [p for p in parks if p["lat"] is not None and p["lon"] is not None]
        parks.sort(key=lambda p: haversine_km(user_lat, user_lon, float(p["lat"]), float(p["lon"])))
        msg = format_results("🅿️ BOLZANO/BOZEN - LIVE PARKING STATUS" + suffix, parks, user_lat, user_lon, limit=5)

    await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)

async def setup(app: Application):
    redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
    app.bot_data["redis"] = redis
    
    # start periodic cache refresh in background (backup only)
    app.bot_data["fetch_task"] = asyncio.create_task(RedisAda.fetch_data_periodically(redis))


def main():
    app = Application.builder().token(TOKEN).post_init(setup).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("findparkingtn", findparking_tn))
    app.add_handler(CommandHandler("findparkingbz", findparking_bz))

    # location messages
    app.add_handler(MessageHandler(filters.LOCATION, on_location))

    app.run_polling()

if __name__ == "__main__":
    main()
