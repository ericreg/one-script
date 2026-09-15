from functools import wraps


def traced(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    wrapper.traced = True
    return wrapper
