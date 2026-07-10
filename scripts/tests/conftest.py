"""测试公共配置。"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
HF_README_DIR = FIXTURE_DIR / "hf_readmes"
