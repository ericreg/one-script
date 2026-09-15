import beta
import collections
from beta import Beta
from importpkg import exported
from importpkg.gamma import Gamma
from .gamma import Gamma as RelativeGamma


class Alpha:
    def make(self):
        return Beta()

    def counter(self):
        return collections.Counter("aba")
