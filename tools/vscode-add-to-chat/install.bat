@echo off
chcp 65001 >nul
echo ========================================
echo   Add to Chat - VS Code 扩展安装
echo ========================================
echo.

set "EXT_DIR=%USERPROFILE%\.vscode\extensions\add-to-chat"

echo [1/3] 创建扩展目录...
if not exist "%EXT_DIR%" mkdir "%EXT_DIR%"

echo [2/3] 复制扩展文件...
copy /Y "%~dp0package.json" "%EXT_DIR%\package.json" >nul
copy /Y "%~dp0extension.js" "%EXT_DIR%\extension.js" >nul
copy /Y "%~dp0README.md" "%EXT_DIR%\README.md" >nul

echo [3/3] 安装完成!
echo.
echo 扩展路径: %EXT_DIR%
echo.
echo 请重启 VS Code / Trae / Cursor 以加载扩展。
echo.
echo 使用方式:
echo   - 选中代码 → Ctrl+Shift+A → 添加到对话上下文
echo   - 终端选中 → Ctrl+Shift+A → 添加到对话上下文
echo   - 点击底部状态栏 "上下文: N" 查看面板
echo.
pause