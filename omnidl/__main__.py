import os
import sys

# 打包成無主控台視窗的 exe 時 stdout/stderr 是 None，uvicorn 與 argparse 寫入會當掉
for _name in ('stdout', 'stderr'):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, 'w', encoding='utf-8'))  # noqa: SIM115

from omnidl.tools import install_override_importer  # noqa: E402

if getattr(sys, 'frozen', False):
    # 打包版：如果使用者已經把 yt-dlp 更新到 lib/，優先載入新版
    install_override_importer()
    # Windows：移除程式資料夾 DLL 的網路下載標記，否則 .NET 會拒絕載入（selftest 可選擇不移除以便診斷）
    if '--no-unblock' not in sys.argv:
        from omnidl.desktop import unblock_bundle
        unblock_bundle()

from omnidl.cli import main  # noqa: E402

if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as e:  # 無主控台的 exe 看不到錯誤，留下紀錄
        from omnidl.desktop import log_error
        log_error('程式意外結束', e)
        raise
