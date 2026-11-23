"""Small Windows process-identity helpers shared by the Stage 4B runner and CLI."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class _FileTime(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


def current_process_creation_filetime() -> int:
    """Return this process's immutable Windows creation FILETIME, or fail closed."""
    if os.name != "nt":
        raise OSError("Windows process identity is unavailable on this platform.")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    created = _FileTime()
    exited = _FileTime()
    kernel = _FileTime()
    user = _FileTime()
    if not kernel32.GetProcessTimes(
        kernel32.GetCurrentProcess(),
        ctypes.byref(created),
        ctypes.byref(exited),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    filetime = (int(created.high) << 32) | int(created.low)
    # Win32_Process CreationDate is represented to microsecond precision.
    return filetime - (filetime % 10)
