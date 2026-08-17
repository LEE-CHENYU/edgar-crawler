"""Pin `scripts` to THIS repo.

An `easy-install.pth` in the python310 env puts /Users/lichenyu/leviathan on
sys.path, and its scripts/ is a REGULAR package (it has __init__.py). Python
resolves a regular package found anywhere on the path ahead of a namespace
package, so `from scripts.x import ...` silently bound to leviathan's scripts/
and every test module here failed to collect with ModuleNotFoundError.

Registering the repo's scripts/ as a namespace package under the name `scripts`
before collection makes the local directory authoritative.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import importlib.util
import types

_local = REPO / "scripts"
_existing = sys.modules.get("scripts")
if _local.is_dir() and (
    _existing is None
    or Path(getattr(_existing, "__file__", "") or ".").resolve().parent != _local
):
    pkg = types.ModuleType("scripts")
    pkg.__path__ = [str(_local)]
    pkg.__spec__ = importlib.util.spec_from_loader("scripts", loader=None, is_package=True)
    pkg.__spec__.submodule_search_locations = [str(_local)]
    sys.modules["scripts"] = pkg
