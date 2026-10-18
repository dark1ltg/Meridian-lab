# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

datas, binaries, hiddenimports = [], [], []


def _is_dev_or_test_path(path: str) -> bool:
    s = path.replace("\\", "/")
    name = Path(s).name
    return (
        "/tests/" in s
        or s.endswith("/tests")
        or "/pytest/" in s
        or name in {"pytest", "py", "pluggy", "iniconfig"}
        or "pytest" in name
        or name == "conftest.py"
    )


for pkg in ("mutagen", "numpy", "aubio"):
    d, b, h = collect_all(pkg)
    datas += [item for item in d if not _is_dev_or_test_path(str(item[0]))]
    binaries += [item for item in b if not _is_dev_or_test_path(str(item[0]))]
    hiddenimports += [
        name
        for name in h
        if ".tests" not in name
        and "pytest" not in name
        and not name.endswith(".conftest")
        and "conftest" not in name.split(".")
    ]

root = Path(SPECPATH).resolve().parent
datas += collect_data_files("PySide6", includes=["Qt/plugins/**"])
datas += [(str(root / "resources" / "meridian.png"), "resources")]
datas += [(str(root / "resources" / "meridian.svg"), "resources")]
# Ubuntu Font Licence fonts are NOT packaged into the PyInstaller tree.
# build-appimage.sh installs them as standalone files under
# AppDir/usr/share/fonts/truetype/meridian/ next to UBUNTU-FONT-LICENCE-1.0.txt.

hiddenimports += [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtNetwork",
    "PySide6.QtSvg",
    "PySide6.QtDBus",
    "shiboken6",
]

a = Analysis(
    [str(root / "meridian_app.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={"pyside6": {"exclude_qml": True}},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "pytest",
        "py",
        "pluggy",
        "iniconfig",
        "numpy.f2py.tests",
        "numpy.tests",
        "numpy.typing.tests",
        "numpy.lib.tests",
        "numpy.random.tests",
        "numpy.fft.tests",
        "numpy.ma.tests",
        "numpy.matrixlib.tests",
        "numpy.polynomial.tests",
        "numpy._pyinstaller.tests",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DAnimation",
        "PySide6.Qt3DExtras",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DRender",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtPdf",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtBluetooth",
        "PySide6.QtNfc",
        "PySide6.QtPositioning",
        "PySide6.QtLocation",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtDesigner",
        "PySide6.QtHelp",
        "PySide6.QtTest",
    ],
    noarchive=False,
)

# libx264 is typically GPL-2.0-only and must not be redistributed inside a
# GPL-3.0-only Meridian AppImage. Leave it on the host; libavcodec will dlopen
# the system soname at runtime when present.
a.binaries = [
    entry
    for entry in a.binaries
    if not Path(str(entry[0])).name.startswith("libx264.so")
]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Meridian",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Meridian",
)
