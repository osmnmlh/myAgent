"""
overlay.py
==========
Click-through, non-focusable status border overlay using pure Win32 API.

Architecture
------------
The overlay is a layered, transparent top-most window covering the full primary
monitor.  The visible portion is a configurable-width coloured border drawn
around the edges.  The interior is punched out via a GDI region so that all
clicks/drags pass through.  Window is driven by its own daemon thread that owns
the message loop.

Win32 style flags used
----------------------
  WS_EX_TRANSPARENT  – mouse events fall through to underlying windows
  WS_EX_LAYERED      – enables alpha/per-pixel blending
  WS_EX_NOACTIVATE   – prevents the window from activating (stealing focus)
  WS_EX_TOOLWINDOW   – excludes from Alt-Tab switcher
  WS_EX_TOPMOST      – always on top (implied by HWND_TOPMOST, but set here too)

The window itself uses WS_POPUP (no title bar / borders from the OS).

Transparency is achieved with SetLayeredWindowAttributes using a chroma key or
LWA_ALPHA+LWA_COLORKEY combo so the interior is fully transparent while the
border retains its vivid colour.

Colors
------
ACTIVE  → neon blue   #00BFFF  (DeepSkyBlue-like but vivid)
IDLE    → invisible   (window hidden)
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import threading
import atexit
from typing import Optional

from .safety import AgentState, _StateManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------

WS_POPUP          = 0x80000000
WS_VISIBLE        = 0x10000000
WS_EX_TOPMOST     = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE  = 0x08000000
WS_EX_TOOLWINDOW  = 0x00000080
WS_EX_LAYERED     = 0x00080000

LWA_COLORKEY   = 0x00000001
LWA_ALPHA      = 0x00000002

HWND_TOPMOST   = ctypes.wintypes.HWND(-1)

SWP_NOMOVE         = 0x0002
SWP_NOSIZE         = 0x0001
SWP_NOACTIVATE     = 0x0010
SWP_SHOWWINDOW     = 0x0040
SWP_HIDEWINDOW     = 0x0080

WM_DESTROY   = 0x0002
WM_PAINT     = 0x000F
WM_QUIT      = 0x0012
WM_APP_UPDATE = 0x8001   # custom message: repaint / show-hide

PM_REMOVE = 0x0001

COLOR_WINDOW = 5

# Chroma key colour – must match the brush used for the interior fill
# Using pure magenta so it is unlikely to clash with real UI content
_CHROMA_R, _CHROMA_G, _CHROMA_B = 255, 0, 254  # near-pure magenta
_CHROMA_COLORREF = _CHROMA_R | (_CHROMA_G << 8) | (_CHROMA_B << 16)

# Active border colour – neon blue (#00BFFF)
_BORDER_R, _BORDER_G, _BORDER_B = 0, 191, 255
_BORDER_COLORREF = _BORDER_R | (_BORDER_G << 8) | (_BORDER_B << 16)

# ---------------------------------------------------------------------------
# ctypes helpers
# ---------------------------------------------------------------------------

_user32   = ctypes.windll.user32    # type: ignore[attr-defined]
_gdi32    = ctypes.windll.gdi32     # type: ignore[attr-defined]
_kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# 64-bit-safe WNDPROC / DefWindowProcW
# ---------------------------------------------------------------------------
# On 64-bit Windows, WPARAM = UINT_PTR (8 bytes), LPARAM = LONG_PTR (8 bytes),
# LRESULT = LONG_PTR (8 bytes).  ctypes.wintypes.WPARAM / LPARAM are only
# 4 bytes on ALL platforms, causing OverflowErrors when Windows passes large
# pointer-sized values (e.g. WM_NCCREATE lParam on x64).
_IS_64: bool = ctypes.sizeof(ctypes.c_void_p) == 8
_WPARAM_T  = ctypes.c_uint64 if _IS_64 else ctypes.c_uint32
_LPARAM_T  = ctypes.c_int64  if _IS_64 else ctypes.c_int32
_LRESULT_T = ctypes.c_int64  if _IS_64 else ctypes.c_long

WNDPROC = ctypes.WINFUNCTYPE(
    _LRESULT_T,
    ctypes.wintypes.HWND,
    ctypes.wintypes.UINT,
    _WPARAM_T,
    _LPARAM_T,
)

# Explicitly declare DefWindowProcW so ctypes marshals 64-bit args correctly.
_user32.DefWindowProcW.restype  = _LRESULT_T
_user32.DefWindowProcW.argtypes = [
    ctypes.wintypes.HWND,
    ctypes.wintypes.UINT,
    _WPARAM_T,
    _LPARAM_T,
]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize",        ctypes.wintypes.UINT),
        ("style",         ctypes.wintypes.UINT),
        ("lpfnWndProc",   WNDPROC),
        ("cbClsExtra",    ctypes.c_int),
        ("cbWndExtra",    ctypes.c_int),
        ("hInstance",     ctypes.wintypes.HINSTANCE),
        ("hIcon",         ctypes.wintypes.HICON),
        ("hCursor",       ctypes.wintypes.HANDLE),
        ("hbrBackground", ctypes.wintypes.HBRUSH),
        ("lpszMenuName",  ctypes.wintypes.LPCWSTR),
        ("lpszClassName", ctypes.wintypes.LPCWSTR),
        ("hIconSm",       ctypes.wintypes.HICON),
    ]


# ---------------------------------------------------------------------------
# StatusOverlay
# ---------------------------------------------------------------------------


class StatusOverlay:
    """
    Non-interactive coloured border drawn around the primary monitor.

    Usage
    -----
    overlay = StatusOverlay(state_manager)
    overlay.start()          # creates window on its own thread
    # ...
    overlay.show_active()    # turn border on  (neon blue)
    overlay.hide()           # turn border off
    overlay.stop()           # destroy window and join thread
    """

    def __init__(
        self,
        state_manager: _StateManager,
        border_px: int = 4,
    ) -> None:
        self._sm = state_manager
        self._border_px = border_px
        self._hwnd: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._thread_id: int = 0
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._visible = False

        # Register as state observer so we automatically sync visuals
        self._sm.register_callback(self._on_state_change)
        atexit.register(self.stop)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the overlay thread and wait for the window to be created."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopped.clear()
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._overlay_thread_main,
            name="StatusOverlay-Thread",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=3.0):
            raise RuntimeError("StatusOverlay: window creation timed out")
        logger.info("StatusOverlay: window created (HWND=%s)", hex(self._hwnd or 0))

    def stop(self) -> None:
        """Destroy the overlay window and join the thread."""
        self._sm.unregister_callback(self._on_state_change)
        if self._hwnd:
            _user32.PostMessageW(self._hwnd, WM_DESTROY, 0, 0)
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("StatusOverlay: stopped")

    def show_active(self) -> None:
        """Show the neon-blue border (call from any thread)."""
        self._post_update(visible=True)

    def hide(self) -> None:
        """Hide the border (call from any thread)."""
        self._post_update(visible=False)

    # ------------------------------------------------------------------
    # State observer
    # ------------------------------------------------------------------

    def _on_state_change(self, new_state: AgentState) -> None:
        if new_state == AgentState.ACTIVE:
            self.show_active()
        else:
            self.hide()

    # ------------------------------------------------------------------
    # Cross-thread communication
    # ------------------------------------------------------------------

    def _post_update(self, visible: bool) -> None:
        """Send WM_APP_UPDATE to the overlay's message queue (thread-safe)."""
        if self._hwnd:
            # wParam = 1 → show, 0 → hide
            _user32.PostMessageW(self._hwnd, WM_APP_UPDATE, int(visible), 0)

    # ------------------------------------------------------------------
    # Overlay thread
    # ------------------------------------------------------------------

    def _overlay_thread_main(self) -> None:
        hinstance = _kernel32.GetModuleHandleW(None)
        class_name = "AgentEngineOverlay"

        # --- Window procedure -------------------------------------------
        def _wnd_proc(
            hwnd: int,
            msg: int,
            wparam: int,
            lparam: int,
        ) -> int:
            if msg == WM_PAINT:
                self._on_paint(hwnd)
                return 0
            elif msg == WM_APP_UPDATE:
                self._visible = bool(wparam)
                if self._visible:
                    _user32.ShowWindow(hwnd, 8)  # SW_SHOWNA (show without activation)
                    _user32.InvalidateRect(hwnd, None, True)
                else:
                    _user32.ShowWindow(hwnd, 0)  # SW_HIDE
                return 0
            elif msg == WM_DESTROY:
                _user32.PostQuitMessage(0)
                return 0
            return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        _proc = WNDPROC(_wnd_proc)

        # --- Register window class --------------------------------------
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = _proc
        wc.hInstance = hinstance
        wc.lpszClassName = class_name
        # NULL background brush – we paint everything ourselves
        wc.hbrBackground = None

        atom = _user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            err = ctypes.get_last_error()
            # Class might already be registered; that's fine (error 1410)
            if err != 1410:
                logger.error("RegisterClassExW failed, error=%d", err)

        # --- Query primary monitor dimensions ---------------------------
        sw = _user32.GetSystemMetrics(0)   # SM_CXSCREEN
        sh = _user32.GetSystemMetrics(1)   # SM_CYSCREEN

        # --- Create window ----------------------------------------------
        ex_style = (
            WS_EX_TOPMOST
            | WS_EX_TRANSPARENT
            | WS_EX_LAYERED
            | WS_EX_NOACTIVATE
            | WS_EX_TOOLWINDOW
        )
        style = WS_POPUP  # no chrome; start hidden (WS_VISIBLE not set)

        hwnd = _user32.CreateWindowExW(
            ex_style,
            class_name,
            "AgentStatusOverlay",
            style,
            0, 0, sw, sh,         # position and size
            None, None,
            hinstance,
            None,
        )
        if not hwnd:
            err = ctypes.get_last_error()
            logger.error("CreateWindowExW failed, error=%d", err)
            self._ready.set()
            return

        self._hwnd = hwnd

        # --- Layered-window transparency --------------------------------
        # LWA_COLORKEY: pixels matching _CHROMA_COLORREF become fully
        # transparent.  LWA_ALPHA gives the whole window 255 opacity so the
        # *border* colour is vivid; only the chroma-keyed interior vanishes.
        ok = _user32.SetLayeredWindowAttributes(
            hwnd,
            _CHROMA_COLORREF,
            255,
            LWA_COLORKEY | LWA_ALPHA,
        )
        if not ok:
            err = ctypes.get_last_error()
            logger.warning("SetLayeredWindowAttributes failed, error=%d", err)

        # --- Punch out the interior with a combined region --------------
        # Outer rect = full screen; inner rect = screen minus border width.
        # The *difference* is just the border strip.
        outer = _gdi32.CreateRectRgn(0, 0, sw, sh)
        inner = _gdi32.CreateRectRgn(
            self._border_px,
            self._border_px,
            sw - self._border_px,
            sh - self._border_px,
        )
        border_rgn = _gdi32.CreateRectRgn(0, 0, 0, 0)
        RGN_DIFF = 4
        _gdi32.CombineRgn(border_rgn, outer, inner, RGN_DIFF)
        _user32.SetWindowRgn(hwnd, border_rgn, True)
        # Note: SetWindowRgn transfers ownership of the region to the OS;
        # do NOT call DeleteObject on border_rgn after this.
        _gdi32.DeleteObject(outer)
        _gdi32.DeleteObject(inner)

        # --- Place always-on-top without activating ---------------------
        _user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

        self._ready.set()

        # --- Message loop -----------------------------------------------
        msg = ctypes.wintypes.MSG()
        while True:
            ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

        self._hwnd = None
        _user32.UnregisterClassW(class_name, hinstance)
        self._stopped.set()

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def _on_paint(self, hwnd: int) -> None:
        """Paint the border strip in the active neon-blue colour."""
        ps = ctypes.create_string_buffer(72)  # PAINTSTRUCT
        hdc = _user32.BeginPaint(hwnd, ps)
        if not hdc:
            return

        # Fill with border colour
        brush = _gdi32.CreateSolidBrush(_BORDER_COLORREF)

        sw = _user32.GetSystemMetrics(0)
        sh = _user32.GetSystemMetrics(1)
        bp = self._border_px

        # Draw four border rects directly
        rects = [
            (0,       0,       sw,      bp),        # top
            (0,       sh - bp, sw,      sh),         # bottom
            (0,       bp,      bp,      sh - bp),    # left
            (sw - bp, bp,      sw,      sh - bp),    # right
        ]
        RECT = ctypes.wintypes.RECT
        for l, t, r, b in rects:
            rc = RECT(l, t, r, b)
            _user32.FillRect(hdc, ctypes.byref(rc), brush)

        _gdi32.DeleteObject(brush)
        _user32.EndPaint(hwnd, ps)
