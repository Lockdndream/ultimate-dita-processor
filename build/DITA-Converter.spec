# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.hooks import copy_metadata

datas = [('D:\\Projects\\ultimate-Dita-Processor\\config', 'config'), ('D:\\Projects\\ultimate-Dita-Processor\\ui', 'ui'), ('D:\\Projects\\ultimate-Dita-Processor\\.streamlit', '.streamlit')]
binaries = []
hiddenimports = ['streamlit.web.cli', 'streamlit.runtime.scriptrunner.magic_funcs', 'streamlit.components.v1', 'tornado.platform.asyncio', 'tornado.platform.windows', 'agents.extractor', 'agents.mapper', 'agents.generator', 'agents.validator', 'agents.image_processor', 'agents.pdf_quality', 'pdfplumber', 'fitz', 'lxml.etree', 'yaml', 'docx', 'PIL', 'PIL.Image']
datas += copy_metadata('streamlit')
datas += copy_metadata('pdfplumber')
datas += copy_metadata('lxml')
datas += copy_metadata('pymupdf')
datas += copy_metadata('python-docx')
datas += copy_metadata('PyYAML')
datas += copy_metadata('pillow')
tmp_ret = collect_all('streamlit')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('tornado')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['D:\\Projects\\ultimate-Dita-Processor\\build\\launcher.py'],
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
    a.binaries,
    a.datas,
    [],
    name='DITA-Converter',
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
)
