"""Transactional paper portfolio, independent policy checks and audit journal."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from decimal import Decimal as D
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import uuid4
import json
import sqlite3
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator

def now():
    return datetime.now(timezone.utc)

def day():
    return now().astimezone(timezone(timedelta(hours=-3))).date().isoformat()

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)

class Quote(Strict):
    symbol: Literal['BTC','ETH','SOL']
    bid: D = Field(gt=0)
    ask: D = Field(gt=0)
    observed_at: AwareDatetime
    source: str
    evidence_id: str

    @model_validator(mode='after')
    def valid_spread(self):
        if self.ask < self.bid: raise ValueError('crossed book')
        return self

    def fresh(self):
        return -10 <= (now()-self.observed_at).total_seconds() <= 180

class Proposal(Strict):
    decision_id: str = Field(min_length=1,max_length=100)
    action: Literal['BUY','SELL','HOLD','REJECT']
    symbol: str = Field(max_length=30)
    amount_brl: D = Field(ge=0)
    stop_pct: D = Field(gt=0,lt=100)
    horizon_minutes: int = Field(ge=1,le=1440)
    reason: str = Field(min_length=1,max_length=2000)
    evidence_ids: list[str] = Field(max_length=20)

class Policy(Strict):
    initial_cash: D = D('200')
    position_fraction: D = D('.05')
    daily_loss: D = D('.03')
    experiment_loss: D = D('.08')
    max_positions: int = 3
    fee: D = D('.001')
    slippage: D = D('.002')

    @property
    def fingerprint(self):
        return sha256(self.model_dump_json().encode()).hexdigest()

class Store:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, at TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS calls(day TEXT PRIMARY KEY, count INTEGER NOT NULL);
            ''')
            c.execute('INSERT OR IGNORE INTO state VALUES(1,?)',(json.dumps({'cash':'200','positions':{},'day':day(),'day_equity':'200','peak':'200','halted_day':None,'halted_experiment':False}),))

    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.path,timeout=10)
        try:
            with c: yield c
        finally: c.close()

    def state(self,c=None):
        if c is not None: return json.loads(c.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
        with self.connect() as conn: return self.state(conn)

    def event(self,kind,body):
        with self.connect() as c:
            c.execute('INSERT INTO events(at,kind,body) VALUES(?,?,?)',(now().isoformat(),kind,json.dumps(body,default=str)))

    def reserve_call(self,limit):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            count=c.execute('SELECT count FROM calls WHERE day=?',(day(),)).fetchone()
            if count and count[0]>=limit: return False
            c.execute('INSERT INTO calls VALUES(?,1) ON CONFLICT(day) DO UPDATE SET count=count+1',(day(),))
            return True

    def report(self):
        with self.connect() as c:
            decisions=[json.loads(r[0]) for r in c.execute('SELECT body FROM decisions ORDER BY at')]
            events=[{'at':r[0],'kind':r[1],'data':json.loads(r[2])} for r in c.execute('SELECT at,kind,body FROM events ORDER BY id DESC LIMIT 20')]
            last=c.execute("SELECT at,body FROM events WHERE kind='valuation' ORDER BY id DESC LIMIT 1").fetchone()
            valuations=[json.loads(r[0]) for r in c.execute("SELECT body FROM events WHERE kind='valuation'")]
            filled=[d for d in decisions if d['status']=='FILLED']
            state=self.state(c)
            summary={'realized_pnl_brl':str(sum((D(d.get('pnl','0')) for d in filled),D(0))),
                     'estimated_fees_brl':str(sum((D(d['fee']) for d in filled),D(0))),
                     'filled_orders':len(filled),'decision_counts':{s:sum(d['status']==s for d in decisions) for s in ('FILLED','HOLD','REJECT','REJECTED')},
                     'max_drawdown':str(max((D(v['drawdown']) for v in valuations),default=D(0))),
                     'unclosed_positions':list(state['positions'])}
            return {'summary':summary,'state':state,'last_valuation':({'at':last[0],**json.loads(last[1])} if last else None),'decisions':decisions,'recent_events':events,'calls':dict(c.execute('SELECT day,count FROM calls'))}

class Broker:
    def __init__(self,store,policy):
        self.store,self.policy=store,policy

    def equity(self,state,quotes):
        return D(state['cash'])+sum(D(p['quantity'])*quotes[s].bid for s,p in state['positions'].items())

    def execute(self,p:Proposal,quotes:dict[str,Quote]):
        policy=self.policy
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM decisions WHERE id=?',(p.decision_id,)).fetchone(): return {'status':'DUPLICATE'}
            state=self.store.state(c)
            result={'status':'REJECTED','reason':'', 'proposal':p.model_dump(mode='json'),'policy_hash':policy.fingerprint}
            q=quotes.get(p.symbol)
            error=None
            if p.action in ('HOLD','REJECT'):
                result.update(status=p.action,reason=p.reason)
            else:
                if not q or not q.fresh(): error='missing_or_stale_quote'
                elif q.evidence_id not in p.evidence_ids: error='missing_evidence'
                elif p.action=='SELL' and p.symbol not in state['positions']: error='no_position'
                elif p.action=='BUY':
                    if any(s not in quotes or not quotes[s].fresh() for s in state['positions']): error='unvalued_position'
                    else:
                        equity=self.equity(state,quotes)
                        if state['day']!=day(): state.update(day=day(),day_equity=str(equity),halted_day=None)
                        if equity <= D(state['day_equity'])*(1-policy.daily_loss): state['halted_day']=day()
                        if equity <= policy.initial_cash*(1-policy.experiment_loss): state['halted_experiment']=True
                        if state['halted_day']==day() or state['halted_experiment']: error='loss_limit'
                        elif p.symbol in state['positions']: error='position_already_open'
                        elif len(state['positions'])>=policy.max_positions: error='position_limit'
                        elif p.amount_brl<=0 or p.amount_brl>equity*policy.position_fraction: error='position_size'
                        elif p.amount_brl>D(state['cash']): error='insufficient_cash'
                        elif (q.ask/q.bid-1)>D('.02'): error='spread_limit'
                if error: result['reason']=error
                elif p.action=='BUY':
                    price=q.ask*(1+policy.slippage)
                    notional=p.amount_brl/(1+policy.fee)
                    quantity=notional/price
                    fee=p.amount_brl-notional
                    state['cash']=str(D(state['cash'])-p.amount_brl)
                    state['positions'][p.symbol]={'quantity':str(quantity),'entry':str(price),'cost':str(p.amount_brl),'stop':str(price*(1-p.stop_pct/100)),'expires':(now()+timedelta(minutes=p.horizon_minutes)).isoformat()}
                    result.update(status='FILLED',price=str(price),quantity=str(quantity),fee=str(fee),reason=p.reason)
                elif p.action=='SELL':
                    position=state['positions'].pop(p.symbol)
                    price=q.bid*(1-policy.slippage)
                    gross=D(position['quantity'])*price
                    fee=gross*policy.fee
                    state['cash']=str(D(state['cash'])+gross-fee)
                    result.update(status='FILLED',price=str(price),quantity=position['quantity'],fee=str(fee),pnl=str(gross-fee-D(position['cost'])),reason=p.reason)
            c.execute('UPDATE state SET body=? WHERE id=1',(json.dumps(state),))
            c.execute('INSERT INTO decisions VALUES(?,?,?)',(p.decision_id,now().isoformat(),json.dumps(result)))
            return result

    def protect(self,quotes,close_all=False):
        state=self.store.state()
        results=[]
        # Loss limits are latched on every monitor tick, independently of AI.
        if all(s in quotes and quotes[s].fresh() for s in state['positions']):
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                state=self.store.state(c)
                equity=self.equity(state,quotes)
                if state['day']!=day(): state.update(day=day(),day_equity=str(equity),halted_day=None)
                state['peak']=str(max(D(state['peak']),equity))
                if equity<=D(state['day_equity'])*(1-self.policy.daily_loss): state['halted_day']=day()
                if equity<=self.policy.initial_cash*(1-self.policy.experiment_loss): state['halted_experiment']=True
                c.execute('UPDATE state SET body=? WHERE id=1',(json.dumps(state),))
            self.store.event('valuation',{'equity':str(equity),'drawdown':str(1-equity/D(state['peak']))})
        for symbol,position in state['positions'].items():
            q=quotes.get(symbol)
            if q and q.fresh() and (close_all or q.bid<=D(position['stop']) or now()>=datetime.fromisoformat(position['expires'])):
                p=Proposal(decision_id=str(uuid4()),action='SELL',symbol=symbol,amount_brl=0,stop_pct=2,horizon_minutes=1,reason='operator_close' if close_all else 'stop_or_expiry',evidence_ids=[q.evidence_id])
                results.append(self.execute(p,quotes))
        return results
