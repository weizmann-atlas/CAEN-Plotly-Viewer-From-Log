import os
import sys

# When running as a PyInstaller --onefile bundle, everything is extracted to
# sys._MEIPASS at startup. Qt WebEngine spawns QtWebEngineProcess as a child
# process and needs to know where to find it in the temp dir.
if getattr(sys, "frozen", False):
    _meipass = sys._MEIPASS
    _target = "QtWebEngineProcess.exe" if sys.platform == "win32" else "QtWebEngineProcess"
    for _root, _dirs, _files in os.walk(_meipass):
        if _target in _files:
            os.environ.setdefault("QTWEBENGINEPROCESS_PATH", os.path.join(_root, _target))
            break
