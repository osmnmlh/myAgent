# 🤖 myAgent

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/osmnmlh/myAgent/ci.yml?branch=master&style=for-the-badge&label=CI)](https://github.com/osmnmlh/myAgent/actions)

**A high-performance, safety-first local AI desktop agent for Windows.**  
*Operate your PC using natural voice or text commands with sub-millisecond safety kill-switches and zero cloud dependencies.*

[Features](#-key-features) • [Architecture](#-architecture) • [Getting Started](#-getting-started) • [Safety System](#-safety--kill-switch) • [Project Structure](#-project-structure)

</div>

---

## ⚡ Overview

**myAgent** is an autonomous desktop execution engine built natively for Windows. It observes on-screen UI elements, plans multi-step actions using a local LLM, and interacts via hardware-level mouse and keyboard inputs—all while keeping the user in full control through an instant physical kill-switch.

### Why myAgent?
- 🔒 **100% Local & Private:** Runs entirely on your machine via local models (LM Studio / Qwen 2.5 and Faster-Whisper). No screenshots or audio sent to the cloud.
- ⚡ **Native Win32 Performance:** Bypasses sluggish web wrappers with direct Windows API hooks and `SendInput` hardware injection.
- 🛑 **Sub-Millisecond Kill-Switch:** Physical mouse movement (> 15 px) or pressing `ESC` instantly terminates agent activity in `< 0.1 ms`.
- 👁️ **Click-Through Visual Indicator:** An unobtrusive, transparent neon-blue border overlay alerts you whenever the agent is actively executing.

---

## 🏗️ Architecture

```
                       [ 🎙️ Microphone ]
                                │
                        (faster-whisper)
                                ▼
                       [ User Instruction ]
                                │
 [ 🖥️ Foreground UI ] ──► (uiautomation) ──► [ UI Element Tree ]
                                                    │
                                                    ▼
                                          [ 🧠 Local LLM Brain ]
                                            (Qwen 2.5 in LM Studio)
                                                    │
                                           Action Plan JSON
                                                    │
                                                    ▼
                     ┌────────────────── [ 🛡️ Safety Engine ] ──────────────────┐
                     │                                                          │
                     │  • Visual Overlay (Neon Border active)                   │
                     │  • Low-level Mouse/KB Hooks listening                    │
                     │  • Hardware Kill-Switch: aborts if human interrupts      │
                     │                                                          │
                     └──────────────────────────┬───────────────────────────────┘
                                                │
                                                ▼
                                    [ 🎯 Win32 Actuator ]
                                    (Instant Click / Type)
```

### The 4 Phases

| Phase | Component | Technology | Description |
| :--- | :--- | :--- | :--- |
| **Phase 1** | **Safety & Execution** | `ctypes`, Win32 API | Low-level input actuator, transparent overlay, and hardware kill-switch. |
| **Phase 2** | **UI Perception** | `uiautomation` | Fast inspection of the foreground window's accessible element tree. |
| **Phase 3** | **Decision Engine** | `OpenAI API`, Qwen 2.5 | Analyzes UI maps and user prompts to produce safe, validated JSON action lists. |
| **Phase 4** | **Voice Integration** | `faster-whisper`, `sounddevice` | Offline real-time speech transcription for hands-free desktop control. |

---

## 🚀 Getting Started

### Prerequisites

- **OS:** Windows 10 / 11 (64-bit)
- **Python:** 3.10 or higher
- **Local LLM Server (Optional for Phase 3/4):** [LM Studio](https://lmstudio.ai/) running `Qwen 2.5` with a local server at `http://127.0.0.1:1234/v1`

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/osmnmlh/myAgent.git
   cd myAgent
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

---

## 🎮 Running the Demos

### 1. Safety & Actuator Demonstration
Launches the Phase 1 test loop with the neon-blue status overlay. Try moving your mouse to trigger the immediate interrupt!
```bash
python main.py
```
*Flags:*
- `--dry-run`: Preview actions without physical mouse movement.
- `--threshold <px>`: Adjust mouse movement interrupt threshold (default: `15` px).
- `--delay <ms>`: Add inter-click dwell time in milliseconds.

### 2. Live UI Element Scanner
Scans the active foreground application and formats actionable UI elements into LLM-ready context.
```bash
python test_phase2.py
```

### 3. Voice-Controlled End-to-End Automation
Listens for a spoken voice command (e.g. *"9 times 8 equals"*), extracts the active window controls (like Windows Calculator), and executes the sequence.
```bash
python live_integration_test.py
```

---

## 🛡️ Safety & Kill-Switch

User safety and system integrity are fundamental to `myAgent`:

- **Hardware vs Synthetic Event Detection:** The low-level Windows hook flags agent-injected events (`LLMHF_INJECTED`) and distinguishes them from physical user input.
- **Physical Override:** Any human mouse move exceeding `15px` or any press of the `ESC` key instantly transitions the engine into `AgentState.INTERRUPTED`.
- **Destructive Action Confirmation:** The Brain schema requires explicit `requires_confirm: true` confirmation for irreversible operations (formatting, deleting, closing files).

---

## 📂 Project Structure

```
myAgent/
├── agent_engine/
│   ├── __init__.py       # Package exports
│   ├── actuator.py       # Win32 SendInput mouse & keyboard automation
│   ├── brain.py          # LLM prompt orchestration & JSON action parser
│   ├── overlay.py        # Transparent click-through border window
│   ├── safety.py         # Low-level WH_MOUSE_LL / WH_KEYBOARD_LL hooks
│   └── voice.py          # Faster-whisper audio recording & transcription
├── main.py               # Phase 1 safety engine entry point
├── ui_detector.py        # UI Automation hierarchy extractor
├── live_integration_test.py # End-to-end voice to UI actuation test
├── requirements.txt      # Project dependencies
└── LICENSE               # MIT License
```

---

## 📄 License

This project is licensed under the [MIT License](LICENSE) - see the LICENSE file for details.
