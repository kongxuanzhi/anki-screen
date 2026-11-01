#!/usr/bin/env bash
# 一键完成：测试 → 打包 → 校验 → 退出旧进程 → 重置屏幕录制权限 → 安装 → 启动。
#
# 用法：
#   bash packaging/build_install_mac.sh
#   bash packaging/build_install_mac.sh --skip-tests        # 快速迭代，跳过测试
#   bash packaging/build_install_mac.sh --keep-permissions  # 保留屏幕录制授权，不重置
#   bash packaging/build_install_mac.sh --no-launch         # 只安装，不自动启动
#   PYTHON=/path/to/python3 bash packaging/build_install_mac.sh
#
# 说明：
#   * 只适用于 macOS；Windows 请用 packaging/build_win.bat。
#   * 默认在安装前执行 `tccutil reset ScreenCapture com.ankisnip.app`，
#     让新版本重新走一次「屏幕录制」授权；不想重置就加 --keep-permissions。
#   * 不会自动 pip install，依赖需提前装好（PySide6 / PyInstaller 等）。
set -euo pipefail
cd "$(dirname "$0")/.."

APP_NAME="AnkiSnip"
BUNDLE_ID="com.ankisnip.app"
DEST="/Applications/${APP_NAME}.app"
PYTHON="${PYTHON:-python3}"
RUN_TESTS=1
LAUNCH=1
RESET_PERMISSIONS=1
DIST="dist/${APP_NAME}.app"

usage() {
    sed -n '2,15p' "$0"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-tests) RUN_TESTS=0 ;;
        --keep-permissions) RESET_PERMISSIONS=0 ;;
        --no-launch)  LAUNCH=0 ;;
        --python)     PYTHON="${2:?--python 需要一个路径}"; shift ;;
        -h|--help)    usage ;;
        *) echo "未知参数：$1（--help 查看用法）" >&2; exit 2 ;;
    esac
    shift
done

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "这个脚本只支持 macOS。" >&2
    exit 1
fi

echo "==> Python: $("$PYTHON" -c 'import sys; print(sys.executable)')"
"$PYTHON" -m PyInstaller --version >/dev/null || {
    echo "当前 Python 没有 PyInstaller：$PYTHON" >&2
    echo "可以设置 PYTHON=/path/to/python3，或先 pip install pyinstaller。" >&2
    exit 1
}

if [[ "$RUN_TESTS" == 1 ]]; then
    echo "==> 运行测试（跳过历史遗留的 tests/base）"
    QT_QPA_PLATFORM=offscreen "$PYTHON" -m pytest -q --ignore=tests/base
fi

echo "==> 清理旧的构建产物"
rm -rf build dist

echo "==> PyInstaller 打包"
"$PYTHON" -m PyInstaller packaging/ankisnip.spec --noconfirm

if [[ ! -d "$DIST" ]]; then
    echo "打包失败：找不到 $DIST" >&2
    exit 1
fi

echo "==> 校验 bundle 里是不是最新源码"
"$PYTHON" tools/verify_bundle.py "$DIST"

echo "==> 退出正在运行的旧版本"
# 同时覆盖 /Applications 和项目 dist 里的两份（开发时经常从 dist 启动）。
pkill -f "${DEST}/Contents/MacOS/${APP_NAME}" 2>/dev/null || true
pkill -f "${PWD}/${DIST}/Contents/MacOS/${APP_NAME}" 2>/dev/null || true
for _ in 1 2 3 4 5 6 7 8 9 10; do
    pgrep -f "${APP_NAME}.app/Contents/MacOS/${APP_NAME}" >/dev/null || break
    sleep 0.3
done

if [[ "$RESET_PERMISSIONS" == 1 ]]; then
    echo "==> 重置「屏幕录制」授权（${BUNDLE_ID}）"
    # tccutil 是系统提供的唯一可靠重置入口；直接改 TCC.db 会被 SIP/tccd 吃掉。
    if ! /usr/bin/tccutil reset ScreenCapture "$BUNDLE_ID"; then
        echo "提示：tccutil 重置失败（可能从未授权过）。可手动到 系统设置 → 隐私与安全性 → 屏幕录制 删除 ${APP_NAME}。" >&2
    fi
else
    echo "==> 保留现有「屏幕录制」授权（--keep-permissions）"
fi

echo "==> 安装到 $DEST"
if [[ ! -w "/Applications" ]]; then
    echo "/Applications 不可写。请用管理员账号运行，或手动：sudo ditto \"$DIST\" \"$DEST\"" >&2
    exit 1
fi
rm -rf "$DEST"
ditto "$DIST" "$DEST"

if [[ "$LAUNCH" == 1 ]]; then
    echo "==> 启动新版本"
    open -a "$DEST"
fi

echo
echo "完成：$DEST"
if [[ "$RESET_PERMISSIONS" == 1 ]]; then
    echo "屏幕录制授权已重置：下次截图会重新弹出系统授权，"
    echo "勾选 ${APP_NAME} 后完全退出再打开即可。"
else
    echo "保留了现有屏幕录制授权。若截图仍提示无权限，请到"
    echo "系统设置 → 隐私与安全性 → 屏幕录制 重新勾选 ${APP_NAME} 并重启。"
fi
