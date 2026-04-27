"""CLI onboarding wizard.

Runs before the Textual TUI boots. Asks the user a minimal set of
questions, validates the agent-wallet pairing against Hyperliquid, then
writes a `.env` file to the standard config location.

Design notes:
  * Pure stdlib (`input`, `getpass`, `webbrowser`, `pathlib`). No Textual
    imports — keeps the wizard crash-proof relative to TUI bugs and lets
    it run on any terminal, including SSH/WSL where Textual can misbehave.
  * Ctrl-C / EOF at any step discards everything; next launch starts the
    wizard fresh. No partial configs.
  * `hyperagent setup` re-runs the wizard with existing values as defaults.
"""

from __future__ import annotations

import getpass
import os
import sys
import webbrowser
from pathlib import Path
from typing import Dict, Optional

from hyperagent.onboarding.validators import (
    PairingResult,
    is_valid_address,
    is_valid_private_key,
    verify_agent_pairing,
)
from hyperagent.onboarding.logo import print_logo


AGENT_APPROVAL_URL_TESTNET = "https://app.hyperliquid-testnet.xyz/API"
AGENT_APPROVAL_URL_MAINNET = "https://app.hyperliquid.xyz/API"


# ---------------------------------------------------------------------------
# Small styling helpers (ANSI — safe to print on any modern terminal)
# ---------------------------------------------------------------------------

def _bold(s: str) -> str:
    return f"\033[1m{s}\033[0m"


def _dim(s: str) -> str:
    return f"\033[2m{s}\033[0m"


def _green(s: str) -> str:
    return f"\033[32m{s}\033[0m"


def _red(s: str) -> str:
    return f"\033[31m{s}\033[0m"


def _yellow(s: str) -> str:
    return f"\033[33m{s}\033[0m"


def _prompt(label: str, default: Optional[str] = None) -> str:
    """input() wrapper that renders a default hint and handles EOF."""
    suffix = f" [{_dim(default)}]" if default else ""
    try:
        raw = input(f"{label}{suffix} > ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise
    return raw or (default or "")


def _yes_no(label: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            raw = input(f"{label} {hint} > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            raise
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print(_red("  please answer y or n"))


# ---------------------------------------------------------------------------
# Arrow-key selector (stdlib only)
# ---------------------------------------------------------------------------
#
# Options are a list of (label, value, enabled) tuples. Only enabled items
# can be chosen. Disabled items are rendered dimmed + a hint and are skipped
# by ↑/↓ navigation. Enter commits; Ctrl-C raises KeyboardInterrupt like
# every other prompt in this module.

def _read_key() -> str:
    """Read one keypress (including multi-byte arrow escapes) in raw mode.

    Returns: 'up', 'down', 'enter', 'ctrl-c', or the literal character.
    """
    import termios, tty  # noqa: E401  — Unix only; guarded in _select()

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            # escape sequence; read the next two bytes
            rest = sys.stdin.read(2)
            if rest == "[A":
                return "up"
            if rest == "[B":
                return "down"
            return "esc"
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x03":
            return "ctrl-c"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _select(label: str, options: list[tuple[str, str, bool]], default_index: int = 0) -> str:
    """Arrow-key menu. Returns the `value` from the chosen option tuple.

    Falls back to a numbered prompt if stdin isn't a TTY (CI, piped input)
    or if the OS doesn't support termios (Windows). The fallback honors the
    `enabled` flag so disabled rows still can't be picked.
    """
    # Non-TTY fallback — standard numbered menu
    fallback = not sys.stdin.isatty() or sys.platform == "win32"
    if not fallback:
        try:
            import termios  # noqa: F401
        except ImportError:
            fallback = True

    if fallback:
        print(_bold(label))
        for i, (opt_label, _value, enabled) in enumerate(options, start=1):
            suffix = "" if enabled else _dim("  (coming soon)")
            print(f"  [{i}] {opt_label}{suffix}")
        while True:
            raw = _prompt("Select", default=str(default_index + 1))
            try:
                idx = int(raw) - 1
            except ValueError:
                print(_red(f"  please enter a number 1–{len(options)}"))
                continue
            if not (0 <= idx < len(options)):
                print(_red(f"  please enter a number 1–{len(options)}"))
                continue
            if not options[idx][2]:
                print(_yellow(f"  {options[idx][0]} is coming soon — pick another."))
                continue
            return options[idx][1]

    # Interactive arrow-key mode
    idx = default_index
    if not options[idx][2]:
        # Advance to first enabled option
        for i, (_l, _v, en) in enumerate(options):
            if en:
                idx = i
                break

    # Print label once; menu rows re-render in place on each keypress
    print(_bold(label))

    def _render(first: bool) -> None:
        if not first:
            # Move cursor up to overwrite the previous menu render
            sys.stdout.write(f"\033[{len(options)}A")
        for i, (opt_label, _value, enabled) in enumerate(options):
            sys.stdout.write("\r\033[K")  # clear the line
            if i == idx:
                prefix = _green("  ▸ ")
                text = _bold(opt_label) if enabled else _dim(opt_label)
            else:
                prefix = "    "
                text = opt_label if enabled else _dim(opt_label)
            suffix = "" if enabled else _dim("  (coming soon)")
            sys.stdout.write(f"{prefix}{text}{suffix}\n")
        sys.stdout.flush()

    _render(first=True)
    try:
        while True:
            key = _read_key()
            if key == "ctrl-c":
                raise KeyboardInterrupt
            if key == "up":
                # Skip disabled options
                for offset in range(1, len(options) + 1):
                    cand = (idx - offset) % len(options)
                    if options[cand][2]:
                        idx = cand
                        break
                _render(first=False)
            elif key == "down":
                for offset in range(1, len(options) + 1):
                    cand = (idx + offset) % len(options)
                    if options[cand][2]:
                        idx = cand
                        break
                _render(first=False)
            elif key == "enter":
                if options[idx][2]:
                    return options[idx][1]
                # Shouldn't happen — we never land on disabled — but defend anyway
    finally:
        # Leave the final menu state visible; add a trailing newline so the
        # next prompt doesn't overlap the last menu row.
        pass


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

def default_config_path() -> Path:
    return Path.home() / ".config" / "hyperagent" / ".env"


def run_wizard(existing: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Run the wizard and return a config dict ready for save_config().

    `existing` is the current `.env` as a dict (for `hyperagent setup`);
    its values pre-fill prompt defaults so users can Enter through
    unchanged fields.

    Raises KeyboardInterrupt on Ctrl-C / EOF — caller decides what to do
    (main() discards and exits).
    """
    existing = existing or {}

    print_logo()
    print(_dim("Let's get you set up. This takes ~60 seconds.\n"))

    # --- Step 1: Network --------------------------------------------------
    # Arrow-key selector — ↑/↓ navigate, Enter commits. Mainnet is shown
    # dimmed with a "coming soon" hint and can't be selected.
    network = _select(
        "1) Network  " + _dim("(↑/↓ to move, Enter to select)"),
        [
            ("Testnet  " + _green("(safe, free testnet USDC)"), "testnet", True),
            ("Mainnet  " + _yellow("Coming soon"), "mainnet", False),
        ],
        default_index=0,
    )
    print()

    # --- Step 2: Reconcile existing positions ----------------------------
    print(_bold("2) Existing positions"))
    reconcile = _yes_no(
        "Do you have open positions on this account you want the agent to adopt?",
        default=False,
    )
    print()

    # --- Step 3: Agent wallet instructions --------------------------------
    approval_url = (
        AGENT_APPROVAL_URL_TESTNET if network == "testnet"
        else AGENT_APPROVAL_URL_MAINNET
    )
    print(_bold("3) Agent wallet"))
    print(
        "  HyperAgent signs trades with an " + _bold("agent wallet") + " — a\n"
        "  trade-only key you generate on Hyperliquid. It " + _green("CAN place trades.") + "\n"
        "  It " + _red("CANNOT withdraw funds") + " from your account. Revocable any time."
    )
    print()
    print("  Steps:")
    print(f"    1. Open {_bold(approval_url)}")
    print("    2. Connect your main wallet")
    print("    3. Click Generate → sign the approveAgent transaction")
    print("    4. Copy the agent private key shown on screen")
    print("    5. Return here")
    print()
    if _yes_no("Open the page in your browser now?", default=True):
        try:
            webbrowser.open(approval_url)
        except Exception:
            pass  # silent fallback — URL is already printed
    print()

    # --- Step 4: Main wallet address -------------------------------------
    print(_bold("4) Main wallet address"))
    while True:
        main_addr = _prompt(
            "Your main wallet address (0x…)",
            default=existing.get("HL_MAIN_ADDRESS") or None,
        )
        if is_valid_address(main_addr):
            break
        print(_red("  not a valid 0x-prefixed 42-char hex address — try again"))
    print()

    # --- Step 5: Agent private key ---------------------------------------
    print(_bold("5) Agent wallet private key"))
    print(_dim("  (input is hidden)"))
    while True:
        try:
            agent_key = getpass.getpass("Paste agent private key (0x…) > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise
        if not agent_key and existing.get("HL_AGENT_PRIVATE_KEY"):
            agent_key = existing["HL_AGENT_PRIVATE_KEY"]
            print(_dim("  keeping existing agent key"))
            break
        if is_valid_private_key(agent_key):
            break
        print(_red("  not a valid 0x-prefixed 64-char hex key — try again"))
    print()

    # --- Step 6: Validate pairing ----------------------------------------
    print(_bold("6) Verifying"))
    print(_dim("  Checking that Hyperliquid can see your main address…"))
    result: PairingResult = verify_agent_pairing(
        agent_key, main_addr, testnet=(network == "testnet")
    )
    if not result.ok:
        print(_red(f"  ✗ {result.error}"))
        print()
        print("Nothing was saved. Check that you approved the agent on the")
        print(f"right network ({network}) and try again.")
        raise KeyboardInterrupt
    print(_green("  ✓ main address reachable on " + network))
    print(_dim(
        "  (Final proof-of-approval happens on your first trade — if the"
        "\n  agent wasn't approved correctly, the TUI will show a clear error.)"
    ))
    print()

    # --- Step 7a: AI reasoning -------------------------------------------
    # Loops below honor `skip` as an explicit escape hatch. The user may
    # have answered "y" and then changed their mind (no key on hand,
    # decided to use a different AWS account, etc.) — without this, the
    # only way out was Ctrl-C, which dropped the entire wizard.
    print(_bold("7) AI trade explanations (optional)"))
    cfg: Dict[str, str] = {}
    if _yes_no(
        "Enable AI explanations via AWS Bedrock (Claude Haiku)?",
        default=bool(existing.get("AWS_ACCESS_KEY_ID")),
    ):
        skipped = False
        while True:
            raw = _prompt(
                "  AWS_ACCESS_KEY_ID (or type 'skip')",
                default=existing.get("AWS_ACCESS_KEY_ID") or None,
            )
            if raw.lower() == "skip":
                print(_dim("  skipped — AI disabled, run `hyperagent setup` later to add"))
                skipped = True
                break
            if raw:
                cfg["AWS_ACCESS_KEY_ID"] = raw
                break
            print(_red("  required — enter a key or type 'skip' to disable AI"))
        if not skipped:
            while True:
                raw = getpass.getpass(
                    "  AWS_SECRET_ACCESS_KEY (hidden, or type 'skip') > "
                ).strip()
                if raw.lower() == "skip":
                    print(_dim("  skipped — AI disabled"))
                    cfg.pop("AWS_ACCESS_KEY_ID", None)  # don't save a half-config
                    skipped = True
                    break
                secret = raw or existing.get("AWS_SECRET_ACCESS_KEY", "")
                if secret:
                    cfg["AWS_SECRET_ACCESS_KEY"] = secret
                    break
                print(_red("  required — enter a secret or type 'skip' to disable AI"))
        if not skipped:
            cfg["AWS_DEFAULT_REGION"] = _prompt(
                "  AWS_DEFAULT_REGION",
                default=existing.get("AWS_DEFAULT_REGION") or "us-east-1",
            )
    print()

    # --- Step 7b: HypeDexer ----------------------------------------------
    print(_bold("8) Liquidation Cascade v2 (optional)"))
    if _yes_no(
        "Enable the Liquidation Cascade v2 strategy (needs HypeDexer API key)?",
        default=bool(existing.get("HYPEDEXER_API_KEY")),
    ):
        while True:
            raw = getpass.getpass(
                "  HYPEDEXER_API_KEY (hidden, or type 'skip') > "
            ).strip()
            if raw.lower() == "skip":
                print(_dim("  skipped — cascade v2 disabled"))
                break
            key = raw or existing.get("HYPEDEXER_API_KEY", "")
            if key:
                cfg["HYPEDEXER_API_KEY"] = key
                break
            print(_red("  required — enter a key or type 'skip' to disable cascade v2"))
    print()

    # --- Step 8: Summary + confirm ---------------------------------------
    cfg["HL_NETWORK"] = network
    cfg["HL_MAIN_ADDRESS"] = main_addr
    cfg["HL_AGENT_PRIVATE_KEY"] = agent_key
    cfg["HL_RECONCILE_ON_BOOT"] = "1" if reconcile else "0"

    print(_bold("9) Review"))
    print(f"  Network:     {network}")
    print(f"  Main addr:   {main_addr[:6]}…{main_addr[-4:]}")
    print(f"  Agent key:   0x…{agent_key[-4:]}  {_dim('(hidden, saved)')}")
    print(f"  AI:          {'enabled' if cfg.get('AWS_ACCESS_KEY_ID') else 'disabled'}")
    print(f"  HypeDexer:   {'enabled' if cfg.get('HYPEDEXER_API_KEY') else 'disabled'}")
    print(f"  Reconcile:   {'yes' if reconcile else 'no'}")
    print(f"  Config file: {default_config_path()}")
    print()

    if not _yes_no("Save and launch?", default=True):
        print(_yellow("Discarded. Run `hyperagent` again to restart the wizard."))
        raise KeyboardInterrupt

    return cfg


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_config(cfg: Dict[str, str], path: Optional[Path] = None) -> Path:
    """Write the wizard's dict to a .env file with chmod 600.

    Keys are written in a stable order for readability. Values are written
    raw (no quoting) — they're all hex strings, ISO codes, or enum-like
    flags, none of which need shell escaping.
    """
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    order = [
        "HL_NETWORK",
        "HL_MAIN_ADDRESS",
        "HL_AGENT_PRIVATE_KEY",
        "HL_RECONCILE_ON_BOOT",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_DEFAULT_REGION",
        "HYPEDEXER_API_KEY",
    ]

    lines = [
        "# HyperAgent config - generated by `hyperagent setup`",
        "# TESTNET AGENT WALLET ONLY. Never paste a mainnet or main-wallet key here.",
        "",
    ]
    for key in order:
        if key in cfg and cfg[key] != "":
            lines.append(f"{key}={cfg[key]}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        # Windows / weird filesystems — best effort. Not a hard failure.
        pass
    return path


# ---------------------------------------------------------------------------
# Helper: parse an existing .env into a dict (for `hyperagent setup`)
# ---------------------------------------------------------------------------

def load_existing(path: Path) -> Dict[str, str]:
    """Minimal .env parser — we only need KEY=VALUE on uncommented lines."""
    out: Dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out
