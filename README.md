# ComfyUI 全能插件

AstrBot 插件 — 连接本地/云端 ComfyUI，实现文生图、图生图、图生视频、图片编辑等功能。自带 WebUI 可视化面板（赛博魔法主题），支持魔导书提示词库。

---

## 📋 目录

1. [快速开始](#快速开始)
2. [命令列表](#命令列表)
3. [WebUI 可视化界面](#webui-可视化界面)
4. [工作流配置](#工作流配置)
5. [魔导书系统](#魔导书系统)
6. [LLM 工具调用](#llm-工具调用)
7. [测试](#测试)
8. [常见问题](#常见问题)
9. [架构概览](#架构概览)
10. [开发者指南](#开发者指南)

---

## 快速开始

### 前置条件

- 已部署 ComfyUI（本地或云端），默认端口 `8188`
- 已安装 AstrBot
- 已将 API 格式的工作流 JSON 文件放入指定工作流目录（默认 `E:\Comfyui\qq\工作流\`）

### 安装插件

将插件文件夹放入 AstrBot 的 `data/plugins/` 目录，重启 AstrBot 即可自动加载。

### 启动后访问

```
WebUI 管理面板：http://127.0.0.1:8898
```

### 基本用法（QQ 群内）

```
/画 一个白发Miku
/图生图 把背景改成红色    （需引用图片）
/帮助
```

---

## 命令列表

| 命令 | 说明 | 示例 |
|------|------|------|
| `/画 [比例] 提示词` | 文生图 | `/画 9:16 白发Miku` |
| `/图生图 [降噪值] 提示词` | 图生图 | `/图生图 0.7 把背景改成红色` |
| `/图生视频` | 图生视频 | 需引用图片或 @用户 |
| `/编辑 提示词` | 编辑图片（最多3张参考图） | `/编辑 把这个猫换成狗` |
| `/工作流 [编号/关键词]` | 查看/切换工作流 | `/工作流 3` |
| `/切换 [编号/关键词]` | 快速切换 | `/切换 anime` |
| `/比例 [编号/比例名]` | 切换图片比例 | `/比例 16:9` |
| `/分辨率 [等级]` | 设置质量等级 | `/分辨率 1080p` |
| `/执行 提示词` | 执行当前工作流（不限分类） | `/执行 风景画` |
| `/队列` | 查看 ComfyUI 队列状态 | `/队列` |
| `/停止` | 停止当前用户的生成任务 | `/停止` |
| `/撤回` | 撤回最后一张生成的图片/视频 | `/撤回` |
| `/帮助` | 显示帮助信息 | `/帮助` |

### 指令详解

#### 🎨 `/画` — 文生图
- 第一个参数可选：比例（`9:16`、`1:1`、`4:3`、`16:9` 等）
- 剩余部分为提示词
- 不加比例时使用当前默认比例

#### 🖼️ `/图生图` — 图生图
- **引用图片**：回复一张带图片的消息 + 发送命令
- **@ 用户头像**：@用户 + 发送命令（无引用图片时生效）
- 可选第一个参数为降噪值（`0.1` ~ `0.8`，越低变化越小）

#### 🎬 `/图生视频` — 图生视频
- 需引用图片或 @用户获取头像
- 当前工作流名称需含 `视频`、`wan`、`ltx`、`animate`、`video` 等关键词
- 视频生成时间较长

#### ✏️ `/编辑` — 图片编辑
- 支持引用图片、@用户、上传图片（最多3张参考图）
- 图片按顺序传入工作流的 LoadImage 节点

#### 📂 `/工作流` — 工作流管理
- 显示所有工作流（按分类分组），`✅` 标记当前工作流
- `/工作流 3` → 切换到编号 3
- `/工作流 anime` → 按关键词搜索并切换
- 选择后 10 秒内回复数字可切换

#### 📐 `/比例` — 图片比例
- 7 种比例：`1:1` `3:4` `4:3` `9:16` `16:9` `2:3` `3:2`
- `/比例 16:9` → 按名称切换

#### 🎚️ `/分辨率` — 质量等级
| 等级 | 像素数 | 说明 |
|------|--------|------|
| 480p | ~399K | 快速出图，SD 质量 |
| 720p | ~922K | 标清 |
| 1080p | ~2.07M | 高清 |
| 2K | ~3.69M | 超清 |
| 4K | ~8.29M | 原画，最慢 |

---

## WebUI 可视化界面

插件启动后自动启动 WebUI（默认端口 `8898`）。

> ⚠️ **浏览器兼容性**：推荐使用 **Firefox**。该 WebUI 使用了大量 `backdrop-filter: blur()` 玻璃态效果，在 Chromium 内核浏览器（Edge/Chrome）上可能因 GPU 黑名单导致卡顿。Firefox（非 Chromium 内核）无此问题，全速运行。

### 设计主题 — 「赛博魔法」

深紫黑基底 + 品红/电光蓝渐变，支持光/暗双主题手动切换，透明滑块控制面板半透明度。

### 功能概览

| 区域 | 功能 |
|------|------|
| **工作流管理** | 查看/切换/搜索/分类/隐藏/编辑工作流 |
| **提示词面板** | 输入正面/负面提示词，自动保存 |
| **节点配置** | 标记各节点角色（🎯 正面 / 🚫 负面 / 📐 分辨率 / 📤 图片上传） |
| **参数面板** | 设置质量等级、图片比例 |
| **魔导书** | 提示词标签搜索引擎（角色/画师/服装/场景/光影/姿势/镜头） |
| **画廊** | 纵向滚动浏览/删除/批量删除/撤回生成图片 |
| **设置面板** | 配置端口、目录、ComfyUI 地址、上传模式 |
| **图片灯箱** | 点击放大预览，支持缩放/拖拽/下载 |
| **实时进度** | WebSocket 实时展示生成进度与阶段 |

### 顶部栏

- **上行**（品牌区）：Logo + 标题 + 主题切换（亮/暗/背景/设置）
- **下行**（状态区）：ComfyUI 连接状态、队列数、当前工作流、LAN/IPv6 开关、透明滑块、上传模式

### 配置项

| 配置 | 默认值 | 说明 |
|------|--------|------|
| 工作流目录 | `E:\Comfyui\qq\工作流\` | 工作流 JSON 文件存放目录 |
| 输出目录 | `E:\Comfyui\qq\保存\` | 生成图片保存目录 |
| ComfyUI 输入目录 | `E:\Comfyui\qq\upload\` | 上传图片到 ComfyUI 的 input 目录 |
| ComfyUI 地址 | `127.0.0.1:8188` | ComfyUI 服务地址 |
| 上传模式 | `local` | 本地模式/远程模式 |
| 局域网访问 | 关 | 开启后可通过局域网 IP 访问 WebUI |
| IPv6 访问 | 关 | 开启后可通过 IPv6 访问 WebUI |
| WebUI 端口 | `8898` | WebUI 管理面板端口 |

---

## 工作流配置

### 目录结构

```
工作流目录/
├── Anima动漫画图.json          ← API 格式的工作流 JSON
├── FLUX漫转真人.json
├── 基础图生视频.json
└── 原json/                    ← 非 API 格式的备份版本
```

### 工作流分类

在 WebUI 的工作流管理器中，可为每个工作流设置分类标签。分类决定了该工作流在 `/画`、`/图生图`、`/图生视频`、`/编辑` 命令下的可用性。

分类存储在 `plugin_config.json` 的 `__wf_categories__` 字段。

### 节点配置（WebUI 中设置）

为工作流指定各节点的角色（在 WebUI 节点面板中操作）：

| 角色 | 标记 | 说明 |
|------|------|------|
| 🎯 正面提示词节点 | `setPromptNode` | 注入用户输入的正面提示词 |
| 🚫 负面提示词节点 | `setNegativeNode` | 注入通用负面提示词 |
| 📐 分辨率节点 | `setResNode` | 覆盖宽高比和分辨率 |
| 📤 图片上传节点 | `setLoadImageNode0~2` | 图生图/编辑时传入参考图 |

**核心原则**：插件只会覆盖用户在 WebUI 中显式标记过的节点。未标记的节点保持工作流原有值不变。

---

## 魔导书系统

「魔导书」是插件的提示词标签搜索引擎，内置超过 24000 条角色数据和海量画师/服装/场景/光影/姿势/镜头标签。

### 数据来源

```
data/
├── anima/
│   ├── characters.json   (2.0MB / 24000+ 角色)
│   ├── artists.json      (7.9MB / 画师)
│   └── clothing.json     (165KB / 服装)
├── scene/environment.json   (场景)
├── lighting/lighting.json   (光影)
├── shot/framing.json        (镜头构图)
├── pose_action/             (姿势)
└── custom/                  (用户自定义扩展)
```

### 功能

- **多维度搜索**：角色（按 IP/发色/瞳色/人数）、画师（按热度）、服装（按分类/颜色）
- **中文别名**：中英文双语标签，自动匹配
- **批量复制**：一键复制当前页全部标签
- **分页浏览**：支持搜索和筛选
- **数据源管理**：WebUI 中可直接管理数据源

### 在 WebUI 中使用

魔导书按钮位于**正面提示词区域顶部**。点击后弹出底部面板，可：
1. 选择数据源（角色/画师/服装/场景等）
2. 使用筛选维度缩小范围
3. 点击标签自动复制到剪贴板

---

## LLM 工具调用

插件注册了 11 个 LLM 可调用工具（通过 `@filter.llm_tool` 注册 dataclass），供支持 Function Calling 的 LLM 自动调用：

| 工具名 | 功能 | 关键参数 |
|--------|------|---------|
| `comfyui_draw` | 文生图 | `prompt`, `ratio` |
| `comfyui_img2img` | 图生图 | `prompt`, `image_url`, `denoise` |
| `comfyui_video` | 图生视频 | `image_url`, `prompt` |
| `comfyui_edit` | 编辑图片 | `prompt`, `image_urls`（最多3张） |
| `comfyui_random` | 随机抽卡 | `count` |
| `comfyui_switch_workflow` | 切换工作流 | `keyword` |
| `comfyui_list_workflows` | 列出可用工作流 | 无 |
| `comfyui_get_current_workflow` | 获取当前工作流 | 无 |
| `comfyui_queue_status` | 查看队列状态 | 无 |
| `comfyui_stop` | 停止生成 | 无 |
| `comfyui_execute` | 执行工作流（不限分类） | `prompt` |

另外通过 `@filter.llm_tool()` 直接注册了 `comfyui_search_tags`（魔导书搜索）。

### 图片处理机制

当用户发送图片时，插件自动拦截 LLM 请求：
1. 将图片（base64/HTTP URL）下载到临时目录
2. 以文本路径形式告知 LLM
3. LLM 调用 img2img/video/edit 工具时传入该路径
4. 图片上传到 ComfyUI 后即时删除临时文件
5. 定时清理兜底（每 1 小时清理超过 24 小时的文件）

适用于纯文本模型（如 DeepSeek V4 Flash）不支持图片消息类型的场景。

---

## 测试

插件附带完整的自动化测试脚本（644 行 / 27 项测试）：

```bash
# 启动测试服务器 + 运行全部测试
python test_plugin.py --start

# 测试已运行的服务器
python test_plugin.py --url http://127.0.0.1:8898

# 禁用 ANSI 颜色
python test_plugin.py --start --no-color
```

### 测试覆盖

| 分类 | 覆盖内容 | 项数 |
|------|---------|------|
| T01-Server | 服务器启动 | 1 |
| T02-HTML | 标签闭合 / DOM ID / data-action 合法性 | 4 |
| T03-JS | 语法错误 / 函数存在 / action 映射完整 | 6 |
| T04-ICONS | 图标键名合法 / 调用有效 / 无残留 emoji | 4 |
| T05-API | 4 个关键 API 路由响应 | 4 |
| T06-CSS | CSS 变量完整 / 无拼写错误 | 2 |
| T07-Edge case | 空列表 / 404 / 无 body / 中文字宽 | 4 |
| T08-Server | 测试服务器可达 | 1 |

---

## 常见问题

### Q: WebUI 非常卡顿，滚动/弹窗掉帧

> **这是已知问题！** 

WebUI 使用了大量 `backdrop-filter: blur(20px~28px)` 玻璃态效果，这些效果依赖 GPU 加速。如果浏览器因 GPU 黑名单而回退到 CPU 软渲染，会严重卡顿。

**解决方案**：使用 **Firefox** 浏览器访问 WebUI。Firefox 使用 WebRender（非 Chromium 内核），没有 GPU 黑名单问题，全速运行。

### Q: 生成失败/超时

- 检查 ComfyUI 是否正常运行（默认 `127.0.0.1:8188`）
- 检查 ComfyUI 地址配置是否正确
- 检查 ComfyUI 是否加载了所需的模型

### Q: WebUI 无法访问

- 默认绑定 `127.0.0.1`，仅本机可访问
- 在设置面板中开启「局域网访问」后，其他设备可通过局域网 IP 访问
- 云服务器需要开放对应端口的安全组/防火墙

### Q: 画廊没有图片

- 检查输出目录配置是否正确
- 只有插件生成并保存到输出目录的图片才会显示

### Q: 撤回不可用

- 撤回依赖 OneBot API 返回的 `message_id`
- 非 OneBot 平台或使用 `context.send_message()` 标准发送时可能不支持

### Q: 多个用户同时使用

- 支持多用户并发，每个用户任务独立排队
- `/停止` 仅停止当前用户自己的任务

### Q: 浏览器缓存问题

- 修改 `webui.html` 后，按 `Ctrl+F5` 硬刷新清除缓存
- 部分浏览器需要更多次刷新或重启

---

## 架构概览

### 技术栈

| 层 | 技术 | 代码量 |
|----|------|--------|
| 后端 | Python 3.12+ / aiohttp / AstrBot Star 框架 | main.py (3765 行) |
| 前端 | 单 HTML 文件（vanilla JS + CSS），无框架依赖 | webui.html (6075 行) |
| 搜索引擎 | AnimaDataManager 倒排索引 | anima_data.py (150 行) |
| 测试 | 自定义轻量测试框架 | test_plugin.py (644 行) |

### 文件结构

```
astrbot_plugin_comfyui_local/
├── main.py                  # 后端核心（3765行）
├── webui.html               # 前端单页应用（6075行）
├── anima_data.py            # 魔导书搜索引擎
├── test_plugin.py           # 自动化测试（27项）
├── __init__.py              # 空包标记
├── metadata.yaml            # 插件元数据
├── _conf_schema.json        # AstrBot 面板配置 Schema
├── .gitignore
└── data/
    ├── anima/               # 角色/画师/服装（~11MB）
    ├── scene/environment.json
    ├── lighting/lighting.json
    ├── shot/framing.json
    ├── pose_action/         # 姿势数据
    └── custom/              # 用户自定义扩展
```

### API 路由（约 45 条）

全部通过 `aiohttp.web.RouteTableDef` 注册：

| 路径组 | 涉及路由数 | 功能 |
|--------|-----------|------|
| `/api/workflows` | 6 | 工作流列表、切换、隐藏、删除、添加 |
| `/api/workflow-params` | 3 | 节点参数配置 |
| `/api/config` | 2 | 全局配置读写 |
| `/api/gallery` | 4 | 画廊列表、文件、删除、撤回 |
| `/api/grimoire` | 7 | 魔导书 CRUD + 搜索 |
| `/api/anima` | 2 | 魔导书搜索、统计 |
| `/api/progress` | 1 | WebSocket 实时进度 |
| `/api/proxy` | 1 | 图片代理（SSRF 白名单） |
| `/api/comfy-models` | 1 | 模型列表 |
| `/api/wf-category*` | 3 | 工作流分类 |
| `/api/workflow-bind*` | 3 | 独立工作流绑定 |
| `/api/upload-image` | 1 | 图片上传 |
| `/api/set-*` | 2 | 质量/比例设置 |
| `/api/reset` | 1 | 重置 |
| `/api/open-dir` | 1 | 打开本地目录 |
| `/api/context-workflows*` | 2 | 上下文工作流 |

### 后端关键设计

| 特性 | 实现 |
|------|------|
| 用户级生成锁 | `_generating_locks[user_id]` 字典 |
| WebSocket 进度 | `_ws_progress_listener` 后台任务 |
| 原子写入 | `.json.tmp` → `os.rename()` |
| SSRF 防护 | `allowed_prefix` 白名单 |
| 画廊 OOM 防护 | `max_scan = 5000` |
| 节点控制 | 仅覆盖 WebUI 显式标记的节点 |
| 定时清理 | 每小时清理 24 小时前的临时文件 |

---

## 开发者指南

### 本地开发

| 修改对象 | 修改后操作 |
|---------|-----------|
| `main.py` | 重启 AstrBot |
| `webui.html` | 浏览器 `Ctrl+F5` 硬刷新 |
| `anima_data.py` | 重启 AstrBot |
| `plugin_config.json` | 可通过 WebUI 设置面板修改，也可直接编辑 |
| 数据文件（`data/*.json`） | 重启 AstrBot |

### 添加新的 data-action（前端交互）

1. 在 HTML 中添加 `data-action="myAction"`
2. 在 JS 的 `initActionDelegation()` → `actionMap` 中注册处理函数：
```javascript
myAction: (el, e) => { /* 处理逻辑 */ },
```

### 添加 LLM 工具

在 `main.py` 中创建新的 `FunctionTool` dataclass，然后在 `_register_tools()` 中注册：
```python
class MyNewTool(FunctionTool):
    def __init__(self):
        super().__init__("工具名", "功能描述", params=[...])
```

### 添加中文别名

在 `anima_data.py` 的 `CN_ALIAS` 字典中添加：
```python
"角色中文名": "角色英文名 (作品名)",
```

### CSS 变量修改

需同时在两处定义：
```css
:root { --my-var: 暗色值; }
[data-theme="light"] { --my-var: 亮色值; }
```

---

## 端口速查

| 服务 | 端口 | 说明 |
|------|------|------|
| WebUI 管理面板 | `8898` | 浏览器访问 |
| ComfyUI 后端 | `8188` | ComfyUI 服务 |
| AstrBot 主面板 | `6185` | AstrBot 管理 |
| 测试服务器 | `19840` | 测试脚本专用 |

---

## 跨平台兼容

- **QQ**（OneBot）：完整功能，含撤回
- **Telegram / Discord / 飞书**：除撤回外所有功能正常
- **浏览器**：推荐 Firefox（GPU 全速），Edge/Chrome 也可用（可能有 GPU 黑名单问题）

---

## 许可证

MIT License

**作者**：lxmsqm, 黑谷  
**仓库**：https://github.com/lxmsqm/astrbot-omnipotent-comfyui
