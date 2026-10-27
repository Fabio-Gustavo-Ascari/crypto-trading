from crypto_harness.agent import parse_answer
import pytest

def test_agent_cannot_invent_action_or_extra_fields():
    with pytest.raises(ValueError): parse_answer('{"action":"EXECUTE_SHELL"}')
    with pytest.raises(ValueError): parse_answer('not json')
