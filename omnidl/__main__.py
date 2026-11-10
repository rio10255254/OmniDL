import os
import sys

# 打包成無主控台視窗的 exe 時 stdout/stderr 是 None，uvicorn 與 argparse 寫入會當掉
for _name in ('stdout', 'stderr'):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, 'w', encoding='utf-8'))  # noqa: SIM115

from omnidl.tools import install_override_importer  # noqa: E402

# 打包版：如果使用者已經把 yt-dlp 更新到 lib/，優先載入新版
if getattr(sys, 'frozen', False):
    install_override_importer()

from omnidl.cli import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
