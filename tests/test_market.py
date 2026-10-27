import httpx
from crypto_harness.market import Market, aggregate_chart_bars
from crypto_harness.market import AssetCandidate, Market, eligible_candidates

def test_all_providers_down_yields_empty_quotes():
    client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    quotes, research, errors=Market(client).collect()
    assert quotes=={} and errors

def test_bad_price_rejected():
    def response(r):
        if 'binance' in str(r.url): return httpx.Response(200,json=[{'symbol':'BTCBRL','bidPrice':'NaN','askPrice':'1','closeTime':1}])
        return httpx.Response(200,json={})
    assert Market(httpx.Client(transport=httpx.MockTransport(response))).collect()[0]=={}

def test_coingecko_trending_is_research_only_when_binance_is_down():
    def response(r):
        if 'binance' in str(r.url): return httpx.Response(429)
        if 'coingecko' in str(r.url): return httpx.Response(200,json={'coins':[{'item':{'symbol':'new'}}]})
        return httpx.Response(200,json=[])
    quotes,research,errors=Market(httpx.Client(transport=httpx.MockTransport(response))).collect(discovery=True)
    assert quotes=={}
    assert research['summary']['assets_researched'] >= 1
    assert 'binance:discovery_unavailable' in errors

def test_insecure_dex_token_remains_research_only():
    candidate=AssetCandidate(symbol='MEME',source='dexscreener',network='solana',contract_address='abc',liquidity_brl=50000,volume_brl=40000,age_hours=12,history_bars=80,security={'sellable':None,'honeypot':None,'holder_concentration':None},evidence_id='x')
    assert eligible_candidates([candidate]) == []
    assert 'security_unverified' in candidate.rejection_reasons

def test_liquid_cex_candidate_is_bounded_for_execution():
    candidates=[AssetCandidate(symbol=f'A{i}',source='binance',venue_symbol=f'A{i}USDT',liquidity_brl=1_000_000,volume_brl=100_000+i,age_hours=200,history_bars=100,security={'sellable':True,'honeypot':False,'holder_concentration':.1},evidence_id=str(i)) for i in range(25)]
    approved=eligible_candidates(candidates,limit=8)
    assert len(approved)==8
    assert all(candidate.execution_eligible for candidate in approved)

def test_stablecoins_are_not_directional_execution_candidates():
    candidate=AssetCandidate(symbol='USDT',source='binance',venue_symbol='USDTUSDT',liquidity_brl=1_000_000,volume_brl=1_000_000,age_hours=100,history_bars=100,security={'sellable':True,'honeypot':False,'holder_concentration':0},evidence_id='stable')
    assert not candidate.execution_eligible
    assert 'stablecoin_not_directional' in candidate.rejection_reasons

def test_chart_timeframes_are_fixed_for_the_current_runner():
    assert Market.CHART_INTERVALS == ('5m','15m','1h','4h')

def test_aggregate_chart_bars_builds_complete_10m_and_20m_periods_only():
    bars=[
        {'time_ms':0,'open':10,'high':12,'low':9,'close':11,'volume':2},
        {'time_ms':300_000,'open':11,'high':14,'low':10,'close':13,'volume':3},
        {'time_ms':600_000,'open':13,'high':15,'low':12,'close':14,'volume':5},
        {'time_ms':900_000,'open':14,'high':16,'low':13,'close':15,'volume':7},
        {'time_ms':1_200_000,'open':15,'high':17,'low':14,'close':16,'volume':11},
    ]
    ten=aggregate_chart_bars(bars,10)
    twenty=aggregate_chart_bars(bars,20)
    assert ten == [
        {'open':10,'high':14,'low':9,'close':13,'volume':5},
        {'open':13,'high':16,'low':12,'close':15,'volume':12},
    ]
    assert twenty == [{'open':10,'high':16,'low':9,'close':15,'volume':17}]
