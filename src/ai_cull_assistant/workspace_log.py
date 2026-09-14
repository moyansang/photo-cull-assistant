"""One persisted log, with diagnostic details hidden from the main window."""
from threading import RLock
from .workspace_layout import workspace_path

DETAIL_PREFIX = '[扫描诊断] '
_lock = RLock()


def append_log(workspace, text):
    with _lock:
        path = workspace_path(workspace, 'session.log')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as stream:
            stream.write(text+'\n')


def visible_log(text):
    return ''.join(line for line in text.splitlines(keepends=True)
                   if not line.startswith(DETAIL_PREFIX))
