"""Atomic JSON persistence and a reentrant, process-wide scanner lock."""
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from functools import wraps

import config

_mutex = threading.RLock()
_depth = 0


@contextmanager
def scanner_lock():
    global _depth
    with _mutex:
        if _depth:
            _depth += 1
            try:
                yield
            finally:
                _depth -= 1
            return
        path = os.path.abspath(config.SEEN_LEDGER_PATH) + ".lock"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("Another scan is using the seen ledger; this scan has stopped.") from exc
            _depth = 1
            try:
                yield
            finally:
                _depth = 0
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def locked(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        with scanner_lock():
            return function(*args, **kwargs)
    return wrapper


def atomic_json_write(path, value):
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".scanner-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
