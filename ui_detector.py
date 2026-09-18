"""
ui_detector.py - Fast, Lightweight UI Element Detector for Active Windows
Extracts clickable/interactive UI elements using Microsoft UI Automation.
"""

from dataclasses import dataclass
import uiautomation as auto

# Actionable control types to extract
ACTIONABLE_CONTROL_TYPES: set[int] = {
    auto.ControlType.ButtonControl,
    auto.ControlType.EditControl,
    auto.ControlType.CheckBoxControl,
    auto.ControlType.RadioButtonControl,
    auto.ControlType.MenuItemControl,
    auto.ControlType.TabItemControl,
    auto.ControlType.HyperlinkControl,
    auto.ControlType.DocumentControl,
    auto.ControlType.ComboBoxControl,
    auto.ControlType.ListItemControl,
    auto.ControlType.TreeItemControl,
    auto.ControlType.SliderControl,
    auto.ControlType.SplitButtonControl,
}


@dataclass
class UIElement:
    id: int
    name: str
    control_type: str
    center_x: int
    center_y: int


def get_active_ui_elements(max_depth: int = 15) -> list[UIElement]:
    """Extracts visible, interactive UI elements from the active foreground window."""
    elements: list[UIElement] = []
    try:
        fg_window = auto.GetForegroundControl()
        if not fg_window:
            return elements
    except Exception:
        return elements

    element_id = 1
    try:
        for control, _depth in auto.WalkControl(fg_window, maxDepth=max_depth):
            try:
                if control.ControlType not in ACTIONABLE_CONTROL_TYPES or control.IsOffscreen:
                    continue
                rect = control.BoundingRectangle
                if rect.width() <= 0 or rect.height() <= 0:
                    continue

                control_type = control.ControlTypeName.removesuffix("Control")
                name = control.Name.strip() if control.Name else ""

                elements.append(
                    UIElement(
                        id=element_id,
                        name=name,
                        control_type=control_type,
                        center_x=rect.xcenter(),
                        center_y=rect.ycenter(),
                    )
                )
                element_id += 1
            except Exception:
                continue
    except Exception:
        pass

    return elements


def format_for_llm(elements: list[UIElement]) -> str:
    """Formats UI elements into a dense, token-saving text block for LLM prompt context."""
    return "\n".join(
        f'[{e.id}] {e.control_type}: "{e.name}" @ ({e.center_x}, {e.center_y})'
        for e in elements
    )
