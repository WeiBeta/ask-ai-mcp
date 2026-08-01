"""Tests for secret-safe credential storage behavior."""

import pytest

from ask_ai_mcp.credential_cli import build_parser
from ask_ai_mcp.credentials import CredentialError, CredentialStore


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
