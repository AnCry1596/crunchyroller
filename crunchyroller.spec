# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('C:\\Users\\vure\\crunchyroller\\web', 'web'), ('C:\\Users\\vure\\crunchyroller\\ffmpeg.exe', '.')]
binaries = []
hiddenimports = ['Crypto', 'pywidevine', 'pymp4', 'curl_cffi', 'webview', 'web_gui', 'tkinter', 'tkinter.filedialog', 'xmltodict', 'sqlite3']
tmp_ret = collect_all('crunchyroll')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['C:\\Users\\vure\\crunchyroller\\main.py'],
    pathex=[],
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
    icon=['C:\\Users\\vure\\crunchyroller\\web\\icon.ico'],
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
