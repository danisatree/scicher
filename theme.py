"""Scicher's themes — Tokyo Night. The dark variant is copied verbatim from
Textual's own built-in "tokyo-night" theme (textual.theme.BUILTIN_THEMES), so
its colors are exact, not approximated. There's no official Tokyo Night light
variant shipped anywhere Scicher depends on, so science-light is this
project's own light-mode adaptation of the same accent colors (same
primary/secondary/accent/warning/error/success hexes, inverted neutrals) —
not a claim that it matches any particular "Tokyo Day" theme byte-for-byte."""

from textual.theme import Theme

SCIENCE_DARK_THEME = Theme(
    name="science-dark",
    primary="#BB9AF7",
    secondary="#7AA2F7",
    accent="#FF9E64",
    warning="#E0AF68",
    error="#F7768E",
    success="#9ECE6A",
    foreground="#A9B1D6",
    background="#1A1B26",
    surface="#24283B",
    panel="#414868",
    boost="#414868",
    dark=True,
)

SCIENCE_LIGHT_THEME = Theme(
    name="science-light",
    primary="#BB9AF7",
    secondary="#7AA2F7",
    accent="#FF9E64",
    warning="#E0AF68",
    error="#F7768E",
    success="#9ECE6A",
    foreground="#1A1B26",
    background="#E1E2E7",
    surface="#FFFFFF",
    panel="#D5D6DB",
    boost="#D5D6DB",
    dark=False,
)
