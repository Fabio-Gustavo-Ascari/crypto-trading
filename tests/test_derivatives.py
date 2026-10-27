from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from crypto_harness.core import Broker, Policy, Proposal, Quote, Store


def quote(price=100):
    return Quote(
        symbol="BTC", bid=price, ask=price + 1,
        observed_at=datetime.now(timezone.utc), source="fixture", evidence_id="q1",
    )


def proposal(action, identifier="p1", leverage="2", stop="2"):
    return Proposal(
        decision_id=identifier, action=action, symbol="BTC", amount_brl=Decimal("9"),
        leverage=Decimal(leverage), stop_pct=Decimal(stop), horizon_minutes=60,
        reason="teste", evidence_ids=["q1"],
    )


@pytest.fixture
def broker(tmp_path):
    return Broker(Store(tmp_path / "derivatives.db"), Policy())


def test_long_is_profitable_when_price_rises(broker):
    assert broker.execute(proposal("OPEN_LONG"), {"BTC": quote()})["status"] == "FILLED"
    result = broker.execute(proposal("CLOSE_LONG", "close"), {"BTC": quote(110)})
    assert result["status"] == "FILLED"
    assert Decimal(result["pnl"]) > 0


def test_short_is_profitable_when_price_falls(broker):
    assert broker.execute(proposal("OPEN_SHORT"), {"BTC": quote()})["status"] == "FILLED"
    result = broker.execute(proposal("CLOSE_SHORT", "close"), {"BTC": quote(90)})
    assert result["status"] == "FILLED"
    assert Decimal(result["pnl"]) > 0


def test_ai_leverage_has_no_fixed_policy_cap_but_is_recorded(broker):
    result = broker.execute(proposal("OPEN_LONG", leverage="37"), {"BTC": quote()})
    assert result["status"] == "FILLED"
    assert broker.store.state()["positions"]["BTC"]["leverage"] == "37"


def test_independent_stop_closes_short_on_adverse_price(broker):
    broker.execute(proposal("OPEN_SHORT", stop="2"), {"BTC": quote()})
    broker.protect({"BTC": quote(104)})
    assert broker.store.state()["positions"] == {}


def test_wrong_close_direction_is_rejected(broker):
    broker.execute(proposal("OPEN_SHORT"), {"BTC": quote()})
    result = broker.execute(proposal("CLOSE_LONG", "bad-close"), {"BTC": quote()})
    assert result["status"] == "REJECTED"
    assert result["reason"] == "wrong_close_action"
