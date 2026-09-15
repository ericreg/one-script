DEFAULT_SIZE = 11


def mark(fn):
    fn.marked = True
    return fn


class Base:
    pass


class LaterThing:
    pass
