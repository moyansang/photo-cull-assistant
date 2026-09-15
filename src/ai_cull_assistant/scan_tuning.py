"""Remember a measured starting point, never override live resource limits."""
import hashlib
import json
import os
from pathlib import Path
from .ai_project import atomic_json
from .project_storage import runtime_data_root


class ScanHistory:
    def __init__(self, snapshot, body_check, path=None):
        self.path = Path(path) if path else runtime_data_root()/'settings'/'scan-performance.json'
        identity = (1, os.environ.get('PROCESSOR_IDENTIFIER', ''), snapshot.cpu_count,
                    snapshot.total_memory_bytes, bool(body_check))
        self.key = hashlib.sha256(repr(identity).encode()).hexdigest()

    def preferred(self):
        try:
            value = json.loads(self.path.read_text('utf-8')).get(self.key)
            return value if type(value) is int and value in (1, 2, 4, 6, 8) else None
        except (OSError, ValueError, AttributeError):
            return None

    def save(self, workers):
        try:
            try:
                data = json.loads(self.path.read_text('utf-8'))
                if not isinstance(data, dict):data = {}
            except (OSError, ValueError):
                data = {}
            data.pop(self.key, None)
            data[self.key] = workers
            atomic_json(self.path, dict(list(data.items())[-16:]))
        except OSError:
            pass
