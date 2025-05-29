import asyncio
import logging
from telethon import TelegramClient, events
from pymongo import MongoClient
import aiohttp
import time
from datetime import datetime
from dotenv import load_dotenv
import os

# --- Load Environment Variables ---
load_dotenv()

# --- Setup ---
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')

client = TelegramClient('bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
mongo = MongoClient(MONGO_URI)
db = mongo['trading_bot']
users = db['users']
trades = db['trades']

symbols = ['btcusdt', 'solusdt', 'ethusdt', 'ethfiusdt']
price_cache = {}

logging.basicConfig(level=logging.INFO)

# --- Price Fetching ---
async def fetch_price(symbol):
    async with aiohttp.ClientSession() as session:
        async with session.get(f'https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}') as resp:
            data = await resp.json()
            return float(data['price'])

async def update_prices():
    while True:
        for symbol in symbols:
            try:
                price_cache[symbol] = await fetch_price(symbol)
            except:
                pass
        await asyncio.sleep(5)

# --- Helpers ---
def get_user(uid):
    user = users.find_one({"_id": uid})
    if not user:
        user = {"_id": uid, "balance": 0.0}
        users.insert_one(user)
    return user

def update_balance(uid, new_balance):
    users.update_one({"_id": uid}, {"$set": {"balance": new_balance}})

# --- Command Handlers ---

@client.on(events.NewMessage(pattern='/start'))
async def start(event):
    get_user(event.sender_id)
    await event.respond("Welcome to the trading simulator! Use /sb <amount> to set your starting balance.")

@client.on(events.NewMessage(pattern=r'/sb (\d+(\.\d{1,2})?)'))
async def set_balance(event):
    uid = event.sender_id
    user = get_user(uid)
    if user['balance'] > 0:
        await event.respond("You have already set your balance.")
        return
    amount = float(event.pattern_match.group(1))
    update_balance(uid, amount)
    await event.respond(f"Balance set to {amount} USDT.")

@client.on(events.NewMessage(pattern=r'/trade (\w+) (\d+(\.\d+)?) (\d+) (long|short) (\d+(\.\d+)?)(?: (\d+(\.\d+)?))?'))
async def trade(event):
    uid = event.sender_id
    user = get_user(uid)

    if user['balance'] <= 0:
        await event.respond("Please set your balance first using /sb <amount>.")
        return

    parts = event.pattern_match.groups()
    symbol = parts[0].lower()
    entry = float(parts[1])
    leverage = int(parts[3])
    side = parts[4].lower()
    target = float(parts[5])
    stoploss = float(parts[7]) if parts[7] else None

    if symbol not in symbols:
        await event.respond(f"Invalid symbol. Supported: {', '.join(symbols)}")
        return

    usdt = user['balance'] / 2  # Use 50% of balance for simplicity
    position = usdt * leverage / entry
    liq = entry * (1 - 1 / leverage) if side == 'long' else entry * (1 + 1 / leverage)

    trades.insert_one({
        "user_id": uid,
        "symbol": symbol,
        "usdt": usdt,
        "side": side,
        "entry": entry,
        "target": target,
        "stop": stoploss,
        "leverage": leverage,
        "position": position,
        "liq": liq,
        "status": "active",
        "opened": time.time()
    })

    users.update_one({"_id": uid}, {"$inc": {"balance": -usdt}})
    await event.respond(
        f"Trade opened: {symbol.upper()} | {side} | Entry: {entry} | Leverage: {leverage}x | "
        f"Target: {target} | Stop Loss: {stoploss or 'None'}"
    )

@client.on(events.NewMessage(pattern='/balance'))
async def balance(event):
    uid = event.sender_id
    user = get_user(uid)
    await event.respond(f"Your current balance is {user['balance']:.2f} USDT")

@client.on(events.NewMessage(pattern='/active'))
async def active_trades(event):
    uid = event.sender_id
    active = list(trades.find({"user_id": uid, "status": "active"}))
    if not active:
        await event.respond("You have no active trades.")
        return

    response = "Your active trades:\n"
    for t in active:
        symbol = t['symbol']
        price = price_cache.get(symbol, t['entry'])
        pnl = (price - t['entry']) * t['position'] * (1 if t['side'] == 'long' else -1)
        response += f"\n{symbol.upper()} | {t['side'].capitalize()} | Entry: {t['entry']:.2f} | Now: {price:.2f} | PnL: {pnl:.2f} USDT"
    await event.respond(response)

@client.on(events.NewMessage(pattern='/terminate'))
async def terminate(event):
    uid = event.sender_id
    active_trade = trades.find_one({"user_id": uid, "status": "active"})
    if not active_trade:
        await event.respond("You have no active trade.")
        return
    price = price_cache.get(active_trade['symbol'], active_trade['entry'])
    pnl = (price - active_trade['entry']) * active_trade['position'] * (1 if active_trade['side'] == 'long' else -1)
    users.update_one({"_id": uid}, {"$inc": {"balance": active_trade['usdt'] + pnl}})
    trades.update_one({"_id": active_trade['_id']}, {"$set": {"status": "closed", "closed": time.time(), "exit": price}})
    await event.respond(f"Trade closed. PnL: {pnl:.2f} USDT")

# --- Trade Monitoring ---
async def monitor_trades():
    while True:
        active_trades = list(trades.find({"status": "active"}))
        for trade in active_trades:
            price = price_cache.get(trade['symbol'], 0)
            if price == 0:
                continue
            pnl = (price - trade['entry']) * trade['position'] * (1 if trade['side'] == 'long' else -1)
            uid = trade['user_id']
            hit_target = (price >= trade['target']) if trade['side'] == 'long' else (price <= trade['target'])
            hit_stop = (price <= trade['stop']) if trade['side'] == 'long' else (price >= trade['stop']) if trade['stop'] else False
            hit_liq = (price <= trade['liq']) if trade['side'] == 'long' else (price >= trade['liq'])

            if hit_target or hit_stop or hit_liq:
                reason = "target" if hit_target else "stop loss" if hit_stop else "liquidation"
                users.update_one({"_id": uid}, {"$inc": {"balance": trade['usdt'] + (pnl if reason != 'liquidation' else -trade['usdt'])}})
                trades.update_one({"_id": trade['_id']}, {"$set": {"status": "closed", "exit": price, "closed": time.time()}})
                await client.send_message(uid, f"Trade closed due to {reason}. Final PnL: {pnl:.2f} USDT")

        await asyncio.sleep(5)

# --- Main ---
async def main():
    await client.start()
    await asyncio.gather(
        update_prices(),
        monitor_trades(),
        client.run_until_disconnected()
    )

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())

