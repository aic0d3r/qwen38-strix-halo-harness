#!/usr/bin/env python3
"""Read-only reader for GitHub Copilot CLI session-state files.

Copilot CLI stores per-session data under ``~/.copilot/session-state/<uuid>/``
as three optional files:

  events.jsonl     — event stream (primary token source via session.shutdown)
  messages.json    — message history (content-side estimate)
  checkpoint.json  — compaction checkpoint (optional token figure)

This module parses those files to power cost pass-through, quality scoring,
and dashboard rollup in Token Optimizer's Copilot adapter.

Design constraints (mirroring hermes_state.py / codex_state.py):

- **Pure stdlib only.**  No Copilot imports.
- **Strictly read-only.**  Never writes any file.
- **No hardcoded user paths.**  Copilot home comes from
  ``runtime_env.copilot_home()``; callers may pass an explicit ``home``
  override.
- **Schema-defensive.**  The events.jsonl schema is community-reverse-engineered
  and changes weekly.  Unknown event types are silently skipped.  Missing keys
  at any nesting level fall back to safe defaults.  The parser never crashes on
  shape drift.
- **Streaming JSONL.**  Lines are processed one at a time; the whole file is
  never loaded into memory.  Lines > 2 MB are skipped.  Processing is capped
  at 500,000 events per file.

Data model (per ``read_session``):
  The ``models`` dict accumulates token totals from ``session.shutdown``
  modelMetrics when present.  For crash/incomplete sessions without a shutdown
  event, totals are filled from an in-flight tally file when available.  The
  ``output_tokens_observed`` field always reflects the sum of persisted
  ``assistant.message.outputTokens`` events, independent of the shutdown totals.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SESSION_STATE_DIR = "session-state"
_TO_DIR = "token-optimizer"
_INFLIGHT_PREFIX = "inflight-"
_EVENTS_FILE = "events.jsonl"
_MESSAGES_FILE = "messages.json"
_CHECKPOINT_FILE = "checkpoint.json"

_MAX_LINE_BYTES = 2 * 1024 * 1024  # 2 MB
_MAX_EVENTS_PER_FILE = 500_000

# Event type strings.
_EV_SESSION_START = "session.start"
_EV_SESSION_SHUTDOWN = "session.shutdown"
_EV_ASSISTANT_MESSAGE = "assistant.message"
_EV_COMPACTION_START = "session.compaction_start"
_EV_COMPACTION_COMPLETE = "session.compaction_complete"
_EV_TOOL_START = "tool.execution_start"
_EV_TOOL_COMPLETE = "tool.execution_complete"
_EV_USER_MESSAGE = "user.message"
_EV_MODEL_CHANGE = "session.model_change"
_EV_SUBAGENT_STARTED = "subagent.started"
_EV_SUBAGENT_COMPLETED = "subagent.completed"

# Known event types we intentionally process.
_KNOWN_EVENT_TYPES = frozenset(
    {
        _EV_SESSION_START,
        _EV_SESSION_SHUTDOWN,
        _EV_ASSISTANT_MESSAGE,
        _EV_COMPACTION_START,
        _EV_COMPACTION_COMPLETE,
        _EV_TOOL_START,
        _EV_TOOL_COMPLETE,
        _EV_USER_MESSAGE,
        _EV_MODEL_CHANGE,
        _EV_SUBAGENT_STARTED,
        _EV_SUBAGENT_COMPLETED,
    }
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value)) if value is not None else default
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_float(value: Any) -> Optional[float]:
    # Contract: returns None on failure (timestamps need absent-vs-zero).
    # copilot_session._safe_float intentionally differs: returns a default.
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ts_seconds(value: Any) -> Optional[float]:
    """Normalize a timestamp to epoch-seconds.

    Copilot hook payloads carry epoch-MILLISECOND timestamps (verified:
    1773370259963); events.jsonl may use either scale. Anything past the year
    ~33658 in seconds (1e12) is treated as milliseconds.
    """
    ts = _safe_float(value)
    if ts is None:
        return None
    if ts > 1e12:
        return ts / 1000.0
    return ts


def _empty_model_entry() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "api_calls": 0,
    }


def _parse_model_metrics(raw_metrics: Any) -> dict:
    """Parse modelMetrics from session.shutdown into our models dict.

    Tolerates two shapes:
      Nested:  {model: {usage: {inputTokens, outputTokens, ...}, apiCalls}}
      Flat:    {model: {inputTokens, outputTokens, ..., apiCalls}}

    Returns a dict keyed by model name with our canonical token field names.
    Never raises.
    """
    if not isinstance(raw_metrics, dict):
        return {}
    result: dict = {}
    for model_name, model_data in raw_metrics.items():
        if not isinstance(model_data, dict):
            continue
        entry = _empty_model_entry()

        # Detect nested vs flat by checking for a "usage" sub-dict.
        usage = model_data.get("usage")
        if isinstance(usage, dict):
            token_src = usage
        else:
            # Flat: token fields live directly on model_data.
            token_src = model_data

        entry["input_tokens"] = _safe_int(token_src.get("inputTokens"))
        entry["output_tokens"] = _safe_int(token_src.get("outputTokens"))
        entry["cache_read_tokens"] = _safe_int(
            token_src.get("cacheReadTokens", token_src.get("cacheCreationInputTokens", 0))
        )
        entry["cache_write_tokens"] = _safe_int(
            token_src.get("cacheWriteTokens", token_src.get("cacheCreationTokens", 0))
        )
        # apiCalls may be at model_data level regardless of nested/flat.
        entry["api_calls"] = _safe_int(model_data.get("apiCalls", model_data.get("api_calls", 0)))
        result[model_name] = entry
    return result


def _stream_events(events_path: Path):
    """Yield parsed event dicts from a JSONL file, one per valid line.

    Skips:
      - lines that fail JSON decode
      - lines > _MAX_LINE_BYTES bytes
      - lines missing a ``type`` (or ``event``) key
      - lines beyond _MAX_EVENTS_PER_FILE

    Tolerates both ``"type"`` and ``"event"`` as the event-type key, matching
    the two variants observed in community research.
    """
    try:
        count = 0
        with events_path.open("rb") as fh:
            for raw_line in fh:
                if count >= _MAX_EVENTS_PER_FILE:
                    logger.debug(
                        "[copilot_state] %s: hit %d-event cap, stopping",
                        events_path,
                        _MAX_EVENTS_PER_FILE,
                    )
                    break
                if len(raw_line) > _MAX_LINE_BYTES:
                    logger.debug(
                        "[copilot_state] %s: skipping oversized line (%d bytes)",
                        events_path,
                        len(raw_line),
                    )
                    continue
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("[copilot_state] %s: skipping malformed JSON line", events_path)
                    continue
                if not isinstance(obj, dict):
                    continue
                # Normalise type key: accept "type" or "event".
                if "type" not in obj and "event" in obj:
                    obj = dict(obj)
                    obj["type"] = obj.pop("event")
                if "type" not in obj:
                    continue
                # Flatten a nested payload object: some emitters put event
                # fields under "attrs" or "data" instead of the top level.
                # Top-level keys win on collision (never overwrite "type").
                for payload_key in ("attrs", "data"):
                    nested = obj.get(payload_key)
                    if isinstance(nested, dict):
                        merged = dict(nested)
                        merged.update(obj)
                        obj = merged
                count += 1
                yield obj
    except OSError as exc:
        logger.debug("[copilot_state] cannot read %s: %s", events_path, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_session_dirs(home: Optional[Path] = None) -> list:
    """Return subdirectories of <copilot_home>/session-state/.

    Each subdirectory is a session UUID directory.  Returns [] when the
    session-state directory does not exist or is not a directory.  Never raises.

    Args:
        home: explicit Copilot home override; defaults to ``copilot_home()``
              from ``runtime_env``.
    """
    if home is None:
        try:
            from runtime_env import copilot_home  # type: ignore[import]

            home = copilot_home()
        except Exception as exc:
            logger.debug("[copilot_state] cannot import runtime_env: %s", exc)
            return []
    ss_dir = home / _SESSION_STATE_DIR
    if not ss_dir.is_dir():
        return []
    try:
        return sorted(
            p for p in ss_dir.iterdir() if p.is_dir()
        )
    except OSError as exc:
        logger.debug("[copilot_state] cannot list %s: %s", ss_dir, exc)
        return []


def read_inflight_tally(session_dir: Optional[Path] = None, *, home: Optional[Path] = None, session_id: Optional[str] = None) -> Optional[dict]:
    """Read the in-flight tally file for a session, if present.

    The tally file lives at:
      <copilot_home>/token-optimizer/inflight-<session_id>.json

    Two calling conventions:
      read_inflight_tally(session_dir)        — derives home + session_id from path
      read_inflight_tally(home=h, session_id=s)

    Schema: {"session_id", "updated_at", "models": {model: {...}}, "tool_calls": int}
    Returns None when the file is absent, malformed, or unreadable.
    """
    if session_dir is not None:
        # Derive: session_dir is <copilot_home>/session-state/<session_id>
        # so home = session_dir.parent.parent
        derived_home = session_dir.parent.parent
        derived_session_id = session_dir.name
        if home is None:
            home = derived_home
        if session_id is None:
            session_id = derived_session_id

    if home is None or session_id is None:
        return None

    # Defense-in-depth: the bridge sanitizes session_id before writing, but a
    # caller could pass a raw id here too. Containment-check the resolved path.
    tally_path = home / _TO_DIR / f"{_INFLIGHT_PREFIX}{session_id}.json"
    try:
        to_dir = (home / _TO_DIR).resolve(strict=False)
        if not tally_path.resolve(strict=False).is_relative_to(to_dir):
            return None
    except (OSError, ValueError):
        return None
    if not tally_path.exists():
        return None
    try:
        raw = tally_path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.debug("[copilot_state] cannot read tally %s: %s", tally_path, exc)
        return None


def read_session(session_dir: Path) -> dict:
    """Parse a single Copilot CLI session directory into a raw session dict.

    Reads events.jsonl, messages.json, and checkpoint.json (all optional /
    missing-tolerant).  When no shutdown event is found, attempts to recover
    totals from an in-flight tally file.

    Returns a dict with keys:
      session_id, start_time, end_time, cwd, version,
      models, total_premium_requests,
      output_tokens_observed, compactions, tool_calls,
      message_count, user_message_count, model_changes,
      subagent_sessions, complete, incomplete_reason,
      content_chars, checkpoint_tokens, data_source
    """
    session_id = session_dir.name

    # Mutable accumulators.
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    cwd: Optional[str] = None
    version: Optional[str] = None
    models: dict = {}
    total_premium_requests: Optional[float] = None
    output_tokens_observed = 0
    compactions: list = []
    # Tool-call correlation: start event first, complete event second.
    _tool_starts: dict = {}  # call_id -> {name, start_ts, args_chars}
    tool_calls: list = []
    message_count = 0
    user_message_count = 0
    model_changes = 0
    subagent_sessions: list = []
    shutdown_seen = False

    # --- Parse events.jsonl ---
    events_path = session_dir / _EVENTS_FILE
    if events_path.exists():
        for ev in _stream_events(events_path):
            ev_type = ev.get("type", "")
            ts = _ts_seconds(ev.get("timestamp"))

            if ev_type not in _KNOWN_EVENT_TYPES:
                # Silently ignore unknown/future event types.
                continue

            if ev_type == _EV_SESSION_START:
                if ts is not None and start_time is None:
                    start_time = ts
                cwd = cwd or (ev.get("cwd") or None)
                version = version or (ev.get("version") or None)

            elif ev_type == _EV_SESSION_SHUTDOWN:
                shutdown_seen = True
                if ts is not None:
                    end_time = ts
                raw_metrics = ev.get("modelMetrics")
                if raw_metrics is not None:
                    models = _parse_model_metrics(raw_metrics)
                prem = _safe_float(ev.get("totalPremiumRequests"))
                if prem is not None:
                    total_premium_requests = prem

            elif ev_type == _EV_ASSISTANT_MESSAGE:
                ot = _safe_int(ev.get("outputTokens"))
                output_tokens_observed += ot
                message_count += 1

            elif ev_type == _EV_USER_MESSAGE:
                user_message_count += 1
                message_count += 1

            elif ev_type == _EV_COMPACTION_START:
                # Store pre-compaction tokens; will be paired with complete event.
                pass  # handled via compaction_complete which carries both fields

            elif ev_type == _EV_COMPACTION_COMPLETE:
                pre = _safe_int(ev.get("preCompactionTokens"))
                post = _safe_int(ev.get("postCompactionTokens"))
                removed = _safe_int(ev.get("tokensRemoved"))
                compactions.append({"pre": pre, "post": post, "removed": removed})

            elif ev_type == _EV_TOOL_START:
                call_id = ev.get("toolCallId") or ev.get("toolCallID") or ""
                tool_name = ev.get("toolName") or ev.get("tool_name") or ""
                args = ev.get("arguments", "")
                args_chars = len(json.dumps(args)) if args else 0
                if call_id:
                    _tool_starts[call_id] = {
                        "name": tool_name,
                        "start_ts": ts,
                        "args_chars": args_chars,
                    }

            elif ev_type == _EV_TOOL_COMPLETE:
                call_id = ev.get("toolCallId") or ev.get("toolCallID") or ""
                success_raw = ev.get("success")
                success = bool(success_raw) if success_raw is not None else True
                result = ev.get("result") or ev.get("error") or ""
                result_chars = len(str(result)) if result else 0
                start_info = _tool_starts.pop(call_id, {}) if call_id else {}
                dur_ms: Optional[int] = None
                if start_info.get("start_ts") is not None and ts is not None:
                    dur_ms = int((ts - start_info["start_ts"]) * 1000)
                tool_calls.append(
                    {
                        "id": call_id,
                        "name": start_info.get("name", ""),
                        "dur_ms": dur_ms,
                        "success": success,
                        "args_chars": start_info.get("args_chars", 0),
                        "result_chars": result_chars,
                    }
                )

            elif ev_type == _EV_MODEL_CHANGE:
                model_changes += 1

            elif ev_type == _EV_SUBAGENT_STARTED:
                sub_id = ev.get("sessionId") or ev.get("session_id") or ""
                if sub_id and sub_id not in subagent_sessions:
                    subagent_sessions.append(sub_id)

    # --- Determine completeness and tally recovery ---
    incomplete_reason: Optional[str] = None
    if not shutdown_seen:
        tally = read_inflight_tally(session_dir)
        if tally is not None and isinstance(tally.get("models"), dict):
            # Recover model totals from tally.
            recovered: dict = {}
            for model_name, tally_model in tally["models"].items():
                if not isinstance(tally_model, dict):
                    continue
                entry = _empty_model_entry()
                entry["input_tokens"] = _safe_int(tally_model.get("input_tokens"))
                entry["output_tokens"] = _safe_int(tally_model.get("output_tokens"))
                entry["cache_read_tokens"] = _safe_int(tally_model.get("cache_read_tokens"))
                entry["cache_write_tokens"] = _safe_int(tally_model.get("cache_write_tokens"))
                entry["api_calls"] = _safe_int(tally_model.get("api_calls"))
                recovered[model_name] = entry
            models = recovered
            incomplete_reason = "recovered_from_tally"
        else:
            incomplete_reason = "no_shutdown_event"

    complete = shutdown_seen

    # --- messages.json for content_chars ---
    content_chars = 0
    messages_path = session_dir / _MESSAGES_FILE
    if messages_path.exists():
        try:
            raw_text = messages_path.read_text(encoding="utf-8", errors="replace")
            msg_data = json.loads(raw_text)
            # Walk all string values in the messages list.
            msgs = msg_data.get("messages", []) if isinstance(msg_data, dict) else []
            if not isinstance(msgs, list):
                msgs = []
            for msg in msgs:
                if isinstance(msg, dict):
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        content_chars += len(content)
                    elif isinstance(content, list):
                        # Some models use content arrays.
                        for block in content:
                            if isinstance(block, dict):
                                text = block.get("text", "")
                                if isinstance(text, str):
                                    content_chars += len(text)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.debug("[copilot_state] cannot read messages.json in %s: %s", session_dir, exc)

    # --- checkpoint.json for checkpoint_tokens ---
    checkpoint_tokens: Optional[int] = None
    checkpoint_path = session_dir / _CHECKPOINT_FILE
    if checkpoint_path.exists():
        try:
            raw_text = checkpoint_path.read_text(encoding="utf-8", errors="replace")
            cp_data = json.loads(raw_text)
            if isinstance(cp_data, dict):
                for key in ("tokenCount", "token_count", "tokens", "contextTokens"):
                    val = cp_data.get(key)
                    if val is not None:
                        checkpoint_tokens = _safe_int(val)
                        break
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.debug(
                "[copilot_state] cannot read checkpoint.json in %s: %s", session_dir, exc
            )

    return {
        "session_id": session_id,
        "start_time": start_time,
        "end_time": end_time,
        "cwd": cwd,
        "version": version,
        "models": models,
        "total_premium_requests": total_premium_requests,
        "output_tokens_observed": output_tokens_observed,
        "compactions": compactions,
        "tool_calls": tool_calls,
        "message_count": message_count,
        "user_message_count": user_message_count,
        "model_changes": model_changes,
        "subagent_sessions": subagent_sessions,
        "complete": complete,
        "incomplete_reason": incomplete_reason,
        "content_chars": content_chars,
        "checkpoint_tokens": checkpoint_tokens,
        "data_source": "copilot_cli_events",
    }


def read_all_sessions(home: Optional[Path] = None) -> list:
    """Parse all Copilot CLI sessions, deduped by session_id.

    Returns a list of session dicts (one per unique session directory).
    Sessions with duplicate session_ids (same dir name seen twice, e.g.
    from symlinks resolved to the same target) are deduplicated: the first
    occurrence wins.

    Args:
        home: explicit Copilot home override; defaults to ``copilot_home()``
              from ``runtime_env``.
    """
    session_dirs = find_session_dirs(home=home)
    seen: set = set()
    results: list = []
    for sd in session_dirs:
        sid = sd.name
        if sid in seen:
            continue
        seen.add(sid)
        try:
            session = read_session(sd)
            results.append(session)
        except Exception as exc:
            logger.debug("[copilot_state] error reading session %s: %s", sid, exc)
    return results


if __name__ == "__main__":
    import json as _json

    sessions = read_all_sessions()
    print(_json.dumps(sessions, indent=2, default=str))
