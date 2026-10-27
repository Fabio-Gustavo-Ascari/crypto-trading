from pathlib import Path

import pytest

from crypto_harness.domain import AssetId
from crypto_harness.policy import load_policy


def test_load_policy_parses_limits_and_produces_stable_hash(tmp_path: Path) -> None:
    policy_file = tmp_path / "risk.yaml"
    policy_file.write_text(
        """policy_version: '0.1'\nbase_currency: BRL\ninitial_cash: 200\nmax_position_fraction: 0.05\nmax_open_positions: 3\nmax_daily_loss_fraction: 0.03\nmax_weekly_loss_fraction: 0.08\nmax_market_age_seconds: 300\nrequire_stop_loss: true\npaper_mode: true\n""",
        encoding="utf-8",
    )

    first = load_policy(policy_file)
    second = load_policy(policy_file)

    assert first.policy.initial_cash == 200.0
    assert first.policy_hash == second.policy_hash
    assert len(first.policy_hash) == 64


def test_asset_identity_requires_chain_contract_and_pair() -> None:
    asset = AssetId(chain="solana", contract_address="mint-123", pair_id="pool-456")

    assert asset.contract_address == "mint-123"
    with pytest.raises(ValueError):
        AssetId(chain="solana", contract_address="", pair_id="pool-456")
