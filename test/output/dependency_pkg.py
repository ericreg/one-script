"""Bundled from package 'dependency_pkg'."""

# imported from: test/fixtures/dependency_pkg/z_base.py
DEFAULT_SIZE = 11


# imported from: test/fixtures/dependency_pkg/b_defaults.py
def make_size(size=DEFAULT_SIZE):
    return size


# imported from: test/fixtures/dependency_pkg/z_base.py
def mark(fn):
    fn.marked = True
    return fn


# imported from: test/fixtures/dependency_pkg/c_decorated.py
@mark
def decorated():
    return "decorated"


# imported from: test/fixtures/dependency_pkg/z_base.py
class Base:
    pass


# imported from: test/fixtures/dependency_pkg/a_child.py
class Child(Base):
    pass


# imported from: test/fixtures/dependency_pkg/z_base.py
class LaterThing:
    pass


# imported from: test/fixtures/dependency_pkg/d_uses_later.py
class UsesLater:
    def build(self):
        return LaterThing()
