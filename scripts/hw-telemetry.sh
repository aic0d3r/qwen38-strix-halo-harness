#!/usr/bin/env bash
# order-9 / DirectMap2M telemetry: one line per day, append-only.
# Answers (after a week+): does vm-compact.timer actually stretch the
# time between reboots? Trend of order-9 high-order pages + DirectMap2M.
LOG="${HW_TELEMETRY_LOG:-$HOME/LLMBench/results/qwen38-27b/telemetry-order9.log}"
o9=$(awk '$4=="Normal"{u=0; for(i=14;i<=NF;i++) u+=$(i)*2**(i-14); print u}' /proc/buddyinfo 2>/dev/null | head -1)
d2m=$(grep DirectMap2M /proc/meminfo 2>/dev/null | awk '{print $2}')
d1g=$(grep DirectMap1G /proc/meminfo 2>/dev/null | awk '{print $2}')
mem=$(awk '/MemAvailable/{print $2}' /proc/meminfo 2>/dev/null)
echo "$(date -Iseconds) order9=${o9:-NA} directmap2m_kb=${d2m:-NA} directmap1g_kb=${d1g:-NA} memavail_kb=${mem:-NA}" >> "$LOG"
