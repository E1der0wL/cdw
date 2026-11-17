from __future__ import annotations

import os
from pathlib import Path

from cdw.domain.contracts import CdwError, ErrorCode


class ProfileLock:
    """OS-owned advisory lock; stale files are harmless after process termination."""

    LOCK_BYTE_COUNT = 1
    LOCK_SEED = b"0"


    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            handle.write(ProfileLock.LOCK_SEED)
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, ProfileLock.LOCK_BYTE_COUNT)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise CdwError(ErrorCode.PROFILE_BUSY, str(self.path)) from exc
        self._file = handle

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
