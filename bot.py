import asyncio
import logging
import os
import time
from telethon import TelegramClient, events
from telethon.tl.custom import Button
from pymongo import MongoClient
from dotenv import load_dotenv
from binance import fetch_price, symbols
from user import get_user, set_balance, get_available_balance, get_equity
from trade import create_trade, monitor_trades, close_trade_by_id

load_dotenv()
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')

client = TelegramClient('bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
price_cache = {}
MONGO_URI = os.getenv('MONGO_URI')

logging.basicConfig(level=logging.INFO)


mongo = MongoClient(MONGO_URI)
db = mongo['trading_bot']
trades = db['trades']
users = db['users']

async def update_prices():
    while True:
        for symbol in symbols:
            try:
                price_cache[symbol] = await fetch_price(symbol)
            except:
                pass
        await asyncio.sleep(5)

@client.on(events.NewMessage(pattern='/start'))
async def start(event):
    get_user(event.sender_id)
    await event.respond("""👋 *Welcome to the Crypto Trading Simulator Bot!*

Simulate leveraged crypto trades using real-time Binance prices.

📘 *Commands*:
/sb <amount> – Set starting balance
/trade <symbol> <lev> <long/short> <target> [stop] <amount> [tp1] [tp2] [tp3]
/balance – Show total equity
/available – Show free funds
/trades – Active trades (inline close)
/history – All trades
/stat – Performance summary
/help – Command list
/about – About this bot
""", parse_mode='markdown')

@client.on(events.NewMessage(pattern=r'/sb (\d+(\.\d{1,2})?)'))
async def sb(event):
    uid = event.sender_id
    user = get_user(uid)
    if user['balance'] > 0:
        await event.respond("You already have a balance.")
        return
    amount = float(event.pattern_match.group(1))
    set_balance(uid, amount)
    await event.respond(f"Balance set to {amount:.2f} USDT")

@client.on(events.NewMessage(pattern=r'/trade (\w+) (\d+) (long|short) (\d+(\.\d+)?)(?: (\d+(\.\d+)?))? (\d+(\.\d+)?)(?: (\d+(\.\d+)?))?(?: (\d+(\.\d+)?))?(?: (\d+(\.\d+)?))?'))
async def trade(event):
    uid = event.sender_id
    parts = event.pattern_match.groups()
    symbol = parts[0].lower()
    leverage = int(parts[1])
    side = parts[2].lower()
    target = float(parts[3])
    stoploss = float(parts[5]) if parts[5] else None
    amount = float(parts[7])
    tp1 = float(parts[9]) if parts[9] else None
    tp2 = float(parts[11]) if parts[11] else None
    tp3 = float(parts[13]) if parts[13] else None

    entry = price_cache.get(symbol)
    if not entry:
        await event.respond("Error fetching current price.")
        return

    available = get_available_balance(uid)
    if amount > available:
        await event.respond(f"Not enough balance. Available: {available:.2f} USDT")
        return

    partial_tps = [p for p in [tp1, tp2, tp3] if p]
    create_trade(uid, symbol, entry, leverage, side, target, stoploss, price_cache, custom_usdt=amount, partial_tps=partial_tps)

    liq = entry * (1 - 1 / leverage) if side == 'long' else entry * (1 + 1 / leverage)
    tp_text = "\n".join([f"TP{i+1}: {tp}" for i, tp in enumerate(partial_tps)]) if partial_tps else "No partial TPs"
    exp_pro = (abs(entry - target) / entry) * amount
    loss = (abs(entry - stoploss) / entry) * amount if stoploss else 0
    await event.respond(
        f"✅ Trade opened!\n\nSymbol: {symbol.upper()} | Side: {side.capitalize()}\n"
        f"Entry: {entry:.2f} | Leverage: {leverage}x\nTarget: {target}\nStop: {stoploss or 'None'}\n"
        f"{tp_text}\n💥 Liquidation: {liq:.2f}",
        f"Expected profit at target {target} is {exp_pro} USDT.",
        f"Expected loss at stop {stoploss or 'None'} is {loss} USDT.",
        parse_mode='markdown'
    )

@client.on(events.NewMessage(pattern='/balance'))
async def balance(event):
    uid = event.sender_id
    equity = get_equity(uid, price_cache)
    await event.respond(f"Total Equity: {equity:.2f} USDT")

@client.on(events.NewMessage(pattern='/available'))
async def available(event):
    uid = event.sender_id
    avail = get_available_balance(uid)
    await event.respond(f"Available for new trades: {avail:.2f} USDT")

@client.on(events.NewMessage(pattern='/trades'))
async def show_active(event):
    uid = event.sender_id
    active = list(trades.find({"user_id": uid, "status": "active"}))
    if not active:
        await event.respond("You have no active trades.")
        return

    for t in active:
        symbol = t['symbol'].upper()
        price = price_cache.get(t['symbol'], t['entry'])
        pnl = (price - t['entry']) * t['position'] * (t['leverage'] if t['side'] == 'long' else -1*[t['leverage']])
        percent = (pnl / t['usdt']) * 100

        msg = (
            f"📍{symbol} | {t['side'].capitalize()}\n"
            f"Entry: {t['entry']:.2f} → Now: {price:.2f}\n"
            f"Leverage: {t['leverage']}x\n"
            f"PnL: {pnl:.2f} USDT ({percent:.2f}%)"
        )

        await event.respond(
            msg,
            buttons=[Button.inline(f"❌ Close {symbol}", data=f"close:{str(t['_id'])}")],
            parse_mode='markdown'
        )

@client.on(events.CallbackQuery(data=lambda d: d.startswith(b'close:')))
async def handle_close_callback(event):
    trade_id = event.data.decode().split(':')[1]
    result, error = close_trade_by_id(trade_id, price_cache)

    if error:
        await event.answer(error, alert=True)
        return

    await event.edit(
        f"✅ *{result['symbol'].upper()}* trade closed.\n"
        f"PnL: {result['pnl']:.2f} USDT ({result['percent']:.2f}%)",
        parse_mode='markdown'
    )

@client.on(events.NewMessage(pattern='/history'))
async def trade_history(event):
    uid = event.sender_id
    all_trades = trades.find({"user_id": uid})
    msg = "📘 *Trade History:*\n"
    for t in all_trades:
        current_price = price_cache.get(t['symbol'], t.get('exit', t['entry']))
        end_price = t.get('exit', current_price)
        pnl = (end_price - t['entry']) * t['position'] * (trade["leverage"] if t['side'] == 'long' else -1 * trade["leverage"])
        percent = (pnl / t['usdt']) * 100
        msg += (
            f"\n🔸 {t['symbol'].upper()} | {t['side'].capitalize()} | {t['status'].capitalize()}\n"
            f"Entry: {t['entry']:.2f} → Exit: {end_price:.2f}\n"
            f"PnL: {pnl:.2f} USDT ({percent:.2f}%)"
        )
    await event.respond(msg, parse_mode='markdown')

@client.on(events.NewMessage(pattern='/stat'))
async def stat(event):
    uid = event.sender_id
    user_trades = list(trades.find({"user_id": uid, "status": "closed"}))
    total_trades = len(user_trades)

    if total_trades == 0:
        await event.respond("You haven't closed any trades yet.")
        return

    wins = 0
    total_pnl = 0
    total_roi = 0

    for t in user_trades:
        entry = t['entry']
        exit_price = t.get('exit', entry)
        pnl = (exit_price - entry) * t['position'] * (trade["leverage"] if t['side'] == 'long' else -1 * trade["leverage"])
        roi = (pnl / t['usdt']) * 100
        if pnl > 0:
            wins += 1
        total_pnl += pnl
        total_roi += roi

    win_rate = (wins / total_trades) * 100
    avg_roi = total_roi / total_trades

    await event.respond(
        f"📊 *Stats*\n\n"
        f"Total Trades: {total_trades}\n"
        f"Win Rate: {win_rate:.2f}%\n"
        f"Total PnL: {total_pnl:.2f} USDT\n"
        f"Average ROI: {avg_roi:.2f}%",
        parse_mode='markdown'
    )

@client.on(events.NewMessage(pattern='/help'))
async def help_cmd(event):
    await event.respond("""📘 *Command List*

🪙 Balance:
/sb <amount> – Set balance
/balance – Show total equity
/available – Show free funds

📈 Trading:
/trade <symbol> <lev> <long/short> <target> [stop] <amount> [tp1] [tp2] [tp3]
/trades – Show open trades (inline close)
/close <symbol> – Close latest trade for symbol
/history – View all trades
/stat – Trade stats summary

ℹ️ Info:
/help – This list
/about – About the bot""", parse_mode='markdown')

@client.on(events.NewMessage(pattern='/about'))
async def about(event):
    await event.respond("📈 *Crypto Trading Simulator Bot*\n\nTrade with real prices, no real risk. Created for learning and fun. Built with Telethon + MongoDB.", parse_mode='markdown')

async def main():
    await client.start()
    await asyncio.gather(update_prices(), monitor_trades(client, price_cache), client.run_until_disconnected())

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
