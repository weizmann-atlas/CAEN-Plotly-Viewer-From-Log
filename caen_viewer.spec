# -*- mode: python ; coding: utf-8 -*-
import os
import sys
import glob
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# Collect plotly JS assets and ALL PyQt5 data (resources, translations, WebEngine pak files)
datas = []
datas += collect_data_files("plotly")
datas += collect_data_files("PyQt5")

binaries = []
binaries += collect_dynamic_libs("PyQt5")

# Explicitly find and include the QtWebEngineProcess native binary.
# collect_dynamic_libs only grabs .dylib/.so — this executable is missed without this.
import PyQt5 as _pyqt5
_pyqt5_dir = os.path.dirname(_pyqt5.__file__)
_proc_name = "QtWebEngineProcess.exe" if sys.platform == "win32" else "QtWebEngineProcess"
for _proc in glob.glob(os.path.join(_pyqt5_dir, "**", _proc_name), recursive=True):
    _rel_dir = os.path.relpath(os.path.dirname(_proc), _pyqt5_dir)
    binaries.append((_proc, os.path.join("PyQt5", _rel_dir)))

a = Analysis(
    ["caen_plotly_viewer_from_log_v13b.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "PyQt5.QtWebEngineWidgets",
        "PyQt5.QtWebEngineCore",
        "PyQt5.QtWebChannel",
        "PyQt5.QtPrintSupport",
        "PyQt5.QtSvg",
        # pandas C extension modules that are commonly missed
        "pandas._libs.tslibs.np_datetime",
        "pandas._libs.tslibs.nattype",
        "pandas._libs.tslibs.timedeltas",
        "pandas._libs.skiplist",
    ],
    hookspath=["hooks"],
    hooksconfig={},
    runtime_hooks=["hooks/rthook_webengine.py"],
    excludes=["tkinter", "matplotlib", "scipy", "IPython"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── Windows: onefile build ───────────────────────────────────────────────────
# All DLLs, PAK files and QtWebEngineProcess.exe are bundled into the single
# exe. PyInstaller's bootloader extracts them to %TEMP%\MEIxxxxxx on first
# run. The runtime hook (hooks/rthook_webengine.py) then:
#   • sets QTWEBENGINEPROCESS_PATH so Qt can find the helper binary
#   • prepends _MEIPASS to PATH so the helper can resolve Qt DLLs
#   • intercepts --type=<role> re-launches and os.execv's directly into
#     QtWebEngineProcess.exe so the renderer/GPU subprocesses work correctly
if sys.platform == "win32":
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="CAEN_Log_Viewer",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        # UPX disabled — it corrupts Qt DLLs and causes crashes
        upx=False,
        console=False,  # no terminal window
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=None,
    )

# ── macOS: onefile wrapped in .app bundle ────────────────────────────────────
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="CAEN_Log_Viewer",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        # UPX disabled — it corrupts Qt DLLs and causes crashes
        upx=False,
        runtime_tmpdir=None,
        console=False,  # no terminal window
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=None,
    )
    app = BUNDLE(
        exe,
        name="CAEN Log Viewer.app",
        icon=None,
        bundle_identifier="com.caen.logviewer",
        info_plist={
            "CFBundleShortVersionString": "15.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
        },
    )
