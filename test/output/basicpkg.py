"""Bundled from package 'basicpkg'."""

import os
from math import sqrt
from functools import wraps
import json


# imported from: test/fixtures/basicpkg/async_tools.py
async def fetch_name(worker):
    return worker.name


# imported from: test/fixtures/basicpkg/constants.py
BASE_VALUE = 7


# imported from: test/fixtures/basicpkg/assignments.py
COUNTS = {"base": BASE_VALUE}

TOTAL: int = BASE_VALUE + 5

TOTAL += 1


# imported from: test/fixtures/basicpkg/constants.py
STATUS: str = "ready"

SCALE = sqrt(16)


# imported from: test/fixtures/basicpkg/decorators.py
def traced(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    wrapper.traced = True
    return wrapper


# imported from: test/fixtures/basicpkg/models.py
# Kept with the class when source text is extracted.
class Entity:
    kind = "entity"

    def __init__(self, name):
        self.name = name


class Worker(Entity):
    def score(self, bonus=BASE_VALUE):
        return sqrt(81) + bonus

    def describe(self):
        return json.dumps({"name": self.name, "status": STATUS})


@traced
def build_worker(name, default=BASE_VALUE):
    return Worker(name)
