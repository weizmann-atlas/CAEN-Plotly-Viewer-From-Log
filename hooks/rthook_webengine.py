import os
import sys

if getattr(sys, "frozen", False):
    _meipass = sys._MEIPASS
    _is_win = sys.platform == "win32"
    _proc_name = "QtWebEngineProcess.exe" if _is_win else "QtWebEngineProcess"

    # Locate QtWebEngineProcess inside the extracted bundle
    _webengine_path = None
    for _root, _dirs, _files in os.walk(_meipass):
        if _proc_name in _files:
            _webengine_path = os.path.join(_root, _proc_name)
            os.environ.setdefault("QTWEBENGINEPROCESS_PATH", _webengine_path)
            break

    # Packaged apps can't use the Chromium sandbox without proper entitlements
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

    # Qt WebEngine spawns helper subprocesses (renderer, GPU, etc.) by re-launching
    # the same executable with --type=<role>. Detect this and exec the real
    # QtWebEngineProcess binary so the helper runs correctly instead of booting
    # the full Python+PyQt5 app, which crashes (SIGABRT in PyQtSlotProxy::unislot).
    if any(arg.startswith("--type=") for arg in sys.argv[1:]):
        if _webengine_path and os.path.isfile(_webengine_path):
            if _is_win:
                # On Windows, QtWebEngineProcess.exe resolves DLLs from its own
                # directory and the system PATH. The Qt/PyQt5 DLLs live in
                # _MEIPASS (the PyInstaller extraction root), so prepend it so
                # the process can find them after os.execv replaces this image.
                os.environ["PATH"] = _meipass + os.pathsep + os.environ.get("PATH", "")
            os.execv(_webengine_path, [_webengine_path] + sys.argv[1:])
        sys.exit(0)
