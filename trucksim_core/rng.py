"""Common random numbers. Every random draw comes from a stream named by (seed, purpose, *keys), never from one
shared generator. So a seat's decisions can't change what anyone is dealt: the same seed gives every run the same
offers, weather, detention and failure mileages, and runs differ only because of what the operators chose."""

import hashlib
import math
import random


def stream(seed: int | str, *key) -> random.Random:
    """An independent generator for one purpose and entity, e.g. stream(seed, "failure", "A2", "tires", 0)."""
    digest = hashlib.sha256(repr((seed, *key)).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def lognormal_mean(rng: random.Random, mean: float, sigma: float) -> float:
    """A lognormal draw whose mean (not median) is `mean`."""
    return rng.lognormvariate(math.log(mean) - sigma * sigma / 2, sigma)


def poisson(rng: random.Random, lam: float) -> int:
    """Knuth's method; fine for the small rates used here."""
    if lam <= 0:
        return 0
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1
