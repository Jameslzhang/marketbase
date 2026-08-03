# Add to Chat - VS Code 扩展

## 本地安装

### 方式一：一键安装（推荐）

双击运行 `install.bat`，自动将扩展安装到 VS Code/Trae/Cursor 中。

### 方式二：手动安装

```bash
# 1. 打包扩展
cd tools/vscode-add-to-chat
npm install -g @vscode/vsce
vsce package

# 2. 安装 .vsix 文件
code --install-extension add-to-chat-1.0.0.vsix
# 或 Trae:
trae --install-extension add-to-chat-1.0.0.vsix
```

### 方式三：开发模式加载

1. 将 `tools/vscode-add-to-chat` 目录复制到 VS Code 扩展目录：
   - Windows: `%USERPROFILE%\.vscode\extensions\add-to-chat`
   - Mac: `~/.vscode/extensions/add-to-chat`
2. 重启 VS Code

## 使用方式

| 操作 | 方式 |
|------|------|
| 添加选中文本 | 编辑器选中文本 → `Ctrl+Shift+A` |
| 添加终端输出 | 终端选中文本 → `Ctrl+Shift+A` |
| 右键添加 | 选中文本 → 右键 → "📎 添加到对话上下文" |
| 查看上下文 | 点击底部状态栏 "上下文: N" |
| 查看上下文 | `Ctrl+Shift+P` → "打开对话上下文面板" |
| 复制全部 | 面板中点击 "复制全部"，或 `Ctrl+Shift+P` → "复制全部上下文" |
| 清空上下文 | 面板中点击 "清空"，或 `Ctrl+Shift+P` → "清空上下文" |

## 配置

在 VS Code 设置中搜索 `add-to-chat`：

- `add-to-chat.maxItems`: 最大保留上下文条数（默认 50）
- `add-to-chat.includeFileName`: 是否包含文件名（默认 true）
- `add-to-chat.includeLineNumbers`: 是否包含行号（默认 true）
- `add-to-chat.autoCopy`: 添加时自动复制到剪贴板（默认 false）