import asyncio
import redis.asyncio as aioredis     # Async version of the Redis client
import httpx        # Modern async HTTP client for API calls
from telegram.ext import Application, CommandHandler

REDIS_URL = "redis://localhost"
ODH_API_URL = "https://mobility.api.opendatahub.com/v2/flat,node/ParkingStation"
FETCH_INTERVAL = 1800  # 30 minutes in seconds

# --- FUNCTION THAT UPDATES REDIS DB EVERY 30 MINUTES  ---
async def fetch_odh_data_periodically(redis):
    while True:
        try:
            print("🔄 Fetching fresh data from Open Data Hub...")
            async with httpx.AsyncClient() as client:
                response = await client.get(ODH_API_URL)
                if response.status_code == 200:
                    # Store the whole JSON string in Redis
                    await redis.set("parking_data_trento", response.text)
                    print("✅ Redis updated successfully.")
                else:
                    print(f"⚠️ API Error: {response.status_code}")
        except Exception as e:
            print(f"❌ Fetcher Error: {e}")

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

    application = Application.builder().token("8581484431:AAH5KmReRZ7rkiNtIrvrrMqu12AN8YOgrSA").build()
    application.bot_data['redis'] = redis
    application.add_handler(CommandHandler("start", start_handler))

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
            await redis.close()

if __name__ == '__main__':
    asyncio.run(main())