#!/bin/bash
# 卸载 OpenRouter 模型检测 launchd 定时任务
set -euo pipefail

LABEL="com.openrouter.model-checker"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
WRAPPER="$HOME/bin/openrouter-model-check.sh"

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
rm -f "$PLIST_DST" "$WRAPPER"
echo "已卸载 $LABEL"
