const vscode = require('vscode');
const path = require('path');

/**
 * Add to Chat - VS Code Extension
 * 
 * 功能:
 * 1. 选中文本 → Ctrl+Shift+A / 右键菜单 → 添加到对话上下文
 * 2. 状态栏显示上下文条数，点击打开面板
 * 3. WebView 面板管理所有上下文片段
 * 4. 支持编辑器、终端、输出面板的选中内容
 */

// ── Global State ──
let contextItems = [];          // { id, text, source, language, lineRange, timestamp }
let statusBarItem = null;
let contextPanel = null;
let nextId = 1;

// ── Activation ──
function activate(context) {
    console.log('Add to Chat: 扩展已激活');

    // 注册命令
    context.subscriptions.push(
        vscode.commands.registerCommand('add-to-chat.addSelection', addSelection),
        vscode.commands.registerCommand('add-to-chat.addFromTerminal', addFromTerminal),
        vscode.commands.registerCommand('add-to-chat.openPanel', openPanel),
        vscode.commands.registerCommand('add-to-chat.copyAll', copyAll),
        vscode.commands.registerCommand('add-to-chat.clearAll', clearAll),
    );

    // 状态栏
    statusBarItem = vscode.window.createStatusBarItem(
        vscode.StatusBarAlignment.Right, 100
    );
    statusBarItem.command = 'add-to-chat.openPanel';
    statusBarItem.tooltip = '点击打开对话上下文面板';
    updateStatusBar();
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // 监听编辑器选区变化（用于后续可能的 CodeLens / hover）
    context.subscriptions.push(
        vscode.window.onDidChangeTextEditorSelection(onSelectionChange)
    );
}

// ── Selection Change (准备 CodeLens / hover 提示) ──
function onSelectionChange(e) {
    if (!e.textEditor || e.selections.length === 0) return;
    const sel = e.selections[0];
    if (sel.isEmpty) {
        statusBarItem.text = contextItems.length > 0
            ? `$(comment-discussion) 上下文: ${contextItems.length}`
            : `$(comment-discussion) 选中文本后 Ctrl+Shift+A 添加`;
        return;
    }
    statusBarItem.text = `$(comment-discussion) 上下文: ${contextItems.length} | Ctrl+Shift+A 添加选中`;
}

// ── Add Selection from Editor ──
async function addSelection() {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        // 尝试从终端获取
        await addFromTerminal();
        return;
    }

    const selection = editor.selection;
    if (selection.isEmpty) {
        vscode.window.showInformationMessage('请先选中要添加的文本');
        return;
    }

    const text = editor.document.getText(selection);
    const fileName = editor.document.fileName;
    const config = vscode.workspace.getConfiguration('add-to-chat');

    let formatted = '';

    // 文件名头
    if (config.get('includeFileName', true)) {
        const workspaceFolder = vscode.workspace.getWorkspaceFolder(editor.document.uri);
        let relativePath = fileName;
        if (workspaceFolder) {
            relativePath = path.relative(workspaceFolder.uri.fsPath, fileName);
        }
        const lineStart = selection.start.line + 1;
        const lineEnd = selection.end.line + 1;
        formatted += `// 📄 ${relativePath}`;
        if (config.get('includeLineNumbers', true)) {
            formatted += lineStart === lineEnd
                ? ` (第 ${lineStart} 行)`
                : ` (第 ${lineStart}-${lineEnd} 行)`;
        }
        formatted += '\n';
    }

    formatted += text;

    addItem({
        text: formatted,
        source: 'editor',
        fileName: fileName,
        language: editor.document.languageId,
        lineRange: {
            start: selection.start.line + 1,
            end: selection.end.line + 1,
        },
    });

    // 自动复制
    if (config.get('autoCopy', false)) {
        await vscode.env.clipboard.writeText(formatted);
    }

    vscode.window.setStatusBarMessage(
        `$(check) 已添加到对话上下文 (${contextItems.length} 条)`,
        3000
    );
}

// ── Add Selection from Terminal ──
async function addFromTerminal() {
    const terminal = vscode.window.activeTerminal;
    if (!terminal) {
        vscode.window.showInformationMessage('没有活动的终端');
        return;
    }

    // 终端选中内容通过 copy+paste 方式获取
    // VS Code 终端选中后先复制
    await vscode.commands.executeCommand(
        'workbench.action.terminal.copySelection'
    );

    // 短暂延迟后读取剪贴板
    await new Promise(resolve => setTimeout(resolve, 150));
    const text = await vscode.env.clipboard.readText();

    if (!text || text.trim().length === 0) {
        vscode.window.showInformationMessage('请先在终端中选中要添加的文本');
        return;
    }

    addItem({
        text: `// 🖥 终端输出\n${text}`,
        source: 'terminal',
        fileName: terminal.name || 'Terminal',
    });

    vscode.window.setStatusBarMessage(
        `$(check) 已从终端添加到对话上下文 (${contextItems.length} 条)`,
        3000
    );
}

// ── Add Item ──
function addItem(item) {
    const config = vscode.workspace.getConfiguration('add-to-chat');
    const maxItems = config.get('maxItems', 50);

    contextItems.unshift({
        id: nextId++,
        ...item,
        timestamp: new Date().toISOString(),
    });

    // 超出上限移除旧条目
    if (contextItems.length > maxItems) {
        contextItems = contextItems.slice(0, maxItems);
    }

    updateStatusBar();
    if (contextPanel) {
        contextPanel.update();
    }
}

// ── Status Bar ──
function updateStatusBar() {
    if (!statusBarItem) return;

    if (contextItems.length === 0) {
        statusBarItem.text = '$(comment-discussion) 选中文本后 Ctrl+Shift+A 添加';
        statusBarItem.backgroundColor = undefined;
    } else {
        statusBarItem.text = `$(comment-discussion) 上下文: ${contextItems.length}`;
        statusBarItem.backgroundColor = new vscode.ThemeColor(
            'statusBarItem.warningBackground'
        );
    }
}

// ── WebView Panel ──
function openPanel() {
    if (contextPanel) {
        contextPanel.reveal();
        return;
    }

    contextPanel = vscode.window.createWebviewPanel(
        'addToChat',
        '对话上下文',
        vscode.ViewColumn.Two,
        {
            enableScripts: true,
            retainContextWhenHidden: true,
        }
    );

    contextPanel.onDidDispose(() => {
        contextPanel = null;
    });

    contextPanel.webview.onDidReceiveMessage(async (message) => {
        switch (message.command) {
            case 'copyItem':
                await vscode.env.clipboard.writeText(message.text);
                vscode.window.setStatusBarMessage('$(check) 已复制', 2000);
                break;
            case 'copyAll':
                const all = contextItems.map(i => i.text).join('\n\n---\n\n');
                await vscode.env.clipboard.writeText(all);
                vscode.window.setStatusBarMessage(
                    `$(check) 已复制全部 ${contextItems.length} 条上下文`,
                    3000
                );
                break;
            case 'removeItem':
                contextItems = contextItems.filter(i => i.id !== message.id);
                updateStatusBar();
                contextPanel.update();
                break;
            case 'clearAll':
                contextItems = [];
                updateStatusBar();
                contextPanel.update();
                vscode.window.setStatusBarMessage('$(check) 上下文已清空', 2000);
                break;
            case 'refresh':
                contextPanel.update();
                break;
        }
    });

    contextPanel.update = () => {
        contextPanel.webview.html = getWebviewContent(contextItems);
    };

    contextPanel.update();
}

function getWebviewContent(items) {
    const itemsHtml = items.length === 0
        ? `<div class="empty">
            <div class="empty-icon">📋</div>
            <div class="empty-title">暂无上下文</div>
            <div class="empty-hint">
                在编辑器中选中文本，按 <kbd>Ctrl+Shift+A</kbd><br>
                或在终端中选中文本，按 <kbd>Ctrl+Shift+A</kbd><br>
                也可以右键选中内容，选择"添加到对话上下文"
            </div>
          </div>`
        : items.map((item, index) => {
            const sourceIcon = item.source === 'terminal' ? '🖥' : '📄';
            const sourceLabel = item.source === 'terminal' ? '终端' : '编辑器';
            const time = new Date(item.timestamp).toLocaleTimeString('zh-CN', {
                hour: '2-digit',
                minute: '2-digit',
            });
            const truncated = item.text.slice(0, 150).replace(/\n/g, '↵');
            const preview = truncated.length < item.text.length
                ? truncated + '...'
                : truncated;

            return `<div class="item">
                <div class="item-header">
                    <span class="item-source">${sourceIcon} ${sourceLabel}</span>
                    <span class="item-time">${time}</span>
                    <span class="item-index">#${items.length - index}</span>
                </div>
                <div class="item-preview" title="${escapeHtml(item.text.slice(0, 500))}">
                    ${escapeHtml(preview)}
                </div>
                <div class="item-actions">
                    <button class="btn" onclick="copyItem(${item.id})" title="复制此条">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                        复制
                    </button>
                    <button class="btn danger" onclick="removeItem(${item.id})" title="删除此条">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                        删除
                    </button>
                </div>
            </div>`;
          }).join('');

    return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
        font-family: var(--vscode-font-family, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif);
        font-size: var(--vscode-font-size, 13px);
        color: var(--vscode-foreground);
        background: var(--vscode-editor-background);
        padding: 0;
    }
    .header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 12px 16px;
        border-bottom: 1px solid var(--vscode-panel-border);
        position: sticky;
        top: 0;
        background: var(--vscode-editor-background);
        z-index: 10;
    }
    .header-title {
        font-weight: 600;
        font-size: 14px;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .header-badge {
        background: var(--vscode-badge-background);
        color: var(--vscode-badge-foreground);
        padding: 1px 8px;
        border-radius: 10px;
        font-size: 11px;
        font-weight: 500;
    }
    .header-actions {
        display: flex;
        gap: 6px;
    }
    .btn {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 4px 10px;
        border-radius: 4px;
        border: 1px solid var(--vscode-panel-border);
        background: var(--vscode-button-secondaryBackground);
        color: var(--vscode-button-secondaryForeground);
        font-size: 12px;
        cursor: pointer;
        transition: all 0.15s;
    }
    .btn:hover {
        background: var(--vscode-button-secondaryHoverBackground);
    }
    .btn.primary {
        background: var(--vscode-button-background);
        color: var(--vscode-button-foreground);
        border-color: var(--vscode-button-background);
    }
    .btn.primary:hover {
        background: var(--vscode-button-hoverBackground);
    }
    .btn.danger {
        color: var(--vscode-errorForeground);
    }
    .btn.danger:hover {
        background: var(--vscode-inputValidation-errorBackground);
        color: var(--vscode-inputValidation-errorForeground);
    }
    .list {
        padding: 8px;
        display: flex;
        flex-direction: column;
        gap: 8px;
    }
    .item {
        border: 1px solid var(--vscode-panel-border);
        border-radius: 6px;
        overflow: hidden;
        transition: border-color 0.15s;
    }
    .item:hover {
        border-color: var(--vscode-focusBorder);
    }
    .item-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 8px 10px;
        background: var(--vscode-sideBar-background);
        font-size: 11px;
        color: var(--vscode-descriptionForeground);
    }
    .item-source {
        display: flex;
        align-items: center;
        gap: 4px;
    }
    .item-time {
        margin-left: auto;
        margin-right: 8px;
    }
    .item-index {
        font-weight: 600;
        color: var(--vscode-textLink-foreground);
    }
    .item-preview {
        padding: 8px 10px;
        font-family: var(--vscode-editor-font-family, 'Consolas', 'Courier New', monospace);
        font-size: 12px;
        line-height: 1.5;
        white-space: pre-wrap;
        word-break: break-all;
        max-height: 80px;
        overflow: hidden;
        color: var(--vscode-editor-foreground);
        background: var(--vscode-editor-background);
    }
    .item-actions {
        display: flex;
        gap: 4px;
        padding: 6px 10px;
        border-top: 1px solid var(--vscode-panel-border);
        background: var(--vscode-sideBar-background);
    }
    .empty {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 40px 20px;
        text-align: center;
        color: var(--vscode-descriptionForeground);
    }
    .empty-icon { font-size: 32px; margin-bottom: 12px; opacity: 0.5; }
    .empty-title { font-size: 14px; font-weight: 500; margin-bottom: 8px; }
    .empty-hint {
        font-size: 12px;
        line-height: 1.8;
        color: var(--vscode-descriptionForeground);
        opacity: 0.7;
    }
    .empty-hint kbd {
        background: var(--vscode-badge-background);
        color: var(--vscode-badge-foreground);
        padding: 1px 6px;
        border-radius: 3px;
        font-family: inherit;
        font-size: 11px;
    }
</style>
</head>
<body>
<div class="header">
    <div class="header-title">
        💬 对话上下文
        <span class="header-badge">${items.length}</span>
    </div>
    <div class="header-actions">
        <button class="btn primary" onclick="copyAll()" title="复制全部">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            复制全部
        </button>
        <button class="btn danger" onclick="clearAll()" title="清空全部">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            清空
        </button>
    </div>
</div>
<div class="list">
    ${itemsHtml}
</div>

<script>
    const vscode = acquireVsCodeApi();

    function copyItem(id) {
        const text = document.querySelector(
            '[data-item-id="' + id + '"]'
        )?.dataset?.text;
        vscode.postMessage({ command: 'copyItem', id: id });
    }

    function removeItem(id) {
        vscode.postMessage({ command: 'removeItem', id: id });
    }

    function copyAll() {
        vscode.postMessage({ command: 'copyAll' });
    }

    function clearAll() {
        if (confirm('确定要清空所有上下文吗？')) {
            vscode.postMessage({ command: 'clearAll' });
        }
    }
</script>
</body>
</html>`;
}

function escapeHtml(str) {
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// ── Copy All ──
async function copyAll() {
    if (contextItems.length === 0) {
        vscode.window.showInformationMessage('上下文为空');
        return;
    }
    const all = contextItems.map(i => i.text).join('\n\n---\n\n');
    await vscode.env.clipboard.writeText(all);
    vscode.window.showInformationMessage(
        `已复制全部 ${contextItems.length} 条上下文到剪贴板`
    );
}

// ── Clear All ──
async function clearAll() {
    if (contextItems.length === 0) {
        vscode.window.showInformationMessage('上下文已经为空');
        return;
    }
    const result = await vscode.window.showWarningMessage(
        `确定要清空全部 ${contextItems.length} 条上下文吗？`,
        { modal: true },
        '确定清空'
    );
    if (result === '确定清空') {
        contextItems = [];
        updateStatusBar();
        if (contextPanel) {
            contextPanel.update();
        }
        vscode.window.setStatusBarMessage('$(check) 上下文已清空', 3000);
    }
}

// ── Deactivation ──
function deactivate() {
    contextItems = [];
    if (contextPanel) {
        contextPanel.dispose();
        contextPanel = null;
    }
}

module.exports = { activate, deactivate };