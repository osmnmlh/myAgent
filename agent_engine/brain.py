"""
brain.py - Stateless LLM Decision Engine
Connects to a local Qwen 2.5 Coder 7B model via LM Studio and translates
(user_command, ui_tree) into a strict JSON action for the actuator.
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
natural-language instruction into EXACTLY ONE action on the active window.

## RULES (STRICT)
1. You can ONLY interact with the exact `id`s provided in the UI list below. \
Do NOT invent, guess, or hallucinate element IDs that are not listed.
2. If no element matches the user's intent, return action "none".
3. Output ONLY a single JSON object. No markdown, no explanation, no commentary.

## ACTION SCHEMA (respond with EXACTLY this JSON structure)
{
  "thought":          "<Brief 1-sentence reasoning>",
  "action":           "<'click' | 'type' | 'none'>",
  "target_id":        <int id from UI list, or null if action is 'none'>,
  "text_to_type":     "<string to type, or null if action is not 'type'>",
  "requires_confirm": <true if action is destructive (delete, format, send, \
pay, close unsaved work), false otherwise>
}

## SAFETY
- Set "requires_confirm": true for ANY action that could cause data loss, \
make a payment, send a message, or close unsaved work.
- When unsure, default to "requires_confirm": true.
"""

# ── Safe fallback ────────────────────────────────────────────────────────────

SAFE_FALLBACK: dict[str, Any] = {
    "thought": "Could not determine a safe action.",
    "action": "none",
    "target_id": None,
    "text_to_type": None,
    "requires_confirm": False,
}

REQUIRED_KEYS: set[str] = {"thought", "action", "target_id", "text_to_type", "requires_confirm"}
VALID_ACTIONS: set[str] = {"click", "type", "none"}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_ui_block(ui_elements: list[dict[str, Any]]) -> str:
    """Render the UI element list into a compact text block for the prompt."""
    if not ui_elements:
        return "(No interactive elements detected)"
    lines: list[str] = []
    for el in ui_elements:
        eid = el.get("id", "?")
        name = el.get("name", "")
        ctype = el.get("control_type", "Unknown")
        cx = el.get("center_x", 0)
        cy = el.get("center_y", 0)
        lines.append(f'[{eid}] {ctype}: "{name}" @ ({cx}, {cy})')
    return "\n".join(lines)


def _extract_json(raw: str) -> dict[str, Any]:
    """
    Forcefully extract a JSON dict from the LLM's raw text output.

    Local models often wrap JSON in markdown fences (```json ... ```)
    or add conversational padding. This strips all of that.
    """
    # Try to find the outermost { ... } block
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in LLM output")
    return json.loads(match.group())


def _validate_action(parsed: dict[str, Any], valid_ids: set[int]) -> dict[str, Any]:
    """
    Validates the parsed action dict against the schema and the available UI IDs.
    Returns the dict if valid, otherwise returns SAFE_FALLBACK.
    """
    # Check all required keys are present
    if not REQUIRED_KEYS.issubset(parsed.keys()):
        log.warning("Action missing required keys: %s", REQUIRED_KEYS - parsed.keys())
        return dict(SAFE_FALLBACK)

    # Validate action field
    action = parsed.get("action")
    if action not in VALID_ACTIONS:
        log.warning("Invalid action '%s', falling back to 'none'", action)
        return dict(SAFE_FALLBACK)

    # Validate target_id references a real element
    target_id = parsed.get("target_id")
    if action in ("click", "type") and target_id not in valid_ids:
        log.warning("target_id %s not in UI element list, falling back", target_id)
        return dict(SAFE_FALLBACK)

    # Validate type action has text
    if action == "type" and not parsed.get("text_to_type"):
        log.warning("action='type' but text_to_type is empty, falling back")
        return dict(SAFE_FALLBACK)

    # Ensure boolean for requires_confirm
    parsed["requires_confirm"] = bool(parsed.get("requires_confirm", False))

    return parsed


# ── AgentBrain ───────────────────────────────────────────────────────────────

class AgentBrain:
    """
    Stateless decision engine. Sends (user_command + UI context) to a local
    LLM and returns a validated action dict.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234/v1",
        api_key: str = "lm-studio",
        model: str = "qwen2.5-coder-7b-instruct",
        temperature: float = 0.1,
        max_tokens: int = 300,
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
    ) -> dict[str, Any]:
        """
        Sends the user's command and UI context to the local LLM,
        parses the JSON response, validates it, and returns a safe action dict.

        Args:
            user_prompt:  Natural-language instruction from the user.
            ui_elements:  List of dicts with keys: id, name, control_type,
                          center_x, center_y.

        Returns:
            Validated action dict conforming to the action schema.
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
            return dict(SAFE_FALLBACK)
        except Exception as exc:
            log.error("LLM request failed: %s", exc)
            return dict(SAFE_FALLBACK)

        # ── Parse & validate ─────────────────────────────────────────────
        try:
            parsed = _extract_json(raw_output)
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("JSON extraction failed: %s — raw: %s", exc, raw_output[:200])
            return dict(SAFE_FALLBACK)

        return _validate_action(parsed, valid_ids)
