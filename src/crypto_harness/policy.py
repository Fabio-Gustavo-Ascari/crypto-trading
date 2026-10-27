"""Versioned risk-policy loading and hashing."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import yaml

from .domain import RiskPolicy


@dataclass(frozen=True)
class LoadedPolicy:
    policy: RiskPolicy
    policy_hash: str


def load_policy(path: Path) -> LoadedPolicy:
    """Load a policy file and hash its canonical validated representation."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    policy = RiskPolicy.model_validate(raw)
    canonical = policy.model_dump_json(exclude_none=True, round_trip=True)
    return LoadedPolicy(policy=policy, policy_hash=sha256(canonical.encode()).hexdigest())
