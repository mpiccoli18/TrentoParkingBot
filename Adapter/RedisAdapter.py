import asyncio
import os
import json
import httpx  # Modern async HTTP client for API calls
import redis.asyncio as aioredis  # Async version of the Redis client
from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
REDIS_URL = "redis://localhost"
ODH_API_URL = "https://mobility.api.opendatahub.com/v2/flat,node/ParkingStation/*/latest?rows=-1&"
FETCH_INTERVAL = 1800  # 30 minutes in seconds
CITY_MAP = {
    "findparkingtn": ("Trento", "Municipality of Trento"),
    "findparkingbz": ("Bolzano - Bozen", "Municipality of Bozen"),
}
TRENTO_STATUS_FALLBACK = {
    "TRENTO:areaexsitviacanestrinip1": "⛔ CHIUSO (Area Dismessa)",
    "TRENTO:piazzalesanseverinop7": "⚠️ Parziale accessibilità (Lavori)",
}



# --- FUNCTION THAT PARSE THE MESSAGE FOR THE USER IN ORDER TO BE READABLE ---
def format_parking_message(city_name, raw_json):
    try:
        data = json.loads(raw_json)
        items = data.get('data', [])

        live_data = [
            i for i in items
            if i.get('ttype') == "Instantaneous" and i.get('tname') == "free"
        ]

        if not live_data:
            return f"❌ No active sensor data for {city_name}."

        message = f"<b>🅿️ {city_name.upper()} STATUS</b>\n\n"

        for item in live_data:
            scode = item.get('scode')
            meta = item.get('smetadata', {})
            name = meta.get('standard_name') or item.get('sname')
            free_spots = int(item.get('mvalue', 0))
            capacity = meta.get('capacity', '??')

            if scode in TRENTO_STATUS_FALLBACK:
                status_text = TRENTO_STATUS_FALLBACK[scode]
                message += f"<b>{name}</b>\n└ Status: <code>{status_text}</code>\n\n"
                continue

            timestamp_str = item.get('mvalidtime')
            is_stale = "2025" not in timestamp_str

            icon = "🟢" if free_spots > 10 else "🔴"
            if is_stale: icon = "⚠️" # Warning for old data

            message += f"{icon} <b>{name}</b>\n"
            message += f"└ Parking spots: <code>{free_spots}</code> of {capacity}\n"

            if is_stale:
                # Format the date for the user
                date_part = timestamp_str.split(" ")[0]
                message += f"└ <i>Last update: {date_part}</i>\n"
            message += "\n"

        return message
    except Exception as e:
        return f"⚠️ Parsing Error: {str(e)}"

# --- FUNCTION THAT DISTINGUISH THE CITY FROM THE COMMAND ---

async def find_parking_basedcom(update, context):
    full_command = update.message.text.split()[0].replace("/", "")

    city_info = CITY_MAP.get(full_command)
    if not city_info:
        await update.message.reply_text("Unknown city command.")
        return
    city_name, _ = city_info
    redis = context.application.bot_data['redis']
    if city_name == "Bolzano - Bozen":
        cache_key = "parking_data_bolzano"
        raw_data = await redis.get(cache_key)
    elif city_name == "Trento":
        cache_key = "parking_data_trento"
        raw_data = await redis.get(cache_key)
    if raw_data:
        # Process and send the data back to user
        clean_message = format_parking_message(city_name, raw_data)
        await update.message.reply_text(clean_message, parse_mode='HTML')
    else:
        await update.message.reply_text(f"Sorry, data for {city_name} is currently updating.")


# --- FUNCTION THAT UPDATES REDIS DB EVERY 30 MINUTES  ---
async def fetch_odh_data_periodically(redis):
    while True:
        async with httpx.AsyncClient() as client:
            for command, (city_name, filter_val) in CITY_MAP.items():
                try:
                    #print(f"{city_name}")
                    url = f"{ODH_API_URL}where=smetadata.municipality.eq.\"{city_name}\"&shownull=false&distinct=true&"
                    #print(f"{url}")
                    response = await client.get(url)
                    if response.status_code == 200:
                        if city_name == "Bolzano - Bozen":
                            cache_key = "parking_data_bolzano"
                        elif city_name == "Trento":
                            cache_key = "parking_data_trento"
                        await redis.set(cache_key, response.text)
                        print(f"✅ Updated cache for {city_name}")
                        #print(f"{response.text}")
                except Exception as e:
                    print(f"❌ Error Fetching {city_name}: {e}")

        # Sleep for 30 minutes without stopping the rest of the script
        await asyncio.sleep(FETCH_INTERVAL)

# FUNCTION FOR LISTENING TO THE BOT ---
async def start_handler(update, context):
    redis = context.application.bot_data['redis']
    cached_data = await redis.get("parking_data_trento")

    if cached_data:
        await update.message.reply_text("Here is the latest (cached) parking info!")
    else:
        await update.message.reply_text("Data is currently unavailable. Try again in a moment.")

async def main():
    redis = await aioredis.from_url("redis://localhost", decode_responses=True)

    application = Application.builder().token(TOKEN).build()
    application.bot_data['redis'] = redis
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler(list(CITY_MAP.keys()), find_parking_basedcom))

    async with application:
        await application.start()
        await application.updater.start_polling()

        print("🚀 Bot is starting and Fetcher is scheduled...")

        try:
            await asyncio.gather(
                fetch_odh_data_periodically(redis), #fetch every 30 minutes the data
                asyncio.Event().wait()              #waits for the calls from the bot
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("🛑 Shutting down...")
        finally:
            # Cleanly stop the bot before the loop closes
            await application.updater.stop()
            await application.stop()
            await redis.aclose()

if __name__ == '__main__':
    asyncio.run(main())
