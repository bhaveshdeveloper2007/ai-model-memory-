from memoryos_backend.security import Actor, redact_payload, require_project_access, token_digest


def test_redacts_sensitive_fields_and_common_secrets():
    result = redact_payload({
        "prompt": "private user text",
        "metadata": {"api_key": "secret-value", "note": "credential=verylongsecretvalue"},
        "event": "using ghp_abcdefghijklmnopqrstuvwxyz1234567890",
    })
    assert result["prompt"] == "[REDACTED]"
    assert result["metadata"]["api_key"] == "[REDACTED]"
    assert "verylongsecretvalue" not in result["metadata"]["note"]
    assert "ghp_" not in result["event"]


def test_agent_tokens_are_stored_as_one_way_digests():
    token = "a-long-random-agent-token"
    assert token_digest(token) != token
    assert token_digest(token) == token_digest(token)


def test_project_access_uses_explicit_agent_grants():
    from types import SimpleNamespace
    from uuid import uuid4

    project_id = uuid4()
    agent_id = uuid4()

    class FakeDb:
        def get(self, _model, _project_id):
            return SimpleNamespace(id=project_id, permissions={"agent_ids": [str(agent_id)]})

    assert require_project_access(FakeDb(), Actor(is_admin=False, agent_id=agent_id), project_id).id == project_id
