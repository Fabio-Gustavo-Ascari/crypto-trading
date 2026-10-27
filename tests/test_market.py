import httpx
from crypto_harness.market import Market

def test_all_providers_down_yields_empty_quotes():
    client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    quotes, research, errors=Market(client).collect()
    assert quotes=={} and errors

def test_bad_price_rejected():
    def response(r):
        if 'binance' in str(r.url): return httpx.Response(200,json=[{'symbol':'BTCBRL','bidPrice':'NaN','askPrice':'1','closeTime':1}])
        return httpx.Response(200,json={})
    assert Market(httpx.Client(transport=httpx.MockTransport(response))).collect()[0]=={}

def test_coingecko_fallback_retains_source_timestamp():
    import time
    timestamp=int(time.time())
    def response(r):
        if 'binance' in str(r.url): return httpx.Response(429)
        return httpx.Response(200,json={name:{'brl':100,'last_updated_at':timestamp} for name in ('bitcoin','ethereum','solana')})
    quotes,_,errors=Market(httpx.Client(transport=httpx.MockTransport(response))).collect()
    assert len(quotes)==3
    assert quotes['BTC'].observed_at.timestamp()==timestamp
    assert 'estimated_spread' in quotes['BTC'].source
    assert len(errors)==3
