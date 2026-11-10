# PyInstaller 打包設定：pyinstaller omnidl.spec
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hidden = (collect_submodules('yt_dlp') + collect_submodules('uvicorn') + collect_submodules('webview')
          + collect_submodules('yt_dlp_ejs'))
datas = [('omnidl/web', 'omnidl/web')] + collect_data_files('yt_dlp_ejs') + collect_data_files('curl_cffi')

a = Analysis(['omnidl/__main__.py'], pathex=['.'], datas=datas, hiddenimports=hidden,
             excludes=['tkinter', 'matplotlib', 'numpy', 'PIL'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='OmniDL', console=False,
          icon='scripts/icon.ico', upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name='OmniDL', upx=False)
