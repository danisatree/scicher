"""Scicher's themes — a clean, academic/lab-notebook palette in light and dark variants."""

from textual.theme import Theme

SCIENCE_DARK_THEME = Theme(
    name="science-dark",
    primary="#5ea8c7",
    secondary="#7c93b3",
    accent="#d9a441",
    warning="#e0b64f",
    error="#c75c5c",
    success="#6fae7f",
    foreground="#e8ecf1",
    background="#10141c",
    surface="#171c26",
    panel="#1d2330",
    boost="#242b3a",
    dark=True,
)

SCIENCE_LIGHT_THEME = Theme(
    name="science-light",
    primary="#2f5d7c",
    secondary="#4d6b85",
    accent="#a8762c",
    warning="#b5861f",
    error="#a33f3f",
    success="#3f7d52",
    foreground="#1e232b",
    background="#f7f5f0",
    surface="#ffffff",
    panel="#ececE5",
    boost="#e2ded4",
    dark=False,
)
