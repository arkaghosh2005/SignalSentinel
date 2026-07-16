# SignalSentinel : AI-Powered CW Emergency Net Controller

<div align="center">

![SignalSentinel](https://img.shields.io/badge/SignalSentinel-CW_Emergency_Net_Controller-00C853?style=for-the-badge)

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-1.x-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-Agent_Graph-1C3C3C?style=for-the-badge&logo=langchain&logoColor=white)
![Gemma 4](https://img.shields.io/badge/Gemma_4-via_Ollama-4285F4?style=for-the-badge&logo=google&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-WAL_Mode-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![SciPy](https://img.shields.io/badge/SciPy-DSP_Engine-8CAAE6?style=for-the-badge&logo=scipy&logoColor=white)
![NumPy](https://img.shields.io/badge/NumPy-Audio_Processing-013243?style=for-the-badge&logo=numpy&logoColor=white)
![scikit--learn](https://img.shields.io/badge/scikit--learn-KMeans_Clustering-F7931E?style=for-the-badge&logo=scikit-learn&logoColor=white)

**A High-Performance Autonomous Morse Code (CW) monitoring and control station built with Python, Streamlit, LangGraph, & Gemma 4. Features real-time folder monitoring with watchdog, DSP-based Morse decoding using bandpass filtering and KMeans clustering, and an AI-powered contextual reconstruction engine. The LLM agent autonomously triages incoming transmissions by precedence (EMERGENCY / PRIORITY / ROUTINE) using simulated frequency contexts, generates dynamic CW reply audio, and logs every action to a live-refreshing SQLite telemetry dashboard with toast notifications, CSV export, and an agent reasoning panel.**

</div>

---

## 📋 Table of Contents

- [Features](#features)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Application Flow](#application-flow)
- [Sponsor](#sponsor)
- [License](#license)

---

<a id="features"></a>

## ✨ Features

### 📡 Dashboard & Monitoring
| Feature | Description |
|---------|-------------|
| **Live Dashboard** | Real-time metrics for messages in, replies sent, relays, no-contact events, errors, and precedence counts |
| **Auto-Refresh** | Non-blocking 2-second auto-refresh via `streamlit-autorefresh` — no frozen UI |
| **Incoming Feed** | Chronological feed of decoded CW messages with inline audio playback of the original WAV |
| **Communications Feed** | Outbound activity feed showing replies, relays, no-contact logs, and log-only entries with audio |
| **Full Event Log** | Expandable table of all events with timestamp, type, station, frequency, status, and payload |
| **CSV Export** | One-click download of the full event log as a CSV file |

### 🤖 AI Agent Pipeline
| Feature | Description |
|---------|-------------|
| **Gemma 4 via Ollama** | Local LLM inference with 8192-token context window for processing the ops manual + decoded messages |
| **LangGraph State Machine** | Single-pass agent graph: `agent → tools → END` — exactly one tool call per message, no infinite loops |
| **Forced Tool Calling** | `tool_choice="any"` ensures the model always calls a tool — never responds with plain text |
| **4 Action Tools** | `send_cw_reply`, `relay_message`, `record_no_contact`, `log_only` — each with structured logging |
| **Ops Manual Grounding** | Agent decisions are grounded in a structured operations manual with station roster, frequency plan, precedence rules, and CW procedure |
| **Autonomous Precedence Triage** | 3-tier routing: EMERGENCY (injury/fire/SOS) vs PRIORITY (important roster traffic) vs ROUTINE (tests/general). Defaults to EMERGENCY when in doubt. |

### 🔊 CW Audio Engine
| Feature | Description |
|---------|-------------|
| **Morse Encoder** | Clean CW WAV generation with PARIS-standard timing, 5ms rise/fall envelope shaping, and configurable WPM/tone |
| **Morse Decoder** | DSP pipeline: bandpass filter → envelope detection → Otsu thresholding → KMeans dot/dash clustering → Morse-to-text |
| **Signal Generator** | Realistic test signal synthesis with configurable noise, timing jitter, fading, and packet loss |
| **Spell Correction** | Post-decode spell checking via `pyspellchecker` to recover garbled characters |
| **Tone Auto-Detection** | FFT-based automatic detection of the CW tone frequency in incoming audio |

### 📻 Operations Manual & Protocol
| Feature | Description |
|---------|-------------|
| **Station Roster** | 3 Indian amateur radio stations: VU2NCS (Net Control), VU2RLY (Relay), VU2FLD (Field) |
| **Frequency Plan** | Primary + fallback frequencies for local tactical, regional relay, emergency, and roll call scenarios |
| **CW Prosigns** | Standard procedure signs (DE, K, KN, AR, SK, BK, R) enforced in all transmissions |
| **Message Templates** | Structured formats for acknowledgments, relays, no-contact logs, CQ answers, and sign-offs |
| **Timeout Rules** | 3 attempts on primary → fallback frequency → no-contact log. No false emergencies from silence. |

### 🛠️ Admin & Database
| Feature | Description |
|---------|-------------|
| **SQLite with WAL** | Thread-safe event logging with Write-Ahead Logging for concurrent read/write access |
| **Atomic Stats** | Running totals in a `stats` table, updated atomically in the same transaction as event inserts |
| **Clear Database** | One-click wipe of all events, stats, and audio files (incoming + outgoing WAVs) |
| **Pipeline Status** | Live sidebar indicators for pipeline health (agent + watchdog) and LLM availability (Ollama ping) |
| **Toast Notifications** | `st.toast()` pop-ups for CW generation and database clearing |
| **Watchdog Deduplication** | Windows-safe file watcher with 5-second dedup window to prevent duplicate processing |

### 🧠 Agent Reasoning Panel
| Feature | Description |
|---------|-------------|
| **Decision Log** | Expandable panel showing the last 5 agent decisions with the decoded message and which tool was called |
| **Reasoning Capture** | LLM's text reasoning (up to 300 chars) stored alongside tool call metadata in the event payload |
| **Tool Tracing** | Each agent invocation logs the tool name, station, frequency, and full payload for debugging |

---

<a id="tech-stack"></a>

## 🛠 Tech Stack

### Core Technologies
| Technology | Purpose |
|------------|---------|
| Python 3.10+ | Runtime — type hints, `from __future__ import annotations` |
| Streamlit | Dashboard UI framework with auto-refresh, sidebar, metrics, and audio widgets |
| streamlit-autorefresh | Non-blocking JavaScript-based page refresh |
| SQLite (WAL mode) | Embedded event database with Write-Ahead Logging for thread-safe concurrent access |
| Pandas | DataFrame-based event log queries and CSV export |
| watchdog | Filesystem monitoring — triggers decode pipeline on new incoming WAV files |

### DSP & Audio Processing
| Technology | Purpose |
|------------|---------|
| SciPy | Butterworth bandpass filter (`sosfiltfilt`), WAV file I/O, morphological mask cleaning |
| NumPy | Signal array operations, FFT tone detection, envelope computation |
| scikit-learn | KMeans clustering to distinguish dots from dashes in decoded Morse signals |
| pyspellchecker | Post-decode spell correction to recover garbled characters from noisy signals |

### AI & LLM
| Technology | Purpose |
|------------|---------|
| LangChain Core | Tool definitions (`@tool`), message types (`HumanMessage`, `SystemMessage`) |
| LangGraph | State machine graph with conditional edges: `agent → tools → END` |
| LangChain Ollama | `ChatOllama` adapter for local Gemma 4 inference via Ollama server |
| Gemma 4 (e4b) | Google's open LLM running locally — processes ops manual + decoded CW to decide actions |
| Ollama | Local LLM inference server at `localhost:11434` with tool-calling support |

---

<a id="project-structure"></a>

## 📁 Project Structure

```
SignalSentinel/
├── app.py                 # Main application — Streamlit dashboard, LangGraph agent,
│                          #   EventLogger, watchdog, sidebar, and all render functions
│
├── encoder.py             # Clean CW WAV generator — PARIS timing, envelope shaping,
│                          #   configurable WPM/tone/sample rate
│
├── decoder.py             # DSP-based CW decoder — bandpass filter, Otsu threshold,
│                          #   KMeans dot/dash clustering, spell correction
│
├── generator.py           # Noisy test signal synthesizer — configurable noise,
│                          #   timing jitter, fading, and packet loss
│
├── utils.py               # Morse code dictionary (A–Z, 0–9, punctuation, prosigns)
│
├── sentinel_protocols.md  # Operations manual — agent identity, station roster,
│                          #   frequency plan, precedence rules, CW procedure,
│                          #   message templates, and timeout rules
│
├── LICENSE                # MIT License
├── README.md              # Project documentation (this file)
└── .gitignore             # Git ignore rules
```

### Runtime Directories (auto-created in `%TEMP%\SignalSentinel\`)

```
%TEMP%\SignalSentinel\
├── incoming_cw/           # Watched by watchdog — drop a WAV here to trigger the pipeline
├── outgoing_cw/           # Agent-generated CW reply WAVs (send_cw_reply, relay_message)
└── cw_net_events.db       # SQLite database (events + stats tables, WAL mode)
```

---

<a id="getting-started"></a>

## 🚀 Getting Started

### Prerequisites

| Requirement | Details |
|-------------|---------|
| Python | 3.10 or higher |
| Ollama | [Install Ollama](https://ollama.ai) — local LLM inference server |
| Gemma 4 | Pull the model: `ollama pull gemma4:e4b` |

### Installation

```bash
# Clone the repository
git clone https://github.com/arkaghosh2005/SignalSentinel.git
cd SignalSentinel

# Install Python dependencies
pip install streamlit streamlit-autorefresh pandas watchdog langchain-core langgraph langchain-ollama scipy numpy scikit-learn pyspellchecker

# Start the Ollama server (in a separate terminal)
ollama serve

# Pull the Gemma 4 model (one-time)
ollama pull gemma4:e4b
```

### Running the App

```bash
streamlit run app.py
```

The dashboard opens at `http://localhost:8501`. The agent + watchdog pipeline starts automatically.

---

<a id="application-flow"></a>

## 📱 Application Flow

```
                    SignalSentinel Pipeline
                    ═══════════════════════

    ┌─────────────────────────────────────────────────────┐
    │  📻 Sidebar: Send Test CW 📻                       │
    │  (User inputs text, adjusts Tone/Noise/Amplitude,   │
    │   selects Frequency → generate_morse() → WAV)       │
    └─────────────────────────┬───────────────────────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────────┐
    │  📂 incoming_cw/ (Filesystem) 📂                   │
    │  Watchdog monitors for new .wav files               │
    └─────────────────────────┬───────────────────────────┘
                              │ on_created event
                              ▼
    ┌─────────────────────────────────────────────────────┐
    │  🔊 CW Decoder (decoder.py) 🔊                     │
    │  WAV → Bandpass Filter → Envelope → Otsu Threshold  │
    │  → KMeans (dot vs dash) → Morse → Text → Spellfix   │
    └─────────────────────────┬───────────────────────────┘
                              │ decoded text
                              ▼
    ┌─────────────────────────────────────────────────────┐
    │  🤖 Gemma 4 Agent (LangGraph) 🤖                   │
    │  SystemMessage: sentinel_protocols.md               │
    │  HumanMessage: decoded CW text + Frequency          │
    │  tool_choice="any" → forced tool call               │
    └─────────────────────────┬───────────────────────────┘
                              │ exactly one tool call
                              ▼
    ┌─────────────────────────────────────────────────────┐
    │  🛠️ Tool Execution (one of four) 🛠️                │
    │                                                     │
    │  📤 send_cw_reply 📤  → encoder.py → outgoing WAV  │
    │  🔁 relay_message 🔁  → encoder.py → outgoing WAV  │
    │  🚫 record_no_contact 🚫 → no transmission         │
    │  🗒️ log_only 🗒️       → log only, no transmission  │
    └─────────────────────────┬───────────────────────────┘
                              │ result + event logged
                              ▼
    ┌─────────────────────────────────────────────────────┐
    │ 📡 Streamlit Dashboard (auto-refresh every 2s) 📡  │
    │                                                     │
    │  ┌──────────────┐  ┌───────────────────────┐        │
    │  │ Metrics Row  │  │ Precedence Counters   │        │
    │  └──────────────┘  └───────────────────────┘        │
    │  ┌──────────────┐  ┌───────────────────────┐        │
    │  │ Incoming     │  │ Latest Communications │        │
    │  │ Messages     │  │ (with audio playback) │        │
    │  └──────────────┘  └───────────────────────┘        │
    │  ┌──────────────────────────────────────────┐       │
    │  │ 🧠 Agent Reasoning  │ 🗂️ Full Event Log │       │
    │  └──────────────────────────────────────────┘       │
    └─────────────────────────────────────────────────────┘
```

---

<a id="sponsor"></a>

## 💖 Sponsor

If you find SignalSentinel helpful and would like to support its continued development, consider sponsoring!

<div align="center">

[![GitHub Sponsors](https://img.shields.io/badge/Sponsor%20on-GitHub-ea4aaa?style=for-the-badge&logo=github-sponsors&logoColor=white)](https://github.com/sponsors/arkaghosh2005)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A-Coffee-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=white)](https://buymeacoffee.com/arkaghosh2005)

</div>

Your support helps cover development time and enables new features. Every contribution is greatly appreciated! ☕✨

---

<a id="license"></a>

## 📄 License

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.

---

<div align="center">

Made with ❤️ using Python, Streamlit, LangGraph and Gemma 4 by **Arka Ghosh, Ankita Roy, Alapan Basu and Anirban Mahata**

</div>