import collections
from importpkg.beta import Beta
from .gamma import Gamma


class Alpha:
    def make(self):
        return Beta()

    def counter(self):
        return collections.Counter("aba")
