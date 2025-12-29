import asyncio
import os
from contextlib import nullcontext

import httpx  # Modern async HTTP client for API calls
import redis.asyncio as aioredis  # Async version of the Redis client
from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
REDIS_URL = "redis://localhost"
ODH_API_URL = "https://mobility.api.opendatahub.com/v2/flat,node/ParkingStation"
FETCH_INTERVAL = 1800  # 30 minutes in seconds
CITY_MAP = {
    "findparkingtn": ("Trento", "Municipality of Trento"),
    "findparkingbz": ("Bolzano - Bozen", "Municipality of Bozen"),
}


# --- FUNCTION THAT DISTINGUISH THE CITY FROM THE COMMAND ---

async def find_parking_basedcom(update, context):
    full_command = update.message.text.split()[0].replace("/", "")

    city_info = CITY_MAP.get(full_command)
    if not city_info:
        await update.message.reply_text("Unknown city command.")
        return

    city_name, odh_filter = city_info
    redis = context.application.bot_data['redis']
    if city_name == "Bolzano - Bozen":
        cache_key = f"parking_data_bolzano"
        cached_data = await redis.get(cache_key)
    elif city_name == "Trento":
        cache_key = f"parking_data_trento"
        cached_data = await redis.get(cache_key)
    if cached_data:
        # Process and send the data back to user
        await update.message.reply_text(f"🅿️ Available spots in {city_name}: {cached_data}")
    else:
        await update.message.reply_text(f"Sorry, data for {city_name} is currently updating.")


# --- FUNCTION THAT UPDATES REDIS DB EVERY 30 MINUTES  ---
async def fetch_odh_data_periodically(redis):
    while True:
        async with httpx.AsyncClient() as client:
            for command, (city_name, filter_val) in CITY_MAP.items():
                try:
                    url = f"{ODH_API_URL}?where=smetadata.municipality.eq.\"{filter_val}\"&shownull=false&distinct=true"
                    response = await client.get(url)
                    if response.status_code == 200:
                        if city_name == "Bolzano - Bozen":
                            cache_key = f"parking_data_bolzano"
                        elif city_name == "Trento":
                            cache_key = f"parking_data_trento"
                        await redis.set(cache_key, response.text)
                        print(f"✅ Updated cache for {city_name}")
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
