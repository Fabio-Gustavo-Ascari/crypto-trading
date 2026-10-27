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
    monkeypatch.setattr('crypto_harness.service.decide',forbidden)
    store=Store(tmp_path/'budget.db')
    store.reserve_call(1)
    assert cycle(store,Broker(store,Policy()),GoodMarket(),ai=True,max_daily_calls=1)['reason']=='Daily AI call budget reached'
