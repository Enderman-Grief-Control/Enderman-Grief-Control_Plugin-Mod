"""The only Windows-specific code: find the game's own window and post messages to it.

Input goes to the client's own window as posted messages; the desktop cursor and keyboard are
never touched. Nothing here runs at import time, and other modules import this one only when they
are about to drive a client, so the offline tests and the Linux CI job never load user32.
"""

from __future__ import annotations

import ctypes
import sys

WM_CLOSE = 0x0010
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
GAME_WINDOW_CLASS = "GLFW30"  # GLFW also creates a hidden "GLFW3 Helper" message window

_user32 = None


def available() -> bool:
    return sys.platform == "win32"


def _api():
    global _user32
    if _user32 is None:
        if not available():
            raise OSError("posted window input needs Windows")
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        _user32 = user32
    return _user32


def glfw_windows(pid: int) -> list[tuple[int, str, bool]]:
    """(handle, class name, visible) for every GLFW window owned by ``pid``."""
    from ctypes import wintypes
    user32 = _api()
    found: list[tuple[int, str, bool]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, name, 64)
            if name.value.startswith("GLFW"):
                found.append((hwnd, name.value, bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return found


def game_window(pid: int) -> int | None:
    """The one visible game window of ``pid``, or None when there is not exactly one."""
    handles = [hwnd for hwnd, name, visible in glfw_windows(pid) if name == GAME_WINDOW_CLASS and visible]
    return handles[0] if len(handles) == 1 else None


def post(hwnd: int, message: int, wparam: int, lparam: int) -> None:
    if not _api().PostMessageW(hwnd, message, wparam, lparam):
        raise ctypes.WinError(ctypes.get_last_error())


def close(pid: int) -> bool:
    """Ask every GLFW window of ``pid`` to close (WM_CLOSE); True when at least one was found."""
    handles = [hwnd for hwnd, _name, _visible in glfw_windows(pid)]
    for hwnd in handles:
        post(hwnd, WM_CLOSE, 0, 0)
    return bool(handles)
