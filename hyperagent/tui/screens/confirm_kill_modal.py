"""
Kill-switch confirmation modal.

Shown when the user presses 'k' (flatten everything) or clicks an
individual position row (close one coin). Returns True on confirm,
False/None on cancel.

Why a dedicated modal instead of a generic confirm? The kill path is
destructive — closes real positions, incurs fees, may realise losses.
A one-key accidental trigger on the main UI would be a disaster. The
modal forces an explicit second gesture (y / Enter on the Confirm
button) and states the exact scope ("Close 3 positions: BTC, ETH, SOL").
"""

from __future__ import annotations

from typing import List, Optional

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmKillModal(ModalScreen[bool]):
    """Modal asking the user to confirm a destructive close.

    Result type: bool — True on confirm, False on cancel.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("y", "confirm", "Confirm", show=True),
        Binding("enter", "confirm", "Confirm", show=False),
        Binding("n", "cancel", "No", show=False),
    ]

    DEFAULT_CSS = """
    ConfirmKillModal {
        align: center middle;
    }

    #kill-modal-container {
        width: 60;
        height: auto;
        background: #161b22;
        /* Red border + warning color — this is the destructive path */
        border: thick #f85149;
        padding: 1 2;
    }

    #kill-modal-title {
        color: #f85149;
        text-style: bold;
        padding-bottom: 1;
    }

    #kill-modal-scope {
        color: #c9d1d9;
        padding-bottom: 1;
    }

    #kill-modal-warning {
        color: #d29922;
        padding-bottom: 1;
    }

    #kill-modal-buttons {
        align-horizontal: right;
        height: auto;
    }

    #kill-modal-buttons Button {
        margin-left: 1;
    }

    #kill-modal-confirm {
        background: #f85149;
        color: #ffffff;
        text-style: bold;
    }
    """

    def __init__(
        self,
        coins: Optional[List[str]] = None,
        position_count: int = 0,
        **kwargs,
    ):
        """
        coins:
          None        -> close ALL positions (shows "Close N positions")
          [c1, c2]    -> close only those specific coins (shows them by name)
        position_count:
          Informational — number of positions that will actually close. For
          'close all', this is len(state.positions). For per-coin, it may
          include pair siblings that will be dragged along.
        """
        super().__init__(**kwargs)
        self.coins = coins
        self.position_count = position_count

    def compose(self) -> ComposeResult:
        with Vertical(id="kill-modal-container"):
            yield Static("⚠ CONFIRM KILL", id="kill-modal-title")
            yield Static(self._scope_text(), id="kill-modal-scope")
            yield Static(
                "This closes positions on Hyperliquid immediately at market. "
                "The strategy loop keeps running.",
                id="kill-modal-warning",
            )
            with Horizontal(id="kill-modal-buttons"):
                yield Button("Cancel [n]", id="kill-modal-cancel", variant="default")
                yield Button("Confirm [y]", id="kill-modal-confirm")

    def _scope_text(self) -> str:
        if self.coins is None:
            n = self.position_count
            if n == 0:
                return "No open positions to close."
            return f"Close ALL {n} open position{'s' if n != 1 else ''}?"
        if not self.coins:
            return "No coins selected."
        coin_list = ", ".join(self.coins)
        return f"Close position{'s' if len(self.coins) != 1 else ''} in: {coin_list}?"

    def on_mount(self) -> None:
        # Focus the Cancel button by default so a reflexive Enter DOES NOT
        # confirm — safer default. User has to explicitly press 'y' or
        # click Confirm.
        self.query_one("#kill-modal-cancel", Button).focus()

    @on(Button.Pressed, "#kill-modal-confirm")
    def _on_confirm(self) -> None:
        self.action_confirm()

    @on(Button.Pressed, "#kill-modal-cancel")
    def _on_cancel(self) -> None:
        self.action_cancel()

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
