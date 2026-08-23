"""Harness mode: chat with a model and spawn resources for it."""

from crucible.harness.chat import (
    HarnessCallbacks,
    HarnessChat,
    HarnessState,
)
from crucible.harness.resources import (
    RESOURCE_KINDS,
    BrowserResource,
    Resource,
    ResourceError,
    TerminalResource,
    create_resource,
    next_name,
)

__all__ = [
    "RESOURCE_KINDS",
    "BrowserResource",
    "HarnessCallbacks",
    "HarnessChat",
    "HarnessState",
    "Resource",
    "ResourceError",
    "TerminalResource",
    "create_resource",
    "next_name",
]
