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
    symbol: str = Field(pattern=r'^[A-Z0-9]{2,20}$')
    bid: D = Field(gt=0)
    ask: D = Field(gt=0)
    observed_at: AwareDatetime
    source: str
    evidence_id: str
    execution_eligible: bool = True

    @model_validator(mode='after')
    def valid_spread(self):
        if self.ask < self.bid: raise ValueError('crossed book')
        return self

    def fresh(self):
        return -10 <= (now()-self.observed_at).total_seconds() <= 180

class Proposal(Strict):
    decision_id: str = Field(min_length=1,max_length=100)
    action: Literal['OPEN_LONG','OPEN_SHORT','CLOSE_LONG','CLOSE_SHORT','HOLD','REJECT']
    symbol: str = Field(max_length=30)
    amount_brl: D = Field(ge=0)
    # Margin in BRL. The model may propose any finite leverage >= 1 in paper mode.
    leverage: D = Field(default=D('1'), ge=1)
    stop_pct: D = Field(gt=0,lt=100)
    horizon_minutes: int = Field(ge=1,le=1440)
    reason: str = Field(min_length=1,max_length=2000)
    evidence_ids: list[str] = Field(max_length=20)

class Policy(Strict):
    initial_cash: D = D('1000')
    currency: Literal['USD'] = 'USD'
    position_fraction: D = D('.08')
    daily_loss: D = D('.03')
    experiment_loss: D = D('.08')
    max_positions: int = 3
    fee: D = D('.001')
    slippage: D = D('.002')
    maintenance_margin: D = D('.005')

    @property
    def fingerprint(self):
        return sha256(self.model_dump_json().encode()).hexdigest()

class Store:
    def __init__(self,path,initial_cash=D('1000')):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, at TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS calls(day TEXT PRIMARY KEY, count INTEGER NOT NULL);
            ''')
            cash=str(initial_cash)
            c.execute('INSERT OR IGNORE INTO state VALUES(1,?)',(json.dumps({'cash':cash,'positions':{},'day':day(),'day_equity':cash,'peak':cash,'halted_day':None,'halted_experiment':False}),))

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

    def release_call(self):
        """Undo a reserved cycle when no model produced a usable decision."""
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT count FROM calls WHERE day=?',(day(),)).fetchone()
            if not row: return
            if row[0] <= 1: c.execute('DELETE FROM calls WHERE day=?',(day(),))
            else: c.execute('UPDATE calls SET count=count-1 WHERE day=?',(day(),))

    def rebase_pristine_capital(self, initial_cash):
        """Upgrade an untouched paper wallet without rewriting its audit trail."""
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            state=self.state(c)
            filled=c.execute("SELECT count(*) FROM decisions WHERE json_extract(body,'$.status')='FILLED'").fetchone()[0]
            if state['positions'] or filled or D(state['cash']) >= D(initial_cash): return False
            cash=str(initial_cash)
            state.update(cash=cash,day_equity=cash,peak=cash,halted_day=None,halted_experiment=False)
            c.execute('UPDATE state SET body=? WHERE id=1',(json.dumps(state),))
            c.execute('INSERT INTO events(at,kind,body) VALUES(?,?,?)',(now().isoformat(),'capital_rebased',json.dumps({'initial_cash':cash,'currency':'USD'})))
            return True

    def report(self):
        with self.connect() as c:
            decisions=[json.loads(r[0]) for r in c.execute('SELECT body FROM decisions ORDER BY at')]
            events=[{'at':r[0],'kind':r[1],'data':json.loads(r[2])} for r in c.execute('SELECT at,kind,body FROM events ORDER BY id DESC LIMIT 20')]
            last=c.execute("SELECT at,body FROM events WHERE kind='valuation' ORDER BY id DESC LIMIT 1").fetchone()
            market=c.execute("SELECT body FROM events WHERE kind='market' ORDER BY id DESC LIMIT 1").fetchone()
            valuations=[json.loads(r[0]) for r in c.execute("SELECT body FROM events WHERE kind='valuation'")]
            filled=[d for d in decisions if d['status']=='FILLED']
            state=self.state(c)
            realized=str(sum((D(d.get('pnl','0')) for d in filled),D(0)))
            fees=str(sum((D(d['fee']) for d in filled),D(0)))
            summary={'realized_pnl_usd':realized,'estimated_fees_usd':fees,
                     # Deprecated compatibility aliases; monetary values are USD.
                     'realized_pnl_brl':realized,'estimated_fees_brl':fees,
                     'filled_orders':len(filled),'decision_counts':{s:sum(d['status']==s for d in decisions) for s in ('FILLED','HOLD','REJECT','REJECTED')},
                     'max_drawdown':str(max((D(v['drawdown']) for v in valuations),default=D(0))),
                     'unclosed_positions':list(state['positions'])}
            latest_market=json.loads(market[0]) if market else {}
            latest_research=latest_market.get('research',{}) if isinstance(latest_market,dict) else {}
            latest_discovery=latest_research.get('summary',{}) if isinstance(latest_research,dict) else {}
            return {'summary':summary,'state':state,'last_valuation':({'at':last[0],**json.loads(last[1])} if last else None),'latest_discovery':latest_discovery,'decisions':decisions,'recent_events':events,'calls':dict(c.execute('SELECT day,count FROM calls'))}

class Broker:
    def __init__(self,store,policy):
        self.store,self.policy=store,policy

    def equity(self,state,quotes):
        value=D(state['cash'])
        for symbol,position in state['positions'].items():
            q=quotes[symbol]
            exit_price=q.bid*(1-self.policy.slippage) if position['side']=='long' else q.ask*(1+self.policy.slippage)
            raw=(exit_price-D(position['entry']))*D(position['quantity'])
            if position['side']=='short': raw=-raw
            value+=D(position['margin'])+raw-exit_price*D(position['quantity'])*self.policy.fee
        return value

    def _gate_losses(self,state,quotes):
        equity=self.equity(state,quotes)
        if state['day']!=day(): state.update(day=day(),day_equity=str(equity),halted_day=None)
        if equity<=D(state['day_equity'])*(1-self.policy.daily_loss): state['halted_day']=day()
        if equity<=self.policy.initial_cash*(1-self.policy.experiment_loss): state['halted_experiment']=True
        return equity

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
                elif p.action in ('OPEN_LONG','OPEN_SHORT') and not q.execution_eligible: error='candidate_not_execution_eligible'
                elif q.evidence_id not in p.evidence_ids: error='missing_evidence'
                elif p.action in ('OPEN_LONG','OPEN_SHORT'):
                    if any(s not in quotes or not quotes[s].fresh() for s in state['positions']): error='unvalued_position'
                    else:
                        equity=self._gate_losses(state,quotes)
                        if state['halted_day']==day() or state['halted_experiment']: error='loss_limit'
                        elif p.symbol in state['positions']: error='position_already_open'
                        elif len(state['positions'])>=policy.max_positions: error='position_limit'
                        elif p.amount_brl<=0 or p.amount_brl>equity*policy.position_fraction: error='position_size'
                        elif (q.ask/q.bid-1)>D('.02'): error='spread_limit'
                        else:
                            side='long' if p.action=='OPEN_LONG' else 'short'
                            price=q.ask*(1+policy.slippage) if side=='long' else q.bid*(1-policy.slippage)
                            notional=p.amount_brl*p.leverage
                            fee=notional*policy.fee
                            liquidation=price*(1-1/p.leverage+policy.maintenance_margin) if side=='long' else price*(1+1/p.leverage-policy.maintenance_margin)
                            stop=price*(1-p.stop_pct/100) if side=='long' else price*(1+p.stop_pct/100)
                            if p.amount_brl+fee>D(state['cash']): error='insufficient_cash_for_margin_and_fee'
                            elif (side=='long' and stop<=liquidation) or (side=='short' and stop>=liquidation): error='stop_not_before_liquidation'
                if error: result['reason']=error
                elif p.action in ('OPEN_LONG','OPEN_SHORT'):
                    side='long' if p.action=='OPEN_LONG' else 'short'
                    price=q.ask*(1+policy.slippage) if side=='long' else q.bid*(1-policy.slippage)
                    notional=p.amount_brl*p.leverage
                    quantity=notional/price
                    fee=notional*policy.fee
                    liquidation=price*(1-1/p.leverage+policy.maintenance_margin) if side=='long' else price*(1+1/p.leverage-policy.maintenance_margin)
                    stop=price*(1-p.stop_pct/100) if side=='long' else price*(1+p.stop_pct/100)
                    state['cash']=str(D(state['cash'])-p.amount_brl-fee)
                    state['positions'][p.symbol]={'side':side,'quantity':str(quantity),'entry':str(price),'margin':str(p.amount_brl),'leverage':str(p.leverage),'stop':str(stop),'liquidation':str(liquidation),'expires':(now()+timedelta(minutes=p.horizon_minutes)).isoformat()}
                    result.update(status='FILLED',price=str(price),quantity=str(quantity),fee=str(fee),pnl=str(-fee),reason=p.reason,margin=str(p.amount_brl),notional=str(notional),leverage=str(p.leverage),liquidation=str(liquidation))
                else:
                    position=state['positions'].get(p.symbol)
                    if not position: result['reason']='no_position'
                    else:
                        expected='CLOSE_LONG' if position['side']=='long' else 'CLOSE_SHORT'
                        if p.action!=expected: result['reason']='wrong_close_action'
                        else:
                            state['positions'].pop(p.symbol)
                            price=q.bid*(1-policy.slippage) if position['side']=='long' else q.ask*(1+policy.slippage)
                            gross=(price-D(position['entry']))*D(position['quantity'])
                            if position['side']=='short': gross=-gross
                            fee=price*D(position['quantity'])*policy.fee
                            pnl=gross-fee
                            state['cash']=str(D(state['cash'])+D(position['margin'])+pnl)
                            result.update(status='FILLED',price=str(price),quantity=position['quantity'],fee=str(fee),pnl=str(pnl),reason=p.reason,side=position['side'])
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
                equity=self._gate_losses(state,quotes)
                state['peak']=str(max(D(state['peak']),equity))
                c.execute('UPDATE state SET body=? WHERE id=1',(json.dumps(state),))
            self.store.event('valuation',{'equity':str(equity),'drawdown':str(1-equity/D(state['peak']))})
        for symbol,position in state['positions'].items():
            q=quotes.get(symbol)
            if q and q.fresh():
                price=q.bid if position['side']=='long' else q.ask
                liquidated=price<=D(position['liquidation']) if position['side']=='long' else price>=D(position['liquidation'])
                stopped=price<=D(position['stop']) if position['side']=='long' else price>=D(position['stop'])
                if not (close_all or liquidated or stopped or now()>=datetime.fromisoformat(position['expires'])): continue
                p=Proposal(decision_id=str(uuid4()),action='CLOSE_LONG' if position['side']=='long' else 'CLOSE_SHORT',symbol=symbol,amount_brl=0,leverage=1,stop_pct=2,horizon_minutes=1,reason='operator_close' if close_all else 'liquidation' if liquidated else 'stop_or_expiry',evidence_ids=[q.evidence_id])
                results.append(self.execute(p,quotes))
        return results
