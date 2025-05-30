import time
import asyncio
from pymongo import MongoClient
from bson import ObjectId
import os
from dotenv import load_dotenv

load_dotenv()
MONGO_URI = os.getenv('MONGO_URI')

mongo = MongoClient(MONGO_URI)
db = mongo['trading_bot']
trades = db['trades']
users = db['users']

def create_trade(uid, symbol, entry, leverage, side, target, stop, price_cache, custom_usdt=None, partial_tps=None):
    user = users.find_one({"_id": uid})
    usdt = custom_usdt or (user['balance'] / 2)
    position = usdt * leverage / entry
    liq = entry * (1 - 1 / leverage) if side == 'long' else entry * (1 + 1 / leverage)

    trades.insert_one({
        "user_id": uid,
        "symbol": symbol,
        "usdt": usdt,
        "side": side,
        "entry": entry,
        "target": target,
        "stop": stop,
        "leverage": leverage,
        "position": position,
        "liq": liq,
        "status": "active",
        "opened": time.time(),
        "partial_tps": partial_tps or [],
        "tp_hits": []
    })

    users.update_one({"_id": uid}, {"$inc": {"balance": -usdt}})


async def monitor_trades(client, price_cache):
    while True:
        active = list(trades.find({"status": "active"}))
        for trade in active:
            price = price_cache.get(trade['symbol'], 0)
            if not price:
                continue
            pnl = (price - trade['entry']) * trade['position'] * (1 if trade['side'] == 'long' else -1)
            uid = trade['user_id']
            hit_target = (price >= trade['target']) if trade['side'] == 'long' else (price <= trade['target'])
            hit_stop = (price <= trade['stop']) if trade['side'] == 'long' else (price >= trade['stop']) if trade['stop'] else False
            hit_liq = (price <= trade['liq']) if trade['side'] == 'long' else (price >= trade['liq'])

            if hit_target or hit_stop or hit_liq:
                reason = "target" if hit_target else "stop loss" if hit_stop else "liquidation"
                final_pnl = pnl if reason != 'liquidation' else -trade['usdt']
                users.update_one({"_id": uid}, {"$inc": {"balance": trade['usdt'] + final_pnl}})
                trades.update_one({"_id": trade['_id']}, {"$set": {
                    "status": "closed",
                    "exit": price,
                    "closed": time.time()
                }})
                await client.send_message(uid, f"Trade closed due to {reason}. Final PnL: {pnl:.2f} USDT")
        await asyncio.sleep(5)

def close_trade_by_id(trade_id, price_cache):
    trade = trades.find_one({"_id": ObjectId(trade_id), "status": "active"})
    if not trade:
        return None, "Trade not found or already closed."

    price = price_cache.get(trade['symbol'], trade['entry'])
    pnl = (price - trade['entry']) * trade['position'] * (1 if trade['side'] == 'long' else -1)
    percent = (pnl / trade['usdt']) * 100

    users.update_one({"_id": trade['user_id']}, {"$inc": {"balance": trade['usdt'] + pnl}})
    trades.update_one({"_id": trade['_id']}, {
        "$set": {"status": "closed", "exit": price, "closed": time.time()}
    })

    return {
        "symbol": trade['symbol'],
        "pnl": pnl,
        "percent": percent
    }, None
