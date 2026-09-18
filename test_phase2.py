"""
test_phase2.py - Phase 2 Diagnostic Test Script
Waits 3 seconds for user to switch windows, then detects and prints active UI elements.
"""

import sys
import time
from ui_detector import get_active_ui_elements, format_for_llm


def main() -> None:
    # Ensure UTF-8 output formatting on Windows terminals
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("==================================================")
    print("           Phase 2: UI Detector Test              ")
    print("==================================================")
    print("Switch to target window now... (scanning in 3 seconds)")

    for i in range(3, 0, -1):
        print(f"Countdown: {i}...", end="\r", flush=True)
        time.sleep(1)

    print("\nScanning active foreground window...")
    start_time = time.perf_counter()

    elements = get_active_ui_elements()
    elapsed_ms = (time.perf_counter() - start_time) * 1000

    llm_output = format_for_llm(elements)

    print("\n--- LLM Formatted UI Map ---")
    if llm_output:
        print(llm_output)
    else:
        print("(No actionable UI elements detected)")

    print("----------------------------")
    print(f"Detected {len(elements)} elements in {elapsed_ms:.2f} ms")


if __name__ == "__main__":
    main()
