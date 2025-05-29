import asyncio
import logging
import os
from telethon import TelegramClient, events
from dotenv import load_dotenv
from binance import fetch_price, symbols
from user import get_user, set_balance, get_available_balance, get_equity
from trade import create_trade, monitor_trades

# Load environment
load_dotenv()
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')

client = TelegramClient('bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
price_cache = {}

logging.basicConfig(level=logging.INFO)

# Price updater
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
    welcome_text = """
👋 *Welcome to the Crypto Trading Simulator Bot!*

You can simulate leveraged crypto trades with live Binance prices.
Start by setting your balance and placing trades.

📘 *Commands*:
/sb <amount> – Set your starting balance
/trade <symbol> <entry> <leverage> <long|short> <target> [stoploss] – Open a trade
/balance – Show your total equity
/available – Show unallocated funds
/help – Show all commands
/about – Learn about this bot
"""
    await event.respond(welcome_text, parse_mode='markdown')



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

@client.on(events.NewMessage(pattern=r'/trade (\w+) (\d+(\.\d+)?) (\d+) (long|short) (\d+(\.\d+)?)(?: (\d+(\.\d+)?))?'))
async def trade(event):
    uid = event.sender_id
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

    create_trade(uid, symbol, entry, leverage, side, target, stoploss, price_cache)
    await event.respond(
        f"Trade opened: {symbol.upper()} | {side} | Entry: {entry} | Leverage: {leverage}x | "
        f"Target: {target} | Stop Loss: {stoploss or 'None'}"
    )

@client.on(events.NewMessage(pattern='/balance'))
async def balance(event):
    uid = event.sender_id
    equity = get_equity(uid, price_cache)
    await event.respond(f"Total Equity: {equity:.2f} USDT")

@client.on(events.NewMessage(pattern='/help'))
async def help_cmd(event):
    help_text = """
📘 *Trading Bot Commands*:

/start – Initialize your account
/sb <amount> – Set starting balance (first time only)
/trade <symbol> <entry> <leverage> <long|short> <target> [stoploss] – Open a new trade
/balance – View your total equity (available + open trades)
/available – See how much is free for new trades
/help – Show this help message
/about – About this bot
"""
    await event.respond(help_text, parse_mode='markdown')

@client.on(events.NewMessage(pattern='/about'))
async def about(event):
    await event.respond(
        "📈 *Crypto Trading Simulator Bot*\n\n"
        "This bot simulates leveraged crypto trades using live Binance prices.\n"
        "- Paper-trade with virtual USDT\n"
        "- Track equity and available balance\n"
        "- Supports BTC, ETH, SOL, and more\n\n"
        "Built with ❤️ using Python, Telethon, MongoDB, and Binance API.",
        parse_mode='markdown'
    )


@client.on(events.NewMessage(pattern='/available'))
async def available(event):
    uid = event.sender_id
    avail = get_available_balance(uid)
    await event.respond(f"Available for new trades: {avail:.2f} USDT")

# Main runner
async def main():
    await client.start()
    await asyncio.gather(update_prices(), monitor_trades(client, price_cache), client.run_until_disconnected())

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
