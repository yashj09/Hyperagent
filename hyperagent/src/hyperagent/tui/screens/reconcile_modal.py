"""
Startup reconciliation modal.

Shown before workers start if HyperAgent detects positions on HL that
it didn't open. User decides per-position: Adopt / Ignore / Close.

UX: table of unknown positions with an editable "Action" column.
Space or Enter on a row cycles Adopt -> Ignore -> Close -> Adopt.
When the user is satisfied, they press Confirm to apply all actions.
Cancel leaves every unactioned row as "Ignore" (safest default).

Result dismissed back to caller: Dict[coin -> action_string].
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static


# The three possible actions. Cycling through them is linear.
# "ignore" is the default — safest if the user cancels or skips a row.
ACTIONS = ["ignore", "adopt", "close"]


class ReconcileModal(ModalScreen[Dict[str, str]]):
    """Modal for resolving unknown HL positions at startup.

    positions input shape: list of dicts with keys
      coin: str
      side: 'long' | 'short'
      size: float         (absolute)
      entry_price: float
      current_price: float (0 if unknown)

    Result: {coin -> 'adopt' | 'ignore' | 'close'}
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("space", "cycle_action", "Cycle Action", show=True),
        Binding("enter", "cycle_action", "Cycle Action", show=False),
        Binding("c", "confirm", "Confirm", show=True),
    ]

    DEFAULT_CSS = """
    ReconcileModal {
        align: center middle;
    }

    #reconcile-container {
        width: 90;
        height: auto;
        max-height: 30;
        background: #161b22;
        border: thick #d29922;
        padding: 1 2;
    }

    #reconcile-title {
        color: #d29922;
        text-style: bold;
        padding-bottom: 1;
    }

    #reconcile-help {
        color: #8b949e;
        padding-bottom: 1;
    }

    #reconcile-table {
        height: 12;
        border: solid #30363d;
        margin-bottom: 1;
    }

    #reconcile-buttons {
        align-horizontal: right;
        height: auto;
    }

    #reconcile-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        positions: List[Dict[str, Any]],
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.positions = positions
        # Default to "adopt": the modal is only shown because the user
        # answered "yes" to the wizard's adopt question, so pressing
        # Confirm without cycling should honour that intent.
        self.actions: Dict[str, str] = {p["coin"]: "adopt" for p in positions}
        # Row key -> coin, so we can resolve DataTable cursor to the coin.
        self._row_coins: List[str] = []

    def compose(self) -> ComposeResult:
        n = len(self.positions)
        with Vertical(id="reconcile-container"):
            yield Static(
                f"⚠ RECONCILE: {n} unknown position{'s' if n != 1 else ''} on Hyperliquid",
                id="reconcile-title",
            )
            yield Static(
                "These positions exist on HL but weren't opened by this session. "
                "Choose per-position:  Adopt = manage it with trailing stop, "
                "Ignore = leave alone and block trading that coin, "
                "Close = market-close now.  Press Space/Enter to cycle.",
                id="reconcile-help",
            )
            yield DataTable(id="reconcile-table")
            with Horizontal(id="reconcile-buttons"):
                yield Button("Cancel (all ignore)", id="reconcile-cancel", variant="default")
                yield Button("Confirm [c]", id="reconcile-confirm", variant="success")

    def on_mount(self) -> None:
        table = self.query_one("#reconcile-table", DataTable)
        table.add_columns("Coin", "Side", "Size", "Entry", "Current", "Action")
        table.cursor_type = "row"
        table.zebra_stripes = True

        for pos in self.positions:
            coin = pos["coin"]
            self._row_coins.append(coin)
            table.add_row(
                coin,
                pos.get("side", "?").upper(),
                f"{pos.get('size', 0):g}",
                f"${pos.get('entry_price', 0):,.2f}",
                f"${pos.get('current_price', 0):,.2f}" if pos.get("current_price") else "—",
                self.actions[coin].upper(),
            )

        table.focus()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_cycle_action(self) -> None:
        """Cycle the currently-highlighted row through Ignore / Adopt / Close."""
        table = self.query_one("#reconcile-table", DataTable)
        cursor_row = table.cursor_row
        if cursor_row is None or cursor_row < 0 or cursor_row >= len(self._row_coins):
            return
        coin = self._row_coins[cursor_row]
        current = self.actions[coin]
        # Cycle forward
        idx = ACTIONS.index(current)
        new_action = ACTIONS[(idx + 1) % len(ACTIONS)]
        self.actions[coin] = new_action

        # Update the table cell in-place
        table.update_cell_at((cursor_row, 5), new_action.upper())

    def action_confirm(self) -> None:
        """Dismiss with the accumulated action map."""
        self.dismiss(dict(self.actions))

    def action_cancel(self) -> None:
        """Cancel — treat every row as 'ignore'. Nothing is touched on HL."""
        self.dismiss({coin: "ignore" for coin in self._row_coins})

    # ------------------------------------------------------------------
    # Button wiring
    # ------------------------------------------------------------------

    @on(Button.Pressed, "#reconcile-confirm")
    def _on_confirm(self) -> None:
        self.action_confirm()

    @on(Button.Pressed, "#reconcile-cancel")
    def _on_cancel(self) -> None:
        self.action_cancel()
