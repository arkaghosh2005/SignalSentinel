from __future__ import annotations

import functools
import inspect
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import Annotated, TypedDict

from encoder import text_to_morse_wav
from generator import generate_morse


import tempfile as _tmpmod
_TEMP_BASE = Path(_tmpmod.gettempdir()) / "SignalSentinel"
_TEMP_BASE.mkdir(exist_ok=True)

INCOMING_DIR = _TEMP_BASE / "incoming_cw"
OUTGOING_DIR = _TEMP_BASE / "outgoing_cw"
DB_PATH = _TEMP_BASE / "cw_net_events.db"
SENTINEL_PROTOCOLS_PATH = Path("./sentinel_protocols.md")
MODEL_ID = "gemma4:e4b"

REFRESH_SECONDS = 2
FEED_ROWS = 15
FULL_LOG_ROWS = 200


# ==========================================================================
# Event logging
# ==========================================================================

#: Every counter shown on the dashboard's top metrics row lives here as a
#: row in the `stats` table, keyed by these names. Pre-seeded to 0 at
#: schema init so reads never have to special-case a missing key.
#:
#: There is no "agent_gave_up" counter anymore: the agent is bound with
#: tool_choice="any", so "the model responded without calling a tool" is
#: no longer a reachable outcome to track.
STAT_KEYS = [
    "messages_in",
    "replies_out",
    "relays",
    "no_contact",
    "logged_only",
    "errors",
    "precedence_emergency",
    "precedence_priority",
    "precedence_routine",
]


def _stat_keys_for(event_type: str, status: str, payload: dict) -> list[str]:
    """Which `stats` counters a given log() call should bump by 1.

    Errors are counted separately and don't also bump the "success" counter
    for that event type -- e.g. a failed send_cw_reply increments "errors",
    not "replies_out".
    """
    if status == "error":
        return ["errors"]

    if event_type == "message_detected":
        return ["messages_in"]
        
    keys = []
    if event_type == "send_cw_reply":
        keys.append("replies_out")
    elif event_type == "relay_message":
        keys.append("relays")
    elif event_type == "record_no_contact":
        keys.append("no_contact")
    elif event_type == "log_only":
        keys.append("logged_only")
        
    precedence = str((payload or {}).get("precedence", "")).upper()
    if precedence == "EMERGENCY":
        keys.append("precedence_emergency")
    elif precedence == "PRIORITY":
        keys.append("precedence_priority")
    elif precedence == "ROUTINE":
        keys.append("precedence_routine")
        
    return keys


class EventLogger:
    """Thread-safe SQLite event log.

    Two tables:
        - `events`: one row per event (message intake, agent invocation,
          tool call, transmit, error -- everything), for the feeds/full log.
        - `stats`: running totals per STAT_KEYS, updated atomically in the
          same transaction as the event insert. The dashboard's top metrics
          read straight from here, so they're always true running counts --
          not something recomputed from whatever slice of `events` happens
          to be loaded.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._local = threading.local()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            self._local.conn = conn
        return self._local.conn

    def _init_schema(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                event_type TEXT NOT NULL,
                station TEXT,
                frequency TEXT,
                status TEXT NOT NULL,
                payload TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stats (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.executemany(
            "INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)",
            [(k,) for k in STAT_KEYS],
        )
        conn.commit()
        conn.close()

    def log(
        self,
        event_type: str,
        status: str = "ok",
        station: Optional[str] = None,
        frequency: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT INTO events (ts, event_type, station, frequency, status, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                event_type,
                station,
                frequency,
                status,
                json.dumps(payload or {}, default=str),
            ),
        )
        for key in _stat_keys_for(event_type, status, payload or {}):
            conn.execute(
                "INSERT INTO stats (key, value) VALUES (?, 1) "
                "ON CONFLICT(key) DO UPDATE SET value = value + 1",
                (key,),
            )
        conn.commit()  # events insert + stats bump commit together, atomically

    def read_stats(self) -> dict:
        """Current running totals, straight from the `stats` table."""
        conn = self._conn()
        rows = conn.execute("SELECT key, value FROM stats").fetchall()
        totals = {k: 0 for k in STAT_KEYS}
        totals.update(dict(rows))
        return totals

    def read_events(self, limit: int = FULL_LOG_ROWS) -> pd.DataFrame:
        """Read the most recent events from the DB."""
        empty = pd.DataFrame(columns=["id", "ts", "event_type", "station", "frequency", "status", "payload"])
        if not self.db_path.exists():
            return empty
        conn = self._conn()
        try:
            df = pd.read_sql_query("SELECT * FROM events ORDER BY id DESC LIMIT ?", conn, params=(limit,))
        except pd.errors.DatabaseError:
            return empty
        if df.empty:
            return df
        df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        df["payload_dict"] = df["payload"].apply(_safe_json)
        return df

    def clear_all(self) -> None:
        """Clear all events, reset stats to 0, and delete all audio files."""
        conn = self._conn()
        conn.execute("DELETE FROM events")
        conn.execute("UPDATE stats SET value = 0")
        conn.commit()
        for directory in (INCOMING_DIR, OUTGOING_DIR):
            if directory.exists():
                for file_path in directory.glob("*.wav"):
                    try:
                        file_path.unlink()
                    except OSError:
                        pass


_EVENT_LOGGER = EventLogger()


def log_event(event_type: str, summarize: Optional[Callable[[dict, Any], dict]] = None):
    """Decorator: logs ONE clean row to SQLite per call (plus one row if it
    raises) -- no raw args/kwargs/self dump.

    `summarize(inputs, result) -> dict` may return:
        - "station":   short string for the `station` column
        - "frequency": short string for the `frequency` column
        - "payload":   small dict of the fields worth keeping

    Put this UNDER @tool (i.e. apply it first) so LangChain's tool schema
    inference still sees the original signature and docstring:

        @tool
        @log_event("send_cw_reply", _summarize_reply)
        def send_cw_reply(...): ...
    """

    def decorator(func: Callable) -> Callable:
        sig = inspect.signature(func)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            bound = sig.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            inputs = {k: v for k, v in bound.arguments.items() if k != "self"}

            try:
                result = func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - log and re-raise
                _EVENT_LOGGER.log(event_type, status="error", payload={"error": str(exc)})
                raise

            summary = summarize(inputs, result) if summarize else {}
            _EVENT_LOGGER.log(
                event_type,
                status="ok",
                station=summary.get("station"),
                frequency=summary.get("frequency"),
                payload=summary.get("payload", {}),
            )
            return result

        return wrapper

    return decorator


# ---- per-function log summarizers ---------------------------------------

def _summarize_message_detected(inputs: dict, result: Any) -> dict:
    path = inputs.get("path")
    return {
        "payload": {
            "file": Path(path).name if path else None,
            "decoded_text": result,
            # Absolute path to the incoming WAV so the dashboard can play it
            # back next to the decoded text.
            "wav_path": str(Path(path).resolve()) if path else None,
        }
    }


def _summarize_reply(inputs: dict, result: Any) -> dict:
    freq = inputs.get("frequency")
    _content, wav_path = result if isinstance(result, tuple) else (result, None)
    return {
        "station": inputs.get("station"),
        "frequency": freq,
        # wav_path is the outgoing WAV this tool call actually generated, so
        # the dashboard can play back exactly what would have gone out over
        # the air for this reply.
        "payload": {"text": inputs.get("text"), "wav_path": wav_path, "precedence": inputs.get("precedence")},
    }


def _summarize_relay(inputs: dict, result: Any) -> dict:
    origin, dest = inputs.get("origin_station"), inputs.get("destination_station")
    freq = inputs.get("frequency")
    _content, wav_path = result if isinstance(result, tuple) else (result, None)
    return {
        "station": f"{origin} -> {dest}" if origin or dest else None,
        "frequency": freq,
        "payload": {"text": inputs.get("text"), "wav_path": wav_path, "precedence": inputs.get("precedence")},
    }


def _summarize_no_contact(inputs: dict, result: Any) -> dict:
    freq = inputs.get("attempted_frequency")
    return {
        "station": inputs.get("station"),
        "frequency": freq,
        "payload": {"attempts": inputs.get("attempts"), "precedence": inputs.get("precedence")},
    }


def _summarize_log_only(inputs: dict, result: Any) -> dict:
    return {
        "station": inputs.get("station"),
        "frequency": inputs.get("frequency"),
        "payload": {"summary": inputs.get("summary"), "precedence": inputs.get("precedence")}
    }


def _summarize_agent_invocation(inputs: dict, result: Any) -> dict:
    """Captures the agent's decision: which tool it called plus any
    reasoning text from the LLM response."""
    decoded_message = inputs.get("decoded_message") or ""
    payload: dict = {"message_preview": decoded_message[:80]}
    
    # result is the list of messages from the graph execution
    messages = result if isinstance(result, list) else [result]
    for msg in messages:
        if getattr(msg, "type", "") == "ai":
            content = getattr(msg, "content", "")
            if content:
                payload["reasoning"] = content[:300]
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                payload["tools_called"] = [tc.get("name") for tc in tool_calls]
                
    return {"payload": payload}


# ==========================================================================
# CW decode / transmit
# ==========================================================================

class CWRadio:
    """Decode via your own decoder.py; transmit by generating a CW WAV file
    (no real radio/rig control yet)."""

    def __init__(self, outgoing_dir: Path = OUTGOING_DIR):
        self.outgoing_dir = outgoing_dir
        self.outgoing_dir.mkdir(parents=True, exist_ok=True)

    def decode_file(self, wav_path: Path) -> str:
        from decoder import decode_morse

        return decode_morse(str(wav_path), correct_spelling=True)

    def transmit(self, text: str, frequency: Optional[str] = None) -> Path:
        timestamp = int(time.time())
        freq_tag = f"_{frequency.replace(' ', '')}" if frequency else ""
        out_path = self.outgoing_dir / f"TX_{timestamp}{freq_tag}.wav"
        return text_to_morse_wav(text, out_path)


_RADIO: Optional[CWRadio] = None


def get_radio() -> CWRadio:
    global _RADIO
    if _RADIO is None:
        _RADIO = CWRadio()
    return _RADIO


# ==========================================================================
# Tools (function-calling surface for the Gemma 4 agent)
# ==========================================================================

@tool(response_format="content_and_artifact")
@log_event("send_cw_reply", _summarize_reply)
def send_cw_reply(text: str, station: str, frequency: str, precedence: str) -> tuple[str, str]:
    """Generate a CW reply to a station for a given frequency (e.g., '7.030 MHz') and precedence."""
    wav_path = get_radio().transmit(text, frequency)
    content = f"Generated CW reply WAV for {station} ({frequency}): {wav_path}"
    return content, str(wav_path)


@tool(response_format="content_and_artifact")
@log_event("relay_message", _summarize_relay)
def relay_message(text: str, origin_station: str, destination_station: str, frequency: str, precedence: str) -> tuple[str, str]:
    """Relay a message from one station to another for a given frequency (e.g., '7.030 MHz') and precedence."""
    payload = f"{origin_station} DE RELAY TO {destination_station} MSG: {text}"
    wav_path = get_radio().transmit(payload, frequency)
    content = f"Generated relay WAV from {origin_station} to {destination_station} ({frequency}): {wav_path}"
    return content, str(wav_path)


@tool
@log_event("record_no_contact", _summarize_no_contact)
def record_no_contact(station: str, attempted_frequency: str, attempts: int, precedence: str) -> str:
    """Log that a station could not be reached after N attempts. Does not transmit anything."""
    return f"No contact with {station} after {attempts} attempts on {attempted_frequency}"


@tool
@log_event("log_only", _summarize_log_only)
def log_only(summary: str, precedence: str, station: Optional[str] = None, frequency: Optional[str] = None) -> str:
    """Log a message that requires no reply or relay -- routine status updates, etc."""
    return f"Logged ({precedence}): {summary}"


TOOLS = [send_cw_reply, relay_message, record_no_contact, log_only]


# ==========================================================================
# Gemma 4 agent (LangGraph)
# ==========================================================================

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


class GemmaAgent:
    """
    No retry/nudge loop. The model is bound with `tool_choice="any"`, which
    forces every single completion out of it to be a tool call -- "respond
    with plain text instead" simply isn't a legal output shape anymore, so
    there's nothing to detect, re-prompt for, or give up on. Every message
    handed to `handle_message` therefore always ends in exactly one of the
    four tools firing (and, for anything that warrants it, a real
    `send_cw_reply` / `relay_message` going out over CW).
    """

    def __init__(self, model_id: str = MODEL_ID, sentinel_protocols_path: Path = SENTINEL_PROTOCOLS_PATH):
        self.model_id = model_id
        self.system_prompt = self._load_sentinel_protocols(sentinel_protocols_path)
        self.llm = self._load_model()
        # tool_choice="any" (a.k.a. "required" on some providers) forces the
        # model to emit a tool call on every turn -- it is not permitted to
        # answer with plain text. This is what removes the need for any
        # nudge/retry/give-up machinery: a tool-less response is no longer
        # a reachable state.
        self.llm_with_tools = self.llm.bind_tools(TOOLS, tool_choice="any")
        self.graph = self._build_graph()

    @staticmethod
    def _load_sentinel_protocols(path: Path) -> str:
        if not path.exists():
            raise FileNotFoundError(
                f"Ops manual not found at {path} -- the agent needs this for "
                f"station/frequency/precedence context."
            )
        return path.read_text()

    def _load_model(self):
        """Loads Gemma 4 via Ollama. Requires `ollama pull gemma4:e2b-it-qat`
        and the Ollama server running locally (default
        http://localhost:11434), on a version recent enough to support
        forced tool_choice."""
        from langchain_ollama import ChatOllama

        return ChatOllama(model=self.model_id, temperature=0.2, num_ctx=8192)

    def _agent_node(self, state: AgentState) -> dict:
        messages = [SystemMessage(content=self.system_prompt)] + state["messages"]
        response = self.llm_with_tools.invoke(messages)
        return {"messages": [response]}

    @staticmethod
    def _route_after_agent(state: AgentState) -> str:
        """With tool_choice="any" forcing a tool call on every agent turn,
        the only two reachable outcomes are:

            - this turn has tool_calls          -> run them ("tools")
            - this turn has no tool_calls, which
              only happens on the wrap-up turn
              *after* a tool has already run     -> END

        There is no nudge/give-up branch: a tool-less first turn is no
        longer something the model is allowed to produce.
        """
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return END

    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("agent", self._agent_node)
        graph.add_node("tools", ToolNode(TOOLS))
        graph.set_entry_point("agent")
        graph.add_conditional_edges(
            "agent",
            self._route_after_agent,
            {"tools": "tools", END: END},
        )
        graph.add_edge("tools", END)
        return graph.compile()

    @log_event("agent_invocation", _summarize_agent_invocation)
    def handle_message(self, decoded_message: str, frequency: str = "7.030 MHz") -> Any:
        """Hand a single decoded CW message straight to the agent.

        Called once per decoded message -- no batching/coalescing. Each
        call is independent, so messages that arrive close together in
        time are still reasoned about (and replied to, if needed) one at
        a time, in the order they were detected.

        Guaranteed to end with exactly one tool call having fired (the
        model has no other option), so every decoded message always
        produces a logged action -- a reply/relay for anything that
        warrants one, or record_no_contact / log_only otherwise.
        """
        prompt = (
            f"You received a new decoded CW message:\n"
            f"- Message: {decoded_message}\n"
            f"- Frequency: {frequency}\n\n"
            "Decide precedence and required action per the ops manual's "
            "decision rules, then take that action using the available "
            "tools. You must call exactly one tool -- send_cw_reply, "
            "relay_message, record_no_contact, or log_only -- to handle "
            "this message."
        )
        result = self.graph.invoke({"messages": [HumanMessage(content=prompt)]})
        return result["messages"]


# ==========================================================================
# Folder watcher
# ==========================================================================

class _NewFileHandler(FileSystemEventHandler):
    def __init__(self, on_new_file: Callable[[Path], None]):
        self._on_new_file = on_new_file
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def on_created(self, event):
        if event.is_directory:
            return
        path = event.src_path
        now = time.time()
        with self._lock:
            # Deduplicate: ignore if we saw the same path within the last 5s
            if path in self._seen and now - self._seen[path] < 5.0:
                return
            self._seen[path] = now
            # Prune old entries to avoid unbounded growth
            self._seen = {p: t for p, t in self._seen.items() if now - t < 10.0}
        self._on_new_file(Path(path))

    def on_modified(self, event):
        # Windows watchdog fires modified events for new files too;
        # ignore them entirely since on_created already handles it.
        pass


class CWFolderWatcher:
    """Watches INCOMING_DIR and, for every new file, decodes it and hands
    the decoded text straight to the agent -- one message at a time, no
    batching window.

    The agent call happens on its own background thread (rather than
    inline in the watchdog callback) so that the LLM's inference time
    never blocks the watchdog observer thread from noticing the next
    incoming file. This preserves one-at-a-time *processing* by the agent
    while still letting detection (and the `message_detected` /
    `messages_in` bump) happen immediately and independently for every
    file, in arrival order.
    """

    def __init__(self, incoming_dir: Path, agent: GemmaAgent):
        self.incoming_dir = incoming_dir
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.agent = agent
        self._observer = Observer()

    @log_event("message_detected", _summarize_message_detected)
    def _handle_new_file(self, path: Path) -> str:
        time.sleep(0.5)  # let the writer finish flushing the file
        decoded = get_radio().decode_file(path)
        
        # Extract frequency from filename if present (e.g. text_7.030MHz_12345.wav)
        freq = "7.030 MHz"
        for part in path.stem.split("_"):
            if part.endswith("MHz"):
                freq = part.replace("MHz", " MHz")
                break
                
        threading.Thread(target=self.agent.handle_message, args=(decoded, freq), daemon=True).start()
        return decoded

    def start(self) -> None:
        handler = _NewFileHandler(self._handle_new_file)
        self._observer.schedule(handler, str(self.incoming_dir), recursive=False)
        # Daemonize so `streamlit run` can actually exit on Ctrl+C instead
        # of hanging on a live non-daemon watchdog thread.
        self._observer.daemon = True
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()


# ==========================================================================
# Pipeline bootstrap -- runs exactly once per process via st.cache_resource
# ==========================================================================

@dataclass
class Pipeline:
    agent: Optional[GemmaAgent] = None
    watcher: Optional[CWFolderWatcher] = None
    error: Optional[str] = None


@st.cache_resource(show_spinner="Starting CW net pipeline (agent + watchdog)...")
def get_pipeline() -> Pipeline:
    try:
        agent = GemmaAgent()
        watcher = CWFolderWatcher(INCOMING_DIR, agent)
        watcher.start()
        return Pipeline(agent=agent, watcher=watcher)
    except Exception as exc:  # noqa: BLE001 - surface in the UI, don't crash the app
        return Pipeline(error=str(exc))


# ==========================================================================
# Dashboard: data access
# ==========================================================================

st.set_page_config(page_title="SignalSentinel", page_icon="📡", layout="wide")


def _safe_json(raw) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}


def _fmt_time(ts) -> str:
    if pd.isna(ts):
        return "-"
    return ts.strftime("%H:%M:%S")


# ==========================================================================
# Dashboard: render functions
# ==========================================================================

def render_stats(stats: dict) -> None:
    """`stats` is the dict from load_stats() -- true running totals from
    the DB's `stats` table, not a count over whatever window of `events`
    happens to be loaded."""
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("📥 Messages In", stats["messages_in"])
    c2.metric("📤 Replies Sent", stats["replies_out"])
    c3.metric("🔁 Relays", stats["relays"])
    c4.metric("🚫 No Contact", stats["no_contact"])
    c5.metric("🗒️ Logged Only", stats["logged_only"])
    c6.metric("⚠️ Errors", stats["errors"])

    p1, p2, p3 = st.columns(3)
    p1.metric("🔴 EMERGENCY", stats["precedence_emergency"])
    p2.metric("🟠 PRIORITY", stats["precedence_priority"])
    p3.metric("🟢 ROUTINE", stats["precedence_routine"])


def _render_wav_player(wav_path: Optional[str]) -> None:
    """Render an inline audio player for a logged WAV path, if it still
    exists on disk. Silently does nothing if there's no path or the file
    is missing (e.g. an old event whose WAV was cleaned up) -- a feed
    entry should still render even if audio can't be replayed.

    Not passing a `key` to st.audio deliberately -- audio/image/video
    elements aren't stateful widgets in Streamlit, so repeated calls don't
    raise DuplicateWidgetID the way repeated st.button/st.text_input calls
    would, and skipping `key` keeps this compatible with older Streamlit
    versions that don't accept it on st.audio.
    """
    if not wav_path:
        return
    path = Path(wav_path)
    if not path.exists():
        st.caption(f"🔇 Audio unavailable ({path.name})")
        return
    st.audio(str(path))


def render_incoming_feed(df: pd.DataFrame) -> None:
    st.subheader("📥 Newly Detected Messages")
    incoming = df[df["event_type"] == "message_detected"].head(FEED_ROWS)
    if incoming.empty:
        st.caption("No messages detected yet.")
        return
    for _, row in incoming.iterrows():
        payload = row["payload_dict"]
        text = payload.get("decoded_text") or "(no text decoded)"
        file = payload.get("file", "")
        status_icon = "✅" if row["status"] == "ok" else "❌"
        st.markdown(f"**{_fmt_time(row['ts'])}** {status_icon} `{file}` → **{text}**")
        _render_wav_player(payload.get("wav_path"))


def render_comms_feed(df: pd.DataFrame) -> None:
    st.subheader("📡 Latest Communications")
    comms = df[df["event_type"].isin(
        ["send_cw_reply", "relay_message", "record_no_contact", "log_only"]
    )].head(FEED_ROWS)
    if comms.empty:
        st.caption("No outbound activity yet.")
        return

    icons = {
        "send_cw_reply": "📤",
        "relay_message": "🔁",
        "record_no_contact": "🚫",
        "log_only": "🗒️",
    }
    for _, row in comms.iterrows():
        payload = row["payload_dict"]
        icon = icons.get(row["event_type"], "•")
        station = row["station"] if pd.notna(row["station"]) else "-"
        freq = row["frequency"] if pd.notna(row["frequency"]) else ""

        if row["event_type"] in ("send_cw_reply", "relay_message"):
            detail = payload.get("text", "")
        elif row["event_type"] == "record_no_contact":
            detail = f"after {payload.get('attempts', '?')} attempts"
        else:  # log_only
            detail = f"[{payload.get('precedence', '-')}] {payload.get('summary', '')}"

        st.markdown(f"**{_fmt_time(row['ts'])}** {icon} **{station}** {freq} — {detail}")
        if row["event_type"] in ("send_cw_reply", "relay_message"):
            # record_no_contact and log_only never transmit anything, so
            # there's no WAV to play back for those.
            _render_wav_player(payload.get("wav_path"))


def render_full_log(df: pd.DataFrame) -> None:
    with st.expander(f"🗂️ Full Event Log (last {len(df)} rows)"):
        if df.empty:
            st.caption("No events logged yet.")
            return
        display_df = df[["ts", "event_type", "station", "frequency", "status", "payload"]].copy()
        display_df["ts"] = display_df["ts"].apply(_fmt_time)
        st.dataframe(display_df, width='stretch', hide_index=True)
        # CSV export
        csv = display_df.to_csv(index=False)
        st.download_button("📥 Download CSV", csv, "signal_sentinel_events.csv", "text/csv")


def render_sidebar(pipeline: Pipeline) -> None:
    # ---- Status indicators at the top ----
    import urllib.request
    import urllib.error

    if pipeline.error:
        st.sidebar.error(f"🔴 Pipeline: {pipeline.error}")
    else:
        st.sidebar.success("🟢 Pipeline: running")

    try:
        urllib.request.urlopen("http://localhost:11434/", timeout=0.2)
        st.sidebar.success("🟢 LLM: Available")
    except (urllib.error.URLError, TimeoutError, OSError):
        st.sidebar.error("🔴 LLM: Offline")

    st.sidebar.divider()

    # ---- Send test CW ----
    st.sidebar.header("📡 Send Test CW")
    message_text = st.sidebar.text_input("Message", value="VU2NCS DE VU2RLY NEED SUPPLIES K")
    message_text = message_text.upper()
    tone_hz = st.sidebar.slider("CW Tone Frequency (Hz)", min_value=400, max_value=1000, value=700, step=50)
    
    # Extract just the "7.030 MHz" part from the selectbox
    rx_freq_display = st.sidebar.selectbox(
        "Simulated Radio Frequency", 
        [
            "7.030 MHz (Local Tactical)", 
            "7.045 MHz (Local Fallback)", 
            "7.010 MHz (Emergency)", 
            "14.010 MHz (Emergency Fallback)", 
            "3.560 MHz (Regional Relay)", 
            "3.580 MHz (Regional Fallback)"
        ]
    )
    rx_freq = rx_freq_display.split(" ")[0] + " MHz"

    if st.sidebar.button("Send", type="primary", use_container_width=True):
        if not message_text.strip():
            st.sidebar.error("Enter a message first.")
        else:
            try:
                INCOMING_DIR.mkdir(parents=True, exist_ok=True)
                path = generate_morse(
                    message_text,
                    amplitude=0.8,
                    noise_power=0.2,
                    pitch=tone_hz,
                    freq_mhz=rx_freq,
                    output_dir=str(INCOMING_DIR),
                )
                st.toast(f"✅ CW generated: {Path(path).name}", icon="📡")
            except Exception as exc:  # noqa: BLE001
                st.sidebar.error(f"Failed: {exc}")

    st.sidebar.divider()

    # ---- Admin ----
    st.sidebar.subheader("🛠️ Admin")
    if st.sidebar.button("🗑️ Clear Database", use_container_width=True):
        _EVENT_LOGGER.clear_all()
        st.toast("🗑️ Database cleared!", icon="✅")
        st.rerun()

    st.sidebar.caption(f"Auto-refresh: {REFRESH_SECONDS}s")
    st.sidebar.caption(f"DB: `{DB_PATH.resolve()}`")


def render_agent_reasoning(df: pd.DataFrame) -> None:
    """Show the last few agent decisions with reasoning."""
    with st.expander("🧠 Agent Reasoning (last decisions)"):
        agent_events = df[df["event_type"] == "agent_invocation"].head(5)
        if agent_events.empty:
            st.caption("No agent decisions recorded yet.")
            return
        for _, row in agent_events.iterrows():
            payload = row["payload_dict"]
            msg = payload.get("message_preview", "(unknown)")
            tools = payload.get("tools_called", [])
            tool_name = tools[0] if tools else "(none)"
            reasoning = payload.get("reasoning", "")
            with st.container(border=True):
                st.markdown(f"**{_fmt_time(row['ts'])}** — Received: `{msg}`")
                st.markdown(f"🛠️ Tool called: **{tool_name}**")
                if reasoning:
                    st.caption(reasoning)


# ==========================================================================
# Page
# ==========================================================================

def main() -> None:
    st_autorefresh(interval=REFRESH_SECONDS * 1000, key="dashboard_refresh")

    pipeline = get_pipeline()
    render_sidebar(pipeline)

    st.title("📡 SignalSentinel")
    st.caption(f"Last refreshed {datetime.now().strftime('%H:%M:%S')}")

    if pipeline.error:
        st.error(
            f"⚠️ Pipeline failed to start: {pipeline.error}\n\n"
            "The dashboard still reads existing data, but nothing new "
            "will be processed until this is fixed."
        )

    df = _EVENT_LOGGER.read_events()
    stats = _EVENT_LOGGER.read_stats()

    render_stats(stats)
    st.divider()

    col_left, col_right = st.columns(2)
    with col_left:
        render_incoming_feed(df)
    with col_right:
        render_comms_feed(df)

    st.divider()
    render_agent_reasoning(df)
    render_full_log(df)


if __name__ == "__main__":
    main()