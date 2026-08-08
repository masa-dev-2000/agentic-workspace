from __future__ import annotations

import os
import time
from pathlib import Path
from typing import BinaryIO


class LocalProcessLock:
    """Hold an OS-backed exclusive lock for one local process lifetime."""

    def __init__(
        self,
        path: Path | str,
        *,
        timeout_seconds: float = 0.0,
        poll_seconds: float = 0.02,
    ) -> None:
        self.path = Path(path)
        self.timeout_seconds = max(0.0, timeout_seconds)
        self.poll_seconds = max(0.001, poll_seconds)
        self._handle: BinaryIO | None = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        if self.acquired:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            handle = self.path.open("a+b")
            try:
                _ensure_lock_byte(handle)
                _lock_nonblocking(handle)
            except OSError:
                handle.close()
                if time.monotonic() >= deadline:
                    return False
                time.sleep(self.poll_seconds)
                continue
            self._handle = handle
            return True

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            _unlock(handle)
        finally:
            handle.close()

    def __enter__(self) -> "LocalProcessLock":
        if not self.acquire():
            raise RuntimeError("local-process-lock-timeout")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def _ensure_lock_byte(handle: BinaryIO) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())
    handle.seek(0)


def _lock_nonblocking(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
