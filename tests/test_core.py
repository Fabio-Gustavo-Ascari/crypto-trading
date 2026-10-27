from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from crypto_harness.core import Store, Quote, Proposal, Policy, Broker

def quote(price=100, age=0):
    return Quote(symbol='BTC', bid=price, ask=price+1, observed_at=datetime.now(timezone.utc)-timedelta(seconds=age), source='fixture', evidence_id='q1')

def buy(id='one', **kw):
    return Proposal(decision_id=id, action='BUY', symbol='BTC', amount_brl=kw.get('amount', 9), stop_pct=2, horizon_minutes=60, reason='test', evidence_ids=['q1'])

@pytest.fixture
def broker(tmp_path):
    return Broker(Store(tmp_path/'test.db'), Policy())

def test_fill_idempotent_and_sell_roundtrip(broker):
    q={'BTC':quote()}
    assert broker.execute(buy(),q)['status']=='FILLED'
    cash=broker.store.state()['cash']
    assert broker.execute(buy(),q)['status']=='DUPLICATE'
    assert broker.store.state()['cash']==cash
    sell=buy('sell').model_copy(update={'action':'SELL'})
    assert broker.execute(sell,q)['status']=='FILLED'
    assert not broker.store.state()['positions']
    assert Decimal(broker.store.state()['cash']) < 200

@pytest.mark.parametrize('q,amount',[(quote(age=400),9),(quote(),11)])
def test_reject_stale_or_oversize(broker,q,amount):
    assert broker.execute(buy(amount=amount),{'BTC':q})['status']=='REJECTED'
    assert Decimal(broker.store.state()['cash'])==200

def test_missing_evidence_and_unknown_asset_rejected(broker):
    p=buy().model_copy(update={'evidence_ids':[]})
    assert broker.execute(p,{'BTC':quote()})['status']=='REJECTED'
    p=buy('two').model_copy(update={'symbol':'MEME'})
    assert broker.execute(p,{'BTC':quote()})['status']=='REJECTED'

def test_budget_persists(tmp_path):
    s=Store(tmp_path/'budget.db')
    assert s.reserve_call(1)
    assert not Store(tmp_path/'budget.db').reserve_call(1)

def test_stop_works_without_model(broker):
    broker.execute(buy(),{'BTC':quote()})
    broker.protect({'BTC':quote(90)})
    assert not broker.store.state()['positions']

def test_nan_is_invalid():
    with pytest.raises(ValueError): quote(float('nan'))

def test_restart_restores_positions(tmp_path):
    path=tmp_path/'restart.db'
    b=Broker(Store(path),Policy())
    b.execute(buy(),{'BTC':quote()})
    assert 'BTC' in Store(path).state()['positions']

def test_loss_limit_latched_and_sell_still_allowed(broker):
    import json
    broker.execute(buy(),{'BTC':quote()})
    with broker.store.connect() as c:
        state=broker.store.state(c)
        state['cash']='170'
        c.execute('UPDATE state SET body=? WHERE id=1',(json.dumps(state),))
    broker.protect({'BTC':quote()})
    p=buy('new').model_copy(update={'symbol':'ETH'})
    eth=quote().model_copy(update={'symbol':'ETH'})
    assert broker.execute(p,{'BTC':quote(),'ETH':eth})['reason']=='loss_limit'
    assert broker.execute(buy('exit').model_copy(update={'action':'SELL'}),{'BTC':quote()})['status']=='FILLED'

def test_future_quote_rejected(broker):
    assert broker.execute(buy(),{'BTC':quote(age=-120)})['status']=='REJECTED'

def test_held_position_blocks_reinforcement(broker):
    broker.execute(buy(),{'BTC':quote()})
    assert broker.execute(buy('another'),{'BTC':quote()})['reason']=='position_already_open'

def test_expired_position_is_closed(broker):
    import json
    broker.execute(buy(),{'BTC':quote()})
    with broker.store.connect() as c:
        state=broker.store.state(c)
        state['positions']['BTC']['expires']=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        c.execute('UPDATE state SET body=? WHERE id=1',(json.dumps(state),))
    broker.protect({'BTC':quote()})
    assert not broker.store.state()['positions']

def test_missing_quote_prevents_fake_close(broker):
    broker.execute(buy(),{'BTC':quote()})
    assert broker.protect({},close_all=True)==[]
    assert broker.store.state()['positions']
