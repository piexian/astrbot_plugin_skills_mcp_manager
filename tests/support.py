"""Load the plugin's modules with an isolated disposable ASTRBOT_ROOT."""

import atexit
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

if "ASTRBOT_ROOT" not in os.environ:
    _TEST_ASTRBOT_ROOT = tempfile.mkdtemp(prefix="skills_manager_astrbot_root_")
    os.environ["ASTRBOT_ROOT"] = _TEST_ASTRBOT_ROOT
    atexit.register(shutil.rmtree, _TEST_ASTRBOT_ROOT, True)

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "skills_manager_test_plugin"
plugin = types.ModuleType(PACKAGE)
plugin.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE, plugin)
