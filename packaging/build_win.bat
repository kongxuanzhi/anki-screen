@echo off
REM Windows 打包：产出 dist\AnkiSnip.exe
REM
REM 前置：
REM   python -m venv .venv
REM   .venv\Scripts\activate
REM   pip install -r requirements.txt -r requirements-dev.txt
REM
REM 说明：
REM   * 建议在**干净的虚拟环境**里打包，否则会把无关依赖一起打进去。
REM   * 产物是单目录（onedir）模式，启动更快、也更容易杀毒软件放行。
REM   * 若杀软误报，可用微软的 signtool 加上自己的代码签名证书后重签。

setlocal
pushd "%~dp0.."

python -m pip install --upgrade pip || goto :err
python -m pip install -r requirements.txt -r requirements-dev.txt || goto :err

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

python -m PyInstaller packaging\ankisnip.spec --noconfirm || goto :err

echo.
echo 产物：dist\AnkiSnip\AnkiSnip.exe
echo 提示：Windows 125%%/150%% 缩放下，截图与挖空坐标按物理像素处理，无需额外设置。
popd
exit /b 0

:err
echo 打包失败，请查看上面的错误信息。
popd
exit /b 1
