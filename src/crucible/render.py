"""How model text reaches the screen.

Models write Markdown whether or not you asked them to — headings, lists,
`code`, fenced blocks — so rendering it is the difference between a chat that
reads like a document and one that reads like a diff. Everything CRUCIBLE
generates itself (reports, transcripts, About) has always been Markdown; this
is what closes the gap for the models' own output.

Raw mode is not an afterthought: this is a testing ground, and sometimes the
question *is* what the model literally emitted, byte for byte.
"""

from __future__ import annotations

import re

from rich.console import RenderableType
from rich.markdown import Markdown
from rich.text import Text

# Borrows the terminal's own 16 colours instead of shipping a second palette
# that fights the theme.
CODE_THEME = "ansi_dark"


def model_text(text: str, markdown: bool = True) -> RenderableType:
    """Render one assistant message.

    Either way the result is a renderable, never a markup string: console markup
    in model output (`[bold]`, `[/]`) is shown, not interpreted.
    """
    if not markdown:
        return Text(text)
    return Markdown(text, code_theme=CODE_THEME, hyperlinks=False)


# A hyphen or number only starts a list at the beginning of a line — matching it
# anywhere would fire on ordinary prose like "well-known".
_BLOCK_START = re.compile(r"^\s*([-*+]\s|\d+\.\s|#{1,6}\s|>\s|```)", re.MULTILINE)
_INLINE = ("`", "**", "__", "|", "](")


def looks_like_markdown(text: str) -> bool:
    """Whether Markdown rendering would visibly change this text.

    Used to keep one-line answers on the speaker's line instead of pushing them
    into a block of their own for no gain.
    """
    if "\n" in text.strip():
        return True
    if _BLOCK_START.search(text):
        return True
    return any(m in text for m in _INLINE)
