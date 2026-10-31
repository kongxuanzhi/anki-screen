#!/usr/bin/env bash
# macOS 打包：产出 dist/AnkiSnip.app
#
# 前置：
#   python3 -m venv .venv && source .venv/bin/activate
#   pip install -r requirements.txt -r requirements-dev.txt
#
# 签名 / 公证（分发给他人时必需，本地自用可跳过）：
#   1) 签名（需要先安装 Developer ID Application 证书）
#      codesign --deep --force --options runtime \
#               --sign "Developer ID Application: Your Name (TEAMID)" \
#               --entitlements packaging/entitlements.plist dist/AnkiSnip.app
#   2) 公证
#      ditto -c -k --keepParent dist/AnkiSnip.app AnkiSnip.zip
#      xcrun notarytool submit AnkiSnip.zip --keychain-profile "AC_PROFILE" --wait
#      xcrun stapler staple dist/AnkiSnip.app
#
# 注意：即使签名公证完成，用户第一次运行仍需手动授予「辅助功能」与「屏幕录制」。
set -euo pipefail
cd "$(dirname "$0")/.."

python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-dev.txt

rm -rf build dist
python -m PyInstaller packaging/ankisnip.spec --noconfirm

echo
echo "产物：dist/AnkiSnip.app"
echo "提示：首次运行请在 系统设置 → 隐私与安全性 中授予「辅助功能」与「屏幕录制」，"
echo "      授权对象是 AnkiSnip.app 本身（不是终端），授权后需完全退出再启动。"
