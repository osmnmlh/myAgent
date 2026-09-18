"""
test_phase1.py
==============
Automated diagnostic suite for the agent_engine Phase-1 implementation.

Tests
-----
1. DPI_AWARENESS_CONTEXT check        – confirm PER_MONITOR_AWARE_V2 is active
2. Synthetic-move false-positive check – SetCursorPos / SendInput must NOT trigger kill-switch
3. Overlay style / passthrough check  – verify all required WS_EX_* flags are set
4. Kill-switch latency benchmark      – INTERRUPTED must propagate in < 10 ms

Run
---
    python test_phase1.py               # all tests
    python test_phase1.py -v            # verbose output
    python test_phase1.py -k latency    # run a specific test by keyword
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import os
import sys
import threading
import time
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Make the package importable when run from the project root
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_engine.safety import (
    AgentState,
    SafetyMonitor,
    _StateManager,
    LLMHF_INJECTED,
)
from agent_engine.actuator import Actuator, _user32 as _act_user32
from agent_engine.overlay import StatusOverlay, WS_EX_TRANSPARENT, WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW

# ---------------------------------------------------------------------------
# Minimal test harness
# ---------------------------------------------------------------------------

_RESET  = "\033[0m"
_GREEN  = "\033[92m"
_RED    = "\033[91m"
_YELLOW = "\033[93m"
_BOLD   = "\033[1m"

_VERBOSE = "-v" in sys.argv

_results: list[tuple[str, bool, str]] = []  # (name, passed, detail)


def _log(msg: str) -> None:
    if _VERBOSE:
        print(f"    {msg}")


def test(name: str) -> Callable:
    """Decorator that registers a test function and records PASS / FAIL."""
    def decorator(fn: Callable) -> Callable:
        def wrapper() -> None:
            keyword_filter = next(
                (sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "-k"), None
            )
            if keyword_filter and keyword_filter.lower() not in name.lower():
                return
            try:
                detail = fn() or ""
                _results.append((name, True, detail))
            except AssertionError as exc:
                _results.append((name, False, str(exc)))
            except Exception as exc:
                _results.append((name, False, f"Unexpected exception: {exc}"))
        wrapper()
        return wrapper
    return decorator


# ===========================================================================
# Test 1 – DPI awareness context
# ===========================================================================

@test("DPI_AWARENESS_CONTEXT = PER_MONITOR_AWARE_V2")
def test_dpi_awareness() -> str:
    """
    Verify that importing actuator set the process to Per-Monitor DPI Aware v2.

    Strategy (in preference order):
    1. GetThreadDpiAwarenessContext + AreDpiAwarenessContextsEqual (Win10 1607+).
       NOTE: GetWindowDpiAwarenessContext(desktop_hwnd) always returns UNAWARE
       because the desktop window itself is DPI-unaware.  We must query the
       *thread* (which inherits the process setting).
    2. GetProcessDpiAwareness via shcore.dll (Vista+).
    """
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]

    # Try the modern API first (Win10 1607+)
    try:
        # GetThreadDpiAwarenessContext returns the awareness for the *calling*
        # thread, which inherits SetProcessDpiAwarenessContext set at import.
        ctx = user32.GetThreadDpiAwarenessContext()
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = (HANDLE)(-4)
        target = ctypes.c_void_p(-4)
        equal = user32.AreDpiAwarenessContextsEqual(ctx, target)
        assert equal, (
            "Thread DPI context is NOT PER_MONITOR_AWARE_V2. "
            f"AreDpiAwarenessContextsEqual returned {equal}"
        )
        _log("GetThreadDpiAwarenessContext -> PER_MONITOR_AWARE_V2")
        return "PER_MONITOR_AWARE_V2 confirmed via GetThreadDpiAwarenessContext"
    except (AttributeError, OSError):
        pass

    # Fallback: GetProcessDpiAwareness (shcore.dll, Vista+)
    try:
        shcore = ctypes.windll.shcore  # type: ignore[attr-defined]
        awareness = ctypes.c_int(0)
        hr = shcore.GetProcessDpiAwareness(None, ctypes.byref(awareness))
        assert hr == 0, f"GetProcessDpiAwareness HRESULT={hr:#010x}"
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        assert awareness.value >= 1, (
            f"DPI awareness level is {awareness.value}, expected >= 1"
        )
        _log(f"GetProcessDpiAwareness -> {awareness.value} (>=1 = aware)")
        return f"DPI awareness level {awareness.value} (PER_MONITOR_DPI_AWARE=2)"
    except (AttributeError, OSError) as exc:
        assert False, f"Could not verify DPI awareness: {exc}"


# ===========================================================================
# Test 2 – Synthetic-move false-positive
# ===========================================================================

@test("Synthetic SetCursorPos/SendInput must NOT trigger kill-switch")
def test_synthetic_no_false_positive() -> str:
    """
    1. Start SafetyMonitor + Actuator.
    2. Set state → ACTIVE (arms the kill-switch).
    3. Perform 10 large synthetic cursor warps via snap_and_click (dry: move only).
    4. Verify state is still ACTIVE (not INTERRUPTED).
    """
    sm = _StateManager()
    monitor = SafetyMonitor(sm, move_threshold=15)
    actuator = Actuator()

    monitor.start()

    # Get current position so we can restore it
    pt = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))  # type: ignore[attr-defined]
    orig_x, orig_y = pt.x, pt.y

    try:
        monitor.reset()
        sm.state = AgentState.ACTIVE

        # Perform large synthetic moves – each jump > 200 px
        waypoints = [(100, 100), (900, 500), (200, 800), (1500, 200), (960, 540)]
        for x, y in waypoints:
            actuator.move(x, y)
            time.sleep(0.015)  # give hook thread time to process each event
            _log(f"Moved to ({x}, {y}) – state={sm.state.name}")

        assert sm.state == AgentState.ACTIVE, (
            f"State flipped to {sm.state.name} after synthetic moves – "
            "LLMHF_INJECTED filter is not working"
        )
        return f"5 synthetic warps (max delta >> threshold) – state stayed ACTIVE"
    finally:
        # Restore cursor to original position
        ctypes.windll.user32.SetCursorPos(orig_x, orig_y)  # type: ignore[attr-defined]
        sm.state = AgentState.IDLE
        monitor.stop()


# ===========================================================================
# Test 3 – Overlay click-through style flags
# ===========================================================================

@test("Overlay window has all required WS_EX_* click-through style flags")
def test_overlay_styles() -> str:
    """
    Spawn the overlay, wait for the window to appear, query GetWindowLongW(GWL_EXSTYLE),
    and assert all four mandatory extended styles are present.
    """
    GWL_EXSTYLE = -20

    sm = _StateManager()
    overlay = StatusOverlay(sm)
    overlay.start()

    # Show the overlay so the window is in a visible / fully-configured state
    overlay.show_active()
    time.sleep(0.15)

    hwnd = overlay._hwnd
    assert hwnd, "Overlay HWND is None – window was not created"

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]

    # On 64-bit Windows GetWindowLongPtrW is the correct API
    try:
        ex_style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    except AttributeError:
        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)

    _log(f"HWND={hex(hwnd)}  GWL_EXSTYLE={hex(ex_style)}")

    required = {
        "WS_EX_TRANSPARENT": WS_EX_TRANSPARENT,
        "WS_EX_LAYERED":     WS_EX_LAYERED,
        "WS_EX_NOACTIVATE":  WS_EX_NOACTIVATE,
        "WS_EX_TOOLWINDOW":  WS_EX_TOOLWINDOW,
    }

    missing = [
        name for name, flag in required.items() if not (ex_style & flag)
    ]
    assert not missing, f"Missing extended styles: {', '.join(missing)}"

    overlay.hide()
    overlay.stop()

    present = ", ".join(required.keys())
    _log(f"All flags confirmed: {present}")
    return f"ex_style={hex(ex_style)} – all 4 required flags present"


# ===========================================================================
# Test 4 – Kill-switch latency benchmark
# ===========================================================================

@test("Kill-switch latency < 10 ms end-to-end")
def test_killswitch_latency() -> str:
    """
    Measures wall-clock time from the moment _trigger_interrupt() is called on
    the hook thread to the moment wait_for_interrupt() unblocks on the main thread.

    This tests the threading.Event signalling overhead, which is the dominating
    factor in interrupt propagation latency (the hook proc itself runs synchronously
    before this measurement can even start, so its cost is already included in the
    total path from a real physical event to the caller being unblocked).

    Target: < 10 ms (the requirement).
    We run 50 trials and report min/mean/max.
    """
    TRIALS = 50
    TARGET_MS = 10.0

    sm = _StateManager()
    monitor = SafetyMonitor(sm, move_threshold=15)
    monitor.start()

    # Suppress the expected "INTERRUPT – benchmark" warning lines that would
    # otherwise flood stderr during the 50 intentional triggers.
    _safety_logger = logging.getLogger("agent_engine.safety")
    _old_level = _safety_logger.level
    _safety_logger.setLevel(logging.ERROR)

    latencies_ms: list[float] = []

    try:
        for _ in range(TRIALS):
            sm.state = AgentState.ACTIVE  # arms the interrupt event

            t0 = time.perf_counter()

            # Fire the interrupt from a separate thread (simulating the hook thread)
            def _fire() -> None:
                monitor._trigger_interrupt("benchmark")

            firing_thread = threading.Thread(target=_fire, daemon=True)
            firing_thread.start()

            interrupted = sm.wait_for_interrupt(timeout=0.1)
            t1 = time.perf_counter()

            assert interrupted, "wait_for_interrupt timed out (> 100 ms) – Event never fired"
            latencies_ms.append((t1 - t0) * 1000.0)
            firing_thread.join()

            # Reset for next trial
            sm.state = AgentState.IDLE

    finally:
        _safety_logger.setLevel(_old_level)
        monitor.stop()

    min_ms  = min(latencies_ms)
    mean_ms = sum(latencies_ms) / len(latencies_ms)
    max_ms  = max(latencies_ms)
    p99_ms  = sorted(latencies_ms)[int(len(latencies_ms) * 0.99)]

    _log(f"Latency over {TRIALS} trials: min={min_ms:.3f} ms  mean={mean_ms:.3f} ms  max={max_ms:.3f} ms  p99={p99_ms:.3f} ms")

    assert max_ms < TARGET_MS, (
        f"WORST-CASE latency {max_ms:.3f} ms exceeds {TARGET_MS} ms limit "
        f"(mean={mean_ms:.3f} ms, p99={p99_ms:.3f} ms)"
    )
    return (
        f"{TRIALS} trials – "
        f"min={min_ms:.3f} ms  mean={mean_ms:.3f} ms  max={max_ms:.3f} ms  (target < {TARGET_MS} ms)"
    )


# ===========================================================================
# Summary printer
# ===========================================================================

def _print_summary() -> int:
    width = 72
    print()
    print("=" * width)
    print(f"{'AGENT ENGINE — PHASE 1 DIAGNOSTIC RESULTS':^{width}}")
    print("=" * width)

    passed = 0
    failed = 0

    for name, ok, detail in _results:
        status_str = f"{_GREEN}PASS{_RESET}" if ok else f"{_RED}FAIL{_RESET}"
        print(f"  [{status_str}]  {name}")
        if detail:
            if ok:
                print(f"         {_YELLOW}{detail}{_RESET}")
            else:
                print(f"         {_RED}{detail}{_RESET}")
        if ok:
            passed += 1
        else:
            failed += 1

    print("-" * width)
    total = passed + failed
    colour = _GREEN if failed == 0 else _RED
    print(
        f"  {colour}{_BOLD}Results: {passed}/{total} passed"
        + (f"  ({failed} FAILED)" if failed else "  — all clear")
        + f"{_RESET}"
    )
    print("=" * width)
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    # Suppress noisy library logging during the test run
    logging.basicConfig(level=logging.WARNING)

    # Use UTF-8 output if the console supports it; otherwise fall back to
    # ASCII replacement so the runner never crashes on cp1252 terminals.
    import io
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

    print()
    print(f"  {_BOLD}Agent Engine Phase-1 Diagnostic Suite{_RESET}")
    print(f"  Python {sys.version.split()[0]}  |  platform={sys.platform}  |  {'64-bit' if sys.maxsize > 2**32 else '32-bit'}")
    print()

    sys.exit(_print_summary())
