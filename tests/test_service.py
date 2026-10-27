from crypto_harness.service import cycle, run_lock
from crypto_harness.core import Store, Policy, Broker
import pytest

class EmptyMarket:
    def collect(self,discovery=False): return {},[],['offline']

def test_offline_cycle_has_hold_without_ai(tmp_path):
    store=Store(tmp_path/'x.db')
    result=cycle(store,Broker(store,Policy()),EmptyMarket(),ai=True,max_daily_calls=1)
    assert result['status']=='HOLD'
    assert store.report()['calls']=={}

def test_single_instance_lock(tmp_path):
    with run_lock(tmp_path):
        with pytest.raises(RuntimeError):
            with run_lock(tmp_path): pass

def test_budget_exhaustion_does_not_call_model(tmp_path,monkeypatch):
    from tests.test_core import quote
    class GoodMarket:
        def collect(self,discovery=False): return {'BTC':quote()},[],[]
    def forbidden(*a,**kw): raise AssertionError('AI called after budget exhausted')
    monkeypatch.setattr('crypto_harness.service.decide_with_fallback',forbidden)
    store=Store(tmp_path/'budget.db')
    store.reserve_call(1)
    assert cycle(store,Broker(store,Policy()),GoodMarket(),ai=True,max_daily_calls=1)['reason']=='Daily AI call budget reached'

def test_hold_is_recorded_only_after_all_model_attempts_fail(tmp_path,monkeypatch):
    from crypto_harness.agent import AgentFailure
    from tests.test_core import quote
    class GoodMarket:
        def collect(self,discovery=False):
            return {'BTC':quote()}, {'approved_candidates':[{'symbol':'BTC'}]}, []
    def failed(*args,**kwargs):
        raise AgentFailure('all_models_failed','all failed','stderr primary; stderr backup','gpt-5.6-luna')
    monkeypatch.setattr('crypto_harness.service.decide_with_fallback',failed)
    store=Store(tmp_path/'models.db')
    result=cycle(store,Broker(store,Policy()),GoodMarket(),ai=True,max_daily_calls=1)
    assert result['status']=='HOLD'
    assert any(event['kind']=='agent_failure' and event['data']['category']=='all_models_failed' for event in store.report()['recent_events'])

def test_failed_models_refund_the_cycle_budget(tmp_path,monkeypatch):
    from crypto_harness.agent import AgentFailure
    from tests.test_core import quote
    class GoodMarket:
        def collect(self,discovery=False): return {'BTC':quote()}, {'approved_candidates':[{'symbol':'BTC'}]}, []
    monkeypatch.setattr('crypto_harness.service.decide_with_fallback',lambda *a,**k: (_ for _ in ()).throw(AgentFailure('invalid_json','bad schema','schema error','gpt-6-luna')))
    store=Store(tmp_path/'refund.db')
    cycle(store,Broker(store,Policy()),GoodMarket(),ai=True,max_daily_calls=1)
    assert store.report()['calls']=={}
