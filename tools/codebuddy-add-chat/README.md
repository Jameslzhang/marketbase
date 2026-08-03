# Add to Chat — CodeBuddy 插件

选中编辑器/终端/输出区域的文本 → 一键添加到对话上下文。

## 功能

| 功能 | 说明 |
|------|------|
| `/add-to-chat:add` | 斜杠命令，将选中内容添加到对话上下文 |
| `context-manager` 技能 | AI 自动识别"添加到对话"意图，管理上下文片段 |
| 快捷键 `Ctrl+Shift+A` | 自定义快捷键（需配置 keybindings） |

## 安装

### 方式一：本地安装（推荐）

```bash
# 在项目根目录下
codebuddy --plugin-dir ./tools/codebuddy-add-chat
```

### 方式二：安装到用户目录

```powershell
# Windows PowerShell
Copy-Item -Recurse tools/codebuddy-add-chat $env:USERPROFILE/.codebuddy/plugins/add-to-chat
```

然后在 CodeBuddy 中运行 `/reload-plugins` 刷新插件。

### 方式三：项目级安装

在项目根目录 `.codebuddy/settings.json` 中添加：

```json
{
  "plugins": {
    "add-to-chat": {
      "enabled": true
    }
  }
}
```

## 快捷键配置

在 CodeBuddy 中运行 `/keybindings` 打开配置文件，添加：

```json
{
  "bindings": [
    {
      "context": "Global",
      "bindings": {
        "ctrl+shift+a": "add-to-chat:add"
      }
    }
  ]
}
```

修改后自动生效，无需重启。

## 使用方式

### 斜杠命令
在对话中输入 `/add-to-chat:add`，自动将选中文本添加到对话上下文。

### 自然语言（AI 技能）
直接对 AI 说：
- "把这个加到对话里"
- "add to context"
- "引用这段数据"
- "查看上下文"

### 内置快捷键（CodeBuddy 原生）
- `Ctrl+I` — 选中代码后内联对话
- `Ctrl+Shift+I` — 选中代码后解释
- 右键菜单 → "Add to Chat"

## 工作流程

```
选中文本 → Ctrl+Shift+A → 添加到对话上下文 → AI 分析/处理
                ↓
         或输入 /add-to-chat:add
                ↓
         或说 "把这个加进去"
```

## 文件结构

```
tools/codebuddy-add-chat/
├── .codebuddy-plugin/
│   └── plugin.json          # 插件清单
├── commands/
│   └── add.md               # /add 斜杠命令
├── skills/
│   └── context-manager/
│       └── SKILL.md         # 上下文管理技能
└── README.md
```