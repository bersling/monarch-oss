"""One build/check writer per checkout; subprocesses inherit the same OS lock."""
import fcntl
import os
from pathlib import Path
import threading

_local = threading.local()


class Lease:
    def __init__(self, root):
        self.root = Path(root).resolve()
        path = self.root / 'generated/.model.lock'
        path.parent.mkdir(parents=True, exist_ok=True)
        self.owned = True
        existing = getattr(_local, 'leases', {}).get(str(self.root))
        inherited = existing.fd if existing else None
        if inherited is None and os.environ.get('MONARCH_MODEL_LOCK_ROOT') == str(self.root):
            try:
                candidate = int(os.environ['MONARCH_MODEL_LOCK_FD'])
                actual, expected = os.fstat(candidate), path.stat()
                if (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino):
                    inherited = candidate
            except (OSError, ValueError, KeyError):
                pass
        if inherited is not None:
            self.fd = os.dup(inherited)
            self.owned = False
        else:
            self.fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        self.previous = existing
        if not hasattr(_local, 'leases'):
            _local.leases = {}
        _local.leases[str(self.root)] = self

    def subprocess_options(self):
        return {'pass_fds': (self.fd,), 'env': {**os.environ,
                'MONARCH_MODEL_LOCK_FD': str(self.fd), 'MONARCH_MODEL_LOCK_ROOT': str(self.root)}}

    def close(self):
        if self.fd is None:
            return
        if self.owned:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)
        self.fd = None
        if self.previous:
            _local.leases[str(self.root)] = self.previous
        else:
            _local.leases.pop(str(self.root), None)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
