# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['ryusync.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('resources/nxx.png', 'resources'),
        ('resources/RyuSync-icon-1024.png', 'resources'),
    ],
    hiddenimports=[
        'fuzzywuzzy',
        'fuzzywuzzy.fuzz',
        'fuzzywuzzy.process',
    ],
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
    name='RyuSync',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RyuSync',
)
app = BUNDLE(
    coll,
    name='RyuSync.app',
    icon='resources/RyuSync.icns',
    bundle_identifier='com.ryusync.app',
)
