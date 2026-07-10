#!/bin/bash
# 安装 OpenRouter 模型检测 launchd 定时任务(每日 08:00,北京时间)
set -euo pipefail

LABEL="com.openrouter.model-checker"
HOME_DIR="$HOME"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PLIST_SRC="$SCRIPT_DIR/com.openrouter.model-checker.plist"
PLIST_DST="$HOME_DIR/Library/LaunchAgents/$LABEL.plist"
WRAPPER="$HOME_DIR/bin/openrouter-model-check.sh"

PYTHON_BIN="$(command -v python3 || echo /usr/bin/python3)"

mkdir -p "$HOME_DIR/bin" "$HOME_DIR/Library/LaunchAgents" "$PROJECT_DIR/logs"

# 写入包装脚本(转发到项目入口)
cat > "$WRAPPER" <<EOF
#!/bin/bash
set -euo pipefail
exec "$PYTHON_BIN" \\
  "$PROJECT_DIR/scripts/check_openrouter_models.py" \\
  --quiet --log-file
EOF
chmod +x "$WRAPPER"

# 替换 plist 中的路径占位符
sed "s|__HOME__|$HOME_DIR|g" "$PLIST_SRC" > "$PLIST_DST"
chmod 644 "$PLIST_DST"
xattr -c "$PLIST_DST" 2>/dev/null || true
plutil -lint "$PLIST_DST"

# 移除旧 crontab(若存在)
if crontab -l 2>/dev/null | grep -q 'check_openrouter_models.py'; then
  crontab -l | grep -v 'check_openrouter_models.py' | crontab -
  echo "已移除 crontab 任务"
fi

# 重新加载 launchd
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST_DST"

echo "launchd 任务已安装: $LABEL"
echo "调度: 每天 08:00 (北京时间)"
echo "日志: $HOME_DIR/Library/Logs/com.openrouter.model-checker.{out,err}.log"
echo "脚本日志: $PROJECT_DIR/logs/check_openrouter_models.log"
echo ""
echo "立即试跑..."
launchctl kickstart -p "gui/$UID/$LABEL"
echo "完成。查看状态: launchctl print gui/$UID/$LABEL"
