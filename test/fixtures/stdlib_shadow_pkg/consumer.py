import json
from queue import Queue as StdQueue

from .json import Json


class Consumer:
    def json_module_name(self):
        return json.__name__

    def local_json(self):
        return Json()
