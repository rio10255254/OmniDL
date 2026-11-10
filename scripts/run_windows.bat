@echo off
chcp 65001 >nul
REM OmniDL 一鍵啟動（原始碼版）：第一次執行會自動建立虛擬環境並安裝套件
cd /d "%~dp0\.."
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv\Scripts\python.exe (
    echo [OmniDL] 建立虛擬環境中...
    %PY% -m venv .venv || (echo 找不到 Python，請先安裝 Python 3.10 以上版本 & pause & exit /b 1)
    .venv\Scripts\python -m pip install -U pip
    .venv\Scripts\python -m pip install -e .[desktop] || (pause & exit /b 1)
    echo [OmniDL] 下載 FFmpeg / aria2c / Deno...
    .venv\Scripts\python -m omnidl install-tools
)
.venv\Scripts\python -m omnidl gui %*
