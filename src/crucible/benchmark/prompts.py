"""Default benchmark prompt set — small enough that a full suite on a 7–8B model
finishes in a couple of minutes, varied enough to exercise short/long prompts
and different generation styles.
"""

from __future__ import annotations

from dataclasses import dataclass

_PASSAGE = (
    "The crucible has been used since antiquity as a vessel in which metals are "
    "melted and refined at extreme temperatures. Early crucibles were made of "
    "clay, and archaeological finds date them to the sixth millennium BCE in "
    "Eastern Europe and Iran. Because a crucible must withstand heat that would "
    "destroy lesser materials, the word acquired a second, figurative life: a "
    "crucible is any severe test or trial, a place where elements interact to "
    "produce something new. Modern laboratory crucibles are made of porcelain, "
    "alumina, or platinum, and are used in gravimetric analysis where samples "
    "are heated to constant mass. In metallurgy, crucible steel was prized for "
    "its uniformity; its production in ancient India as wootz steel and later "
    "in Sheffield by Benjamin Huntsman marked milestones in materials science. "
    "The technique involved sealing iron and carbon sources inside a crucible "
    "and heating them until fully molten, yielding steel free of slag "
    "inclusions. Today the term survives in both senses: chemists reach for "
    "crucibles daily, and writers describe formative ordeals as crucibles."
)


@dataclass
class Prompt:
    id: str
    text: str


DEFAULT_PROMPTS = [
    Prompt("qa-short", "What is the capital of France? Answer with one word."),
    Prompt("summarize", f"Summarize the following passage in two sentences:\n\n{_PASSAGE}"),
    Prompt(
        "code",
        "Write a Python function `top_k(words: list[str], k: int)` that returns "
        "the k most frequent words, ties broken alphabetically. Code only.",
    ),
    Prompt(
        "reason",
        "A farmer has chickens and rabbits, 35 heads and 94 legs in total. "
        "How many of each? Show your reasoning briefly.",
    ),
]

PROMPT_SETS = {"default": DEFAULT_PROMPTS}


def get_prompt_set(name: str) -> list[Prompt]:
    return PROMPT_SETS.get(name, DEFAULT_PROMPTS)
