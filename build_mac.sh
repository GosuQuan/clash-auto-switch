#!/bin/bash
# Clash 自动切换器 - macOS 构建脚本
# 用法：在装有 Python 3.10+ 的 Mac 上，进入本目录执行  bash build_mac.sh
set -e
cd "$(dirname "$0")"

echo "==> 安装依赖"
python3 -m pip install --upgrade pip
python3 -m pip install pyinstaller pystray pyobjc-framework-Cocoa pyobjc-framework-Quartz pillow

echo "==> 构建 ClashAutoSwitch.app"
python3 -m PyInstaller --clean -y \
  --windowed \
  --icon icon.icns \
  --add-data "icon.ico:." \
  --hidden-import pystray --hidden-import pystray._darwin \
  --osx-bundle-identifier com.clash.autoswitch \
  --name ClashAutoSwitch \
  clash_auto_switch.py

echo "==> 打包 zip"
cd dist
zip -rq ../ClashAutoSwitch-macOS.zip ClashAutoSwitch.app
cd ..

echo "==> 打包 DMG"
hdiutil create -volname "ClashAutoSwitch" \
  -srcfolder dist/ClashAutoSwitch.app \
  -ov -format UDZO \
  ClashAutoSwitch-macOS.dmg

echo ""
echo "完成！产物："
echo "  dist/ClashAutoSwitch.app          ← 双击运行（首次：右键 → 打开）"
echo "  ClashAutoSwitch-macOS.dmg         ← DMG 安装包（可直接飞书发送）"
echo "  ClashAutoSwitch-macOS.zip         ← zip 备选"
echo ""
echo "提示：未签名 app 首次打开被 Gatekeeper 拦时："
echo "  右键 ClashAutoSwitch.app → 打开；或执行  xattr -cr dist/ClashAutoSwitch.app"
