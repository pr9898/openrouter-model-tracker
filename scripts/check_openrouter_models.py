#!/usr/bin/env python3
"""OpenRouter 模型上新检测(增强版)— 入口脚本。

保留原 skill 的脚本名与调用契约(crontab / GitHub Actions),
内部逻辑转发到 ``openrouter_checker`` 包。

常用参数:
  --verbose        输出 DEBUG 日志
  --log-file       同时写日志到 logs/check_openrouter_models.log
  --quiet          无变化时跳过通知
  --dry-run        不写状态/不发通知,变化打印到 stderr
  --refresh-zh     强制重新抓取中文介绍
  --no-lock        不使用文件锁(Windows / CI 单进程)
"""

import sys
from pathlib import Path

# 支持 `python scripts/check_openrouter_models.py` 与 `python -m openrouter_checker`
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from openrouter_checker.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
