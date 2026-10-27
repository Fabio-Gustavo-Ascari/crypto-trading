from crypto_harness.agent import AgentFailure, configured_models, decide_with_fallback, default_model, parse_answer
import pytest

def test_agent_cannot_invent_action_or_extra_fields():
    with pytest.raises(ValueError): parse_answer('{"action":"EXECUTE_SHELL"}')
    with pytest.raises(ValueError): parse_answer('not json')

def test_default_model_is_the_selected_light_model():
    assert default_model() == 'gpt-6-luna'

def test_environment_model_and_ordered_fallback(monkeypatch):
    monkeypatch.setenv('CRYPTO_HARNESS_MODEL','primary')
    monkeypatch.setenv('CRYPTO_HARNESS_FALLBACK_MODELS','backup, backup-two')
    assert configured_models() == ['primary','backup','backup-two']

def test_fallback_returns_second_model_and_preserves_first_error(monkeypatch):
    def attempt(context, model, timeout):
        if model == 'gpt-6-luna': raise AgentFailure('model_unavailable','not available','first stderr',model)
        return parse_answer('{"action":"HOLD","symbol":"BTC","amount_brl":0,"stop_pct":2,"horizon_minutes":2,"reason":"ok","evidence_ids":[]}'), {'tokens': 1}
    monkeypatch.setattr('crypto_harness.agent.decide_once',attempt)
    proposal, usage, attempts = decide_with_fallback({}, fallback_models=['gpt-5.6-luna'])
    assert proposal.action == 'HOLD' and usage == {'tokens': 1}
    assert attempts[0]['category'] == 'model_unavailable'
    assert attempts[-1]['model'] == 'gpt-5.6-luna'
