import json
from math import sqrt

from .constants import BASE_VALUE, STATUS
from basicpkg.decorators import traced


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
