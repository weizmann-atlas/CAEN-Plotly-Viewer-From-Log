# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# Collect plotly JS assets and PyQt5 WebEngine resources (locales, .pak files)
datas = []
datas += collect_data_files("plotly")
datas += collect_data_files("PyQt5", includes=["Qt/resources/*", "Qt/translations/*"])

binaries = []
binaries += collect_dynamic_libs("PyQt5")

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

# macOS: wrap the exe in a .app bundle so Finder shows it as a native app
if sys.platform == "darwin":
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
