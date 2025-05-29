import aiohttp

symbols = ['btcusdt', 'solusdt', 'ethusdt', 'ethfiusdt']

async def fetch_price(symbol):
    async with aiohttp.ClientSession() as session:
        async with session.get(f'https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}') as resp:
            data = await resp.json()
            return float(data['price'])
