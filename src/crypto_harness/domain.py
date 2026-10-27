"""Typed contracts shared across harness components."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AssetId(BaseModel):
    """Canonical identity for a tradable asset pool."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    chain: str = Field(min_length=1)
    contract_address: str = Field(min_length=1)
    pair_id: str = Field(min_length=1)


class DecisionAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    REJECT = "REJECT"


class MarketSnapshot(BaseModel):
    asset: AssetId
    price_brl: float = Field(gt=0)
    liquidity_brl: float = Field(ge=0)
    retrieved_at: datetime
    source: str = Field(min_length=1)


class TradeProposal(BaseModel):
    decision_id: str = Field(min_length=1)
    action: DecisionAction
    asset: AssetId
    max_notional_brl: float = Field(ge=0)
    thesis: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    stop_loss_pct: float | None = Field(default=None, gt=0, lt=100)
    time_horizon_minutes: int = Field(gt=0)
    created_at: datetime


class RiskPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_version: str = Field(min_length=1)
    base_currency: str = Field(min_length=1)
    initial_cash: float = Field(gt=0)
    max_position_fraction: float = Field(gt=0, le=1)
    max_open_positions: int = Field(gt=0)
    max_daily_loss_fraction: float = Field(gt=0, le=1)
    max_weekly_loss_fraction: float = Field(gt=0, le=1)
    max_market_age_seconds: int = Field(gt=0)
    require_stop_loss: bool
    paper_mode: bool
