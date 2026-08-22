"""Interactive credential setup that never accepts a key as a command argument."""

from __future__ import annotations

import argparse
import getpass

from ask_ai_mcp.credentials import CredentialError, CredentialStore, OpenCodeCredentialStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ask-ai-mcp-credentials",
        description="Manage provider keys in Windows Credential Manager.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("set", "status"):
        command_parser = subparsers.add_parser(command)
        _add_provider_arguments(command_parser)
    delete_parser = subparsers.add_parser("delete")
    _add_provider_arguments(delete_parser)
    delete_parser.add_argument("--yes", action="store_true", help="skip confirmation")
    return parser


def _add_provider_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", choices=("deepseek", "opencode-go"), default="deepseek")
    parser.add_argument(
        "--account",
        default="primary",
        help="explicit OpenCode Go account alias; no automatic account rotation",
    )


def _label(provider: str, account: str) -> str:
    return "DeepSeek" if provider == "deepseek" else f"OpenCode Go {account}"


def _set_key(store, *, label: str) -> int:
    first = getpass.getpass(f"{label} API key: ")
    second = getpass.getpass("Confirm API key: ")
    if first != second:
        print("Keys did not match. Nothing was stored.")
        return 2
    store.set_api_key(first)
    print(f"{label} API key stored in Windows Credential Manager.")
    return 0


def _status(store, *, label: str) -> int:
    state = "configured" if store.is_configured() else "not configured"
    print(f"{label} API key: {state}.")
    return 0


def _delete(store, *, label: str, assume_yes: bool) -> int:
    if not store.is_configured():
        print(f"{label} API key is not configured.")
        return 0
    if not assume_yes:
        answer = input(f"Delete the stored {label} API key? [y/N] ").strip().casefold()
        if answer not in {"y", "yes"}:
            print("Nothing was deleted.")
            return 0
    store.delete_api_key()
    print(f"{label} API key deleted from Windows Credential Manager.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    label = _label(args.provider, args.account)
    store = (
        CredentialStore()
        if args.provider == "deepseek"
        else OpenCodeCredentialStore(profile=args.account)
    )
    try:
        if args.command == "set":
            return _set_key(store, label=label)
        if args.command == "status":
            return _status(store, label=label)
        return _delete(store, label=label, assume_yes=args.yes)
    except CredentialError as error:
        print(f"Credential operation failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
