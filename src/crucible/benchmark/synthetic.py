"""Deterministic synthetic prompts for standardized benchmarks.

Word-soup at a target token count (~1 common word ≈ 1 BPE token). Exact counts
don't matter: the server reports real prompt_eval_count, which is what gets
recorded — the test keeps its llama-bench-style name (pp512) by target.
"""

from __future__ import annotations

import random

_WORDS = (
    "the of and a to in is was he for it with as his on be at by had not are "
    "but from or have an they which one you were her all she there would their "
    "we him been has when who will more no if out so said what up its about "
    "into than them can only other new some could time these two may then do "
    "first any my now such like our over man me even most made after also did "
    "many before must through back years where much your way well down should "
    "because each just those people how too little state good very make world "
    "still own see men work long get here between both life being under never "
    "day same another know while last might us great old year off come since "
    "against go came right used take three states himself few house use during "
    "without again place around however home small found thought went say part "
    "once general high upon school every don't does got united left number "
    "course war until always away something fact though water less public put "
    "think almost hand enough far took head yet government system better set "
    "told nothing night end why called didn't eyes find going look asked later "
    "knew point next city business give group toward young let room president "
    "side social given order early possible"
).split()


def synthetic_text(target_tokens: int, seed: int = 0) -> str:
    """Deterministic filler text of roughly target_tokens BPE tokens."""
    rng = random.Random(seed)
    return " ".join(rng.choice(_WORDS) for _ in range(max(1, target_tokens)))
