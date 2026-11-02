"""Scicher's themes — Tokyo Night, with every color traced to a specific key in
the official themes/tokyo-night-color-theme.json and
themes/tokyo-night-light-color-theme.json files at
github.com/tokyo-night/tokyo-night-vscode-theme (not Textual's own
approximate built-in copy, nor this project's earlier guesses):

  role       dark source key                              light source key
  primary    button.background / activityBarBadge.bg       (same keys)
  secondary  purple token color (keyword syntax highlight)  (same)
  accent     orange token color (constant/number highlight) (same)
  warning    editorWarning.foreground                       (same)
  error      editorError.foreground                         (same)
  success    gitDecoration.addedResourceForeground          (same)
  foreground editor.foreground                              (same)
  background editor.background                              (same)
  surface    editorWidget.background / sideBar.background   (same)
  panel      editor.lineHighlightBackground                 (same)
"""

from textual.theme import Theme

SCIENCE_DARK_THEME = Theme(
    name="science-dark",
    primary="#3D59A1",
    secondary="#BB9AF7",
    accent="#FF9E64",
    warning="#E0AF68",
    error="#DB4B4B",
    success="#449DAB",
    foreground="#A9B1D6",
    background="#1A1B26",
    surface="#16161E",
    panel="#1E202E",
    boost="#1E202E",
    dark=True,
)

SCIENCE_LIGHT_THEME = Theme(
    name="science-light",
    primary="#2959AA",
    secondary="#65359D",
    accent="#965027",
    warning="#8F5E15",
    error="#BD4040",
    success="#166775",
    foreground="#343B59",
    background="#E6E7ED",
    surface="#D6D8DF",
    panel="#DCDEE3",
    boost="#DCDEE3",
    dark=False,
)
