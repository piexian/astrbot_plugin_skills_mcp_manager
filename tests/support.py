"""Load the plugin's pure modules without booting AstrBot or importing its providers."""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "skills_manager_test_plugin"
plugin = types.ModuleType(PACKAGE)
plugin.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE, plugin)
