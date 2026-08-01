"""Tests for the explicit-charge synthetic smoke command."""

from ask_ai_mcp import smoke_cli
from ask_ai_mcp.models import (
    CandidateFile,
    DeepSeekModel,
    ToolCandidatePayload,
    ToolCandidateResult,
)


def test_smoke_without_confirmation_makes_no_client(monkeypatch, capsys) -> None:
    def forbidden_client():
        raise AssertionError("client must not be constructed")

    monkeypatch.setattr(smoke_cli, "DeepSeekClient", forbidden_client)
    assert smoke_cli.main([]) == 2
    assert "No request made" in capsys.readouterr().out


def test_confirmed_smoke_prints_hash_and_paths_but_not_content(monkeypatch, capsys) -> None:
    secret_content = "candidate-content-must-not-be-printed"
    result = ToolCandidateResult(
        candidate_sha256="a" * 64,
        model=DeepSeekModel.FLASH,
        thinking_enabled=True,
        payload=ToolCandidatePayload(
            summary="Synthetic candidate.",
            files=[CandidateFile(path="tool.py", content=secret_content)],
        ),
    )

    class FakeClient:
        def build_candidate(self, spec, *, client_name):
            assert spec.name == "split_synthetic_fields"
            assert client_name == "phase1_smoke"
            return result

    monkeypatch.setattr(smoke_cli, "DeepSeekClient", FakeClient)
    assert smoke_cli.main(["--confirm-charge"]) == 0
    output = capsys.readouterr().out
    assert "deepseek-v4-flash" in output
    assert "tool.py" in output
    assert "a" * 64 in output
    assert secret_content not in output
