"""Read-only REST adapters; collect() is the seam for future MCP providers."""
from datetime import datetime, timezone
from decimal import Decimal as D
from uuid import uuid4
import json
import os
import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .core import Quote
from .analysis import analyze_ohlcv

class AssetCandidate(BaseModel):
    """Normalized research record. Missing security never becomes execution approval."""
    model_config=ConfigDict(extra='forbid')
    symbol: str = Field(pattern=r'^[A-Z0-9]{2,20}$')
    source: str
    venue_symbol: str | None = None
    network: str | None = None
    contract_address: str | None = None
    liquidity_brl: float = Field(default=0,ge=0)
    volume_brl: float = Field(default=0,ge=0)
    age_hours: float | None = Field(default=None,ge=0)
    history_bars: int = Field(default=0,ge=0)
    chart_status: str = Field(default='unknown')
    security: dict = Field(default_factory=dict)
    evidence_id: str
    execution_eligible: bool = False
    rejection_reasons: list[str] = Field(default_factory=list)
    chart_analysis: list[dict] = Field(default_factory=list)

    @model_validator(mode='after')
    def classify(self):
        reasons=[]
        if self.liquidity_brl < 25_000: reasons.append('liquidity_below_minimum')
        if self.volume_brl < 10_000: reasons.append('volume_below_minimum')
        if self.chart_status == 'not_enriched': reasons.append('chart_not_enriched')
        elif self.chart_status == 'unavailable': reasons.append('chart_data_unavailable')
        elif self.history_bars < 60: reasons.append('chart_window_insufficient')
        if self.source == 'dexscreener':
            if not self.contract_address: reasons.append('missing_contract')
            if self.security.get('sellable') is not True or self.security.get('honeypot') is not False or self.security.get('holder_concentration') is None:
                reasons.append('security_unverified')
        self.rejection_reasons=sorted(set(reasons))
        self.execution_eligible=not self.rejection_reasons
        return self

def eligible_candidates(candidates,limit=12,require_chart_history=True):
    approved=[]
    for candidate in candidates:
        if candidate.execution_eligible:
            approved.append(candidate)
        elif not require_chart_history:
            # Monitoring/position review needs a fresh quote, not a new entry
            # thesis. Do not turn a chart-incomplete candidate into an entry.
            non_chart=[reason for reason in candidate.rejection_reasons if reason.startswith('chart_') is False]
            if candidate.source=='binance' and not non_chart:
                approved.append(candidate)
    return sorted(approved,key=lambda c:(c.volume_brl,c.liquidity_brl),reverse=True)[:limit]

class Market:
    def __init__(self,client=None):
        self.client=client or httpx.Client(timeout=4,headers={'User-Agent':'crypto-harness/0.1'})

    def get(self,url,**kwargs):
        r=self.client.get(url,**kwargs)
        r.raise_for_status()
        return r.json()

    def _charts(self,venue_symbol,rate,errors):
        charts=[]
        for interval in ('5m','15m','1h','4h'):
            try:
                candles=self.get('https://data-api.binance.vision/api/v3/klines',params={'symbol':venue_symbol,'interval':interval,'limit':101})
                stamp=datetime.now(timezone.utc).timestamp()*1000
                closed=[{'open':float(c[1])*rate,'high':float(c[2])*rate,'low':float(c[3])*rate,'close':float(c[4])*rate,'volume':float(c[5])} for c in candles if int(c[6])<=stamp]
                charts.append(analyze_ohlcv(closed[-100:],interval))
            except (httpx.HTTPError,ValueError,KeyError,TypeError,IndexError): errors.append('binance:'+venue_symbol+':'+interval+'_history_unavailable')
        return charts

    def _binance(self,discovery,errors):
        rows=self.get('https://data-api.binance.vision/api/v3/ticker/24hr')
        if not isinstance(rows,list): raise ValueError('ticker list expected')
        by_symbol={row.get('symbol'):row for row in rows}
        conversion=D(str(by_symbol.get('USDTBRL',{}).get('lastPrice','0')))
        if conversion<=0: raise ValueError('USDTBRL conversion unavailable')
        candidates=[]
        blocked=('UPUSDT','DOWNUSDT','BULLUSDT','BEARUSDT')
        liquid=[row for row in rows if isinstance(row,dict) and str(row.get('symbol','')).endswith('USDT') and not str(row.get('symbol','')).endswith(blocked) and float(row.get('quoteVolume',0) or 0)>0]
        for rank,row in enumerate(sorted(liquid,key=lambda item:float(item['quoteVolume']),reverse=True)[:20]):
            venue=row['symbol']; symbol=venue[:-4]
            if not symbol.isalnum() or not 2<=len(symbol)<=20 or symbol in {'USDT','USDC','FDUSD','TUSD','USDP','BUSD'}: continue
            evidence=str(uuid4()); rate=float(conversion)
            # Research may list 20 liquid assets, but only the best 8 receive
            # four-timeframe enrichment and can become execution candidates.
            enriched=discovery and rank<8
            charts=self._charts(venue,rate,errors) if enriched else []
            history=min((chart.get('bars',0) for chart in charts),default=0)
            chart_status='ready' if history>=60 else 'unavailable' if enriched else 'not_enriched'
            candidate=AssetCandidate(symbol=symbol,source='binance',venue_symbol=venue,liquidity_brl=float(row['quoteVolume'])*rate,volume_brl=float(row['quoteVolume'])*rate,age_hours=10_000,history_bars=history,chart_status=chart_status,security={'sellable':True,'honeypot':False,'holder_concentration':0},evidence_id=evidence,chart_analysis=charts)
            candidates.append((candidate,row,conversion))
        return candidates

    def _coingecko(self,errors):
        headers={}
        if os.getenv('COINGECKO_DEMO_KEY'): headers['x-cg-demo-api-key']=os.environ['COINGECKO_DEMO_KEY']
        output=[]
        try:
            trending=self.get('https://api.coingecko.com/api/v3/search/trending',headers=headers)
            for item in (trending.get('coins') or [])[:15]:
                coin=item.get('item') or {}; symbol=str(coin.get('symbol','')).upper()
                if symbol.isalnum() and len(symbol)<=20:
                    output.append(AssetCandidate(symbol=symbol,source='coingecko_trending',network=None,liquidity_brl=0,volume_brl=0,history_bars=0,chart_status='unavailable',security={},evidence_id=str(uuid4())))
        except (httpx.HTTPError,ValueError,KeyError,TypeError): errors.append('coingecko:trending_unavailable')
        return output

    def _dex(self,errors):
        output=[]
        try:
            profiles=self.get('https://api.dexscreener.com/token-profiles/latest/v1')
            for profile in (profiles or [])[:3]:
                network=str(profile.get('chainId',''))[:30] or None
                address=str(profile.get('tokenAddress',''))[:100] or None
                if not network or not address: continue
                try:
                    pairs=self.get('https://api.dexscreener.com/token-pairs/v1/'+network+'/'+address)
                    pair=max((pairs or []),key=lambda item:float((item.get('liquidity') or {}).get('usd',0)))
                    base=pair.get('baseToken') or {}
                    symbol=str(base.get('symbol','')).upper()
                    if not symbol.isalnum() or not 2<=len(symbol)<=20: continue
                    liquidity=float((pair.get('liquidity') or {}).get('usd',0))*5
                    volume=float((pair.get('volume') or {}).get('h24',0))*5
                    age=max(0,(datetime.now(timezone.utc).timestamp()-float(pair.get('pairCreatedAt',0))/1000)/3600) if pair.get('pairCreatedAt') else None
                    output.append(AssetCandidate(symbol=symbol,source='dexscreener',network=network,contract_address=address,liquidity_brl=liquidity,volume_brl=volume,age_hours=age,history_bars=0,chart_status='unavailable',security={},evidence_id=str(uuid4())))
                except (httpx.HTTPError,ValueError,KeyError,TypeError): errors.append('dexscreener:'+network+':pair_unavailable')
        except (httpx.HTTPError,ValueError,KeyError,TypeError): errors.append('dexscreener:profiles_unavailable')
        return output

    def collect(self,discovery=False):
        quotes,errors={},[]
        candidates=[]
        try:
            cex=self._binance(discovery,errors)
            candidates.extend(item[0] for item in cex)
            for candidate,row,conversion in cex:
                if not candidate.execution_eligible: continue
                bid=D(str(row['bidPrice']))*conversion; ask=D(str(row['askPrice']))*conversion
                quote=Quote(symbol=candidate.symbol,bid=bid,ask=ask,observed_at=datetime.fromtimestamp(int(row['closeTime'])/1000,timezone.utc),source='binance:spot:'+candidate.venue_symbol,evidence_id=candidate.evidence_id,execution_eligible=True)
                if quote.fresh(): quotes[quote.symbol]=quote
        except (httpx.HTTPError,ValueError,KeyError,TypeError,OverflowError): errors.append('binance:discovery_unavailable')
        if discovery:
            candidates.extend(self._coingecko(errors)); candidates.extend(self._dex(errors))
        approved=eligible_candidates(candidates,limit=12,require_chart_history=discovery)
        approved_symbols={candidate.symbol for candidate in approved}
        quotes={symbol:quote.model_copy(update={'execution_eligible':symbol in approved_symbols}) for symbol,quote in quotes.items() if symbol in approved_symbols}
        research={'summary':{'assets_researched':len(candidates),'assets_filtered':len(approved),'memecoins_analyzed':sum(c.source=='dexscreener' for c in candidates),'security_rejections':sum('security_unverified' in c.rejection_reasons for c in candidates)},'approved_candidates':[c.model_dump() for c in approved],'rejected_candidates':[c.model_dump() for c in candidates if not c.execution_eligible][:50]}
        return quotes,research,errors

    def close(self): self.client.close()
