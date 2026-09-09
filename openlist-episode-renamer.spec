# -*- mode: python ; coding: utf-8 -*-

import os
import sys


# Anaconda keeps OpenSSL and compression DLLs under Library/bin rather than
# the standard Python DLL directory. Include them when present so HTTPS calls
# continue to work in the packaged executable.
_runtime_dll_names = [
    'libcrypto-3-x64.dll',
    'libssl-3-x64.dll',
    'ffi.dll',
    'liblzma.dll',
    'libbz2.dll',
    'zlib.dll',
    'zstd.dll',
]
_runtime_dlls = []
for _name in _runtime_dll_names:
    for _base in (getattr(sys, 'base_prefix', sys.prefix), sys.prefix):
        _candidate = os.path.join(_base, 'Library', 'bin', _name)
        if os.path.isfile(_candidate):
            _runtime_dlls.append((_candidate, '.'))
            break

a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=_runtime_dlls,
    datas=[
        ('frontend/index.html', 'frontend'),
        ('image/demo01.webp', 'image'),
        ('image/demo02.webp', 'image'),
    ],
    hiddenimports=[
        'core',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.http.httptools_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
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
    a.binaries,
    a.datas,
    [],
    name='openlist-episode-renamer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
