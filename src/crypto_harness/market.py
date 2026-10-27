"""Read-only REST adapters; collect() is the seam for future MCP providers."""
from datetime import datetime, timezone
from decimal import Decimal as D
from uuid import uuid4
import json
import os
import httpx
from .core import Quote
from .analysis import analyze_ohlcv

ASSETS={'BTC':'bitcoin','ETH':'ethereum','SOL':'solana'}

class Market:
    def __init__(self,client=None):
        self.client=client or httpx.Client(timeout=10,headers={'User-Agent':'crypto-harness/0.1'})

    def get(self,url,**kwargs):
        r=self.client.get(url,**kwargs)
        r.raise_for_status()
        return r.json()

    def collect(self,discovery=False):
        quotes, research, errors={},[],[]
        for symbol in ASSETS:
            try:
                row=self.get('https://data-api.binance.vision/api/v3/ticker/24hr',params={'symbol':symbol+'BRL'})
                if row['symbol']!=symbol+'BRL': raise ValueError('identity mismatch')
                q=Quote(symbol=symbol,bid=row['bidPrice'],ask=row['askPrice'],observed_at=datetime.fromtimestamp(row['closeTime']/1000,timezone.utc),source='binance:spot:'+symbol+'BRL',evidence_id=str(uuid4()))
                if not q.fresh(): raise ValueError('stale')
                quotes[symbol]=q
                research.append({'symbol':symbol,'change_24h_pct':float(row['priceChangePercent']),'volume_brl':float(row['quoteVolume']),'evidence_id':q.evidence_id})
                if discovery:
                    charts=[]
                    for interval in ('5m','15m','1h'):
                        try:
                            candles=self.get('https://data-api.binance.vision/api/v3/klines',params={'symbol':symbol+'BRL','interval':interval,'limit':101})
                            timestamp=datetime.now(timezone.utc).timestamp()*1000
                            closed=[{'time_ms':int(c[0]),'open':float(c[1]),'high':float(c[2]),'low':float(c[3]),'close':float(c[4]),'volume':float(c[5])} for c in candles if int(c[6])<=timestamp]
                            charts.append(analyze_ohlcv(closed[-100:],interval))
                        except (httpx.HTTPError,ValueError,KeyError,TypeError,IndexError):
                            errors.append('binance:'+symbol+':'+interval+'_history_unavailable')
                    research.append({'symbol':symbol,'source':'binance:closed_ohlcv_multi_timeframe','chart_analysis':charts,'evidence_id':q.evidence_id})
            except (httpx.HTTPError,ValueError,KeyError,TypeError,OverflowError): errors.append('binance:'+symbol+':unavailable')
        missing=[s for s in ASSETS if s not in quotes]
        if missing:
            try:
                headers={}
                if os.getenv('COINGECKO_DEMO_KEY'): headers['x-cg-demo-api-key']=os.environ['COINGECKO_DEMO_KEY']
                rows=self.get('https://api.coingecko.com/api/v3/simple/price',params={'ids':','.join(ASSETS[s] for s in missing),'vs_currencies':'brl','include_last_updated_at':'true','include_24hr_change':'true'},headers=headers)
                for s in missing:
                    try:
                        row=rows[ASSETS[s]]
                        price=D(str(row['brl']))
                        q=Quote(symbol=s,bid=price*D('.999'),ask=price*D('1.001'),observed_at=datetime.fromtimestamp(row['last_updated_at'],timezone.utc),source='coingecko:'+ASSETS[s]+':estimated_spread',evidence_id=str(uuid4()))
                        if not q.fresh(): raise ValueError('stale')
                        quotes[s]=q
                        research.append({'symbol':s,'change_24h_pct':row.get('brl_24h_change'),'evidence_id':q.evidence_id,'warning':'spread estimated, no order book'})
                    except (ValueError,KeyError,TypeError,OverflowError): errors.append('coingecko:'+s+':invalid')
            except (httpx.HTTPError,ValueError,KeyError,TypeError): errors.append('coingecko:unavailable')
        if discovery:
            try:
                data=self.get('https://api.dexscreener.com/latest/dex/search',params={'q':'SOL/USDC'})
                for p in (data.get('pairs') or [])[:5]:
                    research.append({'source':'dexscreener','chain':str(p['chainId'])[:30],'contract':str(p['baseToken']['address'])[:100],'pool':str(p['pairAddress'])[:100],'liquidity_usd':float((p.get('liquidity') or {}).get('usd',0)),'tradable':False,'reason':'contract security not verified'})
            except (httpx.HTTPError,ValueError,KeyError,TypeError): errors.append('dexscreener:unavailable')
        return quotes,research,errors

    def close(self): self.client.close()
