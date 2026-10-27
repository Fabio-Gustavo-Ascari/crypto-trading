"""Manual lifecycle, single writer and model-independent monitoring."""
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
import json
import os
import threading
from .core import Store, Broker, Policy, Proposal, now
from .market import Market
from .agent import AgentFailure, decide_with_fallback, default_model

@contextmanager
def run_lock(directory):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    f=(directory/'run.lock').open('a+b')
    if f.tell()==0: f.write(b'0'); f.flush()
    f.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise RuntimeError('Harness already running for this data directory')
    try: yield
    finally: f.close()

def hold(reason):
    return Proposal(decision_id=str(uuid4()),action='HOLD',symbol='BTC',amount_brl=0,stop_pct=2,horizon_minutes=60,reason=reason,evidence_ids=[])

def review_positions(store,broker,market,ai=False,max_daily_calls=8,model=None,stop_event=None):
    """Review open positions frequently; risk protection remains model-independent."""
    quotes,research,errors=market.collect(discovery=False)
    broker.protect(quotes)
    state=store.state()
    if not state['positions']:
        store.event('position_review',{'status':'no_open_positions','errors':errors})
        return None
    if stop_event and stop_event.is_set(): return None
    if not ai or not store.reserve_call(max_daily_calls):
        store.event('position_review',{'status':'skipped_ai_budget','positions':list(state['positions']),'errors':errors})
        return None
    context={'time_utc':now().isoformat(),'review_type':'open_position_review','rules':broker.policy.model_dump(mode='json'),'rules_hash':broker.policy.fingerprint,'portfolio':state,'evidence':{'quotes':{s:q.model_dump(mode='json') for s,q in quotes.items()},'approved_candidates':research.get('approved_candidates',[]),'errors':errors}}
    store.event('position_review_input',context)
    try:
        proposal,usage,attempts=decide_with_fallback(context,model=model)
        store.event('position_review_usage',usage)
        store.event('position_review_attempts',{'attempts':attempts,'selected_model':attempts[-1]['model']})
    except AgentFailure as error:
        store.event('agent_failure',{'category':error.category,'model':error.model,'error':str(error),'stderr':error.stderr,'phase':'position_review'})
        return None
    if proposal.action not in ('CLOSE_LONG','CLOSE_SHORT') or proposal.symbol not in state['positions']:
        store.event('position_review',{'status':'hold','reason':'review_did_not_request_valid_close','action':proposal.action})
        return None
    result=broker.execute(proposal,quotes)
    store.event('position_review',{'status':result.get('status'),'action':proposal.action,'symbol':proposal.symbol,'reason':result.get('reason')})
    return result

def cycle(store,broker,market,ai=False,max_daily_calls=8,model=None,stop_event=None):
    quotes,research,errors=market.collect(discovery=True)
    snapshot={'quotes':{s:q.model_dump(mode='json') for s,q in quotes.items()},'research':research,'errors':errors}
    store.event('market',snapshot)
    broker.protect(quotes)
    if not quotes: proposal=hold('No fresh market data')
    elif not ai: proposal=hold('Observation mode / session call budget reached')
    elif not store.reserve_call(max_daily_calls): proposal=hold('Daily AI call budget reached')
    else:
        state=store.state()
        research_data=research if isinstance(research,dict) else {}
        decision_evidence={'quotes':snapshot['quotes'],'discovery_summary':research_data.get('summary',{}),'approved_candidates':research_data.get('approved_candidates',[]),'errors':errors}
        context={'time_utc':now().isoformat(),'rules':broker.policy.model_dump(mode='json'),'rules_hash':broker.policy.fingerprint,'portfolio':state,'evidence':decision_evidence}
        store.event('agent_input',context)
        try:
            proposal,usage,attempts=decide_with_fallback(context,model=model)
            store.event('agent_usage',usage)
            store.event('agent_attempts',{'attempts':attempts,'selected_model':attempts[-1]['model']})
        except AgentFailure as error:
            store.event('agent_failure',{'category':error.category,'model':error.model,'error':str(error),'stderr':error.stderr})
            proposal=hold('All configured AI models failed; execution skipped')
        except (RuntimeError,ValueError,OSError) as error:
            store.event('agent_failure',{'category':'internal_error','error':str(error)})
            proposal=hold('AI orchestration failed; execution skipped')
    if stop_event and stop_event.is_set(): proposal=hold('Stop requested before execution')
    # Recollect for execution: the decision can take up to 90 seconds.
    if proposal.action in ('OPEN_LONG','OPEN_SHORT','CLOSE_LONG','CLOSE_SHORT'):
        fresh,_,refresh_errors=market.collect()
        old=quotes.get(proposal.symbol)
        current=fresh.get(proposal.symbol)
        if old and current and old.evidence_id in proposal.evidence_ids:
            proposal=proposal.model_copy(update={'evidence_ids':proposal.evidence_ids+[current.evidence_id]})
        quotes=fresh
        store.event('execution_market',{'quotes':{s:q.model_dump(mode='json') for s,q in quotes.items()},'errors':refresh_errors})
    result=broker.execute(proposal,quotes)
    print(json.dumps({'at':now().isoformat(),'quotes':len(quotes),'discovery':research.get('summary',{}) if isinstance(research,dict) else {},'status':result['status'],'reason':result.get('reason'),'action':proposal.action},ensure_ascii=False),flush=True)
    return result

def export_report(store,directory):
    directory=Path(directory)
    report=store.report()
    report['notice']='Paper fills estimate fees (0.1%) and slippage (0.2%) each side; exchange minimum sizes and depth are not modeled.'
    path=directory/'report.json'
    path.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    return path

def run(directory,once=False,ai=True,interval=900,monitor_interval=30,position_review_interval=60,max_calls=8,max_daily_calls=8,model=None,max_cycles=None):
    directory=Path(directory)
    with run_lock(directory):
        stop_path=directory/'stop.request'
        stop_path.unlink(missing_ok=True)
        store=Store(directory/'harness.db')
        broker=Broker(store,Policy())
        done=threading.Event()
        calls=0
        failures=0
        cycles=0
        market=Market()
        def monitor():
            watcher=Market()
            elapsed_since_review=0
            try:
                while not done.wait(monitor_interval):
                    if stop_path.exists(): done.set(); break
                    try:
                        quotes,_,errors=watcher.collect()
                        broker.protect(quotes)
                        store.event('monitor',{'errors':errors})
                        elapsed_since_review += monitor_interval
                        if elapsed_since_review >= position_review_interval:
                            review_positions(store,broker,watcher,ai,max_daily_calls,model,done)
                            elapsed_since_review=0
                    except Exception as e:
                        store.event('monitor_failure',{'type':type(e).__name__})
            finally: watcher.close()
        thread=threading.Thread(target=monitor,daemon=True)
        thread.start()
        store.event('started',{'ai':ai,'interval':interval,'position_review_interval':position_review_interval,'monitor_interval':monitor_interval,'max_calls_session':max_calls,'max_calls_day':max_daily_calls,'model':model or default_model()})
        print('Harness running. Ctrl+C or another terminal: crypto-harness stop',flush=True)
        try:
            while not done.is_set():
                enabled=ai and calls<max_calls and failures<3
                before=sum(store.report()['calls'].values())
                result=cycle(store,broker,market,enabled,max_daily_calls,model,done)
                after=sum(store.report()['calls'].values())
                calls+=after-before
                if result.get('reason','').startswith('All configured AI models failed'): failures+=1
                elif enabled and after>before: failures=0
                cycles+=1
                export_report(store,directory)
                if once or (max_cycles and cycles>=max_cycles): break
                for _ in range(interval):
                    if done.wait(1) or stop_path.exists(): done.set(); break
        except KeyboardInterrupt:
            print('Stopping; closing paper positions with fresh quotes...',flush=True)
        finally:
            done.set()
            thread.join(timeout=45)
            quotes,_,errors=market.collect()
            broker.protect(quotes,close_all=True)
            broker.protect(quotes)
            remaining=store.state()['positions']
            store.event('stopped',{'unclosed_positions':list(remaining),'errors':errors})
            print('Report: '+str(export_report(store,directory)),flush=True)
            if remaining: print('ATTENTION: could not close '+','.join(remaining)+'. Resume start or use close when data returns. Monitoring is stopped.',flush=True)
            market.close()
