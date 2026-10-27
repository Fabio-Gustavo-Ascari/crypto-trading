from crypto_harness.agent import default_model, parse_answer
import pytest

def test_agent_cannot_invent_action_or_extra_fields():
    with pytest.raises(ValueError): parse_answer('{"action":"EXECUTE_SHELL"}')
    with pytest.raises(ValueError): parse_answer('not json')

def test_default_model_is_the_selected_light_model():
    assert default_model() == 'gpt-5.6-luna'
