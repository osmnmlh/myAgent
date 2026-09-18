"""
agent_engine
============
Core low-level safety and execution engine for the local AI desktop assistant.

Public surface
--------------
  AgentState   – thread-safe state enum
  SafetyMonitor – low-level hook monitor + kill-switch
  Actuator      – instant mouse / keyboard actuator
  StatusOverlay – click-through border overlay
"""

from .safety import AgentState, SafetyMonitor
from .actuator import Actuator
from .overlay import StatusOverlay

__all__ = [
    "AgentState",
    "SafetyMonitor",
    "Actuator",
    "StatusOverlay",
]
