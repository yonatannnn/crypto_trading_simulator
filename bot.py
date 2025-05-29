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
    uid = event.sender_id
    get_user(uid)
    await event.respond("Welcome to the trading simulator! Enter your starting balance (in USDT):")

@client.on(events.NewMessage)
async def handler(event):
    uid = event.sender_id
    user = get_user(uid)
    msg = event.message.message.strip()

    if user['balance'] == 0:
        try:
            amount = float(msg)
            update_balance(uid, amount)
            await event.respond(f"Balance set to {amount} USDT. Enter trade symbol (e.g. btcusdt):")
        except:
            await event.respond("Please enter a valid number.")
        return

    last_trade = trades.find_one({"user_id": uid, "status": "initiating"})

    if not last_trade:
        if msg.lower() in symbols:
            trades.insert_one({
                "user_id": uid,
                "symbol": msg.lower(),
                "status": "initiating",
                "step": "amount"
            })
            await event.respond("How many USDT do you want to trade?")
        return

    step = last_trade['step']
    if step == "amount":
        try:
            usdt = float(msg)
            if usdt > user['balance']:
                await event.respond("You don’t have enough balance.")
                return
            trades.update_one({"_id": last_trade['_id']}, {"$set": {"usdt": usdt, "step": "side"}})
            await event.respond("Long or Short?")
        except:
            await event.respond("Invalid amount.")
    elif step == "side":
        if msg.lower() in ['long', 'short']:
            trades.update_one({"_id": last_trade['_id']}, {"$set": {"side": msg.lower(), "step": "target"}})
            await event.respond("Enter your target price:")
        else:
            await event.respond("Please type 'Long' or 'Short'.")
    elif step == "target":
        try:
            target = float(msg)
            trades.update_one({"_id": last_trade['_id']}, {"$set": {"target": target, "step": "stoploss"}})
            await event.respond("Enter stop loss (or type 'skip'):")
        except:
            await event.respond("Invalid target price.")
    elif step == "stoploss":
        if msg.lower() == 'skip':
            stop = None
        else:
            try:
                stop = float(msg)
            except:
                await event.respond("Invalid stop loss.")
                return
        trades.update_one({"_id": last_trade['_id']}, {"$set": {"stop": stop, "step": "leverage"}})
        await event.respond("Enter leverage:")
    elif step == "leverage":
        try:
            leverage = int(msg)
            entry_price = price_cache.get(last_trade['symbol'], 0)
            if entry_price == 0:
                await event.respond("Error fetching price. Try again.")
                return
            position = last_trade['usdt'] * leverage / entry_price
            liquidation_price = entry_price * (1 - (1 / leverage)) if last_trade['side'] == 'long' else entry_price * (1 + (1 / leverage))
            trades.update_one({"_id": last_trade['_id']}, {
                "$set": {
                    "entry": entry_price,
                    "leverage": leverage,
                    "position": position,
                    "liq": liquidation_price,
                    "status": "active",
                    "opened": time.time()
                }
            })
            users.update_one({"_id": uid}, {"$inc": {"balance": -last_trade['usdt']}})
            await event.respond(f"Trade opened at {entry_price}, leverage {leverage}x.")
        except:
            await event.respond("Invalid leverage.")

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
async def bootstrap():
    await update_prices()
    await monitor_trades()

with client:
    client.loop.create_task(update_prices())
    client.loop.create_task(monitor_trades())
    client.run_until_disconnected()

