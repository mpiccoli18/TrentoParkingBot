import asyncio
import os
import json
import httpx                                # Modern async HTTP client for API calls
import redis.asyncio as aioredis            # Async version of the Redis client
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
REDIS_URL = "redis://localhost"
BOLZANO_API_URL = "https://mobility.api.opendatahub.com/v2/flat,node/ParkingStation/*/latest?limit=-1&where=smetadata.municipality.eq.%22Bolzano%20-%20Bozen%22&sactive.eq.true"
TRENTO_API_URL = "https://parcheggi.comune.trento.it/static/services/registry_parks.json"
BOLZANO_API_URL_TEST = ""
TRENTO_API_URL_TEST = ""
FETCH_INTERVAL = 300  # 5 minutes in seconds
CITY_MAP = {
    "findparkingtn": ("Trento", "Municipality of Trento"),
    "findparkingbz": ("Bolzano - Bozen", "Municipality of Bozen"),
}

# Detailed pricing and regulation map based on March 2025 data
BZ_PRICING_MAP = {
    "103": {
        "name": "Walther P3",
        "fee_type": "💰 Paid",
        "day_rate": "€4.50/h (06:00-20:00)",
        "night_rate": "€1.00/h (20:00-06:00)",
        "max_24h": "€24.00",
        "website": "www.bestinparking.it",
        "maps": "https://maps.app.goo.gl/nHPnMfq6hYPgWLu6A",
        "note": "City Center"
    }, # Walther P3
    "104": {
        "name": "Luna/Mondschein P4",
        "fee_type": "💰 Paid",
        "day_rate": "€3.00 1st hr, then €1.50 per 30min",
        "night_rate": "€1.00/h (20:00-08:00)",
        "max_24h": "N/A",
        "website": "www.mymondscheinparking.com",
        "maps": "https://maps.app.goo.gl/adkniibwVL2wBXG37",
        "note": "Via Molini"
    }, # Luna P4
    "105": {
        "name": "Laurin P5",
        "fee_type": "💰 Paid",
        "day_rate": "€3.80/h (06:00-20:00)",
        "night_rate": "€2.80/h (20:00-24:00), €1.80/h (24:00-06:00)",
        "max_24h": "€27.00",
        "website": "Not found",
        "maps": "https://maps.app.goo.gl/kyut3F2z69VMMogf8",
        "note": "Via Laurin"
    }, # Laurin
    "106": {
        "name": "Lauben P6",
        "fee_type": "💰 Paid",
        "day_rate": "€2.50/h (07:00-21:00)",
        "night_rate": "€1.00/h (21:00-07:00)",
        "max_24h": "€20.00",
        "website": "Not found",
        "maps": "https://maps.app.goo.gl/JyubZLq83JL6hCPHA",
        "note": "Piazza Stazione"
    }, # Lauben
    "107": {
        "name": "Mareccio/Maretsch P7",
        "fee_type": "💰 Paid",
        "day_rate": "€2.60/h (07:00-20:00)",
        "night_rate": "€1.40/h (20:00-07:00)",
        "max_24h": "Sun: €1.40/h (00-24)",
        "website": "geimende.bozen.it",
        "maps": "https://maps.app.goo.gl/zYcg4RUj1vjFJctT7",
        "note": "Via C. De Medici"
    }, # Mareccio
    "108": {
        "name": "BZ Centro/Mitte P8",
        "fee_type": "💰 Paid",
        "day_rate": "€2.00/h (08:00-20:00)",
        "night_rate": "€1.20/h (20:00-08:00)",
        "max_24h": "€20.00",
        "website": "seab.bz.it",
        "maps": "https://maps.app.goo.gl/QJzdcL7p7dTDCuvp8",
        "note": "Via Mayr Nusser"
    }, # BZ Centro
    "116": {
        "name": "Fiera/Messe",
        "fee_type": "🆓 Partial Free",
        "day_rate": "First 2 hours FREE, then €0.50/h",
        "night_rate": "€0.50/h",
        "max_24h": "N/A",
        "website": "fierabolzano.it",
        "maps": "https://maps.app.goo.gl/1c5enqgRrtV65QXX9",
        "note": "Daily 24h"
    }, # Fiera
    "115": {
        "name": "Palasport/Stadthalle",
        "fee_type": "💰 Budget",
        "day_rate": "€0.30/h",
        "night_rate": "€0.30/h",
        "max_24h": "N/A",
        "website": "seab.bz.it",
        "maps": "https://maps.app.goo.gl/74YnzucpfUZab9t39",
        "note": "Via Milano"
    }, # Palasport
    "101": {
        "name": "Rosenbach",
        "fee_type": "💰 Paid",
        "day_rate": "€0.50/h (06:00-23:00)",
        "night_rate": "€4.00 flat rate (20:00-07:00)",
        "max_24h": "N/A",
        "website": "seab.bz.it",
        "maps": "https://maps.app.goo.gl/NYEQDimYV2np5vBD9",
        "note": "Piazza Nikoletti"
    }, # RosenBach
    "113": {
        "name": "Direzional P13",
        "fee_type": "💰 Paid",
        "day_rate": "€1.60/h (1st 5h), then €1.10/h",
        "night_rate": "€1.50/h (20:00-07:00)",
        "max_24h": "Closed Sun/Holidays",
        "website": "aci.it",
        "maps": "https://maps.app.goo.gl/SoL4PNQAjAjbsuer6",
        "note": "Viale Duca D'Aosta"
    }, # Direzional P13
    "114": {
        "name": "Tribunale/Gerichtsplatz P14",
        "fee_type": "💰 Paid",
        "day_rate": "€1.50/h (08:00-20:00)",
        "night_rate": "€1.00/h (20:00-08:00)",
        "max_24h": "WE Rate: €8.00",
        "website": "seab.bz.it",
        "maps": "https://maps.app.goo.gl/vBUcxsDQckDCCceC7",
        "note": "Piazza Tribunale"
    }, # Tribunale
    "parking-bz:8:0": {
        "name": "Turist Parking",
        "fee_type": "🆓 Free of charge",
        "day_rate": "N/A",
        "night_rate": "N/A",
        "max_24h": "N/A",
        "website": "Not found",
        "maps": "https://maps.app.goo.gl/r1yEeSc36qP1Ap9cA",
        "note": "Via del Macello"
    }, # Turist Parking
    "609883_0": {
        "name": "Waltherpark",
        "fee_type": "💰 Paid",
        "day_rate": "€4.50/h (06:00-20:00)",
        "night_rate": "€1.00/h (20:00-06:00)",
        "max_24h": "€ 24.00",
        "website": "waltherpark.com",
        "maps": "https://maps.app.goo.gl/gdCF4hbBycgKRsRn7",
        "note": "Piazza dell'Alto Adige"
    }
}



# --- FUNCTION THAT PARSE THE MESSAGE FOR THE USER IN ORDER TO BE READABLE (ONLY FOR TRENTO) ---
def format_trento_message(raw_json_str):
    try:
        parks = json.loads(raw_json_str) if isinstance(raw_json_str, str) else raw_json_str

        if not parks:
            return "❌ Trento parking data is currently unavailable."

        message = "<b>🅿️ TRENTO - LIVE PARKING STATUS</b>\n\n"

        car_parks = [p for p in parks if p.get('type') == 'park']

        sorted_parks = sorted(
            car_parks,
            key=lambda x: x.get('freeslots', 0),
            reverse=True
        )

        for p in sorted_parks:
            name = p.get('name', 'Unknown Parking')
            free = p.get('freeslots', 0)
            total = p.get('capacity', '??')
            gmaps = p.get('gmaps', '??')
            website = p.get('link', '??')
            street = p.get('address', '??')

            # --- Payment logic ---
            reg = p.get('regulation', '').lower()
            if "gratuito" in reg or "free" in reg:
                fee_label = "🆓 Free"
            elif "disco orario" in reg:
                fee_label = "🕒 Limited time (Disc)"
            else:
                fee_label = "💰 Paid"

            # --- System check ---
            if p.get('offline') is True:
                message += f"⚪ <b>{name}</b>\n└ <i>Status: Maintenance</i>\n\n"
                continue

            # --- Last update ---
            updated_ts = p.get('updated_at', 0)
            updated_time = datetime.fromtimestamp(updated_ts).strftime('%H:%M')

            # --- Visual Indicator ---
            if free > 20: icon = "🟢"
            elif free > 0: icon = "🟠"
            else: icon = "🔴"

            message += f"{icon} <b>{name}</b>\n"
            message += f"├ Type: {fee_label}\n"
            message += f"├ Free spots: <code>{free}</code> of {total} (updated {updated_time})\n"
            message += f"├ Updated: {updated_time}\n"
            message += f"├ Website: <a href=\"{website}\">{website}</a>\n"
            message += f"├ Info: {street}</a>\n"
            message += f"└ Location: <a href=\"{gmaps}\">click here!</a> \n \n"
        return message

    except Exception as e:
        return f"⚠️ Data Processing Error: {str(e)}"


# --- FUNCTION THAT PARSE THE MESSAGE FOR THE USER IN ORDER TO BE READABLE (ONLY FOR BOLZANO/BOZEN) ---
def format_bolzano_message(raw_json_str):
    try:
        data = json.loads(raw_json_str) if isinstance(raw_json_str, str) else raw_json_str
        items = data.get('data', [])

        #Keep only recent data (not old)
        parking = [
            i for i in items
            if i.get('ttype') == "Instantaneous"
               and i.get('tname') == "free"
               and any(y in str(i.get("mvalidtime", "")) for y in ("2025", "2026"))
        ]

        if not parking:
            return "❌ Bolzano garage data is currently offline."

        message = "<b>🅿️ BOLZANO/BOZEN - LIVE PARKING STATUS</b>\n\n"

        for g in parking:
            scode = g.get('scode')
            meta = g.get('smetadata', {})
            # Name logic
            name = meta.get('name_en') or meta.get('standard_name') or g.get('sname')
            free = g.get('mvalue', 0)
            total = meta.get('capacity', '??')

            # Format time (e.g., 09:06)
            raw_time = g.get('mvalidtime', '')
            time_display = raw_time.split(" ")[1][:5] if " " in raw_time else "recent"

            # Visual Indicator
            if free > 30: icon = "🟢"
            elif free > 0: icon = "🟠"
            else: icon = "🔴"

            #Pricing Indicator
            price_info = BZ_PRICING_MAP.get(scode, {
                "fee_type": "💰 Paid",
                "day_rate": "Rates on the site",
                "maps": "Location unavailable",
                "website": "",
                "note": ""
            })

            message += f"{icon} <b>{name}</b>\n"
            message += f"├ Free spots: <code>{int(free)}</code> of {total}\n"
            message += f"├ Type: {price_info['fee_type']}"
            message += f"├ Day rate: {price_info['day_rate']}\n"
            message += f"├ Night rate: {price_info['night_rate']}\n"
            message += f"├ Max 24h: {price_info['max_24h']}\n"
            message += f"├ Updated: {time_display}\n"
            message += f"├ Website: <a href=\"{price_info['website']}\">{price_info['website']}</a> \n"
            message += f"├ Location: <a href=\"{price_info['maps']}\">click here!</a> \n"
            message += f"└ Info: {price_info['note']}\n \n"

        return message

    except Exception as e:
        return f"⚠️ Data Processing Error: {str(e)}"


"""
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

        if not raw_data:
            await update.message.reply_text(f"Sorry, data for {city_name} is currently updating.")
            return
        clean_message = format_bolzano_message(raw_data)
        await update.message.reply_text(clean_message, parse_mode='HTML')
        return
    elif city_name == "Trento":
        cache_key = "parking_data_trento"
        raw_data = await redis.get(cache_key)
        if not raw_data:
            await update.message.reply_text(f"Sorry, data for {city_name} is currently updating.")
            return

        clean_message = format_trento_message(raw_data)
        await update.message.reply_text(clean_message, parse_mode='HTML')
        return
    else:
        await update.message.reply_text("Unknown city.")

"""

# --- FUNCTION THAT UPDATES REDIS DB EVERY 5 MINUTES  ---
async def fetch_data_periodically(redis):
    while True:
        async with httpx.AsyncClient() as client:
            for command, (city_name, filter_val) in CITY_MAP.items():
                try:
                    #print(f"{city_name}")
                    if city_name == "Bolzano - Bozen":
                        cache_key = "parking_data_bolzano"
                        url = f"{BOLZANO_API_URL}"
                    elif city_name == "Trento":
                        cache_key = "parking_data_trento"
                        url = f"{TRENTO_API_URL}"
                    #print(f"{url}")
                    response = await client.get(url)
                    if response.status_code == 200:
                        await redis.set(cache_key, response.text)
                        print(f"✅ Updated cache for {city_name}")
                        #print(f"{response.text}")
                    else:
                        print(f"❌ Status error: {response.status_code}")
                except Exception as e:
                    print(f"❌ Error Fetching {city_name}: {e}")

        # Sleep for 5 minutes without stopping the rest of the script
        await asyncio.sleep(FETCH_INTERVAL)