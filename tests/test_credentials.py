"""Tests for secret-safe credential storage behavior."""

import pytest

from ask_ai_mcp.credential_cli import _set_key, build_parser
from ask_ai_mcp.credentials import CredentialError, CredentialStore, OpenCodeCredentialStore
from ask_ai_mcp.opencode_account import load_opencode_account


class FakeBackend:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.values[(service, username)]


def test_credential_store_round_trip_without_exposing_value() -> None:
    store = CredentialStore(backend=FakeBackend())
    secret = "sk-" + "a" * 40

    assert store.is_configured() is False
    store.set_api_key(secret)
    assert store.is_configured() is True
    assert store.get_api_key() == secret
    assert store.delete_api_key() is True
    assert store.is_configured() is False
    assert store.delete_api_key() is False


@pytest.mark.parametrize("value", ["", "not-a-key", "sk-short", "sk-has whitespace here"])
def test_invalid_key_shape_is_rejected(value: str) -> None:
    with pytest.raises(CredentialError, match=r"unexpected format|whitespace"):
        CredentialStore(backend=FakeBackend()).set_api_key(value)


def test_cli_never_accepts_secret_as_argument() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["set", "sk-must-not-be-command-history"])


def test_opencode_profiles_are_separate_and_accept_opaque_keys() -> None:
    backend = FakeBackend()
    primary = OpenCodeCredentialStore("primary", backend=backend)
    secondary = OpenCodeCredentialStore("secondary", backend=backend)

    primary.set_api_key("opaque-primary-opencode-key")
    secondary.set_api_key("opaque-secondary-opencode-key")

    assert primary.get_api_key() == "opaque-primary-opencode-key"
    assert secondary.get_api_key() == "opaque-secondary-opencode-key"


def test_cli_selects_provider_without_accepting_a_secret() -> None:
    args = build_parser().parse_args(
        ["set", "--provider", "opencode-go", "--account-uid", "visible-uid-02"]
    )
    assert args.provider == "opencode-go"
    assert args.account_uid == "visible-uid-02"


def test_cli_visible_input_is_explicit_and_stores_matching_key(monkeypatch) -> None:
    values = iter(["opaque-visible-opencode-key", "opaque-visible-opencode-key"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(values))
    store = OpenCodeCredentialStore("visible-uid", backend=FakeBackend())

    assert _set_key(store, label="OpenCode", visible_input=True) == 0
    assert store.get_api_key() == "opaque-visible-opencode-key"
    args = build_parser().parse_args(["set", "--visible-input"])
    assert args.visible_input is True


def test_email_style_opencode_uid_and_alias_are_supported(monkeypatch) -> None:
    monkeypatch.setenv("ASK_AI_MCP_OPENCODE_ACCOUNT_UID", "xujinglong8814@gmail.com")
    monkeypatch.setenv("ASK_AI_MCP_OPENCODE_ACCOUNT_ALIAS", "xujinglong8814@gmail.com")

    account = load_opencode_account()

    assert account.uid == "xujinglong8814@gmail.com"
    assert account.alias == "xujinglong8814@gmail.com"
    assert account.subscription_id == "go-account:xujinglong8814@gmail.com"
