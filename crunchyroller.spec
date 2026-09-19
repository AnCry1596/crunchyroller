# -*- mode: python ; coding: utf-8 -*-
import os

root = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    ['main.py'],
    pathex=[root],
    binaries=[],
    datas=[
        (os.path.join(root, 'web'), 'web'),
    ] + ([(os.path.join(root, 'ffmpeg.exe'), '.')] if os.path.exists(os.path.join(root, 'ffmpeg.exe')) else []),
    hiddenimports=[
        # pywebview backends
        'webview',
        'webview.platforms.winforms',
        'webview.platforms.edgechromium',
        'webview.platforms.mshtml',
        # pycryptodome
        'Crypto',
        'Crypto.Cipher',
        'Crypto.Cipher.AES',
        'Crypto.PublicKey',
        'Crypto.PublicKey.RSA',
        'Crypto.Signature',
        'Crypto.Signature.pss',
        'Crypto.Hash',
        'Crypto.Hash.SHA256',
        'Crypto.Util',
        'Crypto.Util.Padding',
        # pywidevine
        'pywidevine',
        'pywidevine.cdm',
        'pywidevine.device',
        'pywidevine.pssh',
        'pywidevine.session',
        # curl_cffi
        'curl_cffi',
        'curl_cffi.requests',
        # xmltodict / pymp4
        'xmltodict',
        'pymp4',
        'pymp4.parser',
        # protobuf
        'google.protobuf',
        'google.protobuf.descriptor',
        'google.protobuf.descriptor_pool',
        'google.protobuf.message',
        'google.protobuf.reflection',
        # windows-only modules (safe to include – ignored on non-win builds)
        'winreg',
        'ctypes',
        'ctypes.wintypes',
        # stdlib that PyInstaller sometimes misses
        'sqlite3',
        'glob',
        'shutil',
        'tempfile',
        'base64',
        'threading',
        'http.server',
        'webbrowser',
        'urllib.parse',
        'urllib.request',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'PIL',
        'PyQt5',
        'PyQt6',
    ],
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
    icon=os.path.join(root, 'web', 'icon.ico'),
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
