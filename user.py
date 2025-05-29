from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv()
MONGO_URI = os.getenv('MONGO_URI')

mongo = MongoClient(MONGO_URI)
db = mongo['trading_bot']
users = db['users']
trades = db['trades']

def get_user(uid):
    user = users.find_one({"_id": uid})
    if not user:
        user = {"_id": uid, "balance": 0.0}
        users.insert_one(user)
    return user

def set_balance(uid, amount):
    users.update_one({"_id": uid}, {"$set": {"balance": amount}})

def get_available_balance(uid):
    user = get_user(uid)
    return user['balance']

def get_equity(uid, price_cache):
    user = get_user(uid)
    active_trades = trades.find({"user_id": uid, "status": "active"})
    equity = user['balance']
    for trade in active_trades:
        symbol = trade['symbol']
        price = price_cache.get(symbol, trade['entry'])
        pnl = (price - trade['entry']) * trade['position'] * (1 if trade['side'] == 'long' else -1)
        equity += trade['usdt'] + pnl
    return equity
