# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller OzonSorter.spec   (запускать из корня проекта)
# Onedir (как оригинал): OzonSorter.exe + папка _internal.
# datas-кортежи кроссплатформенны — разделитель путей PyInstaller подставит сам.

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=['sqlalchemy.dialects.sqlite'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OzonSorter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # GUI-приложение, без консольного окна
    icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='OzonSorter',
)
