"""One-photo lookahead; this worker never sends an API request."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
import json


class FocusPrefetch:
    def __init__(self, stop, cache_dir, preview_root, settings, assets=(), *, workspace=None):
        self.stop = stop
        self.closed = Event()
        self.cache_dir = cache_dir
        self.preview_root = preview_root
        self.settings = deepcopy(settings)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='focus-prepare')
        self.future = None
        self.sheet_assets = iter(assets)
        self.workspace = workspace

    def _log_error(self, kind, message):
        if self.workspace is None:
            return
        from .workspace_log import append_log, DETAIL_PREFIX
        try:
            # JSON keeps multiline errors on one hidden diagnostic log line.
            append_log(self.workspace, DETAIL_PREFIX + json.dumps(
                {'type': kind, 'message': str(message)}, ensure_ascii=False))
        except OSError:
            # Optional diagnostics cannot turn a cache failure into task failure.
            pass

    def wait(self):
        if self.future is not None:
            try:
                self.future.result()
            except Exception as exc:
                self._log_error('focus_prefetch_error', exc)
            finally:
                self.future = None

    def schedule(self, current, following):
        self.wait()
        if not self.stop.is_set() and not self.closed.is_set():
            from itertools import islice
            extras = [deepcopy(a) for a in islice(self.sheet_assets, 8)]
            self.future = self.executor.submit(self._prepare, deepcopy(current), deepcopy(following), extras)

    def _prepare(self, current, following, extras):
        from .preview import ensure_preview
        from .sheet_thumbnail import prepare_thumbnails
        from .ai_focus import _subject_faces
        from .focus_image_cache import cached_focus_images
        def stopped():
            return self.stop.is_set() or self.closed.is_set()
        # The current photo is already being reviewed; do not mutate its asset.
        for asset in (current, following):
            if asset is None or stopped():
                break
            try:
                ensure_preview(asset, self.preview_root)
                prepare_thumbnails(asset)
                if asset is not following or stopped():
                    continue
                evidence = asset.clarity_evidence or {}
                if (evidence.get('body') or {}).get('review_kind') == 'unsupported' and evidence.get('face_state') == 'clear':
                    continue
                subjects = _subject_faces(asset, self.settings)
                for subject, face in subjects:
                    if stopped():
                        return
                    paths, _ = cached_focus_images(asset, self.settings, self.cache_dir, face,
                                                   subject=subject, include_body=len(subjects) == 1)
                    if not stopped():
                        from .ai_api import _image_content
                        _image_content(paths, warm=True)
            except Exception as exc:
                # Foreground review retries normally and owns user-visible errors.
                self._log_error('focus_prefetch_error', f'{asset.primary_path.name}: {exc}')
        # A small batch also covers locally-clear photos which need no AI review.
        # Never decode RAW solely to speculate on their future sheet layout.
        from pathlib import Path
        for asset in extras:
            if stopped():
                break
            try:
                if asset.preview_path and Path(asset.preview_path).is_file():
                    prepare_thumbnails(asset)
            except Exception as exc:
                self._log_error('sheet_prefetch_error', f'{asset.primary_path.name}: {exc}')

    def close(self):
        self.closed.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
        from .upload_image_cache import clear
        clear()
