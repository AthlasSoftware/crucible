"""The CRUCIBLE banner, pre-rendered so we carry no figlet dependency."""

BANNER = """\
 ██████╗██████╗ ██╗   ██╗ ██████╗██╗██████╗ ██╗     ███████╗
██╔════╝██╔══██╗██║   ██║██╔════╝██║██╔══██╗██║     ██╔════╝
██║     ██████╔╝██║   ██║██║     ██║██████╔╝██║     █████╗
██║     ██╔══██╗██║   ██║██║     ██║██╔══██╗██║     ██╔══╝
╚██████╗██║  ██║╚██████╔╝╚██████╗██║██████╔╝███████╗███████╗
 ╚═════╝╚═╝  ╚═╝ ╚═════╝  ╚═════╝╚═╝╚═════╝ ╚══════╝╚══════╝"""

BANNER_SMALL = """\
╔═╗╦═╗╦ ╦╔═╗╦╔╗ ╦  ╔═╗
║  ╠╦╝║ ║║  ║╠╩╗║  ║╣
╚═╝╩╚═╚═╝╚═╝╩╚═╝╩═╝╚═╝"""

# Width of the large banner; below this we fall back to the small one.
BANNER_WIDTH = 60
BANNER_SMALL_WIDTH = 22

SUBTITLE = "POWERED BY ATHLAS.IO"
TAGLINE = "The LLM testing grounds"


def assemble(small: bool = False) -> str:
    """Banner + tagline as one block, aligned to the art's width.

    Branding ("POWERED BY ATHLAS.IO") deliberately lives on the About page,
    not here — the home screen stays calm.
    """
    art = BANNER_SMALL if small else BANNER
    width = BANNER_SMALL_WIDTH if small else BANNER_WIDTH
    return f"{art}\n[dim italic]{TAGLINE.center(width).rstrip()}[/]"
