"""Share a bounded, lazily decoded source across preview/subject/detail checks."""
from contextlib import ExitStack, closing, contextmanager
from threading import BoundedSemaphore, local

_state = local()
_scope_slots = BoundedSemaphore(2)


@contextmanager
def shared_decode(asset):
    previous = getattr(_state, 'scope', None)
    if previous is not None and previous['asset'] is asset:
        # The scan and screening layers may both establish the same scope.
        yield
        return
    # Nested different-photo scopes borrow the outer slot, avoiding deadlock.
    owns_slot = previous is None
    if owns_slot:
        _scope_slots.acquire()
    scope = {'asset': asset, 'image': None, 'signature': None,
             'borrowers': {}, 'retired': {}}
    _state.scope = scope
    try:
        yield
    finally:
        _state.scope = previous
        # Run every close and release the slot even if a backend close fails.
        with ExitStack() as cleanup:
            if owns_slot:
                cleanup.callback(_scope_slots.release)
            if scope['image'] is not None:
                cleanup.callback(scope['image'].close)
            for image in scope['retired'].values():
                cleanup.callback(image.close)


def _retire(scope, image):
    if image is not None:
        if scope['borrowers'].get(id(image)):
            scope['retired'][id(image)] = image
        else:
            image.close()


def _synchronize_source(scope):
    from .image_decode import asset_source_signature
    signature = asset_source_signature(scope['asset'])
    if signature != scope['signature']:
        previous = scope['image']
        scope['image'] = None
        scope['signature'] = signature
        _retire(scope, previous)


@contextmanager
def _borrow(scope, image):
    key = id(image)
    scope['borrowers'][key] = scope['borrowers'].get(key, 0) + 1
    try:
        yield image
    finally:
        scope['borrowers'][key] -= 1
        if not scope['borrowers'][key]:
            del scope['borrowers'][key]
            retired = scope['retired'].pop(key, None)
            if retired is not None:
                retired.close()


@contextmanager
def preview_image(asset, loader):
    """Borrow the scan source for thumbnailing; the caller must not mutate it."""
    scope = getattr(_state, 'scope', None)
    if scope is None or scope['asset'] is not asset:
        # Pillow Image.__exit__ does not release the decoded pixel core.
        with closing(loader()) as image:
            yield image
        return
    _synchronize_source(scope)
    if scope['image'] is None:
        scope['image'] = loader()
    with _borrow(scope, scope['image']) as image:
        yield image


@contextmanager
def full_image(asset, *, evidence_regions=(), min_region_pixels=96,
               insufficient_evidence=False):
    from .face_focus import load_full_image
    from .image_decode import load_analysis_image, refine_analysis_image
    options = dict(evidence_regions=evidence_regions, min_region_pixels=min_region_pixels,
                   insufficient_evidence=insufficient_evidence)
    scope = getattr(_state, 'scope', None)
    def load():
        # Preserve the public legacy loader hook for downstream integrations.
        if evidence_regions or insufficient_evidence or min_region_pixels != 96:
            return load_analysis_image(asset, **options)
        return load_full_image(asset)
    if scope is None or scope['asset'] is not asset:
        with closing(load()) as image:
            yield image
        return
    _synchronize_source(scope)
    if scope['image'] is None:
        scope['image'] = load()
    elif scope['image'].info.get('decode_evidence'):
        previous = scope['image']
        refined = refine_analysis_image(asset, previous, **options)
        if refined is not previous:
            scope['image'] = refined
            _retire(scope, previous)
    with _borrow(scope, scope['image']) as image:
        yield image


def decoded_image(asset):
    """Return an already decoded image without causing new RAW work."""
    from .image_decode import asset_source_signature
    scope = getattr(_state, 'scope', None)
    if (scope is not None and scope['asset'] is asset
            and scope['signature'] == asset_source_signature(asset)):
        return scope['image']
    return None
