"""Bundled from package 'importpkg'."""

import collections


# imported from: test/fixtures/importpkg/beta.py
class Beta:
    pass


# imported from: test/fixtures/importpkg/alpha.py
class Alpha:
    def make(self):
        return Beta()

    def counter(self):
        return collections.Counter("aba")


# imported from: test/fixtures/importpkg/gamma.py
class Gamma:
    pass
