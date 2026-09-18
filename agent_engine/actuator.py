"""
actuator.py
===========
Instant mouse and keyboard actuator using Win32 SendInput / SetCursorPos.

Key design decisions
--------------------
- DPI awareness is set once at import time via SetProcessDpiAwarenessContext
  (Windows 10+) falling back to SetProcessDPIAware (Vista+).  This ensures
  coordinates are always in physical pixels.
- Mouse movement uses SetCursorPos for absolute positioning (< 1 ms on bare
  metal), not mouse_event ABSOLUTE which requires normalisation to 65535.
- Clicks use SendInput with MOUSEINPUT so they are coalesced into a single
  kernel call for minimum latency.
- Keyboard simulation also uses SendInput for correctness with all IMEs.
- All Win32 calls are checked for failure; errors are logged and raised.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import time
from typing import Literal, Optional, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Win32 setup
# ---------------------------------------------------------------------------

_user32 = ctypes.windll.user32          # type: ignore[attr-defined]
_kernel32 = ctypes.windll.kernel32      # type: ignore[attr-defined]

# --- DPI awareness -----------------------------------------------------------

_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)  # type: ignore[call-overload]

def _init_dpi() -> None:
    """
    Set Per-Monitor DPI Awareness v2 (Win10 1607+).
    Falls back to SetProcessDPIAware on older systems.
    """
    try:
        ok = _user32.SetProcessDpiAwarenessContext(
            _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        )
        if ok:
            logger.debug("DPI: SetProcessDpiAwarenessContext(PER_MONITOR_V2) succeeded")
            return
    except (AttributeError, OSError):
        pass
    # Fallback
    _user32.SetProcessDPIAware()
    logger.debug("DPI: SetProcessDPIAware() (fallback) applied")


_init_dpi()


# --- SendInput structures -----------------------------------------------------

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_UNICODE = 0x0004

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_ABSOLUTE = 0x8000


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.wintypes.LONG),
        ("dy", ctypes.wintypes.LONG),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.wintypes.DWORD),
        ("wParamL", ctypes.wintypes.WORD),
        ("wParamH", ctypes.wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("_input", _INPUT_UNION),
    ]


_INPUT_SIZE = ctypes.sizeof(_INPUT)


# ---------------------------------------------------------------------------
# Actuator
# ---------------------------------------------------------------------------


class Actuator:
    """
    Instant, DPI-aware mouse and keyboard actuator.

    All operations are synchronous and return immediately after the Win32 call.
    There are intentionally NO sleep/delay calls — latency is bounded only by
    the kernel round-trip for SendInput / SetCursorPos (~50–200 µs on modern
    hardware).
    """

    def __init__(self, inter_click_delay_ms: float = 0.0) -> None:
        """
        Parameters
        ----------
        inter_click_delay_ms:
            Optional pause (in ms) *between* DOWN and UP events for a single
            click. Useful if the target application needs a minimum dwell time.
            Defaults to 0 (truly instant).
        """
        self._delay_s = inter_click_delay_ms / 1000.0

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def move(self, x: int, y: int) -> None:
        """Move cursor to physical-pixel coordinate (x, y) instantly."""
        ok = _user32.SetCursorPos(x, y)
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(f"SetCursorPos({x}, {y}) failed, WinError={err}")

    def snap_and_click(
        self,
        x: int,
        y: int,
        button: Literal["left", "right", "middle"] = "left",
        double: bool = False,
    ) -> None:
        """
        Instantly warp the cursor to (x, y) and fire a click.

        Parameters
        ----------
        x, y    : Physical-pixel screen coordinates.
        button  : Which mouse button to press.
        double  : If True, perform a double-click (two DOWN/UP pairs).
        """
        self.move(x, y)
        self._send_click(button)
        if double:
            self._send_click(button)

    def _send_click(self, button: str) -> None:
        down_flag, up_flag = {
            "left":   (MOUSEEVENTF_LEFTDOWN,   MOUSEEVENTF_LEFTUP),
            "right":  (MOUSEEVENTF_RIGHTDOWN,  MOUSEEVENTF_RIGHTUP),
            "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
        }.get(button, (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP))

        inputs = (_INPUT * 2)(
            _INPUT(
                type=INPUT_MOUSE,
                _input=_INPUT_UNION(mi=_MOUSEINPUT(dwFlags=down_flag)),
            ),
            _INPUT(
                type=INPUT_MOUSE,
                _input=_INPUT_UNION(mi=_MOUSEINPUT(dwFlags=up_flag)),
            ),
        )

        if self._delay_s > 0:
            # Split into separate SendInput calls with a dwell
            sent = _user32.SendInput(1, ctypes.byref(inputs[0]), _INPUT_SIZE)
            self._check_sent(sent, 1)
            time.sleep(self._delay_s)
            sent = _user32.SendInput(1, ctypes.byref(inputs[1]), _INPUT_SIZE)
            self._check_sent(sent, 1)
        else:
            sent = _user32.SendInput(2, inputs, _INPUT_SIZE)
            self._check_sent(sent, 2)

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    def key_press(self, vk_code: int) -> None:
        """Press and release a virtual key by its VK_ code."""
        inputs = (_INPUT * 2)(
            _INPUT(
                type=INPUT_KEYBOARD,
                _input=_INPUT_UNION(ki=_KEYBDINPUT(wVk=vk_code, dwFlags=0)),
            ),
            _INPUT(
                type=INPUT_KEYBOARD,
                _input=_INPUT_UNION(ki=_KEYBDINPUT(wVk=vk_code, dwFlags=KEYEVENTF_KEYUP)),
            ),
        )
        sent = _user32.SendInput(2, inputs, _INPUT_SIZE)
        self._check_sent(sent, 2)

    def type_text(self, text: str) -> None:
        """
        Type a Unicode string by injecting KEYEVENTF_UNICODE events.
        Does not depend on keyboard layout.
        """
        events: list[_INPUT] = []
        for ch in text:
            scan = ord(ch)
            events.append(
                _INPUT(
                    type=INPUT_KEYBOARD,
                    _input=_INPUT_UNION(
                        ki=_KEYBDINPUT(wScan=scan, dwFlags=KEYEVENTF_UNICODE)
                    ),
                )
            )
            events.append(
                _INPUT(
                    type=INPUT_KEYBOARD,
                    _input=_INPUT_UNION(
                        ki=_KEYBDINPUT(
                            wScan=scan,
                            dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                        )
                    ),
                )
            )
        arr = (_INPUT * len(events))(*events)
        sent = _user32.SendInput(len(events), arr, _INPUT_SIZE)
        self._check_sent(sent, len(events))

    def hotkey(self, *vk_codes: int) -> None:
        """
        Press a chord of virtual keys simultaneously.

        Example: ``actuator.hotkey(VK_CONTROL, ord('C'))``  → Ctrl+C
        """
        n = len(vk_codes)
        events: list[_INPUT] = []
        # Key-down in order
        for vk in vk_codes:
            events.append(
                _INPUT(
                    type=INPUT_KEYBOARD,
                    _input=_INPUT_UNION(ki=_KEYBDINPUT(wVk=vk, dwFlags=0)),
                )
            )
        # Key-up in reverse order
        for vk in reversed(vk_codes):
            events.append(
                _INPUT(
                    type=INPUT_KEYBOARD,
                    _input=_INPUT_UNION(ki=_KEYBDINPUT(wVk=vk, dwFlags=KEYEVENTF_KEYUP)),
                )
            )
        arr = (_INPUT * len(events))(*events)
        sent = _user32.SendInput(len(events), arr, _INPUT_SIZE)
        self._check_sent(sent, len(events))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_sent(sent: int, expected: int) -> None:
        if sent != expected:
            err = ctypes.get_last_error()
            raise OSError(
                f"SendInput injected {sent}/{expected} events, WinError={err}"
            )
