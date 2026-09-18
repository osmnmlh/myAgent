"""
main.py
=======
Integration demo for the agent_engine Phase-1 safety and execution engine.

Lifecycle demonstrated
----------------------
1.  System boots in IDLE → no overlay.
2.  A simulated task begins → state → ACTIVE → neon-blue border appears.
3.  The actuator fires 5 instant clicks at designated test coordinates.
    Between each click the main loop checks for an interrupt.
4a. If all 5 clicks complete without interruption → IDLE → border disappears →
    SUCCESS message.
4b. If the user physically moves the mouse > 15 px OR presses ESC during the
    task → INTERRUPTED → border disappears → ABORTED message.

Run
---
    python main.py                  # normal demo
    python main.py --threshold 30   # custom move threshold in pixels
    python main.py --delay 50       # add 50 ms inter-click dwell (ms)
    python main.py --dry-run        # skip actual clicks (safe in any env)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Optional

# Ensure the package root is on sys.path when run directly
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_engine.safety import AgentState, SafetyMonitor, _StateManager
from agent_engine.actuator import Actuator
from agent_engine.overlay import StatusOverlay

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class _MsFormatter(logging.Formatter):
    """Logging formatter that appends milliseconds: HH:MM:SS.mmm"""

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:  # type: ignore[override]
        import time as _time
        ct = _time.localtime(record.created)
        base = _time.strftime("%H:%M:%S", ct)
        return f"{base}.{int(record.msecs):03d}"


_handler = logging.StreamHandler()
_handler.setFormatter(
    _MsFormatter("%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s")
)
logging.root.setLevel(logging.INFO)
logging.root.handlers = [_handler]
logger = logging.getLogger("main")

# ---------------------------------------------------------------------------
# Test coordinates (5 points spread across a 1920×1080 screen)
# ---------------------------------------------------------------------------

DEFAULT_TEST_CLICKS: list[tuple[int, int]] = [
    (200,  150),   # top-left region
    (960,  540),   # centre
    (1700, 150),   # top-right region
    (200,  900),   # bottom-left region
    (1700, 900),   # bottom-right region
]


# ---------------------------------------------------------------------------
# Engine façade
# ---------------------------------------------------------------------------

class AgentEngine:
    """Thin façade wiring together safety, actuator, and overlay."""

    def __init__(
        self,
        move_threshold: int = 15,
        inter_click_delay_ms: float = 0.0,
    ) -> None:
        self._sm = _StateManager()
        self._monitor = SafetyMonitor(self._sm, move_threshold=move_threshold)
        self._actuator = Actuator(inter_click_delay_ms=inter_click_delay_ms)
        self._overlay = StatusOverlay(self._sm)

    def start(self) -> None:
        logger.info("Engine: starting subsystems…")
        self._overlay.start()
        self._monitor.start()
        logger.info("Engine: ready  (state=%s)", self._sm.state.name)

    def stop(self) -> None:
        self._monitor.stop()
        self._overlay.stop()
        logger.info("Engine: shut down cleanly")

    @property
    def state(self) -> AgentState:
        return self._sm.state

    def activate(self) -> None:
        """Transition → ACTIVE and prime the safety monitor's reference point."""
        self._monitor.reset()
        self._sm.state = AgentState.ACTIVE

    def idle(self) -> None:
        self._sm.state = AgentState.IDLE

    def is_interrupted(self) -> bool:
        return self._sm.is_interrupted()

    def run_click_sequence(
        self,
        coords: list[tuple[int, int]],
        dry_run: bool = False,
    ) -> bool:
        """
        Execute a sequence of instant clicks, aborting on interrupt.

        Returns True if all clicks completed, False if interrupted.
        """
        for i, (x, y) in enumerate(coords, 1):
            if self.is_interrupted():
                logger.warning("  ✗ Aborted before click #%d", i)
                return False

            if dry_run:
                logger.info("  [DRY-RUN] Would click (%d, %d)", x, y)
            else:
                logger.info("  → Click #%d  at (%d, %d)", i, x, y)
                self._actuator.snap_and_click(x, y)

            # Brief settle — gives the user a chance to trigger the interrupt
            # between clicks.  In a real agent this would be replaced by
            # waiting for an action result / UI event.
            interrupted = self._sm.wait_for_interrupt(timeout=0.4)
            if interrupted:
                logger.warning("  ✗ Interrupted after click #%d", i)
                return False

        return True


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Agent Engine Phase-1 integration demo",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--threshold",
        type=int,
        default=15,
        metavar="PX",
        help="Mouse movement delta (px) that triggers an interrupt",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=0.0,
        metavar="MS",
        help="Inter-click dwell time in milliseconds",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip actual mouse movement and clicks (safe for all environments)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    engine = AgentEngine(
        move_threshold=args.threshold,
        inter_click_delay_ms=args.delay,
    )

    print()
    print("+" + "=" * 58 + "+")
    print("|      Agent Engine -- Phase 1 Integration Demo           |")
    print("+" + "=" * 58 + "+")
    print(f"|  Move threshold : {args.threshold} px".ljust(59) + "|")
    print(f"|  Click delay    : {args.delay} ms".ljust(59) + "|")
    print(f"|  Dry-run        : {args.dry_run}".ljust(59) + "|")
    print("+" + "=" * 58 + "+")
    print()
    print("  Kill-switch: Move the mouse rapidly  OR  press ESC")
    print()

    engine.start()

    # ------------------------------------------------------------------
    # Step 1: idle briefly so the user can see the 'no border' state
    # ------------------------------------------------------------------
    logger.info("State: IDLE — overlay hidden (3 s countdown…)")
    time.sleep(3.0)

    # ------------------------------------------------------------------
    # Step 2: activate
    # ------------------------------------------------------------------
    logger.info("State: ACTIVE — neon-blue border should appear")
    engine.activate()
    time.sleep(0.2)  # Let the overlay render

    # ------------------------------------------------------------------
    # Step 3: run the click sequence
    # ------------------------------------------------------------------
    logger.info("Running %d-click sequence…", len(DEFAULT_TEST_CLICKS))
    completed = engine.run_click_sequence(DEFAULT_TEST_CLICKS, dry_run=args.dry_run)

    # ------------------------------------------------------------------
    # Step 4: report result
    # ------------------------------------------------------------------
    print()
    if completed:
        print("  +==============================+")
        print("  |  [OK]  TASK COMPLETED        |")
        print("  +==============================+")
        exit_code = 0
    else:
        print("  +==============================+")
        print("  |  [!!] TASK ABORTED           |")
        print("  +==============================+")
        exit_code = 1
    print()

    engine.idle()
    logger.info("State: IDLE — overlay hidden")
    time.sleep(0.5)  # Let the overlay hide before we destroy the window

    engine.stop()
    return exit_code


if __name__ == "__main__":
    # Ensure box-drawing and Unicode log messages render correctly even when
    # the terminal is using a legacy code page (e.g. cp1252 on Windows).
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass
    sys.exit(main())
