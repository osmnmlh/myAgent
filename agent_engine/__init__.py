"""
agent_engine
============
Core low-level safety and execution engine for the local AI desktop assistant.

Public surface
--------------
  AgentState    – thread-safe state enum
  SafetyMonitor – low-level hook monitor + kill-switch
  Actuator      – instant mouse / keyboard actuator
  StatusOverlay – click-through border overlay
  AgentBrain    – stateless LLM decision engine (Phase 3)
  VoiceEngine   – local speech-to-text via faster-whisper (Phase 4)
"""

from .safety import AgentState, SafetyMonitor
from .actuator import Actuator
from .overlay import StatusOverlay
from .brain import AgentBrain
from .voice import VoiceEngine, listen_and_transcribe

__all__ = [
    "AgentState",
    "SafetyMonitor",
    "Actuator",
    "StatusOverlay",
    "AgentBrain",
    "VoiceEngine",
    "listen_and_transcribe",
]
