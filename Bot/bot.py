import asyncio
import os, json, math, re
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

# ----------------- parsing Trento -----------------

def parse_trento_parks(raw_json_str: str):
    parks = json.loads(raw_json_str)  # list
    out = []
    for p in parks:
        name = p.get("name", "Unknown")
        free = p.get("freeslots")
        cap = p.get("capacity")
        updated = p.get("updated_at") or p.get("update") or ""
        gmaps = p.get("gmaps") or p.get("location") or ""

        # try direct fields first
        lat = p.get("lat") or p.get("latitude")
        lon = p.get("lon") or p.get("longitude")

        if lat is None or lon is None:
            lat, lon = parse_coords_from_gmaps(gmaps)

        out.append({
            "name": name,
            "free": free,
            "cap": cap,
            "updated": updated,
            "link": gmaps,
            "lat": lat,
            "lon": lon
        })
    return out

# ----------------- parsing Bolzano (ODH) -----------------

def parse_bolzano_parks(raw_json_str: str):
    data = json.loads(raw_json_str)  # dict with "data":[...]
    items = data.get("data", [])

    # aggregate by parking station code: keep "free" measurement + metadata
    by_code = {}
    for it in items:
        # free spots only
        if (it.get("tname") or "").lower() != "free":
            continue

        scode = it.get("scode")
        if not scode:
            continue

        coord = it.get("scoordinate") or {}
        lat = coord.get("y")
        lon = coord.get("x")

        md = it.get("smetadata") or {}
        name = md.get("name") or md.get("title") or scode
        info = md.get("address") or md.get("info") or ""

        free = it.get("mvalue")
        updated = it.get("mvalidtime") or ""

        by_code[scode] = {
            "name": name,
            "free": free,
            "updated": updated,
            "info": info,
            "lat": lat,
            "lon": lon,
            # You can keep website/pricing if you already have a map
            "link": md.get("url") or ""
        }

    return list(by_code.values())

# ----------------- formatting -----------------

def format_results(title, results, user_lat, user_lon, limit=10):
    lines = [f"<b>{title}</b>"]
    for r in results[:limit]:
        name = r["name"]
        free = r.get("free")
        updated = r.get("updated", "")
        link = r.get("link", "")

        lat, lon = r.get("lat"), r.get("lon")
        dist_txt = ""
        if lat is not None and lon is not None:
            d = haversine_km(user_lat, user_lon, float(lat), float(lon))
            dist_txt = f" — <i>{d:.2f} km</i>"

        free_txt = "?" if free is None else str(free)

        lines.append(
            f"\n🟢 <b>{name}</b>{dist_txt}\n"
            f"├ Free spots: <b>{free_txt}</b>\n"
            f"├ Updated: {updated}\n"
            + (f"└ Location: <a href='{link}'>click here</a>\n" if link else "")
        )

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
    await RedisAda.start()

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Here's the list of all available commands you can use:\n"
        "/start\n"
        "/help\n"
        "/findparkingTN\n"
        "/findparkingBZ\n"
        "/stop"
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

async def on_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = context.user_data.get("city")
    if not city:
        await update.message.reply_text("Use /findparkingTN or /findparkingBZ first 🙂")
        return

    redis = context.application.bot_data["redis"]
    key = CITY_TO_KEY[city]
    raw = await redis.get(key)

    if not raw:
        await update.message.reply_text("Cache is updating. Try again in a minute.")
        return

    user_lat = update.message.location.latitude
    user_lon = update.message.location.longitude

    if city == "trento":
        parks = parse_trento_parks(raw)
        # keep only those with coords so distance works (optional)
        parks = [p for p in parks if p["lat"] is not None and p["lon"] is not None]
        parks.sort(key=lambda p: haversine_km(user_lat, user_lon, float(p["lat"]), float(p["lon"])))
        msg = format_results("TRENTO — NEAREST PARKING", parks, user_lat, user_lon)
    else:
        parks = parse_bolzano_parks(raw)
        parks = [p for p in parks if p["lat"] is not None and p["lon"] is not None]
        parks.sort(key=lambda p: haversine_km(user_lat, user_lon, float(p["lat"]), float(p["lon"])))
        msg = format_results("BOLZANO/BOZEN — NEAREST PARKING", parks, user_lat, user_lon)

    await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)

async def setup(app: Application):
    redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
    app.bot_data["redis"] = redis

    async with app:
        #   await app.start()
        #await app.updater.start_polling()

        print("🚀 Bot is starting and Fetcher is scheduled...")

        try:
            await asyncio.gather(
                RedisAda.fetch_data_periodically(redis),     #Fetch every 5 minutes the data
                asyncio.Event().wait()                       #Waits for the calls from the bot
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("🛑 Shutting down...")
        finally:
            # Cleanly stop the bot before the loop closes
            await app.updater.stop()
            await app.stop()
            await redis.aclose()

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
