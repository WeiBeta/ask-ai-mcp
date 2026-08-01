"""Interactive credential setup that never accepts a key as a command argument."""

from __future__ import annotations

import argparse
import getpass

from ask_ai_mcp.credentials import CredentialError, CredentialStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ask-ai-mcp-credentials",
        description="Manage the DeepSeek key in Windows Credential Manager.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("set")
    subparsers.add_parser("status")
    delete_parser = subparsers.add_parser("delete")
    delete_parser.add_argument("--yes", action="store_true", help="skip confirmation")
    return parser


def _set_key(store: CredentialStore) -> int:
    first = getpass.getpass("DeepSeek API key: ")
    second = getpass.getpass("Confirm API key: ")
    if first != second:
        print("Keys did not match. Nothing was stored.")
        return 2
    store.set_api_key(first)
    print("DeepSeek API key stored in Windows Credential Manager.")
    return 0


def _status(store: CredentialStore) -> int:
    state = "configured" if store.is_configured() else "not configured"
    print(f"DeepSeek API key: {state}.")
    return 0


def _delete(store: CredentialStore, *, assume_yes: bool) -> int:
    if not store.is_configured():
        print("DeepSeek API key is not configured.")
        return 0
    if not assume_yes:
        answer = input("Delete the stored DeepSeek API key? [y/N] ").strip().casefold()
        if answer not in {"y", "yes"}:
            print("Nothing was deleted.")
            return 0
    store.delete_api_key()
    print("DeepSeek API key deleted from Windows Credential Manager.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = CredentialStore()
    try:
        if args.command == "set":
            return _set_key(store)
        if args.command == "status":
            return _status(store)
        return _delete(store, assume_yes=args.yes)
    except CredentialError as error:
        print(f"Credential operation failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
