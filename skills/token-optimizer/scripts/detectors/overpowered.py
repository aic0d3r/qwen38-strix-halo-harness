"""Overpowered model detector: expensive model used for simple tasks."""

import re

from runtime_env import detect_runtime

_TOP_TIER_MODELS = ("fable", "opus", "claude-opus")
_SIMPLE_TOOLS = frozenset({"Read", "Glob", "Grep", "Edit", "Write", "Bash"})

# Session-log model strings are attacker-influenceable (a crafted or
# custom-provider transcript controls the model_usage keys). The dominant
# model name is interpolated into evidence/suggestion text that the coach
# prints verbatim, so strip terminal control sequences first.
_ANSI_ESCAPE_RE = re.compile(
    r"\x1b\[[0-9;:<=>?]*[ -/]*[@-~]"   # CSI (full ECMA-48 parameter bytes 0x30-0x3f)
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL or ST
    r"|\x1b[()][0-2A-Z]"                # charset selection
    r"|\x1b[@-Z\\-_]"                   # remaining two-byte escapes
)


def _strip_ansi(text):
    return _ANSI_ESCAPE_RE.sub("", str(text))

# v5.11.1: per-model input token rate ($/Mtok), kept self-contained per this
# file's hot-path convention (no import of the pricing table). Sonnet's input
# rate is 3.0; the savings ratio vs a top-tier model is 1 - (3.0 / input_rate).
_INPUT_RATE = {"fable": 10.0, "opus": 5.0}


def _dominant_top_tier(model_usage):
    """Return (model_name, tokens) for the top-tier model with the most tokens.

    model_name is a normalized key into _INPUT_RATE ("fable" or "opus"); the
    display name returned is the raw model string for the evidence message.
    """
    best_raw = None
    best_norm = "opus"
    best_tokens = 0
    for k, v in model_usage.items():
        kl = k.lower()
        if not any(t in kl for t in _TOP_TIER_MODELS):
            continue
        if v > best_tokens:
            best_tokens = v
            best_raw = k
            best_norm = "fable" if "fable" in kl else "opus"
    return best_raw, best_norm


def detect_overpowered(session_data):
    """Detect sessions where a top-tier model was used for tasks a cheaper model
    could handle.

    Flags when: short output (<5K tokens per turn avg) + mostly simple tools
    + a top-tier model (Fable/Opus) is dominant.
    """
    if detect_runtime() not in ("claude", "hermes"):
        # The ladder and the "Sonnet would save" suggestion are Claude-model
        # routing advice. Under foreign runtimes a Claude-tier key in
        # model_usage (custom provider entry, crafted log) must not produce
        # foreign-runtime guidance. Hermes is a Claude runtime and keeps it.
        return []
    model_usage = session_data.get("model_usage", {})
    if not model_usage:
        return []

    total_tokens = sum(model_usage.values())
    if total_tokens == 0:
        return []

    # Check top-tier dominance.
    top_tier_tokens = sum(v for k, v in model_usage.items()
                          if any(t in k.lower() for t in _TOP_TIER_MODELS))
    top_tier_pct = top_tier_tokens / total_tokens

    if top_tier_pct < 0.5:
        return []

    # Check if work was simple: low output, mostly basic tools
    total_output = session_data.get("total_output_tokens", 0)
    api_calls = session_data.get("api_calls", 1)
    avg_output_per_turn = total_output / max(api_calls, 1)

    tool_calls = session_data.get("tool_calls", {})
    total_tool_count = sum(tool_calls.values())
    simple_tool_count = sum(tool_calls.get(t, 0) for t in _SIMPLE_TOOLS)
    simple_pct = simple_tool_count / max(total_tool_count, 1)

    # Only flag if output is light AND tools are simple
    if avg_output_per_turn > 5000 or simple_pct < 0.7:
        return []

    # v5.11.1: model-aware savings. Fable's input rate (10.0) is twice Opus's
    # (5.0), so dropping to Sonnet (3.0) saves a different fraction depending on
    # which top-tier model was dominant.
    dom_raw, dom_norm = _dominant_top_tier(model_usage)
    dom_display = _strip_ansi(dom_raw or dom_norm)
    input_rate = _INPUT_RATE.get(dom_norm, 5.0)
    savings_ratio = 1 - (3.0 / input_rate)
    sonnet_savings = int(top_tier_tokens * savings_ratio)
    return [{
        "name": "overpowered",
        "confidence": 0.6,
        "evidence": (
            f"{top_tier_pct:.0%} {dom_display} usage, {avg_output_per_turn:.0f} avg output tokens/turn, "
            f"{simple_pct:.0%} simple tool calls"
        ),
        "savings_tokens": sonnet_savings,
        "suggestion": (
            f"This session used {dom_display} for mostly simple edits and file reads. "
            f"Sonnet would save ~{sonnet_savings:,} tokens (~{savings_ratio:.0%} cost reduction) "
            "for equivalent quality on these tasks."
        ),
        "occurrence_count": 1,
    }]
