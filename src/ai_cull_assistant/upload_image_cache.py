"""Bounded base64 lookahead cache; contains images only, never credentials."""
import base64
import hashlib
from collections import OrderedDict
from threading import Lock

_lock = Lock()
_items = OrderedDict()
MAX_BYTES = 32 * 1024 * 1024


def encode(data, *, warm=False):
    key = hashlib.sha256(data).digest()
    with _lock:
        value = _items.get(key) if warm else _items.pop(key, None)
    if value is None:
        value = base64.b64encode(data).decode('ascii')
    if warm and len(value) <= MAX_BYTES:
        with _lock:
            _items[key] = value
            _items.move_to_end(key)
            while sum(map(len, _items.values())) > MAX_BYTES:
                _items.popitem(last=False)
    return value


def clear():
    with _lock:
        _items.clear()
