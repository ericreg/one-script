"""Bundled from package 'stdlib_shadow_pkg'."""

import json
from queue import Queue as StdQueue


# imported from: test/fixtures/stdlib_shadow_pkg/json.py
class Json:
    pass


# imported from: test/fixtures/stdlib_shadow_pkg/consumer.py
class Consumer:
    def json_module_name(self):
        return json.__name__

    def local_json(self):
        return Json()


# imported from: test/fixtures/stdlib_shadow_pkg/queue.py
class Queue:
    pass
