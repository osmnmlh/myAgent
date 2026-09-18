"""
brain.py - Stateless LLM Decision Engine (Phase 4: multi-step actions)
Connects to a local Qwen 2.5 Coder 7B model via LM Studio and translates
(user_command, ui_tree) into a strict JSON array of sequential actions.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import OpenAI

log = logging.getLogger(__name__)

# ── System Prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT: str = """\
You are a precise desktop automation agent. Your job is to translate the user's \
natural-language instruction into a sequence of actions on the active window.

## RULES (STRICT)
1. You can ONLY interact with the exact `id`s provided in the UI list below. \
Do NOT invent, guess, or hallucinate element IDs that are not listed.
2. If no element matches the user's intent, return a single-element array with action "none".
3. Output ONLY a JSON array. No markdown, no explanation, no commentary.
4. Each step in the sequence is one action object. Order matters — steps execute left to right.

## SCHEMA (respond with EXACTLY this — a JSON array of action objects)
[
  {
    "thought":          "<Brief 1-sentence reasoning for this step>",
    "action":           "<'click' | 'type' | 'none'>",
    "target_id":        <int id from UI list, or null if action is 'none'>,
    "text_to_type":     "<string to type, or null if action is not 'type'>",
    "requires_confirm": <true if this step is destructive (delete, format, send, pay, close unsaved work), false otherwise>
  }
]

## EXAMPLES
Instruction: "Click 9, then click multiply, then click 8"
[
  {"thought": "Click the 9 key.", "action": "click", "target_id": 5, "text_to_type": null, "requires_confirm": false},
  {"thought": "Click multiply.", "action": "click", "target_id": 12, "text_to_type": null, "requires_confirm": false},
  {"thought": "Click the 8 key.", "action": "click", "target_id": 4, "text_to_type": null, "requires_confirm": false}
]

## SAFETY
- Set "requires_confirm": true ONLY for actions that cause irreversible data loss, make a payment, send a message, or close unsaved work (e.g. format, delete, submit order, send email).
- Set "requires_confirm": false for: clicking Cancel, Dismiss, Close, Back, OK, navigation buttons, or any read-only action.
- When unsure, default to "requires_confirm": true.
"""

# ── Constants ────────────────────────────────────────────────────────────────

SAFE_FALLBACK: list[dict[str, Any]] = [{
    "thought": "Could not determine a safe action.",
    "action": "none",
    "target_id": None,
    "text_to_type": None,
    "requires_confirm": False,
}]

REQUIRED_KEYS: set[str] = {"thought", "action", "target_id", "text_to_type", "requires_confirm"}
VALID_ACTIONS: set[str] = {"click", "type", "none"}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_ui_block(ui_elements: list[dict[str, Any]]) -> str:
    """Render the UI element list into a compact text block for the prompt."""
    if not ui_elements:
        return "(No interactive elements detected)"
    return "\n".join(
        f'[{el.get("id", "?")}] {el.get("control_type", "Unknown")}: '
        f'"{el.get("name", "")}" @ ({el.get("center_x", 0)}, {el.get("center_y", 0)})'
        for el in ui_elements
    )


def _extract_action_list(raw: str) -> list[dict[str, Any]]:
    """
    Forcefully extract a JSON array from the LLM's raw text output.

    Handles local model hallucinations:
    - Markdown fences (```json ... ```)
    - Conversational padding around the array
    - Single dict instead of array (auto-wrapped for backwards compat)
    """
    # Try array first (primary format)
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        result = json.loads(match.group())
        if isinstance(result, list):
            return result

    # Fallback: model returned a single dict — wrap it
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        result = json.loads(match.group())
        if isinstance(result, dict):
            log.warning("LLM returned a single dict instead of an array — wrapping it")
            return [result]

    raise ValueError("No JSON array or object found in LLM output")


def _validate_step(step: dict[str, Any], valid_ids: set[int]) -> dict[str, Any] | None:
    """
    Validates a single action step. Returns the (possibly coerced) step dict,
    or None if the step is fatally invalid and should be replaced by a fallback.
    """
    if not REQUIRED_KEYS.issubset(step.keys()):
        log.warning("Step missing required keys: %s", REQUIRED_KEYS - step.keys())
        return None

    action = step.get("action")
    if action not in VALID_ACTIONS:
        log.warning("Invalid action '%s' in step", action)
        return None

    target_id = step.get("target_id")
    if action in ("click", "type") and target_id not in valid_ids:
        log.warning("target_id %s not in UI element list", target_id)
        return None

    if action == "type" and not step.get("text_to_type"):
        log.warning("action='type' but text_to_type is empty")
        return None

    step["requires_confirm"] = bool(step.get("requires_confirm", False))
    return step


def _validate_action_list(
    steps: list[dict[str, Any]],
    valid_ids: set[int],
) -> list[dict[str, Any]]:
    """
    Validates every step in the action list.
    Invalid steps are replaced with a safe 'none' action so execution
    can still proceed for the remaining valid steps.
    """
    validated: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        result = _validate_step(step, valid_ids)
        if result is not None:
            validated.append(result)
        else:
            log.warning("Step %d failed validation — substituting safe 'none'", i)
            validated.append(dict(SAFE_FALLBACK[0]))
    return validated if validated else list(SAFE_FALLBACK)


# ── AgentBrain ───────────────────────────────────────────────────────────────

class AgentBrain:
    """
    Stateless decision engine. Sends (user_command + UI context) to a local
    LLM and returns a validated list of sequential action dicts.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234/v1",
        api_key: str = "lm-studio",
        model: str = "qwen2.5-coder-7b-instruct",
        temperature: float = 0.1,
        max_tokens: int = 600,  # Increased for multi-step responses
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        log.info("AgentBrain initialized — model=%s, endpoint=%s", model, base_url)

    def decide_action(
        self,
        user_prompt: str,
        ui_elements: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Sends the user's command and UI context to the local LLM,
        parses the JSON array response, validates each step, and returns
        a safe sequential action list.

        Args:
            user_prompt:  Natural-language instruction (may describe multiple steps).
            ui_elements:  List of dicts with keys: id, name, control_type,
                          center_x, center_y.

        Returns:
            Validated list of action dicts. Always returns at least one step.
        """
        ui_block = _build_ui_block(ui_elements)
        valid_ids: set[int] = {el["id"] for el in ui_elements if "id" in el}

        user_message = (
            f"## Active UI Elements\n{ui_block}\n\n"
            f"## User Instruction\n{user_prompt}"
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            raw_output = response.choices[0].message.content or ""
            log.debug("Raw LLM output:\n%s", raw_output)

        except ConnectionError as exc:
            log.error("LM Studio connection failed: %s", exc)
            return list(SAFE_FALLBACK)
        except Exception as exc:
            log.error("LLM request failed: %s", exc)
            return list(SAFE_FALLBACK)

        # ── Parse & validate ─────────────────────────────────────────────
        try:
            steps = _extract_action_list(raw_output)
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("JSON extraction failed: %s — raw: %s", exc, raw_output[:200])
            return list(SAFE_FALLBACK)

        return _validate_action_list(steps, valid_ids)
