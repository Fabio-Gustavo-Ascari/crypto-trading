from decimal import Decimal
from crypto_harness.core import Store, Broker, Policy
from tests.test_core import quote,buy

def test_policy_is_immutable_and_fingerprint_stable():
    import pytest
    a=Policy(); b=Policy()
    assert a.fingerprint==b.fingerprint
    assert len(a.fingerprint)==64
    with pytest.raises(ValueError): a.fee=Decimal('1')

def test_report_net_pnl_and_fees_match_cash(tmp_path):
    s=Store(tmp_path/'r.db'); b=Broker(s,Policy()); q={'BTC':quote()}
    b.execute(buy(),q)
    b.execute(buy('sell').model_copy(update={'action':'CLOSE_LONG'}),q)
    b.protect(q)
    report=s.report()
    assert abs(Decimal(report['summary']['realized_pnl_brl'])-(Decimal(report['state']['cash'])-1000)) < Decimal('1e-20')
    assert Decimal(report['summary']['estimated_fees_brl'])>0
    assert report['summary']['filled_orders']==2
    assert Decimal(report['last_valuation']['equity'])==Decimal(report['state']['cash'])

def test_position_limit(tmp_path):
    b=Broker(Store(tmp_path/'p.db'),Policy(max_positions=1))
    b.execute(buy(),{'BTC':quote()})
    q={'BTC':quote(),'ETH':quote().model_copy(update={'symbol':'ETH'})}
    assert b.execute(buy('eth').model_copy(update={'symbol':'ETH'}),q)['reason']=='position_limit'

def test_report_exposes_latest_discovery_summary(tmp_path):
    s=Store(tmp_path/'discovery.db')
    s.event('market',{'research':{'summary':{'assets_researched':87,'assets_filtered':8,'security_rejections':14}}})
    assert s.report()['latest_discovery']['assets_researched']==87

def test_report_tolerates_legacy_list_research(tmp_path):
    s=Store(tmp_path/'legacy-discovery.db')
    s.event('market',{'research':[]})
    assert s.report()['latest_discovery']=={}
