"""Share one lazily decoded source between checks of a single photo."""
from contextlib import contextmanager
from threading import local

_state = local()


@contextmanager
def shared_decode(asset):
    previous = getattr(_state, 'scope', None)
    scope = {'asset': asset, 'image': None}
    _state.scope = scope
    try:
        yield
    finally:
        _state.scope = previous
        if scope['image'] is not None:
            scope['image'].close()


@contextmanager
def full_image(asset):
    from .face_focus import load_full_image
    scope = getattr(_state, 'scope', None)
    if scope is None or scope['asset'] is not asset:
        with load_full_image(asset) as image:
            yield image
        return
    if scope['image'] is None:
        scope['image'] = load_full_image(asset)
    yield scope['image']


def decoded_image(asset):
    """Return an already decoded image without causing new RAW work."""
    scope = getattr(_state, 'scope', None)
    return scope['image'] if scope is not None and scope['asset'] is asset else None
