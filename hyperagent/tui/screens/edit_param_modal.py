"""
Modal dialog for editing a single strategy parameter.

Usage:
    app.push_screen(
        EditParamModal(spec, current_value),
        callback=lambda new_value: ...
    )

The callback receives the parsed + validated new value on save, or None
if the user cancelled. The caller is responsible for mutating
`config.<spec.config_key>` and refreshing any display widgets.

The modal keeps itself self-contained: parse + validate happen inside
the modal so the caller never sees malformed input.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from tui.param_schema import ParamSpec


# Validator signature: given a tentative new value, return error text or None.
# Used for cross-field invariants that the single spec can't express.
ExtraValidator = Callable[[Any], Optional[str]]


class EditParamModal(ModalScreen[Optional[Any]]):
    """Modal screen for editing a single ParamSpec's value.

    Result type: Optional[Any] — the parsed new value, or None if cancelled.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("enter", "save", "Save", show=False),
    ]

    DEFAULT_CSS = """
    EditParamModal {
        align: center middle;
    }

    #edit-modal-container {
        width: 64;
        height: auto;
        background: #161b22;
        border: thick #58a6ff;
        padding: 1 2;
    }

    #edit-modal-title {
        color: #58a6ff;
        text-style: bold;
        padding-bottom: 1;
    }

    #edit-modal-help {
        color: #8b949e;
        padding-bottom: 1;
    }

    #edit-modal-current {
        color: #8b949e;
        padding-bottom: 1;
    }

    #edit-modal-range {
        color: #8b949e;
        padding-bottom: 1;
    }

    #edit-modal-input {
        margin-bottom: 1;
        background: #0d1117;
        border: solid #30363d;
    }

    #edit-modal-input:focus {
        border: solid #58a6ff;
    }

    #edit-modal-error {
        color: #f85149;
        min-height: 1;
        padding-bottom: 1;
    }

    #edit-modal-buttons {
        align-horizontal: right;
        height: auto;
    }

    #edit-modal-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        spec: ParamSpec,
        current_value: Any,
        extra_validator: Optional[ExtraValidator] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.spec = spec
        self.current_value = current_value
        # extra_validator runs AFTER per-row min/max. Used for cross-field
        # invariants like "EMA_FAST < EMA_SLOW" where the parent screen has
        # context about other parameter values.
        self.extra_validator = extra_validator

    def compose(self) -> ComposeResult:
        with Vertical(id="edit-modal-container"):
            yield Static(f"Edit: {self.spec.label}", id="edit-modal-title")
            if self.spec.help:
                yield Static(self.spec.help, id="edit-modal-help")
            yield Static(
                f"Current: {self.spec.format(self.current_value)}",
                id="edit-modal-current",
            )
            range_text = self._range_text()
            if range_text:
                yield Static(range_text, id="edit-modal-range")
            yield Input(
                value=self.spec.format(self.current_value),
                placeholder="new value",
                id="edit-modal-input",
            )
            yield Static("", id="edit-modal-error")
            with Horizontal(id="edit-modal-buttons"):
                yield Button("Cancel", id="edit-modal-cancel", variant="default")
                yield Button("Save", id="edit-modal-save", variant="success")

    def on_mount(self) -> None:
        input_widget = self.query_one("#edit-modal-input", Input)
        input_widget.focus()

    def _range_text(self) -> str:
        lo = (
            self.spec.format(self.spec.min) if self.spec.min is not None else None
        )
        hi = (
            self.spec.format(self.spec.max) if self.spec.max is not None else None
        )
        if lo is not None and hi is not None:
            return f"Range: {lo} – {hi}"
        if lo is not None:
            return f"Min: {lo}"
        if hi is not None:
            return f"Max: {hi}"
        return ""

    # ----- event handlers -----

    @on(Input.Changed, "#edit-modal-input")
    def _validate_live(self, event: Input.Changed) -> None:
        """Live validation — show parse/range/invariant errors as user types."""
        error_widget = self.query_one("#edit-modal-error", Static)
        save_btn = self.query_one("#edit-modal-save", Button)
        value = event.value.strip()
        if not value:
            error_widget.update("")
            save_btn.disabled = False
            return
        try:
            parsed = self.spec.parse(value)
        except (ValueError, TypeError, OverflowError) as e:
            error_widget.update(f"Invalid: {e}")
            save_btn.disabled = True
            return
        # Layer 1: per-row bounds
        err = self.spec.validate(parsed)
        if err:
            error_widget.update(err)
            save_btn.disabled = True
            return
        # Layer 2: cross-field invariants (if provided)
        if self.extra_validator:
            err = self.extra_validator(parsed)
            if err:
                error_widget.update(err)
                save_btn.disabled = True
                return
        error_widget.update("")
        save_btn.disabled = False

    @on(Button.Pressed, "#edit-modal-save")
    def _on_save(self) -> None:
        self.action_save()

    @on(Button.Pressed, "#edit-modal-cancel")
    def _on_cancel(self) -> None:
        self.action_cancel()

    @on(Input.Submitted, "#edit-modal-input")
    def _on_input_submit(self) -> None:
        self.action_save()

    # ----- actions -----

    def action_save(self) -> None:
        input_widget = self.query_one("#edit-modal-input", Input)
        error_widget = self.query_one("#edit-modal-error", Static)
        try:
            parsed = self.spec.parse(input_widget.value)
        except (ValueError, TypeError, OverflowError) as e:
            error_widget.update(f"Invalid: {e}")
            return
        err = self.spec.validate(parsed)
        if err:
            error_widget.update(err)
            return
        if self.extra_validator:
            err = self.extra_validator(parsed)
            if err:
                error_widget.update(err)
                return
        self.dismiss(parsed)

    def action_cancel(self) -> None:
        self.dismiss(None)
