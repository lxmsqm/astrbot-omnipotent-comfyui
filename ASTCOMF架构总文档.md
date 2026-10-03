# ASTCOMF 插件架构总文档（AI 接手专用）

> 插件：`astrbot_plugin_comfyui_local`（metadata name: `comfyui_allinone`），当前版本 **v4.9.1**
> 本文档是**结构性/架构性地图**：改什么功能动哪里、加什么功能动哪里。行号基于 2026-10-03 的 main.py（10391 行）与 webui.html（14049 行），行号会随改动漂移，**以函数名/符号名为准定位**。
> 配套文档：`ASTCOMF交接文档.md`（原始交接）、`手机部署交接文档.md`、`llbot环境交接文档.md`（均在同目录）。

---

## 0. 一句话全景

单插件、双文件巨石架构：`main.py`（后端，5 个 Mixin 组成的 Star 插件，内嵌 aiohttp WebUI 服务器）+ `webui.html`（前端，单文件 HTML/CSS/JS，由后端直接 FileResponse）。插件跑在**手机 Termux proot** 的 AstrBot 里，通过 HTTP/WebSocket 控制**PC 上的 ComfyUI**（192.168.0.106:8188）；聊天命令（QQ/飞书）与 WebUI 两条入口最终都汇入同一条生成管线 `_process_and_submit`。

```
聊天入口(QQ/飞书命令 + LLM工具) ─┐
                                ├─→ _process_and_submit() ─→ ComfyUI HTTP/WS ─→ 出图回传/画廊
WebUI入口(前端 fetch /api/*)   ─┘        ↑
        │                                 │
        └── data/user/config.json（所有 __*__ 状态的唯一持久化）
```

**运行拓扑**：
- 手机 192.168.0.105:8022（Termux → proot Ubuntu）跑 AstrBot + 本插件，插件目录 `/root/AstrBot/data/plugins/astrbot_plugin_comfyui_local`，WebUI 端口 8898
- PC 192.168.0.106:8188 跑 ComfyUI（ComfyUI-JZL-MiniMax-H3 等自定义节点）
- 部署方式：PC 上 `E:\AIwork\插件\deploy.sh`（Termux shebang）→ 打包上传 → 手机 `deploy_ok.sh` 覆盖 28 个文件到插件目录（带时间戳 .bak），随后 proot 内 ast 语法校验 + JSON 校验 + random_prompt 导入校验，最后重启 AstrBot
- **PC 侧开发目录**：`E:\AIwork\插件\astrbot_plugin_comfyui_local\`（git 仓库）；补丁工作区 `E:\AIwork\插件\theme_work\`；文档 `E:\AIwork\文档内容\Astrbot插件文档\`

---

## 1. 文件清单与职责

| 文件 | 规模 | 职责 |
|---|---|---|
| `main.py` | 10391 行 | 全部后端：插件类、生成管线、WebUI 服务器、LLM 工具、聊天命令 |
| `webui.html` | 14049 行 | 全部前端：CSS(22–4387) + HTML(4389–5459) + JS 两个 script 块(5459–13970 主逻辑 / 13975–14046 K2 引入) |
| `metadata.yaml` | — | 版本号 + **追加式**版本史 description（新版在前，格式 `vX.Y.Z(标签): 变更`） |
| `_conf_schema.json` | 13 字段 | AstrBot 设置面板 schema（webui/webui_lan/webui_ipv6/prompt_log_days/show_prompt_on_image/send_platform/target_platform/target_id/target_group/anima_* 共 13 项）；**workflow_dir/comfyui_url/webui_port 不在这里**，在 `data/user/config.json` 的 `__local_config__` |
| `anima_data.py` | 868 行起 | Anima 画师/角色/服装数据管理器（AnimaDataManager + 中文分类翻译表），顶层导入 |
| `data_paths.py` | — | v4.3.0 数据分离：`data_dir_resolver()`(词库,插件内) / `user_data_dir_resolver()`(运行时状态,外部 `comfyui_allinone_data/user/`) / `anima_tools_dir_resolver()` / `migrate_out()` |
| `random_prompt.py` | — | 随机提示词组合引擎（SmartComfy 移植）：互斥组/section 顺序/smart_pick；**函数内延迟导入，漏部署会 ImportError 500** |
| `gitee_sync.py` | — | 云端词库同步后端（默认 GitHub：`lxmsqm/astrbot-comfyui-data`），延迟导入 |
| `k2gen/` | 5 文件 | K2 人像提示词引擎 v12：`data.js`(词库,301KB) / `engine.js` / `index.js` / `cli.js`(后端经 node 子进程调用) |
| `data/` | 48 JSON | **静态词库**（k2/ 9 个 + 13 大分类目录 + anima_tools/ 回退源），进 git；**运行时状态不在这里** |
| `theme-xin-*.webp` + favicon* | — | 心主题素材：昼夜横幅、logo、5 导航纹章、8 角色纹章，后端白名单路由伺服 |
| `scripts/legacy/` | 7 脚本 | 一次性数据加工脚本，非运行时依赖，不部署 |

**运行时数据落点（手机端）**：`<AstrBot>/data/comfyui_allinone_data/user/` → `config.json`（一切 `__*__` 状态）、`prompt_log.json`、`cache/`（魔导书图片缓存）、`gitee_sync.json`；工作流 JSON 在用户指定 `workflow_dir`；`.groups.json` sidecar 在工作流文件旁边。

---

## 2. main.py 结构地图

### 2.1 顶层组织（5 个 Mixin）

```
@register (L6617)
class ComfyUILocalPlugin(WorkflowMixin, GenerateMixin, WebUIMixin, GrimoireMixin, LLMToolsMixin, Star)   # L6618
```

| Mixin | 起始行 | 职责域 |
|---|---|---|
| `WorkflowMixin` | 652 | 工作流目录/列表/切换/配置持久化 |
| `GenerateMixin` | 980 | 节点定位、配置应用、注入、提交、组控制、规则硬校验 |
| `WebUIMixin` | 1698 | aiohttp 服务器 + 全部 `/api/*` handler |
| `GrimoireMixin` | 4427 | 魔导书词库 WebUI + 持久化 |
| `LLMToolsMixin` | 6248 | `@filter.llm_tool` 方法（词库 CRUD） |
| 主体类 | 6618 | `__init__`/terminate、聊天命令、ComfyUI 客户端、平台发送 |

模块级还有：`ComfyUITaskError`(L29)、`_PLACEHOLDER_PREFIX_RE`(L44)、`LarkLooseCommandFilter` 飞书宽松命令过滤器(L47–84，容忍消息前 `[图片]/[At:x]` 占位符，其他平台返回 False)、**15 个 FunctionTool dataclass**(L85–651)。

### 2.2 生命周期与配置

- `__init__`(6619)：`_data_migrate_out` → 加载 `data/user/config.json` → 初始化 ~40 个 store/锁 → 注册 LLM 工具(`_register_tools` 6875 → `context.add_llm_tools`) → 启动 WebUI → 扫工作流 → 加载 Anima → 后台任务（上传清理 8645 / 产出清理 8667 / WS 监听 8696）
- `terminate`(10359)：停 WebUI、取消后台任务、清进度态
- 配置读写：`_load_local_config`(6835) / `_save_local_config`(6846, 原子+锁)；工作流配置 `_save_workflow_config`(687) / `_schedule_save_workflow_config`(676, **sync 上下文兜底线程跑 asyncio.run**)；单文件原子写 `_atomic_write_workflow_json`(709)

### 2.3 生成管线（最重要的一条链）

**`_process_and_submit`(8755–9188) 按序执行**，改任何"生成行为"都在这条链上找位置：

1. 刷新工作流列表，快照 wf_name/wf_path 防并发切换（8758）
2. 判定视频工作流：`__is_video__` 或文件名含 wan/ltx/animate/视频；超时图 300s / 视频 14400s（8763）
3. 加载工作流 JSON —— **绝不回退到 原json/ 备份**（节点 ID 不同，8767）
4. 指纹校验 `_compute_fingerprint`(1183)：文件被换则清该工作流全部配置；角色 ID 存在性检查（8794）
5. 分辨率：优先工作流自带 w/h，否则 quality×ratio；`_apply_workflow_config` 后强制重写（8941）
6. 变换链（顺序敏感）：
   `_apply_workflow_config(protect_nodes=LoadImage ids)`(1194) → `_apply_group_modes`(2667, 组禁用=级联删节点) → `_apply_loras`(1337) → `_apply_style_selector`(1388) → 旧版组禁用内联逻辑(8861, source/bind 双闸门) → `__disabled_nodes__` 删除 → cmd_config 合并 → `_set_resolution`(1503) → `_set_load_image`(1535) → `_rebuild_jzl_refs`(2705, JZL ref_images 槽位连续重建) → pin 合并(`_merge_prompt_with_pins` 10189) → `_inject_prompt`(1442) → LoRA 触发词(1484) → 负面词**覆盖写**(8977)
7. GET /queue → POST /prompt（固定 `client_id`，与 WS 共用，8994）→ **立即预置进度态**（防 WS 竞态，9003）→ 注册 task_map
8. `_wait_for_history`(8601) 轮询；`status_str=error` 抛 `ComfyUITaskError` 快速失败；超时后 3 次补查 history 防误报（9064）
9. 下载输出（images + gifs，最多 10 个）→ 落 output_dir → 去重（SaveImage 目录相同时删 ComfyUI 副本 9102）→ 读真实分辨率 → `_closest_ratio`
10. 异常/取消：清理进度态；取消的长视频由 `_watch_and_push_video`(9190) 继续盯 4h 并推送

**注入语义铁律**：
- `_inject_prompt` 是 **APPEND**（WebUI 保存的提示词在前，聊天提示词追加在后，v3.1.1 设计如此）；空提示词直接 return False 不注入
- 负面词是 **SET（覆盖）**，append 会与 `_apply_workflow_config` 的值重复
- `_set_load_image` 先**清空所有 LoadImage 引用**再注入（防残留）；`_apply_workflow_config` 会删除无文件 LoadImage 节点但 protect_nodes 保护本次要注入的

### 2.4 组控制（组控制/Disable Groups）

| 项 | 位置 | 说明 |
|---|---|---|
| 组提取 | `_webui_get_groups`(3228) | API 文件找 `原json/` 同名 UI 文件做包围盒命中提取 → 缓存 `.groups.json` sidecar；回退链：sidecar → per-wf `__groups_data__` → 分桶 store → root |
| 自动应用 | `_webui_groups_auto_apply`(3292) | v4.5.0：提取即写入**所有节点 ID 兼容**的工作流（覆盖检查，缺节点的跳过并报缺 N 个）；保留已有禁用开关 |
| 禁用生效 | `_apply_group_modes`(2667) | **ComfyUI API 忽略 mode=4**（bypass 只是 UI 概念）→ 实际做法是把禁用组节点**级联删除**（复用 `_remove_workflow_nodes` 1605） |
| JZL 引用重建 | `_rebuild_jzl_refs`(2705) | 删节点后 ref_images.* 槽位会留洞 → ComfyUI 填 "" → JZL 节点崩；重建为连续 ref_image_0..N-1；**键比较必须 str() 归一** |
| 存储 | `__group_bindings_store__[wf] = {source,target,data,disabled}` | v4.4.2 分桶自包含，删源工作流/组文件不连坐 |
| 调试 | `/api/debug-group-chain`(3343) | 干跑整条变换链，输出各阶段快照（文件原始→配置应用→组模式→图片注入→引用重建） |

### 2.5 提示词规则系统（魔导书规则，v4.4.3–4.4.6）

| 项 | 位置 | 说明 |
|---|---|---|
| 存储 | `workflow_config['__llm_prompt_templates__'] = {'t2i':[...], 'imgrev':[...]}` + `__llm_template_active__` + per-wf `__llm_rule__` | 规则对象含 name/type/content/chk_quality/chk_weights/chk_commas |
| 生效判定 | `_effective_rule`(2605) / `_effective_rule_obj`(2610) | **默认全不注入**：只有 per-wf `__llm_rule__` 显式绑定（规则名）才生效；'none'=显式关闭；''=不继承 |
| 硬校验 | `_enforce_prompt_rule`(2622) | 提交前清洗：剥质量词黑名单、拆 `(x:1.3)` 权重（一层嵌套）、逗号上限警告；在 comfyui_draw/img2img/video 工具**和** /图生视频 命令路径都调用 |
| 注入 | `_apply_llm_templates`(2746) | 把规则文本写进工具 description；**必须双写**：插件实例对象 + 框架 wrapper（`context.provider_manager.llm_tools.func_list`，框架在 add_llm_tools 时拷贝了 description） |
| API | GET/POST `/api/llm-templates`(2783/2805) | ops: save(全表替换, content≤20000) / active / bind |

### 2.6 LLM 工具全表

**15 个 FunctionTool dataclass（L85–651，经 `_register_tools` 6875 注册）**：

| 工具 | 类行 | 签名要点 |
|---|---|---|
| comfyui_draw | 86 | prompt, workflow?, ratio?, quality?；仅「画」类工作流；t2i 规则 |
| comfyui_img2img | 262 | prompt, image_urls(1–10 逗号分隔), denoise?, workflow?, ratio?；image_urls 可选，缺/下载失败→`_collect_images_from_event`(8388) 回退（直发图/引用图/@头像/最近图缓存） |
| comfyui_video | 346 | image_url, prompt?, workflow?；同上回退；imgrev 规则 |
| comfyui_random | 407 | count?, workflow? |
| comfyui_list_workflows / switch_workflow / get_current_workflow | 143/207/233 | 切换工具返回目标工作流绑定的规则 inline |
| comfyui_execute / stop / queue_status | 558/533/517 | — |
| comfyui_random_image | 598 | 魔导书随机池抽卡 |
| comfyui_list_stars / list_presets / delete_preset | 462/480/495 | 词库辅助 |

**@filter.llm_tool 方法（LLMToolsMixin/GrimoireMixin）**：comfyui_grimoire_add/delete/edit(5999/6037/6064)、comfyui_set_prompt_model(6104)、comfyui_search_tags(6252)、comfyui_list_grimoire(6359)、comfyui_manage_pool(6390)、rand_pool_remove(6429)、manage_pin(6455)、unpin_tag(6507)、search_grimoire_items(6535)、comfyui_get_prompt(6591)。

**LLM 拦截钩子**：`_on_llm_request`(8077) —— 把对话图片上下文转成本地文件路径以文本注入（附"不要透露路径"指令），提取 Reply 消息 ID 提示 comfyui_get_prompt，清空 req.image_urls。

### 2.7 聊天命令全表

| 命令 | 行 | 备注 |
|---|---|---|
| /帮助 /队列 /提示词 /停止 /撤回 | 9248/9276/9286/9345/9361 | 提示词查询走 `_prompt_log`（MD5/dHash 匹配） |
| /比例 | 9409 | `_sync_resolution_to_workflow` 9453 会**回写工作流 JSON 文件** |
| /分辨率 | 9591 | 质量预设 |
| 纯数字回复 | 9637 | `pending_actions` 30s 交互菜单，**注册在命令之前** |
| /工作流 /切换 | 9730/9790 | — |
| /画 | 9800 | `_ensure_command_workflow` 7323 |
| /执行 /随机图 | 9826/9853 | 随机图走 random_prompt.py 组合 |
| /图生图 | 9919（飞书宽松 9982） | `_collect_images_from_event` 8388 |
| /图生视频 | 10008（宽松 9991） | **v4.5.3：命令文本即提示词**（strip `[图片]/[At:x]/@xxx` 占位 → `_enforce_prompt_rule(…, 'imgrev')` → 传入 `_process_and_submit`）；此前硬编码空串是提示词丢失事故根因 |
| ~~（v4.6.0 移除）/反推~~ | — | 已删除：现代 LLM 自带视觉，独立反推冗余。工具/命令/`_run_reverse_prompt` 均已移除，反推分类降级为普通分类（工作流文件保留可执行） |
| 消息后钩子 | 10119 | `_keep_alive_after_send` |
| 图片缓存钩子 | 10125 | `_cache_incoming_images`：按 umo 缓存入站图 URL（TTL 600s），解决飞书图文分条消息问题 |

平台发送群（主体类内）：`_send_image_result`(6924)、飞书 `_send_image_to_feishu`(7420, `_shrink_for_feishu` 7710 限 9MB)、QQ `_send_image_to_qq`(7530)、`_resolve_send_targets`(7659)、`_pref_bot_order`/`_sort_clients_by_pref`/`_onebot_send_via_ws`。

### 2.8 HTTP API 全表（WebUIMixin，路由注册 1862–2000）

服务器 `_start_webui`(1860)；`GET /` → `_serve_webui`(1705) 伺服 webui.html，**带 `Clear-Site-Data: "cache"` 头**（QQ X5 内核无视 no-cache）。

**核心生成/配置**：
| 路由 | 行 | handler |
|---|---|---|
| GET/POST /api/config | 1873/1893 | 快照 / `_webui_save_config`(2055) |
| POST /api/generate | 1894 | `_webui_generate`(2239) → `_process_and_submit` + 推送目标平台 |
| POST /api/deploy-mode | 1901 | 4414 |
| GET /api/data-layout | 1900 | 2235 |
| GET/POST /api/k2gen/data, /api/k2-locks, /api/k2-nsfw | 1863–1868 | 1752/1775/1780/1794 |
| gitee-sync status/run/progress | 1896–1898 | 2135–2220 |

**工作流管理**：
| 路由 | 行 | handler |
|---|---|---|
| GET /api/workflows, /api/workflows/all | 1906/1907 | 刷新列表 / 缓存含隐藏 |
| POST /api/workflows/switch, /toggle-hidden | 1910/1911 | 2415/4216 |
| GET/POST /api/workflow-preview | 1908/1909 | 4159/4171 |
| GET/POST /api/workflow-params | 1926/1928 | 2424 / 2471（保存 `__saved_texts__`/组数据/禁用组 → 分桶） |
| GET /api/workflow-params-config | 1927 | 3759 |
| POST /api/wf-category, /wf-category-order, /wf-delete, /wf-add | 1934–1937 | 2564/2591/2892/2965 |
| GET/POST /api/workflow-bind(, /delete) | 1931–1933 | 4062/4076/4101（群/用户级工作流绑定） |
| GET/POST /api/context-workflows, /context-workflow | 1942/1943 | 4129/4137 |

**节点资源**：comfy-models(3439) / loras(3494) / lora-metadata(+refresh 4230/4238) / lora-preview(3512) / style-libs(3573) / style-list(3596) / style-preview(3628) / view-input(3679) / upload-image(3006, **上传后 `_set_load_image_node` 回写工作流 JSON**) / upload-media(3119) / clear-node-input(3173, "不使用"标记) / open-dir(2335) / pick-dir(2363) / workflow-dir(2384)。

**生成辅助**：progress(3718) / interrupt(4249) / set-quality(3830) / set-ratio(3847) / set-official-res(3864) / set-duration(3943→`_sync_duration_to_workflow` 9521) / reset(3960, 清空 config.json) / bg save/image(4024/4055)。

**组控制**：GET /api/groups(3228) / POST /api/groups/auto-apply(3292) / POST /api/debug-group-chain(3343) / GET(+POST) /api/proxy(3394, 白名单 SSRF 防护)。

**规则**：GET/POST /api/llm-templates(2783/2805)。

**画廊**：GET /api/gallery(4260, 递归扫 output_dir, 上限 5000) / GET /api/gallery/file(4311) / POST /api/gallery/delete(4342)。

**魔导书（GrimoireMixin）**：sources(4430) / data GET+POST+PUT+DELETE(4512/4661/5591/5637) / categories(4612) / source CRUD+排序(5678/5708/5816/5845/5859) / category CRUD(5741/5760/5790) / status GET+POST(5873/5877) / batch-import(4726) / batch-delete(4804) / pins(4849/4863) / rand-pool(4920/4925, **POST 支持 sources 数组批量**, v4.5.1) / random-pick(5017) / presets(4968/4974/4990/5004) / stars(4952/4957) / models(5555/5567/5572) / cache-status/cache-all/cache-progress(6137/6205/6232) / GET /cache/{filename}(4389)。

**Anima**：search(4375) / stats(4386)。**静态资源**：favicon×3 / theme-xin-{mode}.webp 白名单(1735) / apple-touch-icon(1745)。

### 2.9 数据持久化地图

**唯一主存储**：`self.workflow_config` ⇄ `data/user/config.json`（外部 comfyui_allinone_data/user/）。所有键：

- `__local_config__`：comfyui_url / webui_port / workflow_dir / output_dir / 发送目标等
- `__workflow_node_configs__[wf]`：`__saved_texts__`({nid}_{input}→值)、`__prompt_node__`、`__negative_node__`、`__resolution_node__`、`__lora_nodes__`、`__empty_load_nodes__`、`__expanded_text_node__`、`__is_video__`、`__fingerprint__`、`__groups_data__`、`__llm_rule__`、`__disabled_nodes__`
- `__wf_categories__` / `__workflow_aliases__` / `__hidden_workflows__` / `__current_workflow__`（v4.4.1 与 `__bind_target__` 分离，切换工作流不再串台）
- `__group_bindings_store__`（组绑定分桶）、legacy root `__groups_source__/__groups_data__/__disabled_groups__`
- `__llm_prompt_templates__` / `__llm_template_active__` / 各工作流 `__llm_rule__`
- `__grimoire_*__`（enabled/pins/rand_pool/presets/stars/source_order/dir_order）、`__k2_locks__`、`__prompt_model__`、legacy root `{nid}_text`

**其他落盘**：`prompt_log.json`(6721, 双锁：asyncio + threading, 6717)；工作流 JSON 本体（比例/时长/LoadImage 回写）；`<wf>.groups.json` sidecar；魔导书词库 JSON（`data_dir_resolver()` 下，**非原子写** 5964/5977）；Anima 缓存（user 目录）。

### 2.10 ComfyUI 客户端

`_upload_image_remote`(8532, /upload/image multipart) / `_comfy_input_file_exists`(8568, HEAD /view) / `_get_queue_status`(8582) / `_wait_for_history`(8601) / `_ws_progress_listener`(8696, 固定 client_id, 5s 重连) / `_ensure_object_info`(7789, TTL 300s) / LoRA 元数据(7872–8062) / `_webui_proxy`(3394)。

### 2.11 关键常量

`quality_presets`(6753, 480p→4K 像素预算密度制)、`aspect_ratios`(6783)、`official_ratio_map`(6785)、超时 300s/14400s、WS 重连 5s、recent_images TTL 600s、object_info TTL 300s、上传清理 1 天、输出保留 output_keep_days。

---

## 3. webui.html 结构地图

### 3.1 物理分区

| 区 | 行 | 内容 |
|---|---|---|
| `<style>` | 22–4387 | 42 个编号 CSS 区段（见 3.2） |
| SVG sprite | 4402–4553 | ~70 个 `<symbol id="icon-*">`，含渐变 logo |
| HTML body | 4389–5459 | 见 3.3 |
| `<script>` #1 | 5459–13970 | 主逻辑 |
| `<script>` #2 | 13975–14046 | K2 引擎（`/* __K2_INJECTED__ */` 标记；`_k2ComposeMode` 顶层 let，与 script#1 共享全局词法域，**勿在 script#1 顶层访问（TDZ）**） |

### 3.2 心主题（心/Xin）体系

- 深色（默认）`:root` L26–125（oklch 朱红主色/鎏金点缀/功能色/渐变/纹章色），浅色 `[data-theme="light"]` L129–186
- 昼夜背景：`--custom-bg: url('/theme-xin-night.webp')`(L124) / day(L184)
- 切换机制：`<html>` 的 **data-theme 属性**，`setTheme`(5966) + localStorage('theme') + meta theme-color；actionMap 里 `toggleTheme`(5683)
- UI 透明度：`--ui-opacity`，`changeOpacity`(5979)，经 color-mix 参与 `--card-border/--bg-overlay`
- 纹章：`XIN_IC`(8595) 角色纹章 `/theme-xin-icon-role-{k}.webp`；导航/Logo 用 sprite + webp
- 断点：**媒体侧栏 `@media(max-width:899px){.media-panel{display:none!important}}`(L234, PC 专属)**；≤900 魔导书浮动面板(2529)；≤480/768 sidebar 收缩(3034/3055)；≥1400/1920 大桌面(3584/3588)

### 3.3 HTML 区块行号

header.unified-header(4558, 昼夜按钮 4567/媒体面板开关 4566/透明度滑条 4584) · 页签 #page_tabs(4590: wf/gallery/grimoire/wfmgr/settings) · 进度条(4599) · 画廊下拉 #gallery_dropdown(4616) · 设置面板 #settings_modal(4668) · 工作流侧栏 #sidebar(4803) · 生成快捷条 #gen_quick_bar(4829) · 准备面板 #prep_panel(4853: 正/负提示词、官方分辨率/时长、上传、UNet、LoRA、风格、扩写文本) · 组控制条 #groups_bar(4976) · 工作流网格页 #wf_page(5002) · 移动端 bottom-nav(5018) · **媒体资产侧栏 #media_panel(5037)** · 节点详情弹窗 #node_modal(5051) · 魔导书 sheet #grimoire_sheet(5063, 内含 **规则面板 #grimoire_rules_panel(5138): t2i/imgrev/bind 三 tab + 硬校验复选框 #grm_rule_chk_* 5157–59**、K2 面板 5171) · 词库编辑/批量导入弹窗(5202/5270) · 背景设置(5342) · 工作流管理弹窗 #wf_mgr_modal(5378) · toast(5439) · 灯箱 #lightbox(5444)

导航模型：`switchPage`(6635) 切 body.page-* 类；gallery/wfmgr/settings 实为全屏弹窗页。

### 3.4 JS 架构

- **唯一 fetch 封装** `apiFetch`(5578)：AbortController 超时(默认 30s, `options.timeoutMs` 可覆写如 gitee 10min)、`d.ok===false` 自动 toast。裸 fetch 仅 8 处例外（图片缓存/上传/proxy 等）
- **事件系统**：全局 `data-action` 委托(5660) + `actionMap`(5679)。**带参 action 必须声明在 actionMap**（window[name] 调用不传参，5674 注释）
- **初始化序列**(11580–11596)：500ms 测活+列表 → 800ms loadParams → 1500ms pins → 2200ms 恢复上次页面 → schedulePoll(2000)
- **参数面板**：主面板控件 id `param_{nid}_{key}`；**节点弹窗用 `mparam_{nid}_{key}` 前缀**（9551，曾因 id 撞车导致弹窗改动被面板旧值覆盖）；弹窗收集只扫 `#node_modal_body [id^="mparam_"]`(9603)
- **saveParams**(9647)：忙时**排队重试** `_savePending`(9649/9713，此前并发保存被静默丢弃)；节点角色仅在非空时提交（除非 `_explicitClear`，9664）；提示词/负面词 debounce 保存(9720/9745)
- **saved_texts 读取**(7422)：`__workflow_node_configs__.{wf}.__saved_texts__`；**textarea 不预填节点默认值**（7418）
- **组 UI**：`refreshGroupsUI`(9787) / `extractGroups`(9817, 提取即 auto-apply) / `toggleGroupTag`(9830, **必须触发 saveParams 否则 `__disabled_groups__` 不落盘**) 
- **规则 UI**：toggleGrimoireRules(8685) / loadGrimRules(8699) / grimRulesTab(8712) / Render(8725) / Save(8820) / Del(8843) / bind(8772)
- **媒体侧栏**：`loadMediaPanel`(8873, 缓存优先) / `renderMediaPanel`(8888, 首屏 40 条) / PC 单击媒体侧栏 vs 双击全画廊的 260ms 计时器(8914)
- **画廊**：loadGalleryImages(11185) / filters(11199) / lazy-load(11333/11355) / 批量删除(11441)
- **轮询（无 WebSocket/SSE）**：`schedulePoll`(10175)/`pollProgress`(10181) 自适应间隔（生成 1s/排队 2s/空闲 5s，页面隐藏暂停）；**新产出检测 = running→idle 边沿**(10263) → loadGalleryImages + PC 下强制刷新媒体侧栏（延迟 800ms 等文件落盘）
- **随机池批量**(13685–13744)：POST `{action:'add'|'remove', sources:[...]}` 一次往返；路径 `replace(/\\/g,'/')` 归一
- **K2**：script#2 `buildK2Engine`(13984) 从 `/api/k2gen/data` 拉源码 new Function 构造；`k2Compose`(14025)

---

## 4. 「改 X → 动哪里」速查表

> 定位方法：后端 grep 函数名，前端 grep 函数名/id/`/api/` 路径。改完看第 6 节验证与部署。

### 4.1 生成行为类

| 要改的功能 | 后端位置 | 前端位置（如有） |
|---|---|---|
| 提示词注入方式（追加/覆盖/格式） | `_inject_prompt`(1442)；负面覆盖在 `_process_and_submit` 8977 | 无（纯后端语义） |
| 图片注入/槽位分配 | `_set_load_image`(1535)、`_set_single_load_image`(1562)、`_set_all_load_images`(1644)、`_find_all_load_image_nodes`(1139)；文件回写 `_set_load_image_node`(3060) | 上传 `handleImageUpload`(10811)、槽位拖序(8462) |
| 提交前变换链（顺序/新增步骤） | `_process_and_submit`(8755) 第 6 步列表 | — |
| 组禁用机制 | `_apply_group_modes`(2667)、`_remove_workflow_nodes`(1605) | `toggleGroupTag`(9830)、`refreshGroupsUI`(9787) |
| JZL/多图引用修复 | `_rebuild_jzl_refs`(2705) | — |
| 分辨率/比例/质量 | `_set_resolution`(1503)、`_calc_resolution`(1001)、quality_presets(6753) | `updateResPanels`(10657)、官方面板 `_initOfficialResPanel`(10673)、`/api/set-*` 调用 |
| 超时/轮询/进度 | `_wait_for_history`(8601)、`_ws_progress_listener`(8696) | `pollProgress`(10181)、进度条 CSS 区段 23 |
| 出图回传平台 | `_send_image_result`(6924)、`_send_image_to_feishu`(7420)/`_send_image_to_qq`(7530) | — |
| 随机图逻辑 | `random_prompt.py`（延迟导入）、`comfyui_random`(407)、/随机图 9853 | rand-pool UI 13595+ |
| K2 组合 | `_k2_compose_prompt`(1806)、k2gen/cli.js | script#2 13975+ |

### 4.2 规则/提示词工程类

| 要改的功能 | 后端 | 前端 |
|---|---|---|
| 规则生效/绑定语义 | `_effective_rule`(2605)、`_webui_save_llm_templates`(2805, bind op) | grimRulesTab('bind')(8712)、#grimoire_rules_panel(5138) |
| 硬校验清洗逻辑 | `_enforce_prompt_rule`(2622) | 硬校验复选框 5157–59 + grimRulesSave(8820) |
| 规则注入到 LLM | `_apply_llm_templates`(2746)（**双写实例+框架 wrapper**） | 无 |
| 新增规则类型（如 t2v） | templates store 加 type、_llm_template_text(2737)、对应工具 run() 内调用 `_effective_rule` | tab 按钮 5145 + grimRules* 系列函数 |

### 4.3 界面/交互类

| 要改的功能 | 前端 | 后端（如需新数据） |
|---|---|---|
| 新页面/新面板 | HTML body 加区块（4389–5459 内）+ switchPage/actionMap(5679) 注册 + 渲染函数 + CSS 区段 | 无 |
| 新弹窗/新控件 | 注意 id 前缀规范：弹窗内用 `mparam_`/独立前缀，主面板 `param_` | — |
| 主题配色/昼夜 | `:root`(26) / `[data-theme=light]`(129) 两套变量**都要改** | — |
| 侧栏/移动端布局 | 断点区段 31–33（3032–3227）；媒体侧栏保持 ≥900px 专属(234) | — |
| 图标/纹章 | SVG sprite(4402) 或 webp 纹章（后端白名单 `_serve_theme_bg` 1735 需同步加文件名） | `_serve_theme_bg` |
| 新图库交互 | 画廊区 11045–11552 | /api/gallery 系(4260) |

### 4.4 数据/存储类

| 要改的功能 | 位置 |
|---|---|
| 新增持久化状态 | `workflow_config` 加 `__新键__` → `__init__`(6638) 初始化 → `_save_workflow_config`(687) 自动持久化 → 前端经 /api/config 或专用路由读写 |
| 新增 per-workflow 配置 | `__workflow_node_configs__[wf]['__新键__']`；注意 `_compute_fingerprint`(1183) 换文件时的清理逻辑；前端 saveParams(9647) 加字段 |
| 新增 API 路由 | 路由表(1862–2000) `router.add_*` + WebUIMixin 里加 `_webui_xxx` handler；前端尽量走 `apiFetch` |
| 词库（静态） | `data/` 下 JSON，格式见 `data/` 现有文件；魔导书扫描规则 `_webui_grimoire_sources`(4430) 会跳过 anima_tools/cache/user 等目录 |
| 配置项（AstrBot 面板） | `_conf_schema.json` + `__init__` 读取 + `_conf_schema` 与 config.json 的分工见 2.9 |

### 4.5 新增功能的标准动线（checklist）

**后端加一个 WebUI 功能**：
1. WebUIMixin 加 handler + 路由表注册（1862–2000）
2. 若有新持久化：workflow_config `__键__` + `__init__` 初始化
3. metadata.yaml：版本 +1，description **追加**新版条目（新版在前）
4. git commit（格式见 6.4）

**前端加对应 UI**：
1. HTML 区块（body 区）+ CSS（新编号区段或并入现有区段）
2. JS：渲染函数 + apiFetch 调用 + actionMap 注册（带参 action 必须进 actionMap）
3. 初始化序列(11580)按需挂载
4. 若是弹窗控件：id 用独立前缀避免与 `param_*` 撞车

**加一个 LLM 工具**：
1. 新 FunctionTool dataclass（L85–651 区）+ `_register_tools`(6875) 注册列表
2. 或用 `@filter.llm_tool`（LLMToolsMixin）
3. 若受规则管控：run() 里调 `plugin._effective_rule/_enforce_prompt_rule`
4. 记得：框架会拷贝 description → 后续动态改描述必须走 `_apply_llm_templates` 双写模式

**加一个聊天命令**：
1. 主体类加 `@filter.command`；飞书需要宽松匹配时用 `_lark_cmd`(76) custom_filter 再写一个 loose 变体（参考 /图生图 9919/9982）
2. 数字交互菜单走 `pending_actions`（`_set_pending_action` 9580）
3. 纯数字回复匹配注册在命令之前（9637）

---

## 5. 高危陷阱清单（每一条都是踩过的坑）

1. **ComfyUI API 忽略 node `mode`** —— 禁用组/节点必须物理删除（`_remove_workflow_nodes`），设 mode=4 无效。
2. **工作流 JSON 键可能是 int**（类型归一后）—— 所有节点 ID 成员判断必须 `str(k)` 归一（v4.5.2 教训：str 检查漏掉 int 键导致存活引用=0）。
3. **`_inject_prompt` 是 APPEND、负面词是 SET** —— 别改反/改混；空提示词不注入（v4.5.3 加的 guard）。
4. **LLM 工具 description 双写** —— 框架在 add_llm_tools 时拷贝了 description，动态更新必须同时改插件实例和 `provider_manager.llm_tools.func_list` wrapper。
5. **规则默认全不注入** —— 只有 per-wf `__llm_rule__` 显式绑定才生效（v4.4.5 语义，别恢复全局继承）。
6. **`mparam_` vs `param_` id 前缀** —— 弹窗与主面板控件 id 撞车曾导致弹窗改动被旧值覆盖；`.toFixed` 前必须 `Number()`（saved_texts 里是字符串）。
7. **组开关必须经 saveParams 落盘**；saveParams 忙时排队（`_savePending`）勿改成静默丢弃。
8. **删除组/工作流不连坐绑定** —— 组绑定在 `__group_bindings_store__` 分桶自包含；清理逻辑（v4.4.1 孤儿清理事故）不许再引入。
9. **`__current_workflow__` ≠ `__bind_target__`** —— 切换工作流只写前者。
10. **固定 client_id** —— 提交与 WS 共用，否则收不到进度；提交后必须**立即预置进度态**（WS 竞态）。
11. **保护 LoadImage 节点** —— `_apply_workflow_config` 删无文件加载节点时 protect_nodes 保护待注入目标，否则"图生图没反应"回归。
12. **前端缓存** —— webui.html 响应带 `Clear-Site-Data: "cache"`，改前端后手机 QQ 内核才刷新；改前端**不用重启 AstrBot**，改 main.py **必须重启**。
13. **部署漏文件 = ImportError 500** —— random_prompt.py / data_paths.py / gitee_sync.py 是延迟导入，deploy.sh 有专门导入校验，别绕过。
14. **验证生成链路的两条腿** —— ①运行时控制台日志**其实存在**：`restart_ab.sh` 把 AstrBot 进程 stdout 实时写到手机 `$PREFIX/tmp/ab_restart.log`（每次真正重启会截断，只含本次启动以来的日志），plugin logger.info 全在里面；astrbot.log 才是陈旧的。②ComfyUI 侧：`/history`、`/api/debug-group-chain`（干跑快照）、各 /api 诊断字段。
15. **改 ComfyUI 侧自定义节点（如 JZL nodes.py）需重启 ComfyUI 进程**才生效，插件部署重启只覆盖 AstrBot。
16. **前端轮询无 WS** —— 新产出检测靠 running→idle 边沿 + 800ms 缓冲；媒体侧栏 PC(≥900px) 专属，别做到手机端。
17. **AstrBot 临时图路径不可信** —— LLM 传来的 data/temp/compressed_*.png 在消息管线后即清理；工具里下载失败必须回退 `_collect_images_from_event`（v4.5.3）。
18. **跨线程写盘** —— sync 上下文不能 ensure_future（676 兜底）；prompt_log 文件需 threading.Lock（6717）。
19. **词库扫描排除清单** —— 新增 data/ 目录若非词库，要加进 `_webui_grimoire_sources`(4430) 排除列表。
20. **metadata description 是追加式历史** —— 编辑时保留旧条目，新版插到最前；版本号用 bump 脚本或手工同步 main.py `@register` 无版本字段（版本以 metadata.yaml 为准）。
21. **词库写必须原子写、读损坏必须隔离（v4.5.4 已修，勿回退）** —— `_grimoire_write` 曾是裸 `open('w')` 覆盖写 + `_grimoire_read` 损坏时静默返回 `[]`，链式后果是"写入中途崩溃 → 读取得空列表 → 下次保存把整个词库源无声清空"。现在写入走 tmp+replace（与 config.json 同标准），读取遇损坏先把坏文件改名 `.corrupt` 保留现场再返回空。**任何新增的 JSON 持久化都必须用同一套原子写模式**。
22. **组禁用只允许一条路径（v4.5.4 已修）** —— `_process_and_submit` 里曾有 v4.4.1 遗留的内联组禁用块，与 `_apply_group_modes` 双路径并存：遗留块用根级数据二次删除且把下游引用直接置 `""`（断头不重连），数据不一致时留下校验错误的图。已整段移除，组禁用语义唯一入口是 `_apply_group_modes`（级联删除 + 根级兼容合并）。今后给生成链加变换步骤时，先搜旧行为是否有残留副本。
23. **重启假阳性（v4.5.5 排障时发现的重大坑）** —— 通过 ssh 用 `nohup ~/ab_restart_lark.sh &` 后台触发重启**可能根本没杀掉旧进程**（多次部署均如此），而 WebUI 200 是旧进程回的——"已重启并验证" 全是假阳性，v4.5.3~v4.5.4 曾因此长期未生效。**正确姿势**：ssh 前台执行 `bash ~/ab_restart_lark.sh > ~/restart_out.log 2>&1`（**绝不能接 `| grep | head` 管道**——head 退出触发 SIGPIPE 会把重启脚本半路杀死，造成旧进程已杀、新进程未起的死局），完成后 `grep -a "Plugin comfyui_allinone (" $PREFIX/tmp/ab_restart.log` **确认括号里是目标版本号**、且无 "Failed to load metadata" 才算成功。注意 AstrBot 完整启动最长 2 分钟（anima 数据加载），WebUI 端口就绪后事件循环仍可能被同步加载阻塞片刻，PC 侧验证要等日志出现插件加载行。
24. **官方分辨率选择器工作流的比例生效（v4.5.5–4.5.8 三轮演进）** —— ResolutionSelector 类工作流的分辨率存在 `aspect_ratio`+`megapixels` 输入里（没有 width/height 可读）。演进链：v4.5.5 兜底写入但用全局默认值 → 踩掉官方面板设置；v4.5.7 改为以工作流文件值为基准（`_read_workflow_file_resolution`），当次显式传参（LLM 的 ratio/quality）仍优先；v4.5.8 补上"面板改动落盘文件"——面板控件走 saveParams 通道，`/api/workflow-params` 拦截分辨率类键（aspect_ratio/megapixels/multiple/width/height）直接原子写工作流文件并清 saved_texts 残留。**分辨率语义：文件是真相源**（面板、/比例、/分辨率 全部落盘）；只有 LLM 当次参数是临时的。
25. **ComfyUI v0.37 WS 会话是驼峰 `clientId` 且执行事件点对点（v4.5.7 已修）** —— `/ws?client_id=`（蛇形）不被识别，监听连接拿到随机 sid；而这个版本 executing/progress/execution_cached 只发给提交者 sid（`send_json(..., sid)`），**不广播**——插件监听器永远只剩 status/crystools 心跳，进度永远 0%。必须连 `?clientId=<与提交相同的 client_id>`。诊断手法：PC 裸连 WS 抓包，若 status/monitor 正常流动但改 seed 强制真实执行后仍无 executing 事件，即为此症。
26. **改版面板后旧保存函数会变死代码** —— 官方面板重建为 `param_{nid}_*` + `onchange="saveParams()"` 后，`setOfficialRes()` 读的 `official_aspect_ratio` 等旧 id 已不存在，/api/set-official-res 从此收不到请求（日志可证）。改前端面板时必须在运行日志里确认保存请求真的到达后端。分辨率类参数走 `/api/workflow-params` 时由后端识别并落盘文件（v4.5.8），不再依赖 saved_texts。
27. **持久化的"名字列表"必须在改名时同步迁移（v4.7.1 教训）** —— `__category_order__` 这类按名字保存的排序/引用数据，分类/命令/工作流改名后旧名会变成毒数据：前端若用持久化名单过滤当前 DOM，新名全部被滤掉后 indexOf 得 -1 静默 return，功能无声失效且无报错。规则：①改名时用对应 API 重置受影响的持久化名单；②前端逻辑以 DOM/当前实际状态为准，持久化名单只做序提示、不做存在性过滤；③apiFetch 传 silent=true 的写操作失败时用户无感，排查时先看网络面板或后端日志。
28. **多阶段分辨率选择器不得抹平（v4.9.0 教训）** —— 一采+二采类工作流（H3 等）靠多个 ResolutionSelector 的**不同 MP 值**表达逐级放大目标，提交链/比例同步若把所有选择器写成同一个 MP，放大目标被压成一采档，二采白跑且画面正常、极难察觉（症状="输出是一采"）。规则：文件内 MP 值不同的官方选择器视为多阶段，按原比例分配目标总像素、只统一宽高比；分辨率基准取 MP 最大的选择器（最终成品档）。同前缀多输出节点（双 VideoCombine）交付时按体积降序取最大（v4.8.0）。
29. **飞书媒体限制** —— 图片 ≤10MB（`_shrink_for_feishu` 9MB 阈值压图），视频同样 ~10MB（`_shrink_video_for_feishu` ffmpeg 压制，v4.8.1）。飞书平台图片 URL 短时效——任何跨消息复用图片的场景必须落地本地副本（v4.7.2 recent_cache 机制）。
30. **组禁用必须 bypass 直通，不许断头空串（v4.9.1 教训）** —— 级联删除禁用组时，组外下游节点对组内节点的引用要重接到被删节点自己的上游（输出序号→输入序号启发式，即 ComfyUI bypass 语义），直接填 "" 会让下游节点收到字符串输入直接崩（rgthree 的 `'str' object has no attribute 'model'` 即此）。注意这类雷可能是**休眠的**：v4.4.10 之前组禁用是空操作（ComfyUI 忽略 mode=4），让组真正删除的改动会唤醒所有历史上"看起来禁用了其实没生效"的配置——改组机制时把这条写进回归测试。另：rgthree Power Lora Loader 的 `lora_N` 字典格式（on/lora/strength）插件本就写对，排查时先看**连线**再怀疑**参数格式**。

---

## 6. 开发→验证→部署流程

### 6.1 日常循环

1. PC 上改 `E:\AIwork\插件\astrbot_plugin_comfyui_local\`（或经 theme_work 补丁脚本对 main.py/webui.html 做字符串替换注入——历史上每个版本一个 patch_*.py，可复用其模式）
2. main.py 改动 → `python -m py_compile main.py` 本地过 ast
3. webui.html 改动 → 无需重启；main.py 改动 → 需重启 AstrBot
4. 部署：`deploy.sh`（打包 28 文件 + 上传手机 + 备份 + 校验）→ `deploy_remote.sh` / 手机端 `deploy_ok.sh` + `~/ab_restart_lark.sh`
5. 验证（无运行时日志）：
   - WebUI 功能：浏览器直接操作（PC `http://<手机IP>:8898`）
   - 生成链路：ComfyUI `http://192.168.0.106:8188/history` 看最新 prompt_id 的图/提示词/节点状态；组链路用 POST `/api/debug-group-chain` 看五阶段快照
   - 前端回归：10 项检查套件（面板加载/参数/组/规则/画廊/媒体/昼夜/弹窗保存/随机池/控制台零报错）

### 6.2 版本与提交规范（v4.9.1 后生效的约定）

- **版本号只跟"用户认可的交付"走，不跟调试轮次走**：一次会话内部不管迭代部署多少轮，最后只占一个版本号（中间部署用同号反复覆盖没关系——生效验证靠重启日志的插件加载行 + git commit，不靠 metadata 递增）。经验标尺：一天 4 个号是正常节奏，10+ 个说明在用版本号补偿部署流程。
- **每个版本对应一个能一句话说清的主题**；小修并入同主题版本，不单开新号。
- metadata.yaml `description` 追加式版本史：新版条目插最前、格式 `vX.Y.Z(标签): 说明`；**条目里严禁出现 ASCII 双引号**（会截断 YAML 双引号标量），每次 bump 后必须 `yaml.safe_load` 校验。
- description 体量控制：版本史只保留交付级条目，20+ 条时把老条目剪到 README 存档（2026-10-03 已把 15 条浓缩为 10 条示范：4.9.0+4.9.1、4.8.0+4.8.1、4.7.0+4.7.1、4.6.0+4.6.1、4.5.7+4.5.6 五组合并，被吸收版本标注"含vX.Y.Z"）。
- git commit 保持细粒度（`vX.Y.Z: 变更一句话`，见 git log），它是真正可靠的变更记录，可独立于版本号存在。
- 重大机制改动在本架构文档第 5 节补陷阱条目、在第 4 节补速查行、在 6.4 补修复记录。

### 6.3 环境事实速记

- 手机 SSH：`u0_a175@192.168.0.105:8022`（Termux）；AstrBot 在 proot Ubuntu `/root/AstrBot`
- 插件外部数据：`/root/AstrBot/data/comfyui_allinone_data/`
- ComfyUI：PC 192.168.0.106:8188；自定义节点 ComfyUI-JZL-MiniMax-H3（nodes.py 曾打 isinstance(str) 跳过空引用补丁，备份 nodes.py.bak_zcode）
- 测试工作流样本：`theme_work/H3图生视频-1采多段.json`；心主题素材生成脚本 `theme_work/queue_*.py`（经本机 ComfyUI 出图）

### 6.4 修复记录（按版本倒序）

- **v4.9.1（2026-10-03）**：组禁用直通重连——级联删除把幸存节点的已删引用填空串，动漫画图禁用「加速」组（含 LoraLoaderModelOnly 158）时组外 rgthree Power Lora Loader(159) 的 model 变字符串即崩（'str' object has no attribute 'model'；v4.4.10 前组禁用是空操作所以是休眠老雷）；`_remove_workflow_nodes` 现按 ComfyUI bypass 语义直通重连（输出序号→输入序号启发式，接力直到稳定，空串仅兜底），本地单测+真机验证通过（禁用加速组成功出片）。动漫画图双 SaveImage 前缀撞车同修（164→Anima_P1，备份 .bak_prefix_20261003）。
- **v4.9.0（2026-10-03，commit 92a3a2a）**：多阶段分辨率保护（H3「输出是一采」的真正根因）——提交链与 /比例 同步把所有官方 ResolutionSelector 盖成同一个 MP，二采目标尺寸选择器（115 的 1.0MP）被压成 0.5MP，放大链输出仍是一采尺寸；现多阶段选择器（文件内 MP 不同）按原比例分配目标像素、只统一宽高比，`_read_workflow_file_resolution` 取 MP 最大的选择器为基准。**实测：交付视频 960×544 → 1376×768（二采档）**。
- **v4.8.1（2026-10-03）**：飞书视频压缩——`_shrink_video_for_feishu`（ffmpeg libx264，ffprobe 取时长算目标码率，0.65/0.4 两级重试，+faststart），超 9MB 才压、原片保留；挂接 /生成视频 命令、comfyui_video 工具、`_send_image_to_feishu` 三处（QQ 不压）；经 `asyncio.to_thread` 不堵事件循环。**实测：10.4MB 二采片自动压到 7.3MB 推送飞书**。
- **v4.8.0（2026-10-03，commit 61b2901）**：双 VideoCombine 交付修复——一采+二采两个 VHS_VideoCombine 同前缀且都 save_output 时历史里两段都在，字典顺序决定交付谁（曾交付一采低清版）；视频任务多输出按文件体积降序（同时长下体积∝分辨率）高清优先；H3 工作流一采前缀改 `Video/H3_Video_P1`（.bak_prefix_20261003 备份）。
- **v4.7.2（2026-10-03，commit fb33158）**：最近图片本地落地——缓存此前只存平台图片 URL（飞书 URL 短时效），隔消息重跑同一张图必失败（「未能获取图片」）；现收到图片瞬间后台落地 `upload_dir/recent_cache/` 本地副本并替换缓存为本地路径（`_download_image` 允许目录内），TTL 10 分钟→1 小时，过期与每小时清理循环自动回收；下载全失败保留 URL 兜底。涉及链路：`_cache_incoming_images`（钩子）→ `_persist_recent_images`（新增）→ `_get_recent_images`（消费）。
- **v4.7.1（2026-10-03，commit 223497b）**：修复侧栏分类拖动排序无效果——分类更名后 `__category_order__` 残留旧名，前端 `_reorderCategories` 用旧名单过滤 DOM 分类，新分类全被滤掉、`indexOf` 得 -1 静默 return（无任何报错，"拖了白拖"）；现以侧栏实际渲染分类为准构建顺序，未分类保序补末尾；配置旧名排序已重置为新名。**教训入陷阱 27**：凡保存"名字列表"的持久化数据，改名后必须同步迁移，前端逻辑不得用持久化名单过滤当前 DOM。
- **v4.7.0（2026-10-03，commit 3ac5537）**：分类与命令更名——「画」→「文生图」、「图生视频」→「视频」；/画 → /文生图、/图生视频 → /生成视频，**旧命令名保留为等价别名**（转发处理器，帮助文本标注）。涉及面：LLM 工具描述与分类清单、/工作流 与 /切换 导航、数字菜单、工作流排序、随机图分类校验（兼容旧「画」）、`__commands__` 按类配置键、工作流管理页标签、魔导书规则页 t2i 判定（`wf.category === '文生图'`）。数据迁移经 /api/wf-category 完成（分类表+目录同步移动，旧空目录已删，H3 的 .groups.json sidecar 随文件挪到 视频/）。验证：迁移后 6 个工作流分类全部为新名，H3 视频在新分类下生成成功（H3_Video_00020）。
- **v4.6.1（2026-10-03，commit c770760）**：分类体系清除「反推」——LLM 工具分类清单、/工作流 与 /切换 导航菜单、数字菜单分类消费、`_order_workflows_by_category` 排序全部移除；图像反推.json 经分类 API 归为未分类（文件按"分类=目录"设计移回根目录，反推/ 子目录已空可删）。
- **v4.6.0（2026-10-03，commit 7ea76e1）**：移除反推功能——现代 LLM 自带视觉，独立反推冗余。删除 /反推 命令、飞书宽松变体、comfyui_reverse_prompt 工具、`_run_reverse_prompt` 及工具描述中的反推指引；工作流管理页移除反推筛选标签。反推分类降级为普通分类（已有工作流文件保留可执行）。**保留项**：imgrev 规则「图片反推·多图结构化」与反推功能无关（是图生图/图生视频的结构化改写规则），名称沿用未改。
- **v4.5.9（2026-10-03）**：图生图/图生视频跳过魔导书固定标签合并——带 `image_path` 的编辑类任务与随机图一样跳过 pin 合并（画师/画风标签如 `@ebora` 会污染编辑指令/视频描述）；文生图路径不变（实测 pin 照常合并）。K2 组合词本就只在随机图路径，无需改动。若日后要让 WebUI 准备面板的"带图生成"也跳过，需另行设计信号（该路径 image_path 为 None，图片经工作流文件 LoadImage 传入）。
- **v4.5.8（2026-10-03，commit 7a55293）**：官方面板落盘修复——面板走 saveParams 通道而旧 `/api/set-official-res` 是死代码，改动只进 saved_texts 被文件基准盖回（「分辨率选择改变不了」根因）；`/api/workflow-params` 现拦截分辨率类键直接写工作流文件+清 saved_texts 残留。端到端验证：面板设 4:3/1.3MP → 文件 9:16→4:3 → 提交图节点 91 = `4:3 (Standard) 1.3MP ×32`。
- **v4.5.7（2026-10-03，commit 2ab09ba）**：①WS 进度事件修复——ComfyUI v0.37 按**驼峰 clientId** 注册 WS 会话且执行事件点对点发提交者 sid 不广播，插件用蛇形 client_id 永远收不到事件（进度条 0% 不动的终极根因）；改 `?clientId=` 后实测生成中 `68% · ROCmKSampler · 节点 11/16 → 81% · VAEDecodeTiled · 采样 12/12` 实时流动。②提交链分辨率基准改为工作流文件自带值（`_read_workflow_file_resolution`），LLM 当次显式传参优先。
- **v4.5.6（2026-10-03，commit f17adc6 + 3113076）**：进度条实时化——`/api/progress` 新增合成总进度 `percent=(已完成节点数+当前节点采样分数)/总节点数`（封顶 99%），`node_label`（WS 只有裸节点 ID，提交时缓存 node_map 翻译成 class_type）与节点计数；前端生成中显示「节点类名 · 节点 n/N · 采样 x/y」。排障教训：重启脚本接管道 `| grep | head` 会因 SIGPIPE 半路杀死重启；metadata 条目含 ASCII 双引号会截断 YAML（插件加载失败）——**每次 bump 后必须 `yaml.safe_load` 校验**。
- **v4.5.5（2026-10-03，commit ab09b4f）**：①修复官方 ResolutionSelector 工作流比例/质量不生效——`_set_resolution` 只认手动指定的 `__resolution_node__`，未指定即放弃，这类工作流（分辨率在 aspect_ratio+megapixels 输入里）图里永远是文件旧值；现加官方节点自动探测兜底写入（与 /比例 同步路径同语义）。端到端验证：提交图节点 49 从 `9:16/0.9MP` → `16:9 (Widescreen)/1.56MP`，出图 2544×1440。②排障中发现**重启假阳性**：旧 AstrBot 进程自 21:08 一直存活，多次"重启成功"均未生效（详见陷阱 23）；已改为前台重启并验证插件加载行版本号，v4.5.3/4.5.4/4.5.5 全部修复自此才真正上线。
- **v4.5.4（2026-10-03，commit 8b89ecb）**：①魔导书词库原子写 + 损坏文件 `.corrupt` 隔离（原裸覆盖写 + 静默空读可致词库被无声清空）；②移除 `_process_and_submit` 中 v4.4.1 遗留的内联组禁用块（与 `_apply_group_modes` 双路径分歧，断头空引用会留下无效图）。巡查中另记录未修的低危项：图片注入失败不阻断提交（`if image_path:` 不检查返回值，上传失败时空引用提交→ComfyUI 端校验报错，属响亮失败可接受）；前端 `switchWF` 300ms 后 loadParams 的显示层竞态；`_rebuild_jzl_refs` 对所有工作流扫描 `ref_images.` 前缀（当前仅 JZL 使用，无实际影响）；`@register` 装饰器版本号 "1.0.0" 未随 metadata 更新（纯装饰）。
