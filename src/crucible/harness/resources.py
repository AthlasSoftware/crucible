"""Resources a harness model can be handed: a terminal, a browser, more later.

A Resource owns one container, publishes a few OpenAI-style tool schemas, and
executes calls against them. The harness itself never knows what a browser is —
it asks every attached resource for `tools()` and routes tool calls back by
name — so adding a third kind means adding a class and one registry entry.

Tool names are `<resource name>_<verb>` (`terminal_run`, `browser2_goto`), which
keeps them unique when two of a kind are attached and stays inside the
`[a-zA-Z0-9_-]` charset every backend accepts.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable

from crucible.config import HarnessCfg
from crucible.harness.browser_driver import DRIVER_SOURCE
from crucible.sessions.docker import HARNESS_LABEL, DockerSandbox, SandboxError

# on_output(stream, line) where stream is "stdout" | "stderr" | "info"
OutputCb = Callable[[str, str], None]

RESOURCE_KINDS = ("terminal", "browser", "search")

# Where the CDP endpoint lives inside the browser container. Never published to
# the host: every call goes through `docker exec`, so nothing listens outside.
_CDP_PORT = 9222
_DRIVER_PATH = "/tmp/crucible_browser_driver.py"
_BROWSER_READY_TIMEOUT_S = 90
_CHROME_GLOB = "/ms-playwright/chromium-*/chrome-linux/chrome"
# "…/playwright/python:v1.55.0-noble" → pin the client to 1.55.0.
_IMAGE_VERSION_RE = re.compile(r"[:@]v?(\d+\.\d+\.\d+)")


class ResourceError(Exception):
    pass


def _noop(_stream: str, _line: str) -> None:
    pass


class Resource:
    """One spawned thing the model can use. Subclasses define the verbs."""

    kind = ""

    def __init__(self, name: str, cfg: HarnessCfg):
        self.name = name
        self.cfg = cfg
        self.sandbox: DockerSandbox | None = None

    @property
    def alive(self) -> bool:
        return self.sandbox is not None and self.sandbox.alive

    @property
    def container_name(self) -> str:
        return self.sandbox.name if self.sandbox else "—"

    def _new_sandbox(self, image: str, network: bool) -> DockerSandbox:
        return DockerSandbox(
            image=image,
            cpus=self.cfg.cpus,
            memory=self.cfg.memory,
            network=network,
            label=HARNESS_LABEL,
            name_prefix=f"crucible-harness-{self.kind}",
        )

    async def start(self, emit: OutputCb = _noop) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        if self.sandbox and self.sandbox.alive:
            await self.sandbox.destroy()

    def tools(self) -> list[dict]:
        raise NotImplementedError

    def owns(self, tool_name: str) -> bool:
        return tool_name.startswith(f"{self.name}_")

    def needs_approval(self, tool_name: str) -> bool:
        """Whether approve-each should stop for this call.

        Default yes: a resource has to opt its harmless verbs out, so a new
        resource kind is cautious until someone has thought about it.
        """
        return True

    async def call(self, tool_name: str, args: dict, emit: OutputCb = _noop) -> str:
        raise NotImplementedError

    def summary(self) -> str:
        raise NotImplementedError

    def describe_for_model(self) -> str:
        raise NotImplementedError

    def _tool(self, verb: str, description: str, properties: dict, required: list[str]) -> dict:
        return {
            "type": "function",
            "function": {
                "name": f"{self.name}_{verb}",
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }


class TerminalResource(Resource):
    """A persistent shell in a disposable container."""

    kind = "terminal"

    async def start(self, emit: OutputCb = _noop) -> None:
        self.sandbox = self._new_sandbox(self.cfg.terminal_image, self.cfg.terminal_network)
        await self.sandbox.create(on_output=emit)

    def tools(self) -> list[dict]:
        return [
            self._tool(
                "run",
                f"Run a shell command in the '{self.name}' Linux container and get its "
                "output back. Non-interactive: never start editors, pagers, or anything "
                "that waits for input.",
                {"command": {"type": "string", "description": "The shell command to run."}},
                ["command"],
            )
        ]

    async def call(self, tool_name: str, args: dict, emit: OutputCb = _noop) -> str:
        if tool_name != f"{self.name}_run":
            raise ResourceError(f"{self.name}: unknown tool {tool_name}")
        command = str(args.get("command", "")).strip()
        if not command:
            return "error: no command given"
        if not self.sandbox:
            raise ResourceError(f"{self.name}: not started")
        result = await self.sandbox.exec(
            command, timeout_s=self.cfg.command_timeout_s, on_output=emit
        )
        emit("info", f"(exit {result.exit_code}{' · timeout' if result.timed_out else ''})")
        from crucible.sessions import protocol

        return protocol.format_exec_result(
            result.exit_code, result.stdout, result.stderr, result.truncated
        )

    def summary(self) -> str:
        net = "net on" if self.cfg.terminal_network else "net off"
        return f"{self.cfg.terminal_image} · {net}"

    def describe_for_model(self) -> str:
        net = "enabled" if self.cfg.terminal_network else "DISABLED (no internet)"
        return (
            f"- `{self.name}` — a Linux shell ({self.cfg.terminal_image}), network {net}. "
            f"Use `{self.name}_run`. Each command is killed after "
            f"{self.cfg.command_timeout_s}s."
        )


class BrowserResource(Resource):
    """A real headless Chromium the model can drive.

    Chromium runs as a long-lived process inside the container with a CDP port
    open on loopback; each tool call is a short Playwright script that attaches
    over CDP, acts, and detaches. Keeping the browser (not the script) alive is
    what makes navigation stateful — cookies, logins and SPA state survive
    between calls, which a fetch-per-call design cannot do.
    """

    kind = "browser"

    def __init__(self, name: str, cfg: HarnessCfg):
        super().__init__(name, cfg)
        self._ready = False

    async def start(self, emit: OutputCb = _noop) -> None:
        # A browser with no network is a prop, so this one ignores
        # terminal_network and always gets a connection.
        self.sandbox = self._new_sandbox(self.cfg.browser_image, network=True)
        await self.sandbox.create(on_output=emit)
        emit("info", f"starting chromium (CDP :{_CDP_PORT}) ...")
        # Locate the binary by glob rather than by asking Playwright: the image
        # ships browsers but not the Python package, so nothing importable
        # exists yet at this point.
        await self.sandbox.exec_detached(
            f"CHROME=$(ls -d {_CHROME_GLOB} 2>/dev/null | head -1); "
            '[ -n "$CHROME" ] || exit 1; '
            f'exec "$CHROME" --headless=new --remote-debugging-port={_CDP_PORT} '
            "--remote-debugging-address=127.0.0.1 --no-sandbox --disable-gpu "
            "--disable-dev-shm-usage about:blank"
        )
        emit("info", "installing browser driver ...")
        await self._install_playwright(emit)
        await self._install_driver()
        await self._await_ready(emit)
        self._ready = True
        emit("info", "browser ready")

    async def _install_playwright(self, emit: OutputCb) -> None:
        """Add the Playwright Python package; the image only ships the browsers.

        Pinned to the version in the image tag when we can read one, so the
        client matches the bundled browser build instead of drifting to
        whatever is newest on PyPI.
        """
        assert self.sandbox is not None
        match = _IMAGE_VERSION_RE.search(self.cfg.browser_image)
        spec = f"playwright=={match.group(1)}" if match else "playwright"
        result = await self.sandbox.exec(
            f'python3 -c "import playwright" 2>/dev/null || '
            f"pip install --quiet --no-input --disable-pip-version-check {spec}",
            timeout_s=300,
        )
        if result.exit_code != 0:
            raise ResourceError(
                f"{self.name}: could not install {spec} "
                f"(needs internet): {result.stderr.strip()[:200]}"
            )

    async def _install_driver(self) -> None:
        assert self.sandbox is not None
        # base64 so the script's quotes and newlines survive the trip through
        # `sh -c` untouched.
        blob = base64.b64encode(DRIVER_SOURCE.encode()).decode()
        result = await self.sandbox.exec(
            f"printf %s '{blob}' | base64 -d > {_DRIVER_PATH}", timeout_s=30
        )
        if result.exit_code != 0:
            raise ResourceError(f"{self.name}: could not install driver: {result.stderr.strip()}")

    async def _await_ready(self, emit: OutputCb) -> None:
        assert self.sandbox is not None
        probe = (
            "python3 -c \"import urllib.request,sys; "
            f"urllib.request.urlopen('http://127.0.0.1:{_CDP_PORT}/json/version', timeout=2)\""
        )
        deadline_polls = _BROWSER_READY_TIMEOUT_S // 2
        for _ in range(deadline_polls):
            result = await self.sandbox.exec(probe, timeout_s=5)
            if result.exit_code == 0:
                return
            await _sleep(2)
        raise ResourceError(
            f"{self.name}: chromium did not open its debug port within "
            f"{_BROWSER_READY_TIMEOUT_S}s"
        )

    def tools(self) -> list[dict]:
        url = {"url": {"type": "string", "description": "Absolute URL, including scheme."}}
        selector = {
            "selector": {"type": "string", "description": "A CSS selector for the element."}
        }
        return [
            self._tool(
                "goto",
                f"Navigate the '{self.name}' browser to a URL and return the page title and "
                "readable text.",
                url,
                ["url"],
            ),
            self._tool(
                "read",
                f"Return the current page's title, URL and readable text in '{self.name}' "
                "without navigating.",
                {},
                [],
            ),
            self._tool(
                "click",
                f"Click an element in '{self.name}', then return the resulting page text.",
                selector,
                ["selector"],
            ),
            self._tool(
                "type",
                f"Type text into an input in '{self.name}' and optionally press Enter.",
                {
                    **selector,
                    "text": {"type": "string", "description": "Text to type."},
                    "enter": {
                        "type": "boolean",
                        "description": "Press Enter afterwards. Default false.",
                    },
                },
                ["selector", "text"],
            ),
            self._tool(
                "links",
                f"List the links on the current page in '{self.name}' as text → href.",
                {},
                [],
            ),
        ]

    # Reading the current page changes nothing; navigating and clicking do.
    _READ_ONLY_VERBS = ("read", "links")

    def needs_approval(self, tool_name: str) -> bool:
        return tool_name[len(self.name) + 1 :] not in self._READ_ONLY_VERBS

    async def call(self, tool_name: str, args: dict, emit: OutputCb = _noop) -> str:
        if not self.sandbox:
            raise ResourceError(f"{self.name}: not started")
        if not self._ready:
            raise ResourceError(f"{self.name}: browser is not ready yet")
        action = tool_name[len(self.name) + 1 :]
        request = json.dumps({"action": action, "args": args})
        emit("info", f"browser: {action} {json.dumps(args)[:120]}")
        blob = base64.b64encode(request.encode()).decode()
        # The request goes in base64 too — URLs and selectors are full of
        # characters `sh -c` would otherwise eat.
        result = await self.sandbox.exec(
            f"python3 {_DRIVER_PATH} \"$(printf %s '{blob}' | base64 -d)\"",
            timeout_s=self.cfg.command_timeout_s,
        )
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            tail = detail[-1] if detail else f"exit {result.exit_code}"
            return f"error: {tail}"
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return f"error: driver returned unparseable output: {result.stdout[:300]}"
        if payload.get("error"):
            return f"error: {payload['error']}"
        return str(payload.get("result", ""))

    def summary(self) -> str:
        state = "ready" if self._ready else "starting"
        return f"chromium · {state} · net on"

    def describe_for_model(self) -> str:
        return (
            f"- `{self.name}` — a headless Chromium browser with internet access. "
            f"Use `{self.name}_goto` to navigate, `{self.name}_read` to re-read the page, "
            f"`{self.name}_click` / `{self.name}_type` to interact, `{self.name}_links` "
            "to list links. Navigation is stateful: cookies and logins persist."
        )


class SearchResource(Resource):
    """Web search through a SearXNG instance — no browser, no captchas.

    Search engines captcha headless browsers on sight; a metasearch instance
    does the querying server-side and hands back clean JSON. The intended flow:
    search here, then open a promising URL in a browser resource. Runs no
    container — it is an HTTP client, so it costs nothing to keep attached.
    """

    kind = "search"
    _CATEGORIES = ("general", "news", "it", "science", "images")
    _MAX_RESULTS = 8

    def __init__(self, name: str, cfg: HarnessCfg):
        super().__init__(name, cfg)
        self.url = ""
        self._ready = False

    @property
    def alive(self) -> bool:  # no sandbox — alive means "probed and answering"
        return self._ready

    @property
    def container_name(self) -> str:
        return self.url or "—"

    async def start(self, emit: OutputCb = _noop) -> None:
        self.url = (self.cfg.searxng_url or "").rstrip("/")
        if not self.url:
            emit("info", "no searxng_url configured — looking for a searxng container …")
            self.url = await self._autodetect()
        emit("info", f"probing {self.url} …")
        await self._probe()
        self._ready = True
        emit("info", f"search ready via {self.url}")

    async def _autodetect(self) -> str:
        from crucible.sessions.docker import _docker

        code, out, _ = await _docker("ps", "--format", "{{.Names}}")
        candidates = [n for n in out.split() if "searxng" in n.lower()] if code == 0 else []
        if not candidates:
            raise ResourceError(
                "no SearXNG found: set harness.searxng_url in Config, or run a "
                "container with 'searxng' in its name"
            )
        code, out, _ = await _docker(
            "inspect", candidates[0],
            "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}",
        )
        ip = out.split()[0] if code == 0 and out.split() else ""
        if not ip:
            raise ResourceError(f"could not read the IP of container {candidates[0]}")
        return f"http://{ip}:8080"

    async def _probe(self) -> None:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.url}/search", params={"q": "ping", "format": "json"}
                )
        except httpx.HTTPError as e:
            raise ResourceError(f"{self.name}: cannot reach SearXNG at {self.url}: {e}") from e
        if resp.status_code == 403:
            raise ResourceError(
                f"{self.name}: SearXNG at {self.url} has its JSON API disabled — "
                "add 'json' to search.formats in its settings.yml"
            )
        if resp.status_code >= 400:
            raise ResourceError(f"{self.name}: SearXNG returned HTTP {resp.status_code}")

    async def stop(self) -> None:
        self._ready = False

    def tools(self) -> list[dict]:
        return [
            self._tool(
                "web",
                f"Search the web via '{self.name}' (SearXNG metasearch). Returns the top "
                "results as title, URL and snippet. Use this for finding pages — do NOT "
                "use a browser on search engines, they block bots. Open the best result "
                "in a browser resource if you need the full page.",
                {
                    "query": {"type": "string", "description": "The search query."},
                    "category": {
                        "type": "string",
                        "enum": list(self._CATEGORIES),
                        "description": "Result category. Default general.",
                    },
                },
                ["query"],
            )
        ]

    def needs_approval(self, tool_name: str) -> bool:
        return False  # searching reads the web; it changes nothing

    async def call(self, tool_name: str, args: dict, emit: OutputCb = _noop) -> str:
        import httpx

        if tool_name != f"{self.name}_web":
            raise ResourceError(f"{self.name}: unknown tool {tool_name}")
        query = str(args.get("query", "")).strip()
        if not query:
            return "error: empty query"
        category = str(args.get("category", "general"))
        if category not in self._CATEGORIES:
            category = "general"
        emit("info", f"search: {query!r} ({category})")
        params = {"q": query, "format": "json", "categories": category}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(f"{self.url}/search", params=params)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            return f"error: search failed: {e}"
        results = data.get("results", [])[: self._MAX_RESULTS]
        if not results:
            return "no results"
        lines = []
        for i, r in enumerate(results, 1):
            snippet = (r.get("content") or "").strip().replace("\n", " ")[:280]
            lines.append(f"{i}. {r.get('title', '?')}\n   {r.get('url', '')}\n   {snippet}")
        answers = data.get("answers")
        if answers:
            first = answers[0]
            text = first.get("answer") if isinstance(first, dict) else str(first)
            if text:
                lines.insert(0, f"answer: {text}")
        return "\n".join(lines)

    def summary(self) -> str:
        return f"searxng · {self.url or 'autodetect'}" + ("" if self._ready else " · starting")

    def describe_for_model(self) -> str:
        return (
            f"- `{self.name}` — web search (SearXNG). Use `{self.name}_web` with a query "
            "to find pages; results include URLs you can open in a browser resource. "
            "Never browse to search engines directly — they captcha bots."
        )


_KIND_CLASSES: dict[str, type[Resource]] = {
    "terminal": TerminalResource,
    "browser": BrowserResource,
    "search": SearchResource,
}


def create_resource(kind: str, name: str, cfg: HarnessCfg) -> Resource:
    try:
        return _KIND_CLASSES[kind](name, cfg)
    except KeyError:
        raise ResourceError(
            f"unknown resource kind {kind!r}, expected one of {RESOURCE_KINDS}"
        ) from None


def next_name(kind: str, existing: list[Resource]) -> str:
    """First free `terminal`, `terminal2`, `terminal3`, … for this kind."""
    taken = {r.name for r in existing}
    if kind not in taken:
        return kind
    n = 2
    while f"{kind}{n}" in taken:
        n += 1
    return f"{kind}{n}"


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


__all__ = [
    "RESOURCE_KINDS",
    "BrowserResource",
    "SearchResource",
    "Resource",
    "ResourceError",
    "SandboxError",
    "TerminalResource",
    "create_resource",
    "next_name",
]
