import httpx
from crypto_harness.market import Market
from crypto_harness.market import AssetCandidate, eligible_candidates

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
