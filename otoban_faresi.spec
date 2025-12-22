# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# Platform-specific icon
if sys.platform == 'win32':
    app_icon = 'icon.ico'
elif sys.platform == 'darwin':
    app_icon = 'icon.icns' if os.path.exists('icon.icns') else None
else:
    app_icon = None  # Linux doesn't use embedded icons

# Collect data files from packages that need them
playwright_stealth_datas = collect_data_files('playwright_stealth')

a = Analysis(
    ['flask_endpoint.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('whiteboard.html', '.'),
        ('public', 'public'),
    ] + playwright_stealth_datas,
    hiddenimports=[
        'flask',
        'pandas',
        'xlrd',
        'openpyxl',
        'twilio',
        'dotenv',
        'requests',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Using --onedir mode (COLLECT) because the app is too large for --onefile (>4GB with torch/CUDA)
exe = EXE(
    pyz,
    a.scripts,
    [],  # binaries moved to COLLECT
    exclude_binaries=True,  # Required for onedir mode
    name='Golden-Mouse-RPA',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # Set to True to see console output for debugging
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=app_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Golden-Mouse-RPA',
)
