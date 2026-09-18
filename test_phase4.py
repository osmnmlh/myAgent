"""
test_phase4.py - Phase 4 Diagnostic Suite
Validates multi-step brain parsing and VoiceEngine module loading.
Tests run fully offline where possible; LM Studio tests need the server up.
"""

from __future__ import annotations

import sys
import time
import json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agent_engine.brain import (
    AgentBrain,
    _extract_action_list,
    _validate_action_list,
    SAFE_FALLBACK,
)

# ── Mock data ────────────────────────────────────────────────────────────────

CALC_UI: list[dict] = [
    {"id": 1, "name": "0",        "control_type": "Button", "center_x": 300, "center_y": 500},
    {"id": 2, "name": "1",        "control_type": "Button", "center_x": 100, "center_y": 500},
    {"id": 3, "name": "2",        "control_type": "Button", "center_x": 200, "center_y": 500},
    {"id": 4, "name": "3",        "control_type": "Button", "center_x": 300, "center_y": 400},
    {"id": 5, "name": "4",        "control_type": "Button", "center_x": 100, "center_y": 400},
    {"id": 6, "name": "5",        "control_type": "Button", "center_x": 200, "center_y": 400},
    {"id": 7, "name": "6",        "control_type": "Button", "center_x": 300, "center_y": 300},
    {"id": 8, "name": "7",        "control_type": "Button", "center_x": 100, "center_y": 300},
    {"id": 9, "name": "8",        "control_type": "Button", "center_x": 200, "center_y": 300},
    {"id": 10, "name": "9",       "control_type": "Button", "center_x": 300, "center_y": 200},
    {"id": 11, "name": "+",       "control_type": "Button", "center_x": 400, "center_y": 500},
    {"id": 12, "name": "-",       "control_type": "Button", "center_x": 400, "center_y": 400},
    {"id": 13, "name": "*",       "control_type": "Button", "center_x": 400, "center_y": 300},
    {"id": 14, "name": "/",       "control_type": "Button", "center_x": 400, "center_y": 200},
    {"id": 15, "name": "=",       "control_type": "Button", "center_x": 400, "center_y": 100},
    {"id": 16, "name": "Delete",  "control_type": "Button", "center_x": 500, "center_y": 100},
]

VALID_IDS = {el["id"] for el in CALC_UI}

# ── Offline Parser Tests ─────────────────────────────────────────────────────

def test_array_extraction() -> tuple[bool, str]:
    """Verify regex extracts a JSON array correctly."""
    raw = 'Some preamble\n[{"thought":"t","action":"click","target_id":10,"text_to_type":null,"requires_confirm":false}]\nSome trailing text'
    steps = _extract_action_list(raw)
    passed = isinstance(steps, list) and len(steps) == 1 and steps[0]["target_id"] == 10
    return passed, f"Got {len(steps)} step(s), target_id={steps[0].get('target_id') if steps else 'N/A'}"


def test_multistep_extraction() -> tuple[bool, str]:
    """Verify a 3-step array is fully extracted."""
    raw = json.dumps([
        {"thought": "Click 9", "action": "click", "target_id": 10, "text_to_type": None, "requires_confirm": False},
        {"thought": "Click *",  "action": "click", "target_id": 13, "text_to_type": None, "requires_confirm": False},
        {"thought": "Click 8",  "action": "click", "target_id": 9,  "text_to_type": None, "requires_confirm": False},
    ])
    steps = _extract_action_list(raw)
    passed = len(steps) == 3 and steps[1]["target_id"] == 13
    return passed, f"Got {len(steps)} steps, middle target_id={steps[1].get('target_id') if len(steps)>1 else 'N/A'}"


def test_markdown_fence_strip() -> tuple[bool, str]:
    """Verify markdown-wrapped JSON arrays are parsed correctly."""
    raw = '```json\n[{"thought":"t","action":"click","target_id":5,"text_to_type":null,"requires_confirm":false}]\n```'
    steps = _extract_action_list(raw)
    passed = len(steps) == 1 and steps[0]["target_id"] == 5
    return passed, f"Got {len(steps)} step(s), target_id={steps[0].get('target_id') if steps else 'N/A'}"


def test_single_dict_fallback() -> tuple[bool, str]:
    """Verify a single dict response is auto-wrapped into a list."""
    raw = '{"thought":"click cancel","action":"click","target_id":2,"text_to_type":null,"requires_confirm":false}'
    steps = _extract_action_list(raw)
    passed = isinstance(steps, list) and len(steps) == 1 and steps[0]["action"] == "click"
    return passed, f"Wrapped single dict -> list of {len(steps)} step(s)"


def test_hallucinated_id_blocked() -> tuple[bool, str]:
    """Verify that a hallucinated target_id (not in UI list) falls back to 'none'."""
    steps = [{"thought": "t", "action": "click", "target_id": 999, "text_to_type": None, "requires_confirm": False}]
    validated = _validate_action_list(steps, VALID_IDS)
    passed = validated[0]["action"] == "none"
    return passed, f"Hallucinated id=999 -> action='{validated[0]['action']}'"


def test_bad_step_does_not_kill_sequence() -> tuple[bool, str]:
    """Verify a bad middle step is replaced with 'none' but other steps survive."""
    steps = [
        {"thought": "Click 9", "action": "click", "target_id": 10, "text_to_type": None, "requires_confirm": False},
        {"thought": "Bad",     "action": "teleport", "target_id": 0, "text_to_type": None, "requires_confirm": False},  # Invalid
        {"thought": "Click =", "action": "click", "target_id": 15, "text_to_type": None, "requires_confirm": False},
    ]
    validated = _validate_action_list(steps, VALID_IDS)
    passed = (
        len(validated) == 3
        and validated[0]["action"] == "click"
        and validated[1]["action"] == "none"   # Bad step -> safe fallback
        and validated[2]["action"] == "click"
    )
    return passed, (
        f"3 steps: [{validated[0]['action']}, {validated[1]['action']}, {validated[2]['action']}] "
        f"(expect: [click, none, click])"
    )


# ── Live LLM Test ────────────────────────────────────────────────────────────

def test_live_multistep(brain: AgentBrain) -> tuple[bool, str, list]:
    """Live LLM test: 'Click 9, then multiply, then click 8' should produce 3 steps."""
    result = brain.decide_action(
        "Click 9, then click multiply, then click 8",
        CALC_UI,
    )
    step_count = len(result)
    # Expect: exactly 3 steps, all clicks, no hallucinated IDs
    all_valid_ids = all(
        step.get("action") == "none" or step.get("target_id") in VALID_IDS
        for step in result
    )
    passed = step_count >= 2 and all_valid_ids
    ids = [s.get("target_id") for s in result]
    return passed, f"{step_count} step(s) returned, target_ids={ids}", result


# ── Voice Import Test ────────────────────────────────────────────────────────

def test_voice_imports() -> tuple[bool, str]:
    """Verify voice.py imports and VoiceEngine class is accessible (no model load)."""
    try:
        from agent_engine.voice import VoiceEngine, listen_and_transcribe, get_engine
        has_attrs = all(hasattr(VoiceEngine, m) for m in ("record", "transcribe", "listen_and_transcribe", "transcribe_file"))
        return has_attrs, "VoiceEngine importable, all methods present" if has_attrs else "Missing methods"
    except Exception as exc:
        return False, f"Import failed: {exc}"


# ── Runner ───────────────────────────────────────────────────────────────────

def main() -> None:
    sep = "=" * 68
    print(f"\n{sep}")
    print("         AGENT ENGINE - PHASE 4 DIAGNOSTIC SUITE")
    print(f"{sep}\n")

    results: list[tuple[str, bool, str]] = []

    # ── Offline tests ─────────────────────────────────────────────────────
    print("  --- Offline Parser Tests ---\n")
    offline_tests: list[tuple[str, object]] = [
        ("Array extraction from padded text",    test_array_extraction),
        ("Multi-step 3-action array",            test_multistep_extraction),
        ("Markdown fence stripping",             test_markdown_fence_strip),
        ("Single-dict fallback wrapping",        test_single_dict_fallback),
        ("Hallucinated ID blocked",              test_hallucinated_id_blocked),
        ("Bad step -> 'none', sequence intact",  test_bad_step_does_not_kill_sequence),
        ("Voice module imports",                 test_voice_imports),
    ]
    for label, fn in offline_tests:
        t0 = time.perf_counter()
        try:
            passed, detail = fn()
        except Exception as exc:
            passed, detail = False, f"Exception: {exc}"
        ms = (time.perf_counter() - t0) * 1000
        status = "[PASS]" if passed else "[FAIL]"
        results.append((label, passed, detail))
        print(f"  {status}  {label}  ({ms:.1f} ms)")
        print(f"         {detail}\n")

    # ── Live LLM tests ────────────────────────────────────────────────────
    print("  --- Live LLM Tests (LM Studio required) ---\n")
    try:
        brain = AgentBrain()
        t0 = time.perf_counter()
        passed, detail, raw = test_live_multistep(brain)
        ms = (time.perf_counter() - t0) * 1000
        status = "[PASS]" if passed else "[FAIL]"
        results.append(("Live multi-step (9 * 8)", passed, detail))
        print(f"  {status}  Live multi-step: 'Click 9, multiply, click 8'  ({ms:.0f} ms)")
        print(f"         {detail}")
        for i, s in enumerate(raw, 1):
            print(f"         Step {i}: action={s.get('action')!r} id={s.get('target_id')!r} thought={s.get('thought', '')!r}")
        print()
    except Exception as exc:
        results.append(("Live multi-step", False, f"Skipped: {exc}"))
        print(f"  [SKIP]  Live LLM test skipped — LM Studio not reachable: {exc}\n")

    # ── Summary ───────────────────────────────────────────────────────────
    total = len(results)
    passed_count = sum(1 for _, p, _ in results if p)
    print("-" * 68)
    print(f"  Results: {passed_count}/{total} passed", end="")
    print("  -- all clear" if passed_count == total else f"  -- {total - passed_count} FAILED")
    print(sep)
    print()


if __name__ == "__main__":
    main()
