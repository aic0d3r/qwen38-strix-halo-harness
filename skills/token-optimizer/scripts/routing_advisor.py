"""In-session model + effort routing for the current platform.

Given a task, recommend both the model and the reasoning effort to run it with,
expressed in the terms of the platform Token Optimizer is running on. The goal
is to stop reflexively spending the top model at max effort on trivial work, and
to stop under-powering work that actually matters.

Two ideas do the work:

1. Significance. A task is classified easy / standard / hard from local signals
   only. Significance sets a FLOOR: the cheapest models and the lowest efforts
   are reachable only for easy tasks. Nothing a standard or hard task scores can
   drop it below that floor.

2. Native tiers. Each supported platform declares its own model ladder and its
   own effort control. The recommendation is always phrased in the current
   platform's names, and only the current platform's ladder is consulted.

Everything here is local, deterministic, and fails open to a safe middle
default (standard significance, never a cheap model) so a routing hiccup can
never block or mislead the caller.
"""

import re

# --- effort and tier ordering -------------------------------------------------

# Low to high. A floor names the minimum acceptable rung on each axis.
EFFORT_ORDER = ("minimal", "low", "medium", "high", "xhigh")
TIER_ORDER = ("budget", "mid", "capable", "frontier")

# Models that are only ever appropriate for genuinely easy work, named across
# platforms. Kept in one place so the floor, the guidance block, and the tests
# all read the same list.
VERY_CHEAP_MODELS = frozenset({"haiku", "luna"})


def is_very_cheap(model):
    """True if a model name is one of the very-cheap models, matched on name
    parts so a platform-qualified name (e.g. gpt-5.6-luna) is still caught."""
    if not model:
        return False
    parts = re.split(r"[^a-z0-9]+", str(model).lower())
    return any(p in VERY_CHEAP_MODELS for p in parts)


def _rank(value, order, default):
    try:
        return order.index(value)
    except ValueError:
        return order.index(default)


def _at_least(value, floor, order):
    """Return whichever of value/floor is higher on `order`."""
    return value if _rank(value, order, order[0]) >= _rank(floor, order, order[0]) else floor


# --- per-platform native tables (U2) -----------------------------------------

# One row per supported platform. Each row lists its model ladder (tier -> model
# name, in the platform's own vocabulary), the effort levels the platform
# accepts, and how effort is applied ("native" = the platform has a real
# reasoning-effort control; "advisory" = state the effort in the prompt because
# the platform exposes no native control). Adding a platform is a new row; the
# engine below does not change.
ROUTING_TABLES = {
    "claude": {
        "models": {
            "budget": "haiku",
            "mid": "sonnet",
            "capable": "opus",
            "frontier": "opus",
        },
        "efforts": EFFORT_ORDER,
        "effort_kind": "native",          # thinking budget
        "effort_knob": "thinking budget",
    },
    "codex": {
        # GPT-5.6 three-tier family (Sol flagship > Terra workhorse > Luna cheap).
        # Verified 2026-07-23 against OpenAI's GPT-5.6 preview + Codex CLI /model list.
        "models": {
            "budget": "gpt-5.6-luna",
            "mid": "gpt-5.6-terra",
            "capable": "gpt-5.6-sol",
            "frontier": "gpt-5.6-sol",
        },
        "efforts": EFFORT_ORDER,
        "effort_kind": "native",          # model_reasoning_effort
        "effort_knob": "model_reasoning_effort",
    },
    "opencode": {
        # OpenCode runs user-configured models (any provider). The caller resolves
        # the real ladder from opencode.json (`model` / `small_model`) and passes it
        # to recommend(models=...); this table is the fallback when config is absent
        # or unreadable. No native reasoning-effort knob -> advisory.
        "models": {
            "budget": "haiku",
            "mid": "sonnet",
            "capable": "opus",
            "frontier": "opus",
        },
        "efforts": ("low", "medium", "high"),
        "effort_kind": "advisory",
        "effort_knob": "prompt directive",
    },
    "copilot": {
        # Copilot CLI is a rotating multi-vendor catalog with "Auto" as the GA default and
        # no native reasoning-effort knob (advisory). Ladder anchored to real, priced OpenAI
        # slugs Copilot routes to; exact per-plan availability varies. Verified 2026-07-23.
        "models": {
            "budget": "gpt-5.4-mini",
            "mid": "gpt-5.4",
            "capable": "gpt-5.5",
            "frontier": "gpt-5.6-sol",
        },
        "efforts": ("low", "medium", "high"),
        "effort_kind": "advisory",
        "effort_knob": "prompt directive",
    },
    "hermes": {
        # Hermes is a Claude runtime (reads ~/.claude, models = Claude ladder), so the
        # ladder below is correct, not a placeholder. Hermes exposes no per-tier model
        # config; a single active model can be pinned via HERMES_MODEL, which the caller
        # folds into the ladder. Advisory effort (no native reasoning-effort knob).
        "models": {
            "budget": "haiku",
            "mid": "sonnet",
            "capable": "opus",
            "frontier": "opus",
        },
        "efforts": ("low", "medium", "high"),
        "effort_kind": "advisory",
        "effort_knob": "prompt directive",
    },
    "cowork": {
        # Cowork IS Claude Code (same engine, in a VM), so the ladder mirrors
        # "claude" exactly and effort is native (thinking budget). detect_runtime()
        # returns "claude" inside Cowork; this row is here so a caller that has
        # already resolved is_cowork() can pass runtime="cowork" and get a
        # consistent table rather than falling through to the generic row.
        "models": {
            "budget": "haiku",
            "mid": "sonnet",
            "capable": "opus",
            "frontier": "opus",
        },
        "efforts": EFFORT_ORDER,
        "effort_kind": "native",          # thinking budget
        "effort_knob": "thinking budget",
    },
}

# Used when the current platform has no table (keeps the engine total).
_GENERIC_ROW = {
    "models": {"budget": "small", "mid": "standard", "capable": "large", "frontier": "large"},
    "efforts": ("low", "medium", "high"),
    "effort_kind": "advisory",
    "effort_knob": "prompt directive",
}


def platform_row(runtime):
    try:
        return ROUTING_TABLES.get(runtime, _GENERIC_ROW)
    except TypeError:
        # An unhashable runtime can't index the table; treat as unknown.
        return _GENERIC_ROW


# --- significance + category classification (U1) -----------------------------

# Signals are matched as whole words or whole phrases, not raw substrings, so
# "author" never triggers "auth" and "reproduction" never triggers "production".
# Word forms are listed explicitly (authentication, migration, ...) rather than
# relying on prefixes.
_HARD_SIGNALS = (
    "security", "auth", "authentication", "authorization", "oauth", "crypto",
    "credential", "credentials", "production", "migrate", "migration", "migrating",
    "schema change", "architecture", "redesign", "concurrent", "concurrency",
    "deadlock", "race condition", "payment", "payments", "billing", "irreversible",
    "data loss", "delete all", "drop table", "refactor across", "multi-file",
    "distributed", "rollout", "backfill",
)

_EASY_SIGNALS = (
    "typo", "rename", "one-liner", "one liner", "single file", "single-file",
    "format", "reformat", "lint", "list", "count", "summarize", "summary",
    "grep", "print", "echo", "comment", "docstring", "readme",
)

_CODE_SIGNALS = ("code", "function", "class", "bug", "refactor", "implement", "test", "compile", "api")
_REASONING_SIGNALS = ("why", "analyze", "design", "trade-off", "tradeoff", "compare", "evaluate", "plan", "prove")


def _compile(signals):
    return tuple(re.compile(r"\b" + re.escape(s) + r"\b") for s in signals)


_HARD_RE = _compile(_HARD_SIGNALS)
_EASY_RE = _compile(_EASY_SIGNALS)
_CODE_RE = _compile(_CODE_SIGNALS)
_REASONING_RE = _compile(_REASONING_SIGNALS)


def _count_hits(text, patterns):
    return sum(1 for p in patterns if p.search(text))


def classify_significance(task):
    """Return (significance, confidence).

    significance in {"easy","standard","hard"}, confidence in {"high","low"}.
    Local and deterministic. On any error, returns the safe middle: standard/low.
    """
    try:
        text = (task or "")[:8192].lower()
        hard = _count_hits(text, _HARD_RE)
        easy = _count_hits(text, _EASY_RE)

        # Structural nudges: very short with no hard signal leans easy; long or
        # multi-step leans away from easy.
        length = len(text)
        steps = text.count("\n") + text.count(". ") + text.count("; ")
        if length < 80 and hard == 0:
            easy += 1
        if length > 400 or steps >= 3:
            hard += 1

        if hard >= 1 and hard >= easy:
            sig = "hard"
            lead = hard - easy
        elif easy >= 2 and hard == 0:
            sig = "easy"
            lead = easy
        else:
            sig = "standard"
            lead = abs(easy - hard)

        confidence = "high" if lead >= 2 else "low"
        return sig, confidence
    except Exception:
        return "standard", "low"


def classify_category(task):
    """Coarse task kind: 'reasoning' | 'code' | 'simple'. Never raises."""
    try:
        text = (task or "")[:8192].lower()
        r = _count_hits(text, _REASONING_RE)
        c = _count_hits(text, _CODE_RE)
        if r == 0 and c == 0:
            return "simple"
        return "reasoning" if r > c else "code"
    except Exception:
        return "simple"


# --- floor + selection (U3) ---------------------------------------------------

# significance -> (base tier, base effort). This is both the target and the
# floor for that level; confidence can promote it, category can nudge effort up.
_BASE = {
    "easy":     ("budget",   "low"),
    "standard": ("mid",      "medium"),
    "hard":     ("capable",  "high"),
}

# Low confidence promotes only at the easy boundary, where a wrong call is
# expensive: a task scored easy but actually significant would otherwise get a
# cheap model and low effort. Standard and hard already sit above the cheap
# floor, so an uncertain one stays put rather than over-spending toward the top.
_PROMOTE = {"easy": "standard", "standard": "standard", "hard": "hard"}


def baseline(significance, runtime, models=None):
    """Canonical (model, effort) for a significance level on a platform, with no
    confidence or category adjustment. Used to describe the routing policy.

    `models` optionally overrides the tier->model ladder (config-resolved, for
    user-configured platforms); a malformed map falls back to the table."""
    row = platform_row(runtime)
    model_map = _coerce_model_map(models, row["models"])
    tier, effort = _BASE.get(significance, _BASE["standard"])
    model = model_map.get(tier, model_map["mid"])
    if effort not in row["efforts"]:
        higher = [e for e in EFFORT_ORDER if e in row["efforts"]
                  and _rank(e, EFFORT_ORDER, "medium") >= _rank(effort, EFFORT_ORDER, "medium")]
        effort = higher[0] if higher else row["efforts"][-1]
    return model, effort


def _coerce_model_map(models, fallback):
    """Return a complete tier->model map, or `fallback` if `models` is unusable.

    The engine stays pure: platforms whose models are user-configured (opencode,
    hermes) have the caller read the config and pass the resolved map in. A
    partial or malformed map falls back to the table so the floor never breaks.
    """
    if not isinstance(models, dict):
        return fallback
    coerced = {}
    for tier in TIER_ORDER:
        value = models.get(tier)
        if not (isinstance(value, str) and value.strip()):
            return fallback
        coerced[tier] = value.strip()
    return coerced


def recommend(task, runtime, models=None):
    """Return a recommendation dict for `task` on `runtime`.

    `models` optionally overrides the platform's tier->model ladder with values
    resolved from the platform's own config (used for user-configured platforms
    like opencode/hermes); a malformed map is ignored and the table is used.

    Keys: significance, confidence, category, tier, model, effort, effort_kind,
    effort_knob, floor {min_tier, min_effort}, why. Never raises.
    """
    try:
        sig, conf = classify_significance(task)
        cat = classify_category(task)
        row = platform_row(runtime)
        model_map = _coerce_model_map(models, row["models"])

        effective = _PROMOTE[sig] if conf == "low" else sig
        min_tier, min_effort = _BASE[effective]

        # Base pick from the effective significance, then floor it.
        base_tier, base_effort = _BASE[effective]
        tier = _at_least(base_tier, min_tier, TIER_ORDER)
        effort = _at_least(base_effort, min_effort, EFFORT_ORDER)

        # Category nudge: thinking-heavy work gets one extra effort step, but
        # only for non-easy tasks and never below the floor.
        if effective != "easy" and cat in ("reasoning", "code"):
            idx = min(_rank(effort, EFFORT_ORDER, "medium") + 1, len(EFFORT_ORDER) - 1)
            effort = EFFORT_ORDER[idx]

        # Clamp effort to what this platform accepts (advisory platforms carry a
        # shorter ladder); keep it at or above the floor after clamping.
        accepted = row["efforts"]
        if effort not in accepted:
            # nearest accepted at or above, else the platform's top.
            higher = [e for e in EFFORT_ORDER if e in accepted
                      and _rank(e, EFFORT_ORDER, "medium") >= _rank(effort, EFFORT_ORDER, "medium")]
            effort = higher[0] if higher else accepted[-1]

        model = model_map.get(tier, model_map["mid"])

        # Hard floor, enforced last: a very cheap model or a below-floor effort
        # can only stand for an easy task. This cannot be reached by a standard
        # or hard task because their floors already exclude it, but enforce it
        # explicitly so the guarantee does not depend on the tables staying in
        # sync.
        if effective != "easy":
            if is_very_cheap(model) or tier == "budget":
                tier = _at_least(tier, "mid", TIER_ORDER)
                model = model_map.get(tier, model_map["mid"])
            effort = _at_least(effort, min_effort, EFFORT_ORDER)
            if effort in ("minimal", "low"):
                effort = _at_least(effort, "medium", EFFORT_ORDER)
                if effort not in accepted:
                    effort = accepted[-1]

        why = f"{sig} task ({conf} confidence, {cat}) -> {tier} model at {effort} effort"
        return {
            "significance": sig,
            "confidence": conf,
            "category": cat,
            "tier": tier,
            "model": model,
            "effort": effort,
            "effort_kind": row["effort_kind"],
            "effort_knob": row["effort_knob"],
            "floor": {"min_tier": min_tier, "min_effort": min_effort},
            "why": why,
        }
    except Exception:
        # Total fail-open: a safe, non-cheap middle recommendation.
        row = platform_row(runtime)
        return {
            "significance": "standard", "confidence": "low", "category": "simple",
            "tier": "mid", "model": row["models"]["mid"], "effort": "medium",
            "effort_kind": row["effort_kind"], "effort_knob": row["effort_knob"],
            "floor": {"min_tier": "mid", "min_effort": "medium"},
            "why": "fell back to a safe default",
        }
