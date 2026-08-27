---
name: image-reader
description: "Read and parse image files (PNG/JPG/GIF/WebP) to extract visible text, code, UI elements, and structural content. Use when a user attaches an image and asks about its content."
---

# Image Reader

## 描述

读取并解析图片文件内容，提取其中可见的文字、代码、UI 元素和结构信息。当用户上传图片并询问图片内容时使用本 skill。

## 使用场景

- 用户上传代码截图，需要识别代码内容
- 用户上传 UI 设计稿，需要分析界面布局
- 用户上传错误日志截图，需要提取错误信息
- 用户上传配置/文档截图，需要读取文字内容

## 指令

### 读取图片

使用 `read_file` 工具读取图片文件：

```python
read_file(filePath="图片的绝对路径")
```

支持的格式：PNG、JPG、JPEG、GIF、WebP

### 解析内容

读取图片后，根据用户需求分析内容：

1. **代码截图**：识别代码语法、逻辑结构、重复模式
2. **UI 设计**：分析布局、组件、配色、交互元素
3. **错误信息**：提取错误类型、堆栈跟踪、关键行号
4. **文档/配置**：识别文本内容、关键参数、格式

### 输出格式

分析完成后，应清晰描述图片内容，包括：
- 图片类型（代码截图/UI 设计/错误日志/文档等）
- 核心内容概述
- 关键细节（代码行、元素位置、关键文字等）
- 对用户问题的针对性回答

## 示例

用户："@image:screenshot.png 这段代码为什么会报错？"

1. 使用 `read_file` 读取 `screenshot.png`
2. 识别代码内容
3. 分析错误原因
4. 给出修复建议

## 限制

- 仅支持 PNG、JPG、JPEG、GIF、WebP 格式
- 不能处理 PDF、视频等非图片格式
- 低分辨率图片可能导致识别不准确