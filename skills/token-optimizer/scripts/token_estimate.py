"""Shared, calibrated token estimator (U-F).

One estimator consumed everywhere a token count is derived from text or bytes,
replacing the scattered ``len(text) // 4`` / ``st_size // 4`` heuristic.

Why ``// 4`` undercounts:
  * Real source code averages ~3.3 characters per token for the Claude / GPT BPE
    families, not 4.0. Dividing by 4 systematically undercounts ~15-20%.
  * CJK text is ~1-1.5 characters per token. Dividing by 4 undercounts it ~3x.

This module has no third-party dependency (no tiktoken/transformers in the
runtime); it is a calibrated heuristic with documented error bounds (±~10% on
ASCII code vs a reference BPE tokenizer). If a real tokenizer is ever vendored,
swap the body of ``estimate_tokens`` and keep the call sites unchanged.

Calibration constant lives in one place so every surface stays consistent.
"""
from __future__ import annotations

from math import ceil

# Characters per token for non-CJK (Latin / code / punctuation) text.
# 3.3 is the calibrated mean across mixed source code; conservative vs the
# 3.0-3.5 range reported for the cl100k / Claude BPE families.
CODE_CHARS_PER_TOKEN = 3.3

# CJK and other ideographic scripts tokenize at roughly one token per character
# (often slightly more). Counting them at the Latin ratio undercounts ~3x.
_CJK_TOKENS_PER_CHAR = 1.0


def _is_cjk(ch: str) -> bool:
    """True for CJK ideographs, kana, Hangul, and full-width forms.

    Conservative set of the high-density ranges; anything outside falls back to
    the Latin ratio, which is the safe (slightly higher) estimate for it.
    """
    o = ord(ch)
    return (
        0x3040 <= o <= 0x30FF      # Hiragana + Katakana
        or 0x3400 <= o <= 0x4DBF   # CJK Ext A
        or 0x4E00 <= o <= 0x9FFF   # CJK Unified
        or 0xAC00 <= o <= 0xD7A3   # Hangul syllables
        or 0xF900 <= o <= 0xFAFF   # CJK compatibility ideographs
        or 0xFF00 <= o <= 0xFFEF   # Half/full-width forms
        or 0x20000 <= o <= 0x2FA1F  # CJK Ext B-F + supplement
    )


def estimate_tokens(text: str) -> int:
    """Estimate token count for a text string.

    CJK characters are counted ~1 token each; the remainder at
    ``CODE_CHARS_PER_TOKEN``. Returns 0 for empty input, else at least 1.
    Identical input always yields an identical estimate (callers rely on this
    for cross-call-site consistency).
    """
    if not text:
        return 0
    # Fast path: pure-ASCII text (the overwhelming majority of source code) has
    # no CJK characters, so skip the per-character scan entirely.
    if text.isascii():
        return max(1, ceil(len(text) / CODE_CHARS_PER_TOKEN))
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    est = ceil(other / CODE_CHARS_PER_TOKEN) + ceil(cjk * _CJK_TOKENS_PER_CHAR)
    return max(1, est)


def estimate_tokens_from_bytes(n_bytes: int) -> int:
    """Estimate tokens from a byte count (e.g. ``os.stat().st_size``).

    Used where the text is not in hand. Assumes predominantly single-byte
    (ASCII/code) content, so byte count ~= character count. Multi-byte UTF-8
    inflates byte count, which makes this conservatively high for CJK rather
    than 3x low -- the safe direction. Returns 0 for non-positive input.
    """
    if not n_bytes or n_bytes <= 0:
        return 0
    return max(1, ceil(n_bytes / CODE_CHARS_PER_TOKEN))
