# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

base_dir = os.path.abspath(SPECPATH)

datas = [(os.path.join(base_dir, 'web'), 'web')]
if os.path.exists(os.path.join(base_dir, 'ffmpeg.exe')):
    datas.append((os.path.join(base_dir, 'ffmpeg.exe'), '.'))
if os.path.exists(os.path.join(base_dir, 'ffmpeg')):
    datas.append((os.path.join(base_dir, 'ffmpeg'), '.'))

binaries = []
hiddenimports = ['Crypto', 'pywidevine', 'pymp4', 'curl_cffi', 'webview', 'web_gui', 'tkinter', 'tkinter.filedialog', 'xmltodict', 'sqlite3']
tmp_ret = collect_all('crunchyroll')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    [os.path.join(base_dir, 'main.py')],
    pathex=[base_dir],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

icon_path = os.path.join(base_dir, 'web', 'icon.ico')
icon_arg = [icon_path] if os.path.exists(icon_path) else None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='crunchyroller',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_arg,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='crunchyroller',
)
