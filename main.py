import json
import traceback
import uuid
import asyncio
import aiohttp
import re
import shutil
import time
from pathlib import Path
from datetime import datetime
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, FunctionTool
from astrbot.api.provider import ProviderRequest
from astrbot.api.message_components import Image as AstrImage, At, Plain
from aiohttp import web
from dataclasses import dataclass, field


# ====================================================================
# LLM 工具集（共10个）
# ====================================================================

@dataclass
class ComfyUIDrawTool(FunctionTool):
    name: str = "comfyui_draw"
    description: str = "使用本地ComfyUI生成图片（文生图）。可根据工作流名称关键词自动切换工作流。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "提示词（英文或中文），描述要生成的画面"},
            "workflow": {"type": "string", "description": "工作流名称关键词，例如'动漫'、'FLUX'、'真人'。不传则用当前工作流"},
            "ratio": {"type": "string", "description": "图片比例", "enum": ["1:1", "3:4", "4:3", "9:16", "16:9", "2:3", "3:2"]},
            "quality": {"type": "string", "description": "质量等级", "enum": ["480p", "720p", "1080p", "2K", "4K"]}
        },
        "required": ["prompt"]
    })

    async def run(self, event: AstrMessageEvent, prompt: str, workflow: str = None, ratio: str = None, quality: str = None):
        plugin = self._plugin
        if ratio and ratio not in plugin.aspect_ratios: ratio = None
        if workflow:
            for w in plugin._refresh_workflow_list():
                if workflow.lower() in w['name'].lower():
                    plugin._switch_to_workflow(w)
                    break
        if quality and quality in plugin.quality_presets:
            plugin.default_quality = quality
        try:
            umo = event.unified_msg_origin if hasattr(event, 'unified_msg_origin') else None
            if umo:
                from astrbot.api.event import MessageChain
                chain = MessageChain().message("🎨 生成中...")
                await plugin.context.send_message(umo, chain)
        except Exception as e:
            logger.warning(f"[ComfyUI] 发送生成中提示失败: {e}")
        status, text, path = await plugin._process_and_submit(prompt, ratio, user_id=event.get_sender_id())
        if status == "ok":
            sent = await plugin._send_image_result(event, "✨ 生成完成", path)
            if sent: return "✅ 图片已发送"
            try:
                from astrbot.api.message_components import Image
                from astrbot.api.event import MessageChain
                umo = event.unified_msg_origin if hasattr(event, 'unified_msg_origin') else None
                if umo:
                    chain = MessageChain().message("✨ 生成完成").file_image(path)
                    await plugin.context.send_message(umo, chain)
                    return "✅ 图片已发送"
            except Exception as e:
                logger.error(f"[ComfyUI] 发送图片失败: {e}")
            return f"✅ 图片已生成！文件: {path}"
        return text


@dataclass
class ComfyUIListWorkflowsTool(FunctionTool):
    name: str = "comfyui_list_workflows"
    description: str = "列出本地ComfyUI所有可用工作流"
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}, "required": []})

    async def run(self, event: AstrMessageEvent):
        wfs = self._plugin._refresh_workflow_list()
        if not wfs: return "当前没有可用工作流"
        result = "当前可用工作流：\n"
        for i, w in enumerate(wfs, 1): result += f"{i}. {w.get('display_name', w['name'])}{' ✅' if w['is_current'] else ''}\n"
        return result


@dataclass
class ComfyUISwitchWorkflowTool(FunctionTool):
    name: str = "comfyui_switch_workflow"
    description: str = "切换本地ComfyUI当前工作流"
    parameters: dict = field(default_factory=lambda: {
        "type": "object", "properties": {"keyword": {"type": "string"}}, "required": ["keyword"]
    })

    async def run(self, event: AstrMessageEvent, keyword: str):
        plugin = self._plugin
        for w in plugin._refresh_workflow_list():
            if keyword.lower() in w['name'].lower():
                plugin._switch_to_workflow(w)
                return f"✅ 已切换到【{w.get('display_name', w['name'])}】"
        return f"❌ 未找到包含'{keyword}'的工作流"


@dataclass
class ComfyUIGetCurrentWorkflowTool(FunctionTool):
    name: str = "comfyui_get_current_workflow"
    description: str = "获取当前工作流"
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}, "required": []})

    async def run(self, event: AstrMessageEvent):
        return f"当前工作流：【{self._plugin._get_display_name(self._plugin.current_workflow_name)}】" if self._plugin.current_workflow_name else "未设置"


# ====================================================================
# 新增工具：图生图 / 图生视频 / 编辑 / 随机图 / 队列 / 停止
# ====================================================================

@dataclass
class ComfyUIImg2ImgTool(FunctionTool):
    name: str = "comfyui_img2img"
    description: str = "使用本地ComfyUI图生图——以一张图片为基础，根据提示词修改生成新图片"
    parameters: dict = field(default_factory=lambda: {
        "type": "object", "properties": {
            "prompt": {"type": "string", "description": "提示词，描述要修改的方向，例如'把背景改成红色'"},
            "image_url": {"type": "string", "description": "输入图片的URL或本地文件路径"},
            "denoise": {"type": "number", "description": "降噪值 0.1~0.8，越低变化越小"},
            "workflow": {"type": "string", "description": "工作流名称关键词，不传则用当前工作流"},
            "ratio": {"type": "string", "description": "图片比例", "enum": ["1:1", "3:4", "4:3", "9:16", "16:9", "2:3", "3:2"]}
        }, "required": ["prompt", "image_url"]
    })

    async def run(self, event: AstrMessageEvent, prompt: str, image_url: str, denoise: float = None, workflow: str = None, ratio: str = None):
        plugin = self._plugin
        if ratio and ratio not in plugin.aspect_ratios: ratio = None
        if workflow:
            for w in plugin._refresh_workflow_list():
                if workflow.lower() in w['name'].lower():
                    plugin._switch_to_workflow(w); break
        save_path = plugin._get_image_save_dir() / f"llm_img_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
        try:
            umo = event.unified_msg_origin if hasattr(event, 'unified_msg_origin') else None
            if umo:
                from astrbot.api.event import MessageChain
                await plugin.context.send_message(umo, MessageChain().message("🖼️ 图生图中..."))
        except Exception: pass
        if not await plugin._download_image(image_url, save_path):
            return "❌ 下载图片失败，请检查image_url是否正确"
        cmd_config = {}
        if denoise is not None:
            try:
                with open(plugin.workflow_path, 'r', encoding='utf-8') as f: wf = json.load(f)
                sn = plugin._find_sampler_node(wf)
                if sn: cmd_config[f'{sn}_denoise'] = denoise
            except Exception: pass
        status, text, out_path = await plugin._process_and_submit(prompt, ratio, str(save_path), cmd_config=cmd_config or None, user_id=event.get_sender_id())
        if status == "ok":
            sent = await plugin._send_image_result(event, f"✨ 图生图完成 当前{text}", out_path)
            if sent: return "✅ 图片已发送"
            try:
                from astrbot.api.message_components import Image; from astrbot.api.event import MessageChain
                umo = event.unified_msg_origin if hasattr(event, 'unified_msg_origin') else None
                if umo:
                    await plugin.context.send_message(umo, MessageChain().message("✨ 图生图完成").file_image(out_path))
                    return "✅ 图片已发送"
            except Exception: pass
            return f"✅ 图片已生成！文件: {out_path}"
        return text


@dataclass
class ComfyUIVideoTool(FunctionTool):
    name: str = "comfyui_video"
    description: str = "使用本地ComfyUI生成视频（图生视频）。需要一张输入图片和视频工作流。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object", "properties": {
            "prompt": {"type": "string", "description": "提示词，描述视频内容"},
            "image_url": {"type": "string", "description": "输入图片的URL或本地文件路径"},
            "workflow": {"type": "string", "description": "工作流名称关键词，不传则用当前工作流"}
        }, "required": ["image_url"]
    })

    async def run(self, event: AstrMessageEvent, image_url: str, prompt: str = "", workflow: str = None):
        plugin = self._plugin
        if workflow:
            for w in plugin._refresh_workflow_list():
                if workflow.lower() in w['name'].lower():
                    plugin._switch_to_workflow(w); break
        video_kw = ['视频', 'wan', 'ltx', 'animate', 'video', 'WAN']
        if not any(k in plugin.current_workflow_name for k in video_kw):
            return f"❌ 当前工作流「{plugin._get_display_name(plugin.current_workflow_name)}」不是视频工作流"
        save_path = plugin._get_image_save_dir() / f"llm_vid_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
        try:
            umo = event.unified_msg_origin if hasattr(event, 'unified_msg_origin') else None
            if umo:
                from astrbot.api.event import MessageChain
                await plugin.context.send_message(umo, MessageChain().message("🎬 生成视频中..."))
        except Exception: pass
        if not await plugin._download_image(image_url, save_path):
            return "❌ 下载图片失败"
        status, text, out_path = await plugin._process_and_submit(prompt, None, str(save_path), user_id=event.get_sender_id())
        if status == "ok":
            try:
                from astrbot.api.message_components import Video, At, Plain
                umo = event.unified_msg_origin if hasattr(event, 'unified_msg_origin') else None
                if umo:
                    await plugin.context.send_message(umo, MessageChain().message("🎬 视频生成完毕").file_image(out_path))
                return "✅ 视频已生成并发送"
            except Exception as e:
                logger.error(f"[ComfyUI] 发送视频失败: {e}")
            return f"✅ 视频已生成！文件: {out_path}"
        return text


@dataclass
class ComfyUIEditTool(FunctionTool):
    name: str = "comfyui_edit"
    description: str = "使用本地ComfyUI编辑图片——输入1到3张参考图，根据指令进行编辑修改"
    parameters: dict = field(default_factory=lambda: {
        "type": "object", "properties": {
            "prompt": {"type": "string", "description": "编辑指令，描述要如何修改图片，例如'把猫换成狗'"},
            "image_urls": {"type": "string", "description": "1~3张图片URL，用英文逗号分隔"},
            "workflow": {"type": "string", "description": "工作流名称关键词，不传则用当前工作流"}
        }, "required": ["prompt", "image_urls"]
    })

    async def run(self, event: AstrMessageEvent, prompt: str, image_urls: str, workflow: str = None):
        plugin = self._plugin
        if workflow:
            for w in plugin._refresh_workflow_list():
                if workflow.lower() in w['name'].lower():
                    plugin._switch_to_workflow(w); break
        urls = [u.strip() for u in image_urls.split(',') if u.strip()][:3]
        if not urls:
            return "❌ 请提供至少一张图片URL"
        saved_paths = []
        for i, url in enumerate(urls):
            sp = plugin._get_image_save_dir() / f"llm_edit_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{i}.png"
            if await plugin._download_image(url, sp):
                saved_paths.append(str(sp))
        if not saved_paths:
            return "❌ 下载图片失败"
        try:
            umo = event.unified_msg_origin if hasattr(event, 'unified_msg_origin') else None
            if umo:
                from astrbot.api.event import MessageChain
                await plugin.context.send_message(umo, MessageChain().message(f"✏️ 编辑中...（{len(saved_paths)}张参考图）"))
        except Exception: pass
        cmd_config = dict(plugin.workflow_config.get('__commands__', {}).get('编辑', {})) or None
        status, text, out_path = await plugin._process_and_submit(prompt, None, saved_paths, cmd_config=cmd_config, user_id=event.get_sender_id())
        # 清理临时下载的图片（HTTP 上传后已不需要）
        for p in saved_paths:
            try: Path(p).unlink(missing_ok=True)
            except Exception: pass
        if status == "ok":
            sent = await plugin._send_image_result(event, f"✨ 编辑完成 当前{text}", out_path)
            if sent: return "✅ 编辑完成，图片已发送"
            try:
                from astrbot.api.message_components import Image; from astrbot.api.event import MessageChain
                umo = event.unified_msg_origin if hasattr(event, 'unified_msg_origin') else None
                if umo:
                    await plugin.context.send_message(umo, MessageChain().message("✨ 编辑完成").file_image(out_path))
                    return "✅ 编辑完成，图片已发送"
            except Exception: pass
            return f"✅ 编辑完成！文件: {out_path}"
        return text


@dataclass
class ComfyUIRandomTool(FunctionTool):
    name: str = "comfyui_random"
    description: str = "使用本地ComfyUI随机抽卡——在抽卡工作流上随机生成图片。每次可指定张数。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object", "properties": {
            "count": {"type": "integer", "description": "抽卡张数，默认1张，最多10张"},
            "workflow": {"type": "string", "description": "工作流名称关键词，需包含'抽卡'关键字"}
        }, "required": []
    })

    async def run(self, event: AstrMessageEvent, count: int = 1, workflow: str = None):
        plugin = self._plugin
        if workflow:
            for w in plugin._refresh_workflow_list():
                if workflow.lower() in w['name'].lower():
                    plugin._switch_to_workflow(w); break
        if "抽卡" not in plugin.current_workflow_name:
            return "❌ 随机抽卡只能在名称包含「抽卡」的工作流上使用"
        count = max(1, min(count or 1, 10))
        results = []
        for i in range(count):
            status, text, path = await plugin._process_and_submit("", None, user_id=event.get_sender_id())
            if status == "ok":
                sent = await plugin._send_image_result(event, f"✨ 抽卡完成 当前{text}", path)
                if sent: results.append(f"第{i+1}张: ✅ 已发送")
                else: results.append(f"第{i+1}张: ✅ 已生成")
            else:
                results.append(f"第{i+1}张: ❌ {text}")
            if i < count - 1: await asyncio.sleep(1)
        return f"🎴 抽卡结果（共{count}张）：\n" + "\n".join(results)


@dataclass
class ComfyUIQueueTool(FunctionTool):
    name: str = "comfyui_queue_status"
    description: str = "查看ComfyUI队列状态——检查当前是否有任务在运行或在排队"
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}, "required": []})

    async def run(self, event: AstrMessageEvent):
        try:
            total, running, pending = await self._plugin._get_queue_status()
            if total == 0: return "✅ 队列为空，可以提交新任务"
            return f"📊 队列状态：运行中 {running} 个 | 等待中 {pending} 个 | 总计 {total} 个"
        except Exception as e:
            logger.warning(f"[ComfyUI] 获取队列失败: {e}")
            return "❌ 无法获取队列状态（ComfyUI可能未运行）"


@dataclass
class ComfyUIStopTool(FunctionTool):
    name: str = "comfyui_stop"
    description: str = "停止当前用户的ComfyUI生成任务。当用户说'停下'、'取消'、'别画了'时调用。"
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}, "required": []})

    async def run(self, event: AstrMessageEvent):
        plugin = self._plugin
        user_id = event.get_sender_id()
        async with plugin._task_lock:
            my_tasks = [pid for pid, uid in plugin.task_map.items() if uid == user_id]
        if not my_tasks:
            return "✅ 没有正在运行的任务"
        try:
            import aiohttp
            async with aiohttp.ClientSession() as s:
                await s.post(f"http://{plugin.comfyui_url}/interrupt")
                async with plugin._task_lock:
                    for pid in my_tasks: plugin.task_map.pop(pid, None)
            return f"⏹️ 已停止 {len(my_tasks)} 个任务"
        except Exception as e:
            logger.warning(f"[ComfyUI] 停止任务失败: {e}")
            return "❌ 停止失败"


@dataclass
class ComfyUIExecuteTool(FunctionTool):
    name: str = "comfyui_execute"
    description: str = "执行当前选中的工作流，不对分类做限制。适用于用户说'执行'、'生成'但没有明确指定分类的场景。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object", "properties": {
            "prompt": {"type": "string", "description": "提示词，描述要生成的画面或内容"},
            "workflow": {"type": "string", "description": "工作流名称关键词，例如'动漫'、'FLUX'。不传则用当前工作流"}
        }, "required": ["prompt"]
    })

    async def run(self, event: AstrMessageEvent, prompt: str, workflow: str = None):
        plugin = self._plugin
        if workflow:
            for w in plugin._refresh_workflow_list():
                if workflow.lower() in w['name'].lower():
                    plugin._switch_to_workflow(w); break
        if not plugin.current_workflow_name:
            return "❌ 未选中工作流，请先选择工作流"
        try:
            umo = event.unified_msg_origin if hasattr(event, 'unified_msg_origin') else None
            if umo:
                from astrbot.api.event import MessageChain
                await plugin.context.send_message(umo, MessageChain().message("⚡ 执行中..."))
        except Exception: pass
        status, text, path = await plugin._process_and_submit(prompt, None, user_id=event.get_sender_id())
        if status == "ok":
            sent = await plugin._send_image_result(event, f"✨ 执行完成 当前{text}", path)
            if sent: return "✅ 执行完成，图片已发送"
            try:
                from astrbot.api.message_components import Image; from astrbot.api.event import MessageChain
                umo = event.unified_msg_origin if hasattr(event, 'unified_msg_origin') else None
                if umo:
                    await plugin.context.send_message(umo, MessageChain().message("✨ 执行完成").file_image(path))
                    return "✅ 执行完成，图片已发送"
            except Exception: pass
            return f"✅ 执行完成！文件: {path}"
        return text


@register("astrbot_plugin_comfyui_local", "BLack_Rin_ROBOT", "连接本地ComfyUI生成图片", "1.0.0")
class ComfyUILocalPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context, config)
        if config is not None: self.config = config
        local_cfg = self._load_local_config()
        self.comfyui_url = local_cfg.get("comfyui_url") or self.config.get("comfyui_url", "127.0.0.1:8188")
        # 目录配置：本地配置存在但键不存在/为空时，不fallback到config默认值，保持为空让前端显示占位符
        out = local_cfg.get("output_dir")
        self.output_dir = Path(out) if out else Path()
        self.upload_mode = local_cfg.get("upload_mode") or self.config.get("upload_mode", "local")
        self.webui_port = int(local_cfg.get("webui_port") or self.config.get("webui_port", 8898))
        self.webui_lan = bool(local_cfg.get("webui_lan", False) or self.config.get("webui_lan", False) or False)
        self.webui_ipv6 = bool(local_cfg.get("webui_ipv6", False) or self.config.get("webui_ipv6", False) or False)
        self.upload_dir = self.output_dir / "upload" if self.output_dir.parts else Path()
        self.preview_dir = Path(__file__).parent / "previews"
        if self.output_dir.parts:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        self.workflow_path = ""
        self.workflow_dir = Path()
        self.current_workflow_name = ""
        self.workflow_list_cache = []
        self._context_workflows = {}
        self._config_lock = asyncio.Lock()   # 保护 workflow_config 的并发写入
        self._ctx_lock = asyncio.Lock()      # 保护 _context_workflows 的并发写入
        self.workflow_config = {}
        config_path = Path(__file__).resolve().parent / "plugin_config.json"
        if config_path.exists():
            try:
                with open(str(config_path), 'r', encoding='utf-8') as f:
                    self.workflow_config = json.load(f)
            except Exception as e:
                logger.warning(f"[ComfyUI] 读取工作流配置失败: {e}")
        # 将文件中显式保存的工作流目录同步到实例变量，确保 /api/config 返回正确值
        saved_wf_dir = self._load_local_config().get("workflow_dir", "")
        if saved_wf_dir:
            self.workflow_dir = Path(saved_wf_dir)
        self.task_map = {}
        self._task_lock = asyncio.Lock()
        self._cancelled_pids = set()  # 被取消的 prompt_id，旧任务检测到后立即停止轮询
        self._progress = {}  # prompt_id -> {value, max, node} 实时进度（兼容旧逻辑）
        self._prompt_node_count = {}  # prompt_id -> int 总节点数
        self._prompt_progress = {}  # prompt_id -> {nodes_done, nodes_total, node_name, node_value, node_max, running}
        self._generating_lock = False  # 是否正在生成（由 _process_and_submit 设置/清除）
        self._prompt_start_time = {}  # prompt_id -> float timestamp
        self._ws_client_id = str(uuid.uuid4())  # 固定 client_id，WS 监听器和 prompt 提交使用同一个
        self.current_prompt_id = None
        self.pending_actions = {}  # {user_id: {"action": str, "data": dict, "expires_at": float}}
        # 已发送图片记录 {abs_path: [{"message_id": str, "umo": str, "sent_at": float}, ...]}
        self._sent_images = {}
        self._sent_images_lock = asyncio.Lock()
        # OneBot bot 引用（从 event.bot 获取，供撤回使用）
        self._bot_ref = None
        # 质量预设（像素密度等级，非固定分辨率；实际宽高由比例动态计算）
        self.quality_presets = {
            "480p": {"name": "SD", "pixels": 400_000},
            "720p": {"name": "标清", "pixels": 900_000},
            "1080p": {"name": "高清", "pixels": 2_000_000},
            "2K": {"name": "超清", "pixels": 3_700_000},
            "4K": {"name": "原画", "pixels": 8_300_000},
        }
        self.default_quality = local_cfg.get("default_quality") or "720p"
        # 比例列表（纯字符串，宽高由质量动态计算）
        self.aspect_ratios = ["1:1", "3:4", "4:3", "9:16", "16:9", "2:3", "3:2"]
        self.default_ratio = local_cfg.get("default_ratio") or self.config.get("default_ratio", "9:16")
        # 默认宽高由质量和比例动态计算
        self.default_width, self.default_height = self._calc_resolution(self.default_quality, self.default_ratio)
        self._start_webui()
        wdir = self._get_workflow_dir(); wdir.mkdir(parents=True, exist_ok=True)
        self._refresh_workflow_list()
        self._register_tools()
        # 创建后台任务。AstrBot 的 __init__ 在事件循环中执行，使用 get_running_loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        loop.create_task(self._cleanup_upload_loop())
        loop.create_task(self._ws_progress_listener())
        lan = f"，局域网 http://你的IP:{self.webui_port}" if self.webui_lan else ""
        ipv6 = f"，IPv6 http://[你的IPv6]:{self.webui_port}" if self.webui_ipv6 else ""
        logger.info(f"[ComfyUI] WebUI: http://127.0.0.1:{self.webui_port}{lan}{ipv6} | 工作流目录: {wdir} | 上传模式: {self.upload_mode}")

    def _load_local_config(self):
        p = Path(__file__).resolve().parent / "plugin_config.json"
        if p.exists():
            try:
                with open(str(p), 'r', encoding='utf-8-sig') as f:
                    data = json.load(f)
                return data.get('__local_config__', {})
            except Exception as e:
                logger.warning(f"[ComfyUI] 读取配置失败: {e}")
        return {}

    def _save_local_config(self, updates: dict):
        """保存 __local_config__ 到文件，失败时不会破坏原文件"""
        if not updates:
            return
        p = Path(__file__).resolve().parent / "plugin_config.json"
        tmp_p = p.with_suffix('.json.tmp')
        try:
            existing = {}
            if p.exists():
                with open(str(p), 'r', encoding='utf-8-sig') as f:
                    existing = json.load(f)
            lc = existing.get('__local_config__', {})
            lc.update(updates)
            existing['__local_config__'] = lc
            with open(str(tmp_p), 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            tmp_p.replace(p)  # 原子替换
            # 同步更新内存中的 self.workflow_config，防止 _save_workflow_config 覆盖
            if hasattr(self, 'workflow_config') and isinstance(self.workflow_config, dict):
                self.workflow_config.update(existing)
        except Exception as e:
            logger.warning(f"[ComfyUI] 保存配置失败: {e}")
            if tmp_p.exists():
                try:
                    tmp_p.unlink()
                except Exception:
                    pass

    def _register_tools(self):
        try:
            tools = [ComfyUIDrawTool(), ComfyUIListWorkflowsTool(), ComfyUISwitchWorkflowTool(), ComfyUIGetCurrentWorkflowTool(),
                     ComfyUIImg2ImgTool(), ComfyUIVideoTool(), ComfyUIEditTool(), ComfyUIRandomTool(),
                     ComfyUIQueueTool(), ComfyUIStopTool(), ComfyUIExecuteTool()]
            for t in tools: t._plugin = self
            self.context.add_llm_tools(*tools)
        except Exception as e:
            logger.warning(f"[ComfyUI] LLM工具注册失败: {e}")

    def _get_workflow_dir(self):
        # 实例变量优先（用于用户已通过 WebUI 保存过的目录）
        if self.workflow_dir.parts:
            return self.workflow_dir
        p = Path(__file__).resolve().parent / "plugin_config.json"
        if p.exists():
            try:
                with open(p, 'r', encoding='utf-8-sig') as f:
                    data = json.load(f)
                d = data.get('__local_config__', {}).get("workflow_dir", "")
                if d: return Path(d)
            except Exception as e:
                logger.warning(f"[ComfyUI] 读取工作流目录配置失败: {e}")
        return Path(self.config.get("workflow_dir", "E:\\AIwork\\NapCat.Shell.Windows.Node\\napcat\\temp\\comfyui_data\\workflows"))

    def _save_workflow_dir(self, path):
        self.workflow_dir = Path(path)
        self.config['workflow_dir'] = str(path)
        self._save_local_config({"workflow_dir": str(path)})

    async def _save_workflow_config(self):
        """持久化 workflow_config 到文件（线程安全）

        注意：此函数不自带锁（async with self._config_lock）。
        caller 需要自行确保在持锁或安全的上下文中调用，避免与
        asyncio.Lock（不可重入）产生死锁。
        """
        config_path = Path(__file__).resolve().parent / "plugin_config.json"
        try:
            # 先读取文件最新内容，合并到内存数据中，防止丢失 __local_config__ 等
            if config_path.exists():
                with open(str(config_path), 'r', encoding='utf-8-sig') as f:
                    file_data = json.load(f)
                file_data.update(self.workflow_config)
                self.workflow_config = file_data
            with open(str(config_path), 'w', encoding='utf-8') as f:
                json.dump(self.workflow_config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[ComfyUI] 保存工作流配置失败: {e}\n{traceback.format_exc()}")

    def _switch_to_workflow(self, wf, context_key=None):
        if context_key:
            self._context_workflows[context_key] = wf['name']
        self.workflow_path = wf['path']
        self.current_workflow_name = wf['name']
        self._refresh_workflow_list()

    async def _send_image_result(self, event, text, path):
        """发图。群消息会附带 At @mention，返回 True/False"""
        try:
            umo = getattr(event, 'unified_msg_origin', None)
            if not umo:
                return False

            # 去掉 text 中可能残留的 [CQ:at,...] 前缀（仅 OneBot/QQ 平台）
            clean_text = text
            if hasattr(event, 'bot') or 'CQ:' in text:
                clean_text = re.sub(r'\[CQ:at[^\]]*\]', '', text).strip()

            abs_path = str(Path(path).resolve())
            msg_id = ''
            _onebot_ok = False

            # 通过 event.bot 直接调用 OneBot API（可获取 message_id）
            bot = getattr(event, 'bot', None)
            if bot:
                self._bot_ref = bot  # 存引用，供后续撤回使用
                try:
                    # 解析 UMO 获取消息类型和目标
                    umo_parts = umo.split(':')
                    target_type = ''
                    target_id = ''
                    if len(umo_parts) >= 3:
                        if 'group' in umo_parts[1].lower():
                            target_type = 'group'
                            target_id = umo_parts[2]
                        else:
                            target_type = 'private'
                            target_id = umo_parts[2]
                    elif len(umo_parts) >= 2:
                        target_type = 'private'
                        target_id = umo_parts[1]

                    if target_type and target_id:
                        # 使用 event.bot 直接调用 OneBot API
                        method_name = 'send_group_msg' if target_type == 'group' else 'send_private_msg'
                        params_key = 'group_id' if target_type == 'group' else 'user_id'
                        # 用 CQ 码字符串发送（含文字和图片）
                        cq_msg = f"{clean_text}\n[CQ:image,file={path}]"
                        txt_result = await getattr(bot, method_name)(**{params_key: int(target_id), 'message': cq_msg})
                        logger.info(f"[ComfyUI] OneBot send result: {txt_result}")
                        if isinstance(txt_result, dict):
                            msg_id = str(txt_result.get('message_id', ''))
                        elif isinstance(txt_result, (int, str)):
                            msg_id = str(txt_result)
                        if msg_id:
                            _onebot_ok = True
                            logger.info(f"[ComfyUI] event.bot 发送成功，msg_id={msg_id}")
                        else:
                            logger.warning(f"[ComfyUI] event.bot 未返回 message_id")
                    else:
                        logger.info(f"[ComfyUI] UMO 解析失败（parts={umo_parts}），走 context.send_message")
                except Exception as e:
                    logger.warning(f"[ComfyUI] event.bot 发送异常: {type(e).__name__}: {e}")
            else:
                logger.info(f"[ComfyUI] event.bot 不存在（type(event)={type(event).__name__}），走 context.send_message")

            # 如果 OneBot 发送失败或不存在，用 AstrBot 标准方式发送
            if not _onebot_ok:
                parts = []
                if not event.is_private_chat():
                    sender_id = event.get_sender_id()
                    parts.append(At(qq=sender_id))
                parts.append(Plain(text=clean_text))
                parts.append(AstrImage(file=path))
                chain = MessageChain(chain=parts)
                result = await self.context.send_message(umo, chain)
                # 尝试从 result 提取 message_id
                if isinstance(result, dict):
                    msg_id = str(result.get('message_id', ''))
                elif isinstance(result, (list, tuple)) and len(result) > 0:
                    first = result[0]
                    msg_id = str(first.get('message_id', '')) if isinstance(first, dict) else str(first)
                elif hasattr(result, 'message_id'):
                    msg_id = str(result.message_id)
                elif hasattr(result, 'message_ids'):
                    msg_id = str(result.message_ids[0]) if result.message_ids else ''
                elif isinstance(result, str) and result.strip():
                    msg_id = result.strip()

            # 记录发送信息到 _sent_images
            try:
                if msg_id:
                    logger.info(f"[ComfyUI] 记录发送图片: {abs_path} -> msg_id={msg_id}")
                else:
                    logger.info(f"[ComfyUI] 未获取到 message_id，撤回将不可用")
                async with self._sent_images_lock:
                    record = {"message_id": msg_id, "umo": umo, "sent_at": time.time()}
                    if abs_path not in self._sent_images:
                        self._sent_images[abs_path] = []
                    self._sent_images[abs_path].append(record)
                    if len(self._sent_images[abs_path]) > 20:
                        self._sent_images[abs_path] = self._sent_images[abs_path][-20:]
            except Exception as e:
                logger.debug(f"[ComfyUI] 记录发送图片失败: {e}")
            return True
        except Exception as e:
            logger.error(f"[ComfyUI] 发送组合消息失败: {e}")
            return False

    def _get_context_key(self, event):
        """统一获取上下文key。优先用 get_parent_id() 判断群消息。"""
        user_id = event.get_sender_id()
        umo = getattr(event, 'unified_msg_origin', '') or ''

        # 方法1: get_parent_id() — AstrBot官方API，群消息返回群号
        group_id = ''
        try:
            if hasattr(event, 'get_parent_id') and callable(event.get_parent_id):
                pid = event.get_parent_id()
                if pid is not None and str(pid).strip() and str(pid) != 'None':
                    group_id = str(pid)
        except Exception:
            pass

        # 方法2: 底层 event.group_id 属性 (aiocqhttp/NapCat 直接提供)
        if not group_id:
            try:
                if hasattr(event, 'group_id') and event.group_id is not None:
                    gid = str(event.group_id).strip()
                    if gid:
                        group_id = gid
            except Exception:
                pass

        # 方法3: 从 UMO 解析 (例: aiocqhttp:group_message:群号 或 aiocqhttp:群号:QQ号)
        if not group_id and umo:
            parts = umo.split(':')
            if len(parts) >= 3 and 'group' in parts[1].lower():
                group_id = parts[2].strip()
            elif len(parts) >= 2 and 'group' in parts[0].lower():
                # 格式可能是 group_xxx:...
                group_id = parts[0].replace('group_', '', 1).strip()

        # 方法4: 检查 event.is_group_message()
        if not group_id:
            is_group = event.is_group_message() if hasattr(event, 'is_group_message') else False
            if is_group and umo:
                for p in reversed(umo.split(':')):
                    p = p.strip()
                    if p.isdigit():
                        group_id = p
                        break

        if group_id:
            return f"group_{group_id}"
        return f"private_{user_id}" if user_id else ""

    def _extract_group_id(self, event) -> str:
        """从事件中统一提取群号"""
        # 方法1: get_parent_id() 最权威
        try:
            if hasattr(event, 'get_parent_id') and callable(event.get_parent_id):
                pid = event.get_parent_id()
                if pid is not None and str(pid).strip() and str(pid) != 'None':
                    return str(pid)
        except Exception:
            pass

        # 方法2: 底层 event.group_id
        try:
            if hasattr(event, 'group_id') and event.group_id is not None:
                return str(event.group_id).strip()
        except Exception:
            pass

        # 方法3: UMO 解析
        umo = getattr(event, 'unified_msg_origin', '') or ''
        if umo:
            parts = umo.split(':')
            if len(parts) >= 3 and 'group' in parts[1].lower():
                if parts[2].strip():
                    return parts[2].strip()
            # 兜底: 找纯数字段
            for p in parts:
                p = p.strip()
                if p.isdigit():
                    return p

        # 方法4: 向后兼容旧格式
        if umo:
            for p in umo.split(':'):
                if p.startswith('group_'):
                    return p.replace('group_', '')

        return ''

    async def _get_event_bindings(self, event):
        """获取事件的绑定列表，返回 (user_id, group_id, allowed_workflows)"""
        await self._clean_stale_bindings()  # 自动清理孤立绑定
        user_id = event.get_sender_id()
        group_id = self._extract_group_id(event)
        gb = self.workflow_config.get('__group_bindings__', {}) or {}
        ub = self.workflow_config.get('__user_bindings__', {}) or {}

        async def _resolve_bindings(bindings_dict, key):
            """从绑定字典中查找 key（自动处理脏数据），并自动修复"""
            if not key:
                return []
            # 直接查
            val = bindings_dict.get(key)
            if val is not None:
                if isinstance(val, str): val = [val]
                return val
            # 脏 key 容错：遍历找 strip() 后匹配的
            for stored_key in list(bindings_dict.keys()):
                if stored_key.strip() == key:
                    val = bindings_dict[stored_key]
                    # 自动修复：把脏 key 写回干净 key，删掉脏的
                    if stored_key != key:
                        bindings_dict[key] = val
                        del bindings_dict[stored_key]
                        await self._save_workflow_config()
                    if isinstance(val, str): val = [val]
                    return val
            return []

        uws = await _resolve_bindings(ub, user_id)
        gws = await _resolve_bindings(gb, group_id)
        allowed = uws if uws else gws
        return user_id, group_id, allowed

    async def _ensure_workflow_for_event(self, event):
        context_key = self._get_context_key(event)
        if not context_key:
            return
        saved = self._context_workflows.get(context_key)

        # 如果 saved 存在，验证它是否仍然是有效的（文件未被删除）
        saved_valid = False
        if saved:
            for wf in (self.workflow_list_cache or []):
                if wf['name'] == saved:
                    saved_valid = True
                    break

        if saved and saved_valid and saved != self.current_workflow_name:
            # 恢复之前保存的上下文工作流
            for wf in self.workflow_list_cache:
                if wf['name'] == saved:
                    self._switch_to_workflow(wf)
                    break
        elif not saved or not saved_valid:
            # 无上下文或上下文失效 → 检查绑定
            _, _, allowed = await self._get_event_bindings(event)
            target = (allowed or [None])[0]
            if target and target != self.current_workflow_name:
                for wf in self.workflow_list_cache:
                    if wf['name'] == target:
                        self._switch_to_workflow(wf)
                        break

        # 更新上下文记录
        self._context_workflows[context_key] = self.current_workflow_name

    async def _ensure_command_workflow(self, event, cmd_name):
        """根据 __wf_categories__ 分类确保当前工作流允许该命令执行。

        分类名与命令名一致（如 画、图生图、编辑），只有分类匹配的工作流才能用该命令。
        未分类的工作流可通过 /执行 命令运行。

        返回值：元组 (can_execute, needs_selection, matching_workflows)
        - can_execute=True → 可以直接执行（当前工作流已匹配 或 已自动切到唯一匹配）
        - needs_selection=True → 需要用户选择，调用方应展示菜单并设 pending_action
        - 两者都 False → 无可用工作流，调用方应报错
        """
        cats = self.workflow_config.get('__wf_categories__', {}) or {}
        cur = self.current_workflow_name
        cur_cat = cats.get(cur, '')

        logger.info(f"[ComfyUI] _ensure_command_workflow: cmd={cmd_name}, cur={cur}, cur_cat={cur_cat!r}, cats_keys={list(cats.keys())}")

        if not cur_cat:
            cur_cat = '(未分类)'
        elif cur_cat == cmd_name:
            return (True, False, [])  # 分类匹配 → 直接执行

        # 分类不匹配 → 拒绝执行，提示用户
        logger.warning(f"[ComfyUI] /{cmd_name}：当前「{cur}」分类「{cur_cat}」，不是「{cmd_name}」类工作流，拒绝执行")
        return (False, False, [])

    def _build_wf_selection_menu(self, event, cmd_name, matching_wfs):
        """构建工作流选择菜单文本，并设置 pending_action。调用方 yield 该文本后 return。"""
        user_id = event.get_sender_id()
        cmd_icons = {'画': '🎨', '随机图': '🎲', '图生图': '🖼️', '图生视频': '🎬', '编辑': '✏️'}
        icon = cmd_icons.get(cmd_name, '📋')
        m = f"{icon} 找到 {len(matching_wfs)} 个「{cmd_name}」工作流，请选择：\n\n"
        for i, wf in enumerate(matching_wfs, 1):
            dn = wf.get('display_name', wf['name'])
            m += f"  [{i}] {dn}\n"
        m += "\n回复数字选择（30s 内有效）"
        self._set_pending_action(user_id, 'switch_workflow', {
            'workflows': matching_wfs,
            'context_key': self._get_context_key(event),
        }, timeout=30)
        return m

    def _start_webui(self):
        app = web.Application()
        app.router.add_get('/', lambda r: web.FileResponse(Path(__file__).parent / 'webui.html'))
        app.router.add_get('/api/config', lambda r: web.json_response({
            "comfyui_url": self.comfyui_url,
            "workflow_dir": str(self.workflow_dir) if self.workflow_dir.parts else "",
            "output_dir": str(self.output_dir) if self.output_dir.parts else "",
            "upload_mode": self.upload_mode,
            "webui_port": self.webui_port,
            "webui_lan": self.webui_lan,
            "webui_ipv6": self.webui_ipv6,
        }))
        app.router.add_post('/api/config', self._webui_save_config)
        app.router.add_get('/api/groups', self._webui_get_groups)
        app.router.add_get('/api/proxy', self._webui_proxy)
        app.router.add_get('/api/workflows', lambda r: web.json_response(self._refresh_workflow_list() or []))
        app.router.add_get('/api/workflows/all', lambda r: web.json_response(self.workflow_list_cache or []))
        app.router.add_post('/api/workflows/switch', self._webui_switch_workflow)
        app.router.add_post('/api/workflows/toggle-hidden', self._webui_toggle_hidden)
        app.router.add_get('/api/comfy-models', self._webui_get_models)
        app.router.add_get('/api/progress', self._webui_get_progress)
        app.router.add_post('/api/open-dir', self._webui_open_dir)
        app.router.add_post('/api/workflow-dir', self._webui_set_workflow_dir)
        app.router.add_get('/api/workflow-params', self._webui_get_workflow_params)
        app.router.add_get('/api/workflow-params-config', self._webui_get_workflow_params_config)
        app.router.add_post('/api/workflow-params', self._webui_save_workflow_params)
        app.router.add_get('/api/workflow-bind', self._webui_get_bindings)
        app.router.add_post('/api/workflow-bind', self._webui_save_binding)
        app.router.add_post('/api/workflow-bind/delete', self._webui_delete_binding)
        app.router.add_post('/api/wf-category', self._webui_set_wf_category)
        app.router.add_post('/api/wf-category-order', self._webui_save_category_order)
        app.router.add_post('/api/wf-delete', self._webui_delete_workflow)
        app.router.add_post('/api/wf-add', self._webui_add_workflows)
        app.router.add_post('/api/upload-image', self._webui_upload_image)
        app.router.add_get('/api/context-workflows', self._webui_get_context_workflows)
        app.router.add_post('/api/context-workflow', self._webui_set_context_workflow)
        # 工作流预览图 API
        app.router.add_get('/api/wf_previews', self._webui_get_wf_previews)
        app.router.add_post('/api/wf_preview', self._webui_upload_wf_preview)
        app.router.add_get('/api/wf_preview', self._webui_get_wf_preview)
        # WebUI 质量与比例 API
        app.router.add_post('/api/set-quality', self._webui_set_quality)
        app.router.add_post('/api/set-ratio', self._webui_set_ratio)
        app.router.add_post('/api/reset', self._webui_reset_all)
        # 画廊 API
        app.router.add_get('/api/gallery', self._webui_get_gallery)
        app.router.add_get('/api/gallery/file', self._webui_gallery_file)
        app.router.add_post('/api/gallery/delete', self._webui_gallery_delete)
        app.router.add_post('/api/gallery/recall', self._webui_gallery_recall)
        asyncio.get_event_loop().create_task(self._run_webui(web.AppRunner(app)))

    async def _run_webui(self, runner):
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', self.webui_port).start()
        if self.webui_lan:
            await web.TCPSite(runner, '0.0.0.0', self.webui_port).start()
        if self.webui_ipv6:
            await web.TCPSite(runner, '::', self.webui_port).start()

    async def _webui_save_config(self, r):
        try:
            d = await r.json()
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        updates = {}
        if "comfyui_url" in d:
            self.comfyui_url = d["comfyui_url"]
            self.config["comfyui_url"] = d["comfyui_url"]
            updates["comfyui_url"] = d["comfyui_url"]
        if "workflow_dir" in d:
            self._save_workflow_dir(d["workflow_dir"])
        if "output_dir" in d:
            self.output_dir = Path(d["output_dir"])
            self.upload_dir = self.output_dir / "upload"
            try:
                self.output_dir.mkdir(parents=True, exist_ok=True)
                self.upload_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"[ComfyUI] 创建输出目录失败: {e}")
            updates["output_dir"] = d["output_dir"]
        if "upload_mode" in d:
            self.upload_mode = d["upload_mode"]
            updates["upload_mode"] = d["upload_mode"]
            logger.info(f"[ComfyUI] 上传模式切换为: {d['upload_mode']}")
        if "webui_port" in d:
            try:
                port = int(d["webui_port"])
                if not (1 <= port <= 65535):
                    return web.json_response({"ok": False, "error": "端口范围 1-65535"})
                self.webui_port = port
                updates["webui_port"] = port
            except ValueError:
                return web.json_response({"ok": False, "error": "端口必须是数字"})
        if "webui_lan" in d:
            self.webui_lan = bool(d["webui_lan"])
            updates["webui_lan"] = self.webui_lan
        if "webui_ipv6" in d:
            self.webui_ipv6 = bool(d["webui_ipv6"])
            updates["webui_ipv6"] = self.webui_ipv6
        if updates:
            self._save_local_config(updates)
        return web.json_response({"ok": True})

    async def _webui_open_dir(self, r):
        data = await r.json()
        path = data.get('path', str(self._get_workflow_dir()))
        # 路径安全校验：只允许白名单目录
        allowed_dirs = [
            str(Path(self._get_workflow_dir()).resolve()),
            str(Path(self.output_dir).resolve()),
        ]
        resolved = str(Path(path).resolve())
        if not any(resolved.startswith(d) for d in allowed_dirs):
            logger.warning(f"[ComfyUI] 拒绝访问非白名单目录: {resolved}")
            return web.json_response({"ok": False, "error": "拒绝访问"})
        try:
            import platform, subprocess
            Path(path).mkdir(parents=True, exist_ok=True)
            if platform.system() == 'Windows':
                subprocess.Popen(['explorer', path], shell=True)
            elif platform.system() == 'Darwin':
                subprocess.Popen(['open', path])
            else:
                subprocess.Popen(['xdg-open', path])
            return web.json_response({"ok": True})
        except Exception as e:
            logger.warning(f"[ComfyUI] 打开目录失败 {path}: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_set_workflow_dir(self, r):
        new_dir = (await r.json()).get('path', '')
        if not new_dir: return web.json_response({"ok": False, "error": "路径为空"})
        try:
            old_dir = self._get_workflow_dir(); new_path = Path(new_dir)
            new_path.mkdir(parents=True, exist_ok=True)
            moved = 0
            if old_dir.exists() and str(old_dir) != str(new_path):
                for f in old_dir.glob("*.json"):
                    t = new_path / f.name
                    if not t.exists():
                        try:
                            f.rename(t); moved += 1
                        except Exception as e:
                            logger.warning(f"[ComfyUI] 移动文件失败 {f.name}: {e}")
                remaining = list(old_dir.glob("*.json"))
                if not remaining:
                    try:
                        shutil.rmtree(old_dir)
                    except Exception as e:
                        logger.warning(f"[ComfyUI] 删除旧工作流目录失败: {e}")
                else:
                    logger.info(f"[ComfyUI] 旧目录还有 {len(remaining)} 个文件未移动，保留旧目录")
            self._save_workflow_dir(new_dir)
            wf_list = self._refresh_workflow_list() or []
            return web.json_response({"ok": True, "moved": moved, "workflows": wf_list})
        except Exception as e:
            logger.error(f"[ComfyUI] 设置工作流目录失败: {e}")
            # 文件操作失败时不保存路径，防止写入乱码/无效路径
            return web.json_response({"ok": False, "error": f"目录设置失败，请检查路径是否有效或使用英文路径: {e}"})

    async def _webui_toggle_hidden(self, r):
        try:
            data = await r.json()
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        name = data.get('name', '')
        async with self._config_lock:
            hidden = self.workflow_config.get('__hidden_workflows__', [])
            if name in hidden: hidden.remove(name)
            else: hidden.append(name)
            self.workflow_config['__hidden_workflows__'] = hidden
        await self._save_workflow_config()
        return web.json_response({"ok": True, "hidden": hidden})

    async def _webui_set_wf_category(self, r):
        """设置工作流分类"""
        try:
            data = await r.json()
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        name = data.get('name', '')
        category = data.get('category', '')
        async with self._config_lock:
            cats = self.workflow_config.get('__wf_categories__', {}) or {}
            if category:
                cats[name] = category
            else:
                cats.pop(name, None)
            self.workflow_config['__wf_categories__'] = cats
        await self._save_workflow_config()
        return web.json_response({"ok": True, "categories": cats})

    async def _webui_save_category_order(self, r):
        """保存分类排序"""
        try:
            data = await r.json()
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        order = data.get('order', [])
        if not isinstance(order, list):
            return web.json_response({"ok": False, "error": "order 必须为数组"})
        async with self._config_lock:
            self.workflow_config['__category_order__'] = order
        await self._save_workflow_config()
        return web.json_response({"ok": True, "order": order})

    async def _webui_delete_workflow(self, r):
        """删除工作流文件"""
        data = await r.json()
        name = data.get('name', '').strip()
        if not name or '..' in name or '/' in name or '\\' in name:
            return web.json_response({"ok": False, "error": "非法文件名"})
        wdir = self._get_workflow_dir()
        target = wdir / name
        if not target.exists():
            return web.json_response({"ok": False, "error": "文件不存在"})
        try:
            # 安全检查：确保在 workflow 目录内
            target.resolve().relative_to(wdir.resolve())
            target.unlink()
            # 同时删除预览图
            preview_file = self.preview_dir / f"{name}.jpg"
            if preview_file.exists():
                try:
                    preview_file.unlink()
                    logger.info(f"[ComfyUI] 已删除预览图: {preview_file}")
                except Exception as e:
                    logger.warning(f"[ComfyUI] 删除预览图失败: {e}")
            logger.info(f"[ComfyUI] 已删除工作流: {name}")
            async with self._config_lock:
                # 删除相关配置（分类、隐藏、别名）
                for key in ('__wf_categories__', '__hidden_workflows__', '__workflow_aliases__'):
                    d = self.workflow_config.get(key, {})
                    if isinstance(d, dict) and name in d: del d[name]
                    elif isinstance(d, list) and name in d: d.remove(name)
                # 清理组相关配置
                gs = self.workflow_config.get('__groups_source__', '')
                bt = self.workflow_config.get('__bind_target__', '')
                if gs == name:
                    self.workflow_config['__groups_source__'] = ''
                    self.workflow_config['__disabled_groups__'] = {}
                    self.workflow_config['__groups_data__'] = []
                    self.workflow_config['__bind_target__'] = ''
                elif bt == name:
                    self.workflow_config['__disabled_groups__'] = {}
                    self.workflow_config['__groups_data__'] = []
                    self.workflow_config['__bind_target__'] = ''
                # 清理绑定中引用该工作流的条目
                for bind_key in ('__group_bindings__', '__user_bindings__'):
                    bd = self.workflow_config.get(bind_key, {}) or {}
                    for bid in list(bd.keys()):
                        wfs = bd[bid]
                        if isinstance(wfs, list) and name in wfs:
                            wfs = [w for w in wfs if w != name]
                            if wfs:
                                bd[bid] = wfs
                            else:
                                del bd[bid]
                        elif isinstance(wfs, str) and wfs == name:
                            del bd[bid]
                await self._save_workflow_config()
            return web.json_response({"ok": True})
        except ValueError:
            return web.json_response({"ok": False, "error": "安全限制：不能删除目录外的文件"})
        except Exception as e:
            logger.error(f"[ComfyUI] 删除工作流失败 {name}: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_add_workflows(self, r):
        """上传添加新工作流文件"""
        try:
            reader = await r.multipart()
            wdir = self._get_workflow_dir()
            added = 0
            errors = []
            async for part in reader:
                if part.name != 'files':
                    continue
                fname = part.filename or ''
                if not fname.lower().endswith('.json'):
                    errors.append(f"{fname}: 非 JSON 文件")
                    continue
                # 安全检查文件名
                safe_name = Path(fname).name  # 去掉路径部分，只保留文件名
                if not safe_name or safe_name.startswith('.'):
                    errors.append(f"{fname}: 非法文件名")
                    continue
                dest = wdir / safe_name
                if dest.exists():
                    errors.append(f"{safe_name}: 文件已存在（跳过）")
                    continue
                data = await part.read()
                # 验证是否为合法 JSON
                try:
                    json.loads(data)
                except (ValueError, TypeError):
                    errors.append(f"{safe_name}: 非 ComfyUI 工作流 JSON")
                    continue
                dest.write_bytes(data)
                added += 1
                logger.info(f"[ComfyUI] 添加工作流: {safe_name}")
            msg = f"成功添加 {added} 个"
            if errors:
                msg += f"，{len(errors)} 个失败: {'; '.join(errors[:3])}"
            return web.json_response({"ok": True, "added": added, "errors": errors})
        except Exception as e:
            logger.error(f"[ComfyUI] 添加工作流异常: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_upload_image(self, r):
        """上传图片到临时目录，后续由 HTTP upload 发送到 ComfyUI"""
        try:
            reader = await r.multipart()
            target_dir = self.upload_dir
            if not target_dir.parts:
                return web.json_response({"ok": False, "error": "未设置输出目录"})
            if not target_dir.exists():
                target_dir.mkdir(parents=True, exist_ok=True)
            async for part in reader:
                if part.name != 'image':
                    continue
                fname = part.filename or 'upload.png'
                # 安全文件名
                safe_name = Path(fname).name
                if not safe_name:
                    return web.json_response({"ok": False, "error": "非法文件名"})
                # 生成唯一文件名避免冲突
                stem = Path(safe_name).stem
                suffix = Path(safe_name).suffix or '.png'
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                unique_name = f"{stem}_{timestamp}{suffix}"
                dest = target_dir / unique_name
                data = await part.read()
                dest.write_bytes(data)
                logger.info(f"[ComfyUI] 上传图片: {unique_name} ({len(data)//1024}KB)")
                return web.json_response({"ok": True, "filename": unique_name})
            return web.json_response({"ok": False, "error": "未找到图片数据"})
        except Exception as e:
            logger.error(f"[ComfyUI] 上传图片异常: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_get_groups(self, request):
        if not self.workflow_path: return web.json_response({"groups": []})
        src_path = Path(self.workflow_path)
        if 'API' in src_path.name or 'api' in src_path.name:
            orig_name = src_path.name.replace('API', '原', 1)
            orig_dir = src_path.parent / '原json'
            if orig_dir.exists():
                orig_path = orig_dir / orig_name
                if orig_path.exists(): src_path = orig_path
        try:
            with open(str(src_path), 'r', encoding='utf-8') as f: wf = json.load(f)
            if 'nodes' in wf and isinstance(wf.get('nodes'), list):
                groups = wf.get('groups', [])
                nodes = wf.get('nodes', [])
                result = []
                for g in groups:
                    bx, by, bw, bh = g.get('bounding', [0, 0, 0, 0])
                    g_nodes = []
                    for n in nodes:
                        nx, ny = n.get('pos', [0, 0])
                        if bx <= nx <= bx + bw and by <= ny <= by + bh:
                            g_nodes.append(str(n['id']))
                    result.append({"id": str(g.get("id", "")), "title": g.get("title", ""), "color": g.get("color", ""), "nodes": g_nodes})
                groups_path = src_path.with_suffix('.groups.json')
                with open(str(groups_path), 'w', encoding='utf-8') as gf: json.dump({"groups": result}, gf, ensure_ascii=False)
                return web.json_response({"groups": result})
            groups_path = src_path.with_suffix('.groups.json')
            if groups_path.exists():
                with open(str(groups_path), 'r', encoding='utf-8') as gf: return web.json_response(json.load(gf))
            return web.json_response({"groups": []})
        except Exception as e:
            logger.error(f"[ComfyUI] groups error: {e}")
            return web.json_response({"groups": []})

    async def _webui_proxy(self, r):
        target = r.query.get('url', '')
        if not target: return web.Response(text='{"error":"missing url"}', content_type='application/json')
        allowed_prefix = f"http://{self.comfyui_url}"
        if not target.startswith(allowed_prefix):
            logger.warning(f"[ComfyUI] 代理请求被拒绝（非白名单地址）: {target}")
            return web.Response(text=json.dumps({"error": "proxy denied"}), content_type='application/json', status=403)
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(target, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    return web.Response(text=await resp.text(), content_type='application/json')
        except Exception as e:
            logger.warning(f"[ComfyUI] 代理请求失败 {target}: {e}")
            return web.Response(text=json.dumps({"error": "proxy error"}), content_type='application/json')

    async def _webui_switch_workflow(self, r):
        try:
            name = (await r.json()).get('name', '')
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        for wf in self.workflow_list_cache:
            if wf['name'] == name: self._switch_to_workflow(wf); return web.json_response({"ok": True})
        return web.json_response({"ok": False, "error": f"未找到工作流: {name}"})

    async def _clean_stale_bindings(self):
        """清理绑定中已不存在的工作流（文件被删后产生孤立绑定），返回是否做了清理"""
        changed = False
        existing = {w['name'] for w in (self.workflow_list_cache or [])}
        for key in ('__group_bindings__', '__user_bindings__'):
            bd = self.workflow_config.get(key, {}) or {}
            for bid in list(bd.keys()):
                wfs = bd[bid]
                if isinstance(wfs, str):
                    wfs = [wfs]
                clean = [w for w in wfs if w in existing]
                if len(clean) != len(wfs):
                    changed = True
                    if clean:
                        bd[bid] = clean
                    else:
                        del bd[bid]
        if changed:
            await self._save_workflow_config()
        return changed

    async def _webui_get_bindings(self, request):
        try:
            gb = self.workflow_config.get('__group_bindings__', {}) or {}
            ub = self.workflow_config.get('__user_bindings__', {}) or {}
            await self._clean_stale_bindings()
            for k in gb:
                if isinstance(gb[k], str): gb[k] = [gb[k]]
            for k in ub:
                if isinstance(ub[k], str): ub[k] = [ub[k]]
            return web.json_response({"group_bindings": gb, "user_bindings": ub, "context_workflows": self._context_workflows})
        except Exception as e:
            logger.warning(f"[ComfyUI] 获取绑定失败: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_save_binding(self, request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        bind_type = data.get('type', '')
        bind_id = data.get('id', '').strip()  # ← trim
        wf_name = data.get('workflow', '')
        if not bind_id or not wf_name:
            return web.json_response({"ok": False, "error": "参数不完整"})
        wfs = self._refresh_workflow_list()
        if not any(w['name'] == wf_name for w in wfs):
            return web.json_response({"ok": False, "error": "工作流不存在"})
        key = '__group_bindings__' if bind_type == 'group' else '__user_bindings__'
        async with self._config_lock:
            bindings = self.workflow_config.get(key, {}) or {}
            existing = bindings.get(bind_id, [])
            if isinstance(existing, str): existing = [existing]
            if wf_name not in existing:
                existing.append(wf_name)
            bindings[bind_id] = existing
            self.workflow_config[key] = bindings
        await self._save_workflow_config()
        return web.json_response({"ok": True})

    async def _webui_delete_binding(self, request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        bind_type = data.get('type', '')
        bind_id = data.get('id', '').strip()
        wf_name = data.get('workflow', '')
        if not bind_id:
            return web.json_response({"ok": False, "error": "参数不完整"})
        key = '__group_bindings__' if bind_type == 'group' else '__user_bindings__'
        async with self._config_lock:
            bindings = self.workflow_config.get(key, {}) or {}
            if wf_name:
                existing = bindings.get(bind_id, [])
                if isinstance(existing, str): existing = [existing]
                if wf_name in existing:
                    existing.remove(wf_name)
                if existing:
                    bindings[bind_id] = existing
                else:
                    bindings.pop(bind_id, None)
            else:
                bindings.pop(bind_id, None)
            self.workflow_config[key] = bindings
        await self._save_workflow_config()
        return web.json_response({"ok": True})

    async def _webui_get_context_workflows(self, request):
        if 'reset' in request.query:
            wk = request.query.get('context_key', '')
            if wk and wk in self._context_workflows:
                del self._context_workflows[wk]
                return web.json_response({"ok": True})
        return web.json_response(self._context_workflows)

    async def _webui_set_context_workflow(self, request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        context_key = data.get('context_key', '')
        wf_name = data.get('workflow', '')

        if not context_key:
            return web.json_response({"ok": False, "error": "缺少context_key"})
        if wf_name:
            wfs = self._refresh_workflow_list()
            for wf in wfs:
                if wf['name'] == wf_name:
                    self._switch_to_workflow(wf, context_key=context_key)
                    break
            else:
                return web.json_response({"ok": False, "error": "工作流不存在"})
        else:
            self._context_workflows.pop(context_key, None)
        return web.json_response({"ok": True, "context_key": context_key, "workflow": self._context_workflows.get(context_key, "")})

    def _parse_workflow_params(self, workflow):
        nodes = []
        for nid, node in workflow.items():
            ct = node.get('class_type', ''); title = node.get('_meta', {}).get('title', ct)
            params = []
            for key, val in node.get('inputs', {}).items():
                if key.startswith('_'): continue
                if isinstance(val, (str, int, float, bool)):
                    ptype = 'text' if isinstance(val, str) and len(val) > 50 else ('bool' if isinstance(val, bool) else ('string' if isinstance(val, str) else 'number'))
                elif isinstance(val, list): ptype = 'select'
                else: continue
                params.append({"key": key, "value": val, "type": ptype})
            nodes.append({"id": nid, "title": title, "class_type": ct, "params": params})
        return {"nodes": nodes, "workflow_name": self.current_workflow_name}

    async def _webui_get_models(self, request):
        """从ComfyUI获取所有可用模型列表（不限特定节点类型）。"""
        try:
            async with aiohttp.ClientSession() as s:
                # 先从当前工作流找出所有有模型选择器的节点
                workflow_node_types = set()
                if self.workflow_path:
                    try:
                        with open(self.workflow_path, 'r', encoding='utf-8') as f:
                            wf = json.load(f)
                        for node in wf.values():
                            ct = node.get('class_type', '')
                            if ct:
                                workflow_node_types.add(ct)
                    except Exception:
                        pass

                # 全面扫描：常见模型加载器 + 当前工作流中用到的所有节点类型
                scan_types = set(workflow_node_types)
                scan_types.update([
                    'DiffusionModelLoaderKJ', 'CheckpointLoaderKJ', 'CheckpointLoaderSimple',
                    'UNETLoader', 'CLIPLoader', 'DualCLIPLoader', 'TripleCLIPLoader',
                    'VAELoader', 'LoraLoader', 'ControlNetLoader', 'DiffControlNetLoader',
                    'StyleModelLoader', 'GLIGENLoader', 'CLIPVisionLoader',
                    'IPAdapterModelLoader', 'PhotoMakerLoader', 'InstructIRLoader',
                ])
                model_keywords = ['model_name', 'ckpt_name', 'unet_name', 'clip_name', 'vae_name',
                                  'lora_name', 'control_net_name', 'style_model_name']

                models = {}
                for node_name in scan_types:
                    try:
                        async with s.get(f"http://{self.comfyui_url}/object_info/{node_name}",
                                         timeout=aiohttp.ClientTimeout(total=5)) as r:
                            if r.status != 200:
                                continue
                            data = await r.json()
                            info = data.get(node_name, {})
                            required = info.get('input', {}).get('required', {})
                            for key, val in required.items():
                                if isinstance(val, list) and len(val) > 0:
                                    model_list = val[0]
                                    if isinstance(model_list, list) and len(model_list) > 0:
                                        # 只存看起来是模型选择器的参数
                                        if any(kw in key.lower() for kw in model_keywords):
                                            models[node_name + '_' + key] = model_list
                    except Exception as e:
                        logger.debug(f"[ComfyUI] 获取 {node_name} 模型列表失败: {e}")
                return web.json_response(models)
        except Exception as e:
            logger.warning(f"[ComfyUI] 获取模型失败: {e}")
            return web.json_response({})

    async def _webui_get_workflow_params(self, request):
        if not self.workflow_path: self._refresh_workflow_list()
        if not self.workflow_path: return web.json_response({"error": "没有工作流"})
        try:
            with open(self.workflow_path, 'r', encoding='utf-8') as f: wf = json.load(f)
            result = self._parse_workflow_params(wf)
            for node in result["nodes"]:
                for p in node["params"]:
                    ck = f"{node['id']}_{p['key']}"
                    if ck in self.workflow_config: p["value"] = self.workflow_config[ck]
            return web.json_response(result)
        except Exception as e:
            logger.warning(f"[ComfyUI] 获取工作流参数失败: {e}")
            return web.json_response({"error": str(e)})

    async def _webui_save_workflow_params(self, request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        try:
            wf_name = self.current_workflow_name
            # 诊断日志：记录传入数据的关键字段
            logger.info(f"[ComfyUI] _webui_save_workflow_params: wf_name={wf_name!r}, data_keys={list(data.keys())}")
            for rk in ['__prompt_node__', '__resolution_node__', '__load_image_nodes__', '__negative_node__']:
                if rk in data:
                    logger.info(f"[ComfyUI]   save role {rk}={data[rk]!r}")
            async with self._config_lock:
                for key_name in ['__prompt_node__', '__resolution_node__', '__load_image_node__', '__load_image_nodes__', '__negative_node__']:
                    if key_name in data:
                        wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
                        wf_configs[wf_name] = wf_configs.get(wf_name, {})
                        wf_configs[wf_name][key_name] = data.pop(key_name)
                        data['__workflow_node_configs__'] = wf_configs
                preserved_keys = ['__local_config__', '__hidden_workflows__', '__groups_data__', '__groups_source__', '__disabled_groups__', '__bind_target__', '__workflow_node_configs__', '__group_bindings__', '__user_bindings__', '__workflow_aliases__', '__wf_categories__']
                for key in preserved_keys:
                    if key not in data and key in self.workflow_config: data[key] = self.workflow_config[key]
                # 更新内存中的 workflow_config，再通过统一函数写文件（含合并逻辑）
                self.workflow_config.update(data)
            # 锁已释放，再调用 _save_workflow_config（它不自带锁，与 asyncio.Lock 的重入危险已解除）
            await self._save_workflow_config()
            logger.info(f"[ComfyUI]   save OK, wf_name={wf_name!r}, node_configs keys: {list(self.workflow_config.get('__workflow_node_configs__', {}).get(wf_name, {}).keys())}")

            # 将 node_id_key 类的参数同步写入 API 工作流 .json 文件
            if self.workflow_path:
                try:
                    with open(self.workflow_path, 'r', encoding='utf-8') as f:
                        wf_data = json.load(f)
                    changed = False
                    for ck, val in data.items():
                        if ck.startswith('__') or ck in preserved_keys:
                            continue
                        parts = ck.split('_', 1)
                        if len(parts) == 2:
                            nid, key = parts
                            if nid in wf_data and key in wf_data[nid].get('inputs', {}):
                                orig = wf_data[nid]['inputs'][key]
                                if isinstance(orig, bool):
                                    wf_data[nid]['inputs'][key] = str(val).lower() in ('true', '1', 'yes')
                                elif isinstance(orig, int):
                                    try: wf_data[nid]['inputs'][key] = int(val)
                                    except ValueError: pass
                                elif isinstance(orig, float):
                                    try: wf_data[nid]['inputs'][key] = float(val)
                                    except ValueError: pass
                                elif isinstance(orig, str):
                                    wf_data[nid]['inputs'][key] = str(val) if val is not None else orig
                                changed = True
                    if changed:
                        with open(self.workflow_path, 'w', encoding='utf-8') as f:
                            json.dump(wf_data, f, ensure_ascii=False, indent=2)
                        logger.info(f"[ComfyUI] 已同步参数到 API 工作流文件: {self.workflow_path}")
                except Exception as e:
                    logger.warning(f"[ComfyUI] 写入 API 工作流文件失败: {e}")

            return web.json_response({"ok": True, "message": "已保存"})
        except Exception as e:
            logger.warning(f"[ComfyUI] 保存工作流参数失败: {e}\n{traceback.format_exc()}")
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_get_progress(self, request):
        """返回当前正在执行的任务进度（含阶段性进度）。"""
        pid = self.current_prompt_id
        prog = self._progress.get(pid, {})
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://{self.comfyui_url}/queue", timeout=aiohttp.ClientTimeout(total=3)) as r:
                    qd = await r.json()
                    running = len(qd.get('queue_running', []))
                    pending = len(qd.get('queue_pending', []))
        except Exception:
            running = pending = 0
        # 计算已运行时间
        start_ts = self._prompt_start_time.get(pid, 0)
        elapsed = int(time.time() - start_ts) if start_ts else 0
        is_running = running > 0 or self._generating_lock
        return web.json_response({
            "prompt_id": pid or "",
            "queue_running": running,
            "queue_pending": pending,
            "running": is_running,
            "elapsed": elapsed,
            "state": "generating" if self._generating_lock else ("queued" if running > 0 else "idle"),
        })

    async def _webui_get_workflow_params_config(self, request):
        config_path = Path(__file__).resolve().parent / "plugin_config.json"
        if config_path.exists():
            try:
                with open(str(config_path), 'r', encoding='utf-8') as f:
                    self.workflow_config = json.load(f)
            except Exception as e:
                logger.warning(f"[ComfyUI] 读取工作流参数配置失败: {e}")
        wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
        wf_config_name = self.current_workflow_name
        wf_config = wf_configs.get(wf_config_name, {})
        # 诊断日志
        logger.info(f"[ComfyUI] _webui_get_workflow_params_config: wf={wf_config_name!r}, "
                    f"node_configs_keys={list(wf_configs.keys())}, "
                    f"current_wf_node_roles={ {k: wf_config.get(k) for k in ['__prompt_node__','__resolution_node__','__load_image_nodes__','__negative_node__']} }")
        return web.json_response({
            "__prompt_node__": wf_config.get("__prompt_node__", "") or self.workflow_config.get("__prompt_node__", ""),
            "__resolution_node__": wf_config.get("__resolution_node__", "") or self.workflow_config.get("__resolution_node__", ""),
            "__load_image_node__": wf_config.get("__load_image_node__", "") or self.workflow_config.get("__load_image_node__", ""),
            "__load_image_nodes__": wf_config.get("__load_image_nodes__", "") or self.workflow_config.get("__load_image_nodes__", ""),
            "__negative_node__": wf_config.get("__negative_node__", "") or self.workflow_config.get("__negative_node__", ""),
            "__commands__": self.workflow_config.get("__commands__", {}),
            "__groups_source__": self.workflow_config.get("__groups_source__", ""),
            "__disabled_groups__": self.workflow_config.get("__disabled_groups__", {}),
            "__bind_target__": self.workflow_config.get("__bind_target__", ""),
            "__groups_data__": self.workflow_config.get("__groups_data__", []),
            "__hidden_workflows__": self.workflow_config.get("__hidden_workflows__", []),
            "__workflow_aliases__": self.workflow_config.get("__workflow_aliases__", {}),
            "__category_order__": self.workflow_config.get("__category_order__", []),
            "__workflow_node_configs__": self.workflow_config.get("__workflow_node_configs__", {}),
            "__wf_categories__": self.workflow_config.get("__wf_categories__", {}),
            # 质量与比例信息
            "current_quality": self.default_quality,
            "current_ratio": self.default_ratio,
            "current_width": self.default_width,
            "current_height": self.default_height,
            "quality_presets": {k: v for k, v in self.quality_presets.items()},
            "aspect_ratios": self.aspect_ratios,
        })

    async def _webui_set_quality(self, request):
        """WebUI 设置质量等级"""
        try:
            data = await request.json()
            quality = data.get("quality", "")
            if quality not in self.quality_presets:
                return web.json_response({"ok": False, "error": f"未知质量: {quality}"})
            w, h = self._calc_resolution(quality, self.default_ratio)
            self.default_quality = quality
            self.default_width, self.default_height = w, h
            self._save_local_config({"default_quality": quality})
            self._sync_resolution_to_workflow(self.default_ratio, w, h)
            return web.json_response({"ok": True, "quality": quality, "width": w, "height": h, "ratio": self.default_ratio})
        except Exception as e:
            logger.warning(f"[ComfyUI] WebUI 设置质量失败: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_set_ratio(self, request):
        """WebUI 设置比例"""
        try:
            data = await request.json()
            ratio = data.get("ratio", "")
            if ratio not in self.aspect_ratios:
                return web.json_response({"ok": False, "error": f"未知比例: {ratio}"})
            w, h = self._calc_resolution(self.default_quality, ratio)
            self.default_ratio = ratio
            self.default_width, self.default_height = w, h
            self._save_local_config({"default_ratio": ratio})
            self._sync_resolution_to_workflow(ratio, w, h)
            return web.json_response({"ok": True, "ratio": ratio, "width": w, "height": h, "quality": self.default_quality})
        except Exception as e:
            logger.warning(f"[ComfyUI] WebUI 设置比例失败: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_reset_all(self, request):
        """重置所有用户设定：清空 plugin_config.json 中的所有配置，回到全新状态"""
        try:
            # 清空内存中所有配置
            self.workflow_config = {}
            self._context_workflows = {}
            # 清理 self.config 中残留的旧路径，防止 _get_workflow_dir() 回退到旧值
            for key in ["workflow_dir"]:
                self.config.pop(key, None)
            # 重置运行时变量到初始默认值，确保页面重载后输入框为空
            self.workflow_dir = Path()
            self.comfyui_url = "127.0.0.1:8188"
            self.output_dir = Path()
            self.upload_dir = Path()
            self.upload_mode = "local"
            self.webui_port = 8898
            self.webui_lan = False
            self.webui_ipv6 = False
            self.default_quality = "720p"
            self.default_ratio = "9:16"
            self.default_width, self.default_height = self._calc_resolution(self.default_quality, self.default_ratio)
            # 写回一个干净的配置文件（保留 __local_config__ 和 __groups_binding__ 空结构）
            config_path = Path(__file__).resolve().parent / "plugin_config.json"
            clean = {
                "__local_config__": {},
                "__groups_binding__": {}
            }
            async with self._config_lock:
                with open(str(config_path), 'w', encoding='utf-8') as f:
                    json.dump(clean, f, ensure_ascii=False, indent=2)
            return web.json_response({"ok": True, "message": "✅ 已重置所有用户设定，请刷新页面"})
        except Exception as e:
            logger.warning(f"[ComfyUI] WebUI 重置失败: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    def _refresh_workflow_list(self):
        wdir = self._get_workflow_dir(); wdir.mkdir(parents=True, exist_ok=True)
        files = list(wdir.glob("*.json"))
        files = [f for f in files if not f.name.endswith('.groups.json') and not f.name.startswith('.')]
        hidden = self.workflow_config.get('__hidden_workflows__', [])
        aliases = self.workflow_config.get('__workflow_aliases__', {}) or {}
        all_files = []
        for f in files:
            alias = aliases.get(f.name, '')
            display_name = alias if alias else f.name
            all_files.append({
                "name": f.name,
                "path": str(f),
                "is_current": str(f) == self.workflow_path,
                "hidden": f.name in hidden,
                "alias": alias,
                "display_name": display_name
            })
        self.workflow_list_cache = all_files
        visible = [w for w in all_files if not w["hidden"]]
        if not self.workflow_path and visible:
            self.workflow_path = visible[0]["path"]
            self.current_workflow_name = visible[0]["name"]
            visible[0]["is_current"] = True
        return visible

    def _get_display_name(self, fname):
        """获取工作流的显示名（别名优先，无别名返回原名）"""
        if not fname:
            return ''
        for w in self.workflow_list_cache:
            if w['name'] == fname:
                return w.get('display_name', fname)
        return fname

    def _find_positive_prompt_node(self, workflow):
        wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
        wf_config = wf_configs.get(self.current_workflow_name, {})
        manual = wf_config.get('__prompt_node__', '') or self.workflow_config.get('__prompt_node__', '')
        if manual and manual in workflow: return manual
        for nid, node in workflow.items():
            if node.get('class_type') == 'CLIPTextEncode':
                if '正面' in node.get('_meta', {}).get('title', ''): return nid
        for nid, node in workflow.items():
            if node.get('class_type') in ['CR Text', 'easy positive', 'PromptExpand']:
                if '正面' in node.get('_meta', {}).get('title', ''): return nid
        for nid, node in workflow.items():
            if node.get('class_type') == 'CLIPTextEncode':
                t = node.get('_meta', {}).get('title', '')
                if '负面' not in t: return nid
        return None

    def _find_negative_prompt_node(self, workflow):
        wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
        wf_config = wf_configs.get(self.current_workflow_name, {})
        manual = wf_config.get('__negative_node__', '') or self.workflow_config.get('__negative_node__', '')
        if manual and manual in workflow: return manual
        for nid, node in workflow.items():
            if node.get('class_type') == 'CLIPTextEncode':
                if '负面' in node.get('_meta', {}).get('title', ''): return nid
        return None

    def _calc_resolution(self, quality: str, ratio: str) -> tuple:
        """根据质量等级和比例动态计算分辨率（宽 × 高）。"""
        preset = self.quality_presets.get(quality, self.quality_presets["720p"])
        target = preset["pixels"]
        try:
            a_str, b_str = ratio.split(":")
            a, b = int(a_str), int(b_str)
        except (ValueError, AttributeError):
            a, b = 9, 16  # 兜底
        # area = a*x * b*x = a*b*x², solve for x
        x = (target / (a * b)) ** 0.5
        w = round(a * x / 8) * 8
        h = round(b * x / 8) * 8
        # 确保最短边 >= 256
        min_side = min(w, h)
        if min_side < 256:
            scale = 256 / min_side
            w = round(w * scale / 8) * 8
            h = round(h * scale / 8) * 8
        return (w, h)

    def _find_resolution_node(self, workflow):
        wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
        wf_config = wf_configs.get(self.current_workflow_name, {})
        manual = wf_config.get('__resolution_node__', '') or self.workflow_config.get('__resolution_node__', '')
        if manual and manual in workflow: return manual
        for nid, node in workflow.items():
            if node.get('class_type') == 'AspectRatioNode': return nid
        for nid, node in workflow.items():
            if node.get('class_type') == 'EmptyLatentImage': return nid
        return None

    def _find_load_image_node(self, workflow):
        wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
        wf_config = wf_configs.get(self.current_workflow_name, {})
        manual = wf_config.get('__load_image_node__', '') or self.workflow_config.get('__load_image_node__', '')
        if manual and manual in workflow: return manual
        for nid, node in workflow.items():
            if node.get('class_type') == 'LoadImage': return nid
        return None

    def _find_all_load_image_nodes(self, workflow):
        """按节点ID排序，找出工作流中所有 LoadImage 节点"""
        nodes = []
        for nid, node in workflow.items():
            if node.get('class_type') == 'LoadImage':
                nodes.append(nid)
        nodes.sort()
        return nodes

    def _find_sampler_node(self, workflow):
        for nid, node in workflow.items():
            if node.get('class_type') in ['KSampler', 'ROCMOptimizedKSampler', 'KSamplerAdvanced']:
                if 'denoise' in node.get('inputs', {}): return nid
        return None

    def _apply_workflow_config(self, workflow):
        bind_target = self.workflow_config.get('__bind_target__', '')
        if bind_target and bind_target != self.current_workflow_name:
            return
        skip_keys = {'rgthree_comparer', 'loras', '__lm_autocomplete_meta_text', 'any', 'any_input'}
        for ck, val in self.workflow_config.items():
            if ck.startswith('__') or val is None or val == '': continue
            parts = ck.split('_', 1)
            if len(parts) == 2:
                nid, key = parts
                if key in skip_keys: continue
                if nid in workflow and key in workflow[nid].get('inputs', {}):
                    orig = workflow[nid]['inputs'][key]
                    if isinstance(orig, (int, float)):
                        try:
                            val = float(val) if '.' in str(val) else int(val)
                        except ValueError:
                            continue
                    elif isinstance(orig, bool): val = str(val).lower() in ('true', '1', 'yes')
                    elif not isinstance(orig, str): continue
                    workflow[nid]['inputs'][key] = val

    def _inject_prompt(self, workflow, prompt, target_node=None):
        nid = target_node or self._find_positive_prompt_node(workflow)
        if nid and nid in workflow: workflow[nid]['inputs']['text'] = prompt; return True
        return False

    def _inject_negative_prompt(self, workflow, negative_prompt):
        nid = self._find_negative_prompt_node(workflow)
        if nid and nid in workflow:
            workflow[nid]['inputs']['text'] = negative_prompt
            return True
        return False

    def _set_resolution(self, workflow, width, height):
        nid = self._find_resolution_node(workflow)
        if nid: workflow[nid]['inputs']['width'] = width; workflow[nid]['inputs']['height'] = height; return True
        return False

    async def _set_load_image(self, workflow, image_path):
        """设置工作流中的 LoadImage 节点。
        image_path 可以是单个路径字符串，也可以是路径列表（设置多个 LoadImage 节点）。"""
        if isinstance(image_path, (list, tuple)):
            return await self._set_all_load_images(workflow, list(image_path))
        return await self._set_single_load_image(workflow, image_path)

    async def _set_single_load_image(self, workflow, image_path):
        """设置单个 LoadImage 节点，并删除工作流中其他多余的 LoadImage 节点"""
        nid = self._find_load_image_node(workflow)
        if not nid: return False
        # 统一通过 HTTP 上传到 ComfyUI input 目录（无论 local/remote 模式）
        name = await self._upload_image_remote(Path(image_path))
        if not name:
            logger.error(f"[ComfyUI] 上传图片失败: {image_path}")
            return False
        workflow[nid]['inputs']['image'] = name
        logger.info(f"[ComfyUI] LoadImage {nid} <- {name}")
        try:
            Path(image_path).unlink(missing_ok=True)
        except Exception as e:
            logger.debug(f"[ComfyUI] 删除临时文件失败: {e}")
        # 删除其他多余的 LoadImage 节点
        all_nodes = self._find_all_load_image_nodes(workflow)
        extra = [x for x in all_nodes if x != nid]
        if extra:
            self._remove_workflow_nodes(workflow, extra)
            logger.info(f"[ComfyUI] 单图模式，删除多余 LoadImage 节点: {extra}")
        return True

    def _remove_workflow_nodes(self, workflow, remove_ids):
        """智能级联删除：删除指定节点。若下游节点的所有输入都来自已删节点则也删除，否则仅清理引用。"""
        to_remove = set(str(x) for x in remove_ids)
        if not to_remove:
            return
        while True:
            new_removals = set()
            for nid, node in list(workflow.items()):
                if nid in to_remove or not isinstance(node, dict):
                    continue
                inputs = node.get('inputs', {})
                refs_deleted = [k for k, v in inputs.items()
                                if isinstance(v, list) and len(v) >= 1 and str(v[0]) in to_remove]
                if not refs_deleted:
                    continue
                # 检查该节点的所有输入是否都来自已删节点
                all_inputs = [v for v in inputs.values() if isinstance(v, list) and len(v) >= 1]
                all_from_deleted = all(str(v[0]) in to_remove for v in all_inputs)
                if all_from_deleted and all_inputs:
                    new_removals.add(nid)
                else:
                    # 还有活着的输入源 → 只清理已删引用，保留节点
                    for k in refs_deleted:
                        del inputs[k]
            if not new_removals:
                break
            to_remove.update(new_removals)
        for nid in to_remove:
            workflow.pop(nid, None)
        if to_remove:
            logger.info(f"[ComfyUI] 级联删除: {sorted(to_remove)}")

    async def _set_all_load_images(self, workflow, image_paths):
        """设置工作流中 LoadImage 节点。图片少于节点时，多余节点及其引用将被删除。"""
        nodes = self._find_all_load_image_nodes(workflow)
        if not nodes:
            logger.warning(f"[ComfyUI] 工作流中没有 LoadImage 节点，跳过")
            return False
        keep_count = len(image_paths)
        if keep_count == 0:
            return True
        # 上传图片并设置前 keep_count 个节点
        for i in range(min(keep_count, len(nodes))):
            nid = nodes[i]
            path = image_paths[i] if i < len(image_paths) else ""
            if not path:
                workflow[nid]['inputs']['image'] = ""
                continue
            name = await self._upload_image_remote(Path(path))
            if name:
                workflow[nid]['inputs']['image'] = name
                logger.info(f"[ComfyUI] LoadImage {nid} <- {name}")
                continue
            # HTTP 上传失败，仅设置文件名（ComfyUI 可能本地找不到，但留最后一线希望）
            logger.warning(f"[ComfyUI] LoadImage {nid} HTTP 上传失败，尝试使用原始文件名")
            workflow[nid]['inputs']['image'] = Path(path).name
        # 删除多余的 LoadImage 节点及其下游引用
        if keep_count < len(nodes):
            remove_ids = nodes[keep_count:]
            self._remove_workflow_nodes(workflow, remove_ids)
            logger.info(f"[ComfyUI] 删除多余节点: {remove_ids}")
        return True

    def _ensure_png(self, path):
        """将非 PNG 图片转换为 PNG，确保 ComfyUI 能正常读取"""
        try:
            from PIL import Image
            with Image.open(str(path)) as img:
                img.save(str(path), 'PNG')
            return True
        except Exception as e:
            logger.warning(f"[ComfyUI] 转 PNG 失败: {path}, {e}")
            return False

    # ========================================================================
    # LLM 请求前置拦截 — 将 image_url 降级为文本路径
    # （解决 DeepSeek 等纯文本模型不支持 image_url 的问题）
    # ========================================================================
    @filter.on_llm_request()
    async def _on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """拦截 LLM 请求，将图片转存到本地后以文本路径注入 prompt。"""
        saved_images = []
        for ctx in req.contexts:
            content = ctx.get("content")
            if not isinstance(content, list):
                continue
            new_content = []
            for item in content:
                if item.get("type") == "image_url":
                    url = item.get("image_url", {}).get("url", "")
                    save_name = f"llm_input_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{len(saved_images)}.png"
                    save_path = self._get_image_save_dir() / save_name
                    ok = False
                    # base64 data URL
                    if url.startswith("data:image/"):
                        try:
                            import base64
                            b64 = url.split(",", 1)[-1] if "," in url else url
                            data = base64.b64decode(b64)
                            save_path.write_bytes(data)
                            file_size = len(data)
                            if file_size > 0 and self._ensure_png(save_path):
                                ok = True
                        except Exception as e:
                            logger.warning(f"[ComfyUI] 解码 base64 图片失败: {e}")
                    # HTTP URL
                    elif url.startswith("http"):
                        ok = await self._download_image(url, save_path)
                    if ok:
                        saved_images.append(str(save_path))
                        new_content.append({
                            "type": "text",
                            "text": f"[用户发送了图片，已保存到: {save_path}](请使用此路径调用图生图/编辑工具)"
                        })
                    else:
                        new_content.append({"type": "text", "text": "[用户发送了图片，但下载失败]"})
                else:
                    new_content.append(item)
            ctx["content"] = new_content
        # 安全兜底：处理 image_urls 中可能未合并入 contexts 的图片
        for i, url in enumerate(list(req.image_urls)):
            if url.startswith("http"):
                save_name = f"llm_input_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_url{i}.png"
                save_path = self._get_image_save_dir() / save_name
                if await self._download_image(url, save_path):
                    saved_images.append(str(save_path))
        # 清空图片 URL 列表，防止上层再次组装
        req.image_urls = []
        if saved_images:
            logger.info(f"[ComfyUI] LLM 请求拦截: 已转存 {len(saved_images)} 张图片 -> {saved_images}")

    def _get_image_save_dir(self) -> Path:
        """统一返回 upload_dir 临时保存目录，随后通过 HTTP upload 发送到 ComfyUI。"""
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        return self.upload_dir

    async def _download_image(self, url, save_path):
        # 本地文件路径：直接复制
        if url and (url.startswith(('/', 'C:', 'D:', 'E:', 'F:')) or url.startswith('\\')):
            src = Path(url)
            if src.exists():
                shutil.copy2(str(src), str(save_path))
                if self._ensure_png(save_path):
                    logger.info(f"[ComfyUI] 复制本地图片 {src} -> {save_path}")
                    return True
                logger.warning(f"[ComfyUI] 本地图片转PNG失败: {src}")
                return False
            logger.warning(f"[ComfyUI] 本地图片不存在: {src}")
            return False
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                    if r.status == 200:
                        data = await r.read()
                        save_path.write_bytes(data)
                        file_size = len(data)
                        # 统一转为 PNG（ComfyUI 兼容性最好）
                        if file_size > 0 and self._ensure_png(save_path):
                            logger.info(f"[ComfyUI] 下载图片 {file_size} 字节 -> {save_path}")
                            return True
                        logger.warning(f"[ComfyUI] 图片无效: {url}, 大小={file_size}")
        except Exception as e:
            logger.warning(f"[ComfyUI] 下载图片失败 {url}: {e}")
        return False

    def _get_bot_id(self, event):
        """获取机器人自己的 QQ 号，用于过滤自身 @"""
        try:
            if hasattr(event, 'get_self_id') and callable(event.get_self_id):
                return event.get_self_id()
            if hasattr(event, 'self_id'):
                return event.self_id
        except Exception:
            pass
        return None

    def _normalize_img_url(self, url):
        """归一化图片 URL：提取 scheme://netloc/path 作为去重键，忽略查询参数/片段。

        同一张 QQ 图可能有两种不同 URL（不同 CDN/查询参数），路径归一化后视为同一张。
        """
        if not url:
            return None
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    async def _collect_images_from_event(self, event, max_images=3):
        """从事件中收集图片 URL：直接图片/引用图片 > @头像，最多 max_images 张，跳过机器人自身 @。

        关键规则：
        - 若有直接/引用图片 → 仅用这些图片（@头像忽略），匹配「或」语义
        - 若无直接/引用图片 → 尝试 @头像
        - URL 路径归一化去重（同一张 QQ 图以不同 URL 出现时不会重复计数）
        """
        image_urls = []
        seen_normalized = set()  # 已收集的归一化路径，用于去重
        bot_id = self._get_bot_id(event)

        def _is_new(url):
            """检查 url 是否是新图（基于归一化路径去重）"""
            if not url:
                return False
            key = self._normalize_img_url(url)
            if not key or key in seen_normalized:
                return False
            seen_normalized.add(key)
            return True

        # 1. 收集直接图片组件 + 引用消息中的图片（高优先级，互斥于 @头像）
        for comp in event.get_messages():
            if isinstance(comp, AstrImage):
                url = getattr(comp, 'url', None)
                if _is_new(url):
                    image_urls.append(url)
                    if len(image_urls) >= max_images:
                        return image_urls
                continue
            d = comp.__dict__ if hasattr(comp, '__dict__') else {}
            if d.get('type') == 'Reply':
                for item in d.get('chain', []):
                    url = None
                    if hasattr(item, 'url'):
                        url = item.url
                    elif hasattr(item, '__dict__') and item.__dict__.get('url'):
                        url = item.__dict__['url']
                    if _is_new(url):
                        image_urls.append(url)
                        if len(image_urls) >= max_images:
                            return image_urls
                continue
            # 非 AstrImage/非 Reply 组件，跳过
            continue

        # 2. 仅当第 1 步没搜到任何图片时，才尝试 @头像（与直接/引用图互斥）
        if not image_urls:
            for comp in event.get_messages():
                if isinstance(comp, At):
                    qq = getattr(comp, 'qq', None)
                    if qq is not None and str(qq) and str(qq) != str(bot_id):
                        avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={qq}&s=640"
                        if _is_new(avatar_url):
                            image_urls.append(avatar_url)
                            if len(image_urls) >= max_images:
                                return image_urls

        return image_urls

    def _extract_user_prompt(self, event, command_name):
        """提取用户自己输入的提示词（从 Plain 组件提取，排除引用消息和 @ 提及）"""
        texts = []
        for comp in event.get_messages():
            d = comp.__dict__ if hasattr(comp, '__dict__') else {}
            # 跳过 Reply（引用消息内容）
            if d.get('type') == 'Reply':
                continue
            # 跳过 At 组件
            if isinstance(comp, At):
                continue
            # 收集纯文本组件
            txt = None
            if hasattr(comp, 'text'):
                txt = comp.text
            elif isinstance(comp, str):
                txt = comp
            if txt:
                texts.append(txt)
        msg = ''.join(texts)
        # 移除命令前缀（支持 /编辑、编辑 两种写法）
        msg = msg.replace(f"/{command_name}", '').replace(command_name, '').strip()
        # 清理残留 @ID 格式
        msg = re.sub(r'\[At:\d+\]', '', msg).strip()
        msg = re.sub(r'@\S+', '', msg).strip()
        return msg

    async def _upload_image_remote(self, local_path):
        """通过 HTTP 上传图片到 ComfyUI input 目录，返回文件名"""
        local_path = Path(local_path)
        if not local_path.exists() or local_path.stat().st_size == 0:
            logger.error(f"[ComfyUI] 上传文件不存在或为空: {local_path}")
            return None
        url = f"http://{self.comfyui_url}/upload/image"
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                with open(local_path, 'rb') as f:
                    data = aiohttp.FormData()
                    data.add_field('image', f, filename=local_path.name, content_type='image/png')
                    data.add_field('type', 'input')
                    data.add_field('overwrite', 'true')
                    async with session.post(url, data=data) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            name = result.get('name')
                            logger.info(f"[ComfyUI] 上传成功: {local_path.name} -> {name}")
                            return name
                        text = await resp.text()
                        logger.error(f"[ComfyUI] 上传失败 HTTP {resp.status}: {text[:200]}")
                        return None
        except Exception as e:
            logger.error(f"[ComfyUI] 上传异常: {e}")
            return None

    async def _get_queue_status(self):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://{self.comfyui_url}/queue") as r:
                    qd = await r.json()
                    running = len(qd.get('queue_running', []))
                    pending = len(qd.get('queue_pending', []))
                    return running + pending, running, pending
        except Exception as e:
            logger.debug(f"[ComfyUI] 获取队列状态失败: {e}")
            return 0, 0, 0

    def _format_queue_msg(self, total):
        if total > 1: return f" | 队列: {total}（前面{total-1}个）"
        elif total == 1: return " | 队列: 1（当前生成中）"
        return ""

    async def _wait_for_history(self, prompt_id, session, timeout=300):
        """轮询 /history/{prompt_id} 直到 outputs 有内容，返回 outputs 字典"""
        start = time.time()
        while time.time() - start < timeout:
            if prompt_id in self._cancelled_pids:
                logger.info(f"[ComfyUI] 任务被取消(新任务替代): {prompt_id}")
                return None
            try:
                async with session.get(f"http://{self.comfyui_url}/history/{prompt_id}") as r:
                    hist = await r.json()
                if prompt_id in hist:
                    outputs = hist[prompt_id].get('outputs', {}) or {}
                    # 等待 outputs 里真的有图片/文件产出
                    has_files = False
                    for _no, out in outputs.items():
                        if isinstance(out, dict) and (out.get('images') or out.get('gifs')):
                            has_files = True
                            break
                    if has_files:
                        logger.info(f"[ComfyUI] 任务完成: {prompt_id}, outputs: {list(outputs.keys())}")
                        return outputs
                    # history 存在但 outputs 为空 → 再等等
                    logger.debug(f"[ComfyUI] history 已就绪但 outputs 为空，继续等待: {prompt_id}")
            except Exception as e:
                logger.debug(f"[ComfyUI] 轮询异常: {e}")
            await asyncio.sleep(1)
        logger.warning(f"[ComfyUI] 轮询超时，未找到 outputs: {prompt_id}")
        return None

    async def _cleanup_upload_loop(self):
        while True:
            try:
                await asyncio.sleep(3600)
                if not self.upload_dir.exists(): continue
                now = time.time()
                cutoff = now - 86400
                deleted = 0
                for f in self.upload_dir.iterdir():
                    if f.is_file():
                        try:
                            if f.stat().st_mtime < cutoff:
                                f.unlink(); deleted += 1
                        except Exception:
                            pass
                if deleted:
                    logger.info(f"[ComfyUI] 清理了 {deleted} 个过期上传文件")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[ComfyUI] 清理上传目录异常: {e}")

    async def _ws_progress_listener(self):
        """连接 ComfyUI WebSocket，监听实时进度，写入 self._progress 和 self._prompt_progress。"""
        logger.info("[ComfyUI] WS 进度监听线程启动")
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    ws_url = f"ws://{self.comfyui_url}/ws?client_id={self._ws_client_id}"
                    async with session.ws_connect(ws_url, timeout=10) as ws:
                        logger.info(f"[ComfyUI] WS 进度监听已连接: {ws_url}")
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = json.loads(msg.data)
                                    msg_type = data.get('type', '')
                                    if msg_type == 'status':
                                        pass  # keep alive
                                except Exception:
                                    pass
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.info(f"[ComfyUI] WS 断开，5秒后重连: {e}")
                await asyncio.sleep(5)
            await asyncio.sleep(0.5)

    async def _process_and_submit(self, prompt, ratio, image_path=None, cmd_config=None, user_id=None):
        self._refresh_workflow_list()
        if not self.workflow_path: return ("error", "未设置工作流", None)
        is_video = any(k in self.current_workflow_name.lower() for k in ['视频', 'wan', 'ltx', 'animate'])
        timeout_sec = 900 if is_video else 300
        try:
            # 1. 加载工作流 JSON（优先用原版， fallback 到 API 版）
            wf_path = self.workflow_path
            if 'API' in Path(wf_path).name:
                orig_name = Path(wf_path).name.replace('API', '原', 1)
                orig_path = Path(wf_path).parent / '原json' / orig_name
                if orig_path.exists(): wf_path = str(orig_path)
            with open(wf_path, 'r', encoding='utf-8') as f: wf = json.load(f)

            # 2. 从工作流 JSON 读取分辨率节点已保存的宽高（Web UI 已写入）
            w, h = None, None
            res_nid = self._find_resolution_node(wf)
            if res_nid and 'width' in wf[res_nid]['inputs'] and 'height' in wf[res_nid]['inputs']:
                try:
                    w = int(wf[res_nid]['inputs']['width'])
                    h = int(wf[res_nid]['inputs']['height'])
                except (ValueError, TypeError): pass

            # 3. 根据质量+比例动态计算分辨率（覆盖工作流内置宽高）
            #    质量由 self.default_quality 决定，比例由 ratio 参数或 self.default_ratio 决定
            quality = self.default_quality
            effective_ratio = ratio or self.default_ratio or "9:16"
            w_new, h_new = self._calc_resolution(quality, effective_ratio)
            should_set_resolution = True
            ratio = effective_ratio
            # 只有抽卡工作流跳过写分辨率（保留内置随机逻辑）
            if "抽卡" in self.current_workflow_name:
                should_set_resolution = False
                w, h = w_new, h_new  # 内存中保留新值用于显示
            else:
                w, h = w_new, h_new

            # 5. 应用配置 + 写入分辨率
            self._apply_workflow_config(wf)
            disabled_groups = self.workflow_config.get('__disabled_groups__', {})
            groups_data = self.workflow_config.get('__groups_data__', [])
            bind_target = self.workflow_config.get('__bind_target__', '')
            if disabled_groups and groups_data and bind_target == self.current_workflow_name:
                for g in groups_data:
                    if disabled_groups.get(str(g.get('id', ''))):
                        to_delete = []
                        for nid in g.get('nodes', []):
                            if nid in wf:
                                for onid, onode in wf.items():
                                    if not isinstance(onode, dict): continue
                                    inputs = onode.get('inputs', {})
                                    if not isinstance(inputs, dict): continue
                                    for k, v in inputs.items():
                                        if isinstance(v, list) and len(v) == 2 and str(v[0]) == nid:
                                            onode['inputs'][k] = ""
                                to_delete.append(nid)
                        for nid in to_delete:
                            del wf[nid]
            if cmd_config:
                for ck, val in cmd_config.items():
                    if ck.startswith('__') or val is None or val == '': continue
                    parts = ck.split('_', 1)
                    if len(parts) == 2:
                        nid, key = parts
                        if nid in wf and key in wf[nid].get('inputs', {}):
                            orig = wf[nid]['inputs'][key]
                            if isinstance(orig, (int, float)):
                                try: val = float(val) if '.' in str(val) else int(val)
                                except ValueError: continue
                            elif isinstance(orig, bool): val = str(val).lower() in ('true', '1', 'yes')
                            wf[nid]['inputs'][key] = val
            if should_set_resolution:
                self._set_resolution(wf, w, h)
            if image_path: await self._set_load_image(wf, image_path)
            if prompt:
                target_node = cmd_config.get('__prompt_node__') if cmd_config else None
                self._inject_prompt(wf, prompt, target_node)
            cid = self._ws_client_id  # 使用固定 client_id 以确保 WS 监听器能收到进度消息
            async with aiohttp.ClientSession() as s:
                total_q, _, _ = await self._get_queue_status()
                queue_info = self._format_queue_msg(total_q)
                async with s.post(f"http://{self.comfyui_url}/prompt", json={"prompt": wf, "client_id": cid}) as r:
                    rj = await r.json()
                if 'prompt_id' not in rj:
                    self._generating_lock = False
                    return ("error", "提交失败", None)
                pid = rj['prompt_id']
                # ⚡ 立即预置进度数据，防止 WS 监听器在 async 间隙读到空值
                self.current_prompt_id = pid
                self._generating_lock = True
                self._prompt_start_time[pid] = time.time()
                self._prompt_node_count[pid] = len(wf)
                self._prompt_progress[pid] = {
                    'nodes_done': 0, 'nodes_total': len(wf),
                    'node_name': '', 'node_value': 0, 'node_max': 0,
                    'running': True,
                }
                if user_id:
                    async with self._task_lock:
                        self.task_map[pid] = user_id
                        existing = [p for p, u in self.task_map.items() if u == user_id]
                        if len(existing) > 1:
                            logger.info(f"[ComfyUI] 用户 {user_id} 有 {len(existing)} 个排队任务")
                logger.info(f"[ComfyUI] 任务提交: {pid}, 节点数: {len(wf)}, 等待完成...")
                outputs = await self._wait_for_history(pid, s, timeout=timeout_sec)
                # 无论结果如何，任务已结束，清除进度状态
                self._generating_lock = False
                if pid in self._prompt_progress:
                    self._prompt_progress[pid]['running'] = False
                # 清理旧 pid 的进度数据，防止内存泄漏
                self._progress.pop(pid, None)
                self._prompt_node_count.pop(pid, None)
                self._prompt_progress.pop(pid, None)
                self._prompt_start_time.pop(pid, None)
                async with self._task_lock:
                    self.task_map.pop(pid, None)
                    self._cancelled_pids.discard(pid)
                if outputs is None:
                    logger.warning(f"[ComfyUI] 超时无产出: {pid}")
                    return ("timeout", "生成超时" if not is_video else "生成超时", None)
                logger.info(f"[ComfyUI] 开始下载图片，outputs 节点: {list(outputs.keys())}")
                for no_key, no in outputs.items():
                    for img in no.get('images', []):
                        vu = f"http://{self.comfyui_url}/view?filename={img['filename']}&subfolder={img.get('subfolder','')}&type={img.get('type','output')}"
                        logger.info(f"[ComfyUI] 下载: {img['filename']}")
                        async with s.get(vu, timeout=aiohttp.ClientTimeout(total=30)) as ir:
                            if ir.status == 200:
                                sp = self.output_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:4]}_{img['filename']}"
                                sp.write_bytes(await ir.read())
                                logger.info(f"[ComfyUI] 已保存: {sp}")
                                # 如果工作流的 SaveImage 输出目录与 output_dir 相同，
                                # 删除 ComfyUI 直接写入的原始文件，避免画廊出现重复
                                original_file = self.output_dir / img['filename']
                                if original_file.exists() and str(original_file.resolve()) != str(sp.resolve()):
                                    try:
                                        original_file.unlink()
                                        logger.info(f"[ComfyUI] 已清理工作流原始输出(去重): {original_file}")
                                    except Exception as e:
                                        logger.warning(f"[ComfyUI] 清理原始输出失败: {e}")
                                # 从实际图片文件读取真实分辨率（所有工作流）
                                try:
                                    from PIL import Image as _PILImage
                                    with _PILImage.open(str(sp)) as _img:
                                        real_w, real_h = _img.size
                                except Exception:
                                    try:
                                        # fallback: struct 解析 PNG 头部
                                        import struct
                                        with open(str(sp), 'rb') as _f:
                                            _f.read(8)  # PNG signature
                                            _f.read(4)  # chunk length
                                            _f.read(4)  # chunk type (IHDR)
                                            real_w, real_h = struct.unpack('>II', _f.read(8))
                                    except Exception:
                                        real_w, real_h = w, h  # fallback 到算法值
                                # 根据真实分辨率计算实际比例（最简整数比）
                                import math
                                g = math.gcd(real_w, real_h)
                                actual_ratio = f"{real_w // g}:{real_h // g}"
                                return ("ok", f"{actual_ratio} {real_w}x{real_h}", str(sp))
                            else:
                                logger.warning(f"[ComfyUI] 下载失败 HTTP {ir.status}: {img['filename']}")
                    # 也检查 gifs（图生视频）
                    for gif in no.get('gifs', []):
                        vu = f"http://{self.comfyui_url}/view?filename={gif['filename']}&subfolder={gif.get('subfolder','')}&type={gif.get('type','output')}"
                        logger.info(f"[ComfyUI] 下载视频: {gif['filename']}")
                        async with s.get(vu, timeout=aiohttp.ClientTimeout(total=60)) as ir:
                            if ir.status == 200:
                                sp = self.output_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{gif['filename']}"
                                sp.write_bytes(await ir.read())
                                logger.info(f"[ComfyUI] 已保存视频: {sp}")
                                # GIF 也做同样的去重清理
                                original_file = self.output_dir / gif['filename']
                                if original_file.exists() and str(original_file.resolve()) != str(sp.resolve()):
                                    try:
                                        original_file.unlink()
                                        logger.info(f"[ComfyUI] 已清理工作流原始输出(去重): {original_file}")
                                    except Exception as e:
                                        logger.warning(f"[ComfyUI] 清理原始输出失败: {e}")
                                return ("ok", f"{ratio} {w}x{h}", str(sp))
                logger.warning(f"[ComfyUI] outputs 中未找到可下载的文件: {pid}")
                return ("error", "未找到输出文件(history 有记录但无 images)", None)
        except Exception as e:
            logger.error(traceback.format_exc())
            return ("error", str(e), None)

    # ==================== QQ 命令 ====================

    @filter.command("帮助")
    async def show_help(self, event: AstrMessageEvent):
        await self._ensure_workflow_for_event(event)
        ctx = self._get_context_key(event)
        ctx_wf = self._context_workflows.get(ctx, self.current_workflow_name) if ctx else self.current_workflow_name
        m = f"当前工作流: {self._get_display_name(ctx_wf)}\n\n"
        m += "  /画 [比例] 提示词 - 文生图（发文字即可）\n"
        m += "  /图生图 [降噪值] 提示词 - 图生图（引用图片 或 @用户获取头像）\n"
        m += "  /图生视频 - 图生视频（引用图片 或 @用户获取头像）\n"
        m += "  /随机图 [数量] - 随机抽卡（需含「抽卡」的工作流）\n"
        m += "  /编辑 提示词 - 编辑图片（直接传图、引用图片 或 @用户获取头像，最多3张）\n"
        m += "  /执行 提示词 - 执行当前工作流（不限分类，未分类工作流专用）\n"
        m += "  /工作流 [编号/关键词] - 查看/切换工作流\n"
        m += "  /切换 [编号/关键词] - 快速切换工作流\n"
        m += "  /比例 [编号/比例名] - 查看/切换比例\n"
        m += "  /分辨率 [等级] - 设置质量等级（480p/720p/1080p/2K/4K）\n"
        m += "  /队列 - 查看 ComfyUI 队列状态\n"
        m += "  /停止 - 停止当前生成\n"
        m += "  /撤回 - 撤回最后一张生成的图片/视频\n"
        m += "  /帮助 - 显示此帮助\n"
        urls = [f"http://127.0.0.1:{self.webui_port} (本机)"]
        if self.webui_lan:
            urls.append(f"局域网 http://你的IP:{self.webui_port}")
        if self.webui_ipv6:
            urls.append(f"IPv6 http://[你的IPv6]:{self.webui_port}")
        m += "WebUI: " + " / ".join(urls)
        yield event.plain_result(m)

    @filter.command("队列")
    async def show_queue(self, event: AstrMessageEvent):
        try:
            total, running, pending = await self._get_queue_status()
            if total == 0: yield event.plain_result("队列为空")
            else: yield event.plain_result(f"运行中:{running} 等待中:{pending} 总计:{total}")
        except Exception as e:
            logger.warning(f"[ComfyUI] 获取队列失败: {e}")
            yield event.plain_result("无法获取队列状态")

    @filter.command("停止")
    async def stop_generation(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        async with self._task_lock:
            my_tasks = [pid for pid, uid in self.task_map.items() if uid == user_id]
        if not my_tasks: yield event.plain_result("没有正在生成的任务"); return
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(f"http://{self.comfyui_url}/interrupt")
                async with self._task_lock:
                    for pid in my_tasks: self.task_map.pop(pid, None)
            yield event.plain_result(f"已停止 {len(my_tasks)} 个任务")
        except Exception as e:
            logger.warning(f"[ComfyUI] 停止任务失败: {e}")
            yield event.plain_result("停止失败")

    @filter.command("撤回")
    async def recall_last(self, event: AstrMessageEvent):
        """撤回最近一次本插件发送的图片/视频消息"""
        umo = getattr(event, 'unified_msg_origin', None) or ''
        bot = self._bot_ref or getattr(event, 'bot', None)
        if not bot:
            yield event.plain_result("无法获取 bot 引用，撤回不可用")
            return

        # 查找该会话中最近发送的消息
        latest = None
        latest_path = ''
        async with self._sent_images_lock:
            for path, records in self._sent_images.items():
                for rec in records:
                    if rec.get('umo', '') == umo:
                        if latest is None or rec.get('sent_at', 0) > latest['sent_at']:
                            latest = rec
                            latest_path = path

        if not latest:
            yield event.plain_result("没有找到可撤回的消息")
            return

        msg_id = latest.get('message_id', '')
        if not msg_id:
            yield event.plain_result("该消息没有 message_id，无法撤回")
            return

        try:
            recalled = False
            if hasattr(bot, 'delete_msg'):
                await bot.delete_msg(message_id=int(msg_id))
                recalled = True
            elif hasattr(bot, 'call_api'):
                await bot.call_api('delete_msg', message_id=int(msg_id))
                recalled = True
            if not recalled:
                yield event.plain_result("撤回失败：bot 不支持 delete_msg 方法")
                return
            # 从记录中移除
            async with self._sent_images_lock:
                self._sent_images[latest_path] = [r for r in self._sent_images.get(latest_path, []) if r.get('message_id') != msg_id]
            yield event.plain_result(f"✅ 已撤回消息")
        except Exception as e:
            logger.warning(f"[ComfyUI] 撤回失败: {e}")
            yield event.plain_result(f"撤回失败: {e}")

    @filter.command("比例")
    async def list_ratios(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        parts = event.message_str.split(maxsplit=1)
        msg = parts[1].strip() if len(parts) > 1 else ""

        # 按编号切换
        if msg.isdigit():
            idx = int(msg) - 1
            if 0 <= idx < len(self.aspect_ratios):
                ratio = self.aspect_ratios[idx]
                w, h = self._calc_resolution(self.default_quality, ratio)
                self.default_ratio = ratio
                self.default_width, self.default_height = w, h
                self._save_local_config({"default_ratio": ratio})
                self._sync_resolution_to_workflow(ratio, w, h)
                if user_id in self.pending_actions:
                    del self.pending_actions[user_id]
                yield event.plain_result(f"✅ 比例已切换为 {ratio}（质量 {self.default_quality} → {w}x{h}）")
                return

        # 按比例名称切换
        if msg and msg in self.aspect_ratios:
            w, h = self._calc_resolution(self.default_quality, msg)
            self.default_ratio = msg
            self.default_width, self.default_height = w, h
            self._save_local_config({"default_ratio": msg})
            self._sync_resolution_to_workflow(msg, w, h)
            if user_id in self.pending_actions:
                del self.pending_actions[user_id]
            yield event.plain_result(f"✅ 比例已切换为 {msg}（质量 {self.default_quality} → {w}x{h}）")
            return

        # 无参数，展示列表并等待输入
        t = (f"📐 比例切换（当前 {self.default_ratio}，"
             f"质量 {self.default_quality} → {self.default_width}x{self.default_height}）\n\n")
        for i, r in enumerate(self.aspect_ratios, 1):
            w, h = self._calc_resolution(self.default_quality, r)
            tag = " ✅" if r == self.default_ratio else ""
            t += f"  [{i}] {r} → {w}x{h} ({w*h/10000:.1f}万){tag}\n"
        t += f"\n也可直接发送 /比例 9:16 切换（10s 内有效）"
        self._set_pending_action(user_id, "set_ratio", {"ratios": list(self.aspect_ratios)}, timeout=10)
        yield event.plain_result(t)

    def _sync_resolution_to_workflow(self, ratio, width, height):
        """将分辨率同步写入当前工作流 JSON 文件（供 /比例 命令和 Web UI 共用）"""
        if not self.workflow_path: return
        # 抽卡工作流含内置随机分辨率逻辑，禁止写死，否则随机图每次输出固定比例
        if "抽卡" in self.current_workflow_name:
            logger.info(f"[ComfyUI] 抽卡工作流跳过同步分辨率（保留内置随机逻辑）")
            return
        try:
            wf_path = self.workflow_path
            if 'API' in Path(wf_path).name:
                orig_name = Path(wf_path).name.replace('API', '原', 1)
                orig_path = Path(wf_path).parent / '原json' / orig_name
                if orig_path.exists(): wf_path = str(orig_path)
            with open(wf_path, 'r', encoding='utf-8') as f: wf = json.load(f)
            nid = self._find_resolution_node(wf)
            if nid:
                wf[nid]['inputs']['width'] = width
                wf[nid]['inputs']['height'] = height
                with open(wf_path, 'w', encoding='utf-8') as f:
                    json.dump(wf, f, ensure_ascii=False, indent=2)
                logger.info(f"[ComfyUI] /比例 已同步分辨率到工作流: {wf_path} {width}x{height}")
        except Exception as e:
            logger.warning(f"[ComfyUI] /比例 同步分辨率到工作流失败: {e}")

    # ========= 交互式选择（pending_actions）========

    def _check_pending_action(self, user_id, action_type=None, cleanup=True):
        """检查用户是否有待处理的交互动作。返回动作数据或None。"""
        import time
        if user_id in self.pending_actions:
            pa = self.pending_actions[user_id]
            if time.time() > pa["expires_at"]:
                if cleanup:
                    del self.pending_actions[user_id]
                return None
            if action_type and pa.get("action") != action_type:
                return None
            return pa
        return None

    def _set_pending_action(self, user_id, action_type, data, timeout=10):
        """设置等待用户数字输入的交互动作。"""
        import time
        self.pending_actions[user_id] = {
            "action": action_type,
            "data": data,
            "expires_at": time.time() + timeout
        }

    # ========= /分辨率 命令 =========

    @filter.command("分辨率")
    async def set_resolution_cmd(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        parts = event.message_str.split(maxsplit=1)
        msg = parts[1].strip() if len(parts) > 1 else ""

        # 检查是否有待处理的数字选择
        pa = self._check_pending_action(user_id, "set_resolution", cleanup=False)
        if pa and msg.isdigit() and 1 <= int(msg) <= len(self.quality_presets):
            idx = int(msg) - 1
            quality_names = list(self.quality_presets.keys())
            if 0 <= idx < len(quality_names):
                quality = quality_names[idx]
                w, h = self._calc_resolution(quality, self.default_ratio)
                self.default_quality = quality
                self.default_width, self.default_height = w, h
                self._save_local_config({"default_quality": quality})
                self._sync_resolution_to_workflow(self.default_ratio, w, h)
                del self.pending_actions[user_id]
                yield event.plain_result(f"✅ 质量已切换为 {quality} → {self.default_ratio} {w}x{h}")
                return

        # 直接带参数切换（/分辨率 720p）
        if msg and msg in self.quality_presets:
            w, h = self._calc_resolution(msg, self.default_ratio)
            self.default_quality = msg
            self.default_width, self.default_height = w, h
            self._save_local_config({"default_quality": msg})
            self._sync_resolution_to_workflow(self.default_ratio, w, h)
            yield event.plain_result(f"✅ 质量已切换为 {msg} → {self.default_ratio} {w}x{h}")
            return

        # 显示列表并等待输入
        current = self.default_quality
        quality_names = list(self.quality_presets.keys())
        m = f"🎨 质量等级（当前: {current}）:\n\n"
        for i, p in enumerate(quality_names, 1):
            w, h = self._calc_resolution(p, self.default_ratio)
            tag = " ✅(当前)" if p == current else ""
            m += f"  [{i}] {p} {self.quality_presets[p]['name']} → {self.default_ratio} {w}x{h}{tag}\n"
        m += f"\n也可直接发送 /分辨率 720p 切换（10s 内有效）"
        self._set_pending_action(user_id, "set_resolution", {"presets": quality_names}, timeout=10)
        yield event.plain_result(m)

        # ==================== 数字选择处理器 ====================

    @filter.regex(r'^\d+$')
    async def handle_numeric_choice(self, event: AstrMessageEvent):
        """处理数字选择，用于交互式切换。需在 @filter.command 之前注册。"""
        user_id = event.get_sender_id()
        msg = event.message_str.strip()

        pa = self._check_pending_action(user_id, cleanup=False)
        if not pa:
            return  # 没有待处理动作，忽略
        if not msg.isdigit():
            return

        idx = int(msg) - 1

        if pa['action'] == 'set_resolution':
            quality_names = pa['data']['presets']
            if 0 <= idx < len(quality_names):
                quality = quality_names[idx]
                w, h = self._calc_resolution(quality, self.default_ratio)
                self.default_quality = quality
                self.default_width, self.default_height = w, h
                self._save_local_config({"default_quality": quality})
                self._sync_resolution_to_workflow(self.default_ratio, w, h)
                del self.pending_actions[user_id]
                yield event.plain_result(f"✅ 质量已切换为 {quality} → {self.default_ratio} {w}x{h}")

        elif pa['action'] == 'set_ratio':
            ratio_keys = pa['data']['ratios']
            if 0 <= idx < len(ratio_keys):
                ratio = ratio_keys[idx]
                w, h = self._calc_resolution(self.default_quality, ratio)
                self.default_ratio = ratio
                self.default_width, self.default_height = w, h
                self._save_local_config({"default_ratio": ratio})
                self._sync_resolution_to_workflow(ratio, w, h)
                del self.pending_actions[user_id]
                yield event.plain_result(f"✅ 比例已切换为 {ratio}（质量 {self.default_quality} → {w}x{h}）")

        elif pa['action'] == 'switch_workflow':
            wfs = pa['data']['workflows']
            if 0 <= idx < len(wfs):
                found = wfs[idx]
                ctx = pa['data'].get('context_key')
                self._switch_to_workflow(found, context_key=ctx)
                del self.pending_actions[user_id]
                yield event.plain_result(f"✅ 已切换工作流: {found.get('display_name', found['name'])}")

    # ========= 辅助：根据用户输入切换工作流（数字或关键词） =========

    async def _switch_workflow_by_msg(self, event, msg, user_id):
        """根据用户输入（数字或关键词）切换工作流，供 /工作流 和 /切换 命令共用"""
        wfs = self._refresh_workflow_list()
        target = wfs

        if msg.isdigit():
            idx = int(msg) - 1
            if 0 <= idx < len(target):
                found = target[idx]
                ctx = self._get_context_key(event)
                self._switch_to_workflow(found, context_key=ctx)
                yield event.plain_result("✅ 已切换工作流: " + found.get('display_name', found['name']))
            else:
                yield event.plain_result("❌ 数字超出范围（1-" + str(len(target)) + "）")
        else:
            matched = [w for w in target if msg.lower() in w['name'].lower() or msg.lower() in w.get('display_name', '').lower()]
            if len(matched) == 1:
                found = matched[0]
                ctx = self._get_context_key(event)
                self._switch_to_workflow(found, context_key=ctx)
                yield event.plain_result("✅ 已切换工作流: " + found.get('display_name', found['name']))
            elif len(matched) > 1:
                m = "找到多个匹配，请精确指定:\n"
                for i, w in enumerate(matched[:10], 1):
                    m += "  [" + str(i) + "] " + w.get('display_name', w['name']) + "\n"
                yield event.plain_result(m)
            else:
                yield event.plain_result("❌ 未找到包含'" + msg + "'的工作流。发送 /工作流 查看列表")

    @filter.command("工作流")
    async def get_workflows(self, event: AstrMessageEvent):
        await self._ensure_workflow_for_event(event)
        user_id = event.get_sender_id()
        parts = event.message_str.split(maxsplit=1)
        msg = parts[1].strip() if len(parts) > 1 else ""

        wfs = self._refresh_workflow_list()
        if not wfs: yield event.plain_result(f"没有\n目录: {self._get_workflow_dir()}"); return

        # 带参数直接切换
        if msg:
            async for r in self._switch_workflow_by_msg(event, msg, user_id):
                yield r
            return

        # 无参数，展示列表并等待输入（按分类分组展示）
        ctx = self._get_context_key(event)
        ctx_wf = self._context_workflows.get(ctx, self.current_workflow_name) if ctx else self.current_workflow_name
        _, _, allowed = await self._get_event_bindings(event)
        cur_dn = self._get_display_name(ctx_wf) if ctx_wf else "无"
        target = allowed if allowed else wfs
        # allowed 是字符串列表（如 ["A.json"]），转为完整字典列表确保 _switch_to_workflow 能正确调用
        if allowed and isinstance(allowed[0], str):
            target = [w for w in wfs if w['name'] in allowed]

        # 按分类分组
        cats = self.workflow_config.get('__wf_categories__', {}) or {}
        # 命令名 → 图标映射
        cmd_icons = {'画': '/画', '图生图': '/图生图', '图生视频': '/图生视频', '编辑': '/编辑', '随机图': '/随机图'}
        groups = {}  # cat_name -> [(index, wf_dict)]
        ungrouped = []
        for i, w in enumerate(target, 1):
            wf_name = w["name"] if isinstance(w, dict) else w
            cat = cats.get(wf_name, '')
            entry = (i, w)
            if cat:
                groups.setdefault(cat, []).append(entry)
            else:
                ungrouped.append(entry)

        m = f"当前: {cur_dn}\n"
        # 分类顺序：画 → 图生图 → 图生视频 → 编辑 → 随机图 → 其他
        cat_order = ['画', '图生图', '图生视频', '编辑', '随机图']
        for cat in cat_order:
            if cat not in groups:
                continue
            cmd_hint = cmd_icons.get(cat, f'/{cat}')
            m += f"\n{cmd_hint} 提示词:\n"
            for idx, w in groups[cat]:
                dn = w.get("display_name", w["name"]) if isinstance(w, dict) else self._get_display_name(w)
                is_cur = (w["name"] if isinstance(w, dict) else w) == ctx_wf
                m += f"  [{idx}] {dn}" + (" ✅\n" if is_cur else "\n")

        if ungrouped:
            m += "\n未分类:\n"
            for idx, w in ungrouped:
                dn = w.get("display_name", w["name"]) if isinstance(w, dict) else self._get_display_name(w)
                is_cur = (w["name"] if isinstance(w, dict) else w) == ctx_wf
                m += f"  [{idx}] {dn}" + (" ✅\n" if is_cur else "\n")

        self._set_pending_action(user_id, "switch_workflow", {"workflows": target, "context_key": ctx}, timeout=10)
        yield event.plain_result(m.strip())

    @filter.command("切换")
    async def switch_workflow(self, event: AstrMessageEvent):
        await self._ensure_workflow_for_event(event)
        msg = event.message_str.split(maxsplit=1)
        a = msg[1].strip() if len(msg) > 1 else ""
        if not a:
            async for r in self.get_workflows(event): yield r
            return
        async for r in self._switch_workflow_by_msg(event, a, event.get_sender_id()):
            yield r
    @filter.command("画")
    async def draw_image(self, event: AstrMessageEvent):
        await self._ensure_workflow_for_event(event)
        can_exec, needs_sel, matching_wfs = await self._ensure_command_workflow(event, '画')
        if needs_sel:
            yield event.plain_result(self._build_wf_selection_menu(event, '画', matching_wfs))
            return
        if not can_exec:
            yield event.plain_result("当前无可用画图工作流")
            return
        msg = event.message_str.replace("/画", "").replace("画", "").strip()
        msg = re.sub(r'\[At:\d+\]', '', msg).strip()
        msg = re.sub(r'@\S+', '', msg).strip()
        if not msg: yield event.plain_result("/画 提示词"); return
        prompt = msg
        total_q, _, _ = await self._get_queue_status()
        queue_msg = self._format_queue_msg(total_q)
        yield event.plain_result(f"生成中...{queue_msg}")
        cmd_config = self.workflow_config.get('__commands__', {}).get('画', {})
        status, text, path = await self._process_and_submit(prompt, None, cmd_config=cmd_config if cmd_config else None, user_id=event.get_sender_id())
        if status == "ok":
            sent = await self._send_image_result(event, f"✨ 生成完成 当前{text}", path)
            if not sent:
                yield event.image_result(path)
        else: yield event.plain_result(text)

    @filter.command("执行")
    async def execute_wf(self, event: AstrMessageEvent):
        await self._ensure_workflow_for_event(event)
        if not self.current_workflow_name:
            yield event.plain_result("当前未选中工作流，请先通过 /工作流 或 WebUI 选择")
            return
        msg = event.message_str.replace("/执行", "").strip()
        msg = re.sub(r'\[At:\d+\]', '', msg).strip()
        msg = re.sub(r'@\S+', '', msg).strip()
        if not msg: yield event.plain_result("/执行 提示词"); return
        prompt = msg
        # 获取当前工作流的分类，使用对应分类的 cmd_config（如有自定义提示词节点等设置）
        cats = self.workflow_config.get('__wf_categories__', {}) or {}
        cur_cat = cats.get(self.current_workflow_name, '')
        cmd_config = dict(self.workflow_config.get('__commands__', {}).get(cur_cat, {})) if cur_cat else None
        cmd_config = cmd_config if cmd_config else None
        total_q, _, _ = await self._get_queue_status()
        queue_msg = self._format_queue_msg(total_q)
        yield event.plain_result(f"执行中...{queue_msg}")
        status, text, path = await self._process_and_submit(prompt, None, cmd_config=cmd_config, user_id=event.get_sender_id())
        if status == "ok":
            sent = await self._send_image_result(event, f"✨ 执行完成 当前{text}", path)
            if not sent:
                yield event.image_result(path)
        else: yield event.plain_result(text)

    @filter.command("随机图")
    async def random_draw(self, event: AstrMessageEvent):
        await self._ensure_workflow_for_event(event)
        can_exec, needs_sel, matching_wfs = await self._ensure_command_workflow(event, '随机图')
        if needs_sel:
            yield event.plain_result(self._build_wf_selection_menu(event, '随机图', matching_wfs))
            return
        if not can_exec:
            yield event.plain_result("当前无可用随机图工作流")
            return
        if "抽卡" not in self.current_workflow_name:
            yield event.plain_result("/随机图 只能在含「抽卡」的工作流上使用\n当前: " + self._get_display_name(self.current_workflow_name))
            return
        msg = event.message_str.replace("/随机图", "").strip()
        count = 1
        if msg:
            m = re.search(r'(\d+)\s*张?', msg)
            if m: count = min(int(m.group(1)), 10)
        for i in range(count):
            total_q, _, _ = await self._get_queue_status()
            queue_msg = self._format_queue_msg(total_q)
            if count > 1 and i == 0:
                yield event.plain_result(f"随机抽卡中...（共{count}张）{queue_msg}")
            elif count == 1:
                yield event.plain_result(f"随机抽卡中...{queue_msg}")
            status, text, path = await self._process_and_submit("", None, user_id=event.get_sender_id())
            if status == "ok":
                sent = await self._send_image_result(event, f"✨ 生成完成 当前{text}", path)
                if not sent:
                    yield event.image_result(path)
            else: yield event.plain_result(text)
            if i < count - 1: await asyncio.sleep(1)

    @filter.command("图生图")
    async def img2img(self, event: AstrMessageEvent):
        await self._ensure_workflow_for_event(event)
        can_exec, needs_sel, matching_wfs = await self._ensure_command_workflow(event, '图生图')
        if needs_sel:
            yield event.plain_result(self._build_wf_selection_menu(event, '图生图', matching_wfs))
            return
        if not can_exec:
            yield event.plain_result("当前无可用图生图工作流")
            return
        msg = self._extract_user_prompt(event, '图生图')
        dm = re.search(r'\b0\.\d+\b', msg)
        denoise = float(dm.group(1)) if dm and 0 <= float(dm.group(1)) <= 0.8 else None
        if denoise:
            msg = msg.replace(dm.group(1), '', 1).strip()
        prompt = msg if msg else ""
        image_urls = await self._collect_images_from_event(event, max_images=1)
        image_url = image_urls[0] if image_urls else None
        if image_url:
            save_path = self._get_image_save_dir() / f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            total_q, _, _ = await self._get_queue_status()
            queue_msg = self._format_queue_msg(total_q)
            denoise_hint = f"（降噪: {denoise}）" if denoise else ""
            yield event.plain_result(f"生成中...{denoise_hint}{queue_msg}")
            if not await self._download_image(image_url, save_path): yield event.plain_result("下载图片失败"); return
            cmd_config = dict(self.workflow_config.get('__commands__', {}).get('图生图', {}))
            if denoise:
                try:
                    with open(self.workflow_path, 'r', encoding='utf-8') as f: wf = json.load(f)
                    sn = self._find_sampler_node(wf)
                    if sn: cmd_config[f'{sn}_denoise'] = denoise
                except Exception as e:
                    logger.warning(f"[ComfyUI] 设置降噪值失败: {e}")
            status, text, out_path = await self._process_and_submit(prompt, None, str(save_path), cmd_config=cmd_config if cmd_config else None, user_id=event.get_sender_id())
            if status == "ok":
                sent = await self._send_image_result(event, f"✨ 生成完成 当前{text}", out_path)
                if not sent:
                    yield event.image_result(out_path)
            else: yield event.plain_result(text)
            return
        yield event.plain_result("请发送图片或 @用户 后使用 /图生图 [降噪] 提示词")

    @filter.command("图生视频")
    async def img2vid(self, event: AstrMessageEvent):
        await self._ensure_workflow_for_event(event)
        can_exec, needs_sel, matching_wfs = await self._ensure_command_workflow(event, '图生视频')
        if needs_sel:
            yield event.plain_result(self._build_wf_selection_menu(event, '图生视频', matching_wfs))
            return
        if not can_exec:
            yield event.plain_result("当前无可用图生视频工作流")
            return
        image_urls = await self._collect_images_from_event(event, max_images=1)
        image_url = image_urls[0] if image_urls else None
        if image_url:
            # 检查当前工作流是否支持视频生成
            video_kw = ['视频', 'wan', 'ltx', 'animate', 'video', 'WAN']
            if not any(k in self.current_workflow_name for k in video_kw):
                yield event.plain_result(f"当前工作流「{self._get_display_name(self.current_workflow_name)}」不是视频工作流，请先切换到视频工作流再使用 /图生视频")
                return
            save_path = self._get_image_save_dir() / f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            total_q, _, _ = await self._get_queue_status()
            queue_msg = self._format_queue_msg(total_q)
            yield event.plain_result(f"生成视频中...{queue_msg}")
            if not await self._download_image(image_url, save_path): yield event.plain_result("下载图片失败"); return
            cmd_config = self.workflow_config.get('__commands__', {}).get('图生视频', {})
            status, text, out_path = await self._process_and_submit("", None, str(save_path), cmd_config=cmd_config if cmd_config else None, user_id=event.get_sender_id())
            if status == "ok":
                try:
                    from astrbot.api.message_components import Video, At, Plain
                    # 先发视频
                    yield event.chain_result([Video.fromFileSystem(out_path)])
                    # 再发 @用户 的完成通知
                    yield event.chain_result([
                        At(qq=event.get_sender_id()),
                        Plain(" 视频生成完毕"),
                    ])
                except Exception as e:
                    logger.error(f"[ComfyUI] 发送视频消息失败: {e}")
                    yield event.plain_result(f"视频已生成，但无法自动发送，文件路径: {out_path}")
            else: yield event.plain_result(text)
            return
        yield event.plain_result("请引用图片或 @用户 后输入 /图生视频")

    # ========= /编辑 命令（@头像/引用图片，最多3张） =========

    @filter.command("编辑")
    async def edit_image(self, event: AstrMessageEvent):
        await self._ensure_workflow_for_event(event)
        can_exec, needs_sel, matching_wfs = await self._ensure_command_workflow(event, '编辑')
        if needs_sel:
            yield event.plain_result(self._build_wf_selection_menu(event, '编辑', matching_wfs))
            return
        if not can_exec:
            yield event.plain_result("当前无可用编辑工作流")
            return
        msg = self._extract_user_prompt(event, '编辑')
        prompt = msg if msg else ""

        # 收集图片：@头像 + 引用图片，最多3张
        image_urls = await self._collect_images_from_event(event, max_images=3)
        if not image_urls:
            yield event.plain_result("请 @用户 获取头像图片，或引用图片后使用 /编辑 提示词")
            return

        # 下载所有图片并校验
        saved_paths = []
        for i, url in enumerate(image_urls):
            save_path = self._get_image_save_dir() / f"edit_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{i}.png"
            if await self._download_image(url, save_path):
                saved_paths.append(save_path)
            elif save_path.exists():
                logger.warning(f"[ComfyUI] 丢弃无效图片: {save_path}")
                save_path.unlink(missing_ok=True)

        if not saved_paths:
            yield event.plain_result("下载图片失败")
            return

        total_q, _, _ = await self._get_queue_status()
        queue_msg = self._format_queue_msg(total_q)
        yield event.plain_result(f"生成中...（已加载 {len(saved_paths)} 张图片）{queue_msg}")

        cmd_config = dict(self.workflow_config.get('__commands__', {}).get('编辑', {}))
        imgs = [str(p) for p in saved_paths]
        status, text, out_path = await self._process_and_submit(
            prompt, None, imgs,
            cmd_config=cmd_config if cmd_config else None,
            user_id=event.get_sender_id()
        )

        # 清理临时下载的图片（upload_dir 中的临时文件，HTTP 上传后已不需要）
        for p in saved_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

        if status == "ok":
            sent = await self._send_image_result(event, f"✨ 编辑完成 当前{text}", out_path)
            if not sent:
                yield event.image_result(out_path)
        else:
            yield event.plain_result(text)

    async def _webui_get_wf_previews(self, r):
        """返回所有有预览图的工作流列表"""
        try:
            previews = {}
            if self.preview_dir.exists():
                for f in self.preview_dir.iterdir():
                    if f.is_file():
                        wf_name = f.stem  # 去掉扩展名
                        previews[wf_name] = True
            return web.json_response({"previews": previews})
        except Exception as e:
            logger.warning(f"[ComfyUI] 获取预览列表失败: {e}")
            return web.json_response({"previews": {}})

    async def _webui_upload_wf_preview(self, r):
        """上传工作流预览图（接收 base64 JSON）"""
        try:
            data = await r.json()
            name = data.get('name', '').strip()
            base64_data = data.get('image', '')
            if not name or '..' in name or '/' in name or '\\' in name:
                return web.json_response({"ok": False, "error": "非法工作流名称"})
            if not base64_data:
                return web.json_response({"ok": False, "error": "未收到图片数据"})
            # 解码 base64
            import base64
            header, _, encoded = base64_data.partition(',')
            try:
                img_bytes = base64.b64decode(encoded)
            except Exception:
                return web.json_response({"ok": False, "error": "图片数据解码失败"})
            # 保存预览图（始终用 .jpg 扩展名）
            safe_name = Path(name).name  # 去掉路径部分
            preview_path = self.preview_dir / f"{safe_name}.jpg"
            preview_path.write_bytes(img_bytes)
            logger.info(f"[ComfyUI] 预览图已保存: {preview_path}")
            return web.json_response({"ok": True})
        except Exception as e:
            logger.error(f"[ComfyUI] 上传预览图失败: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_get_wf_preview(self, r):
        """获取工作流预览图"""
        try:
            name = r.query.get('name', '').strip()
            if not name or '..' in name or '/' in name or '\\' in name:
                return web.Response(status=400, text="非法工作流名称")
            safe_name = Path(name).name
            preview_path = self.preview_dir / f"{safe_name}.jpg"
            if not preview_path.exists():
                return web.Response(status=404, text="预览图不存在")
            return web.FileResponse(preview_path, headers={'Content-Type': 'image/jpeg'})
        except Exception as e:
            logger.error(f"[ComfyUI] 获取预览图失败: {e}")
            return web.Response(status=500, text=str(e))

    # ── 画廊 API ──────────────────────────────────────────

    async def _webui_get_gallery(self, r):
        """获取输出目录中的图片列表（按修改时间倒序，递归子目录）"""
        try:
            output_dir = self.output_dir.resolve()
            if not output_dir.exists():
                return web.json_response({"images": []})

            allowed_ext = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.mp4', '.mov', '.avi'}
            video_ext = {'.mp4', '.mov', '.avi', '.gif'}
            images = []
            from urllib.parse import quote

            # 递归扫描所有子目录，跳过 upload 目录
            for f in output_dir.rglob('*'):
                if not f.is_file() or f.suffix.lower() not in allowed_ext:
                    continue
                # 跳过 upload 子目录中的文件
                rel = f.relative_to(output_dir)
                if str(rel).startswith('upload\\') or str(rel).startswith('upload/'):
                    continue
                # 生成可通过 gallery/file 端点访问的 URL（使用相对路径）
                url = f'/api/gallery/file?path={quote(str(rel))}'
                images.append({
                    "path": str(f),
                    "url": url,
                    "name": f.name,
                    "size": f.stat().st_size,
                    "mtime": f.stat().st_mtime,
                    "type": "video" if f.suffix.lower() in video_ext else "image"
                })

            # 按修改时间倒序排列
            images.sort(key=lambda x: x["mtime"], reverse=True)
            # 按文件名去重（保留最新的，防止子目录中同名文件导致重复）
            seen_names = set()
            deduped = []
            for img in images:
                if img["name"] not in seen_names:
                    seen_names.add(img["name"])
                    deduped.append(img)
            return web.json_response({"images": deduped})
        except Exception as e:
            logger.error(f"[ComfyUI] 获取画廊列表失败: {e}")
            return web.json_response({"images": [], "error": str(e)})

    async def _webui_gallery_file(self, r):
        """提供画廊图片文件"""
        try:
            path_str = r.query.get('path', '')
            if not path_str or '..' in path_str:
                return web.Response(status=400, text="非法路径")
            # 路径安全校验：禁止绝对路径和路径遍历
            if path_str.startswith('/') or path_str.startswith('\\'):
                return web.Response(status=400, text="非法路径")
            safe_path = Path(path_str)
            file_path = (self.output_dir / safe_path).resolve()
            # 安全校验：用 relative_to 判断是否在 output_dir 内（自动处理大小写）
            try:
                file_path.relative_to(self.output_dir.resolve())
            except ValueError:
                logger.warning(f"[ComfyUI] gallery_file 拒绝访问: file_path={file_path}, out_dir={self.output_dir.resolve()}")
                return web.Response(status=403, text="拒绝访问")
            if not file_path.exists() or not file_path.is_file():
                return web.Response(status=404, text="文件不存在")
            # 根据扩展名设置 Content-Type
            ext = file_path.suffix.lower()
            content_type_map = {
                '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                '.webp': 'image/webp', '.gif': 'image/gif', '.bmp': 'image/bmp'
            }
            ctype = content_type_map.get(ext, 'application/octet-stream')
            return web.FileResponse(file_path, headers={'Content-Type': ctype, 'Cache-Control': 'no-cache'})
        except Exception as e:
            logger.error(f"[ComfyUI] 提供画廊文件失败: {e}")
            return web.Response(status=500, text=str(e))

    async def _webui_gallery_delete(self, r):
        """删除画廊中的图片文件（按文件名，避免全路径中文编码问题）"""
        try:
            data = await r.json()
            name_str = data.get('name', '')
            if not name_str:
                return web.json_response({"ok": False, "error": "名为空"})
            # 安全校验：禁止路径穿越
            if '..' in name_str or '/' in name_str or '\\' in name_str:
                return web.json_response({"ok": False, "error": "非法文件名"})
            target = (self.output_dir / name_str).resolve()
            out_dir = self.output_dir.resolve()
            try:
                target.relative_to(out_dir)
            except ValueError:
                logger.warning(f"[ComfyUI] 拒绝删除非输出目录文件: name={name_str}, target={target}, out_dir={out_dir}")
                return web.json_response({"ok": False, "error": "拒绝删除：文件不在输出目录中"})
            if not target.exists():
                return web.json_response({"ok": False, "error": "文件不存在"})
            target.unlink()
            logger.info(f"[ComfyUI] 画廊删除文件: {target}")
            # 同时清理发送记录
            self._sent_images.pop(str(target), None)
            return web.json_response({"ok": True})
        except Exception as e:
            logger.error(f"[ComfyUI] 删除画廊文件失败: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_gallery_recall(self, r):
        """撤回已发送到 QQ 的图片消息（按文件名）"""
        try:
            data = await r.json()
            name_str = data.get('name', data.get('path', ''))
            if not name_str:
                return web.json_response({"ok": False, "error": "名为空"})
            # 提取文件名（兼容新旧请求）
            safe_name = Path(name_str).name
            abs_path = str((self.output_dir / safe_name).resolve())
            logger.info(f"[ComfyUI] 撤回查找: name={safe_name}, abs_path={abs_path}, _sent_images记录数={len(self._sent_images)}")

            # 查找该图片的发送记录（按完整路径）
            records = self._sent_images.get(abs_path, [])
            if not records:
                # 尝试按文件名匹配
                for img_path, recs in self._sent_images.items():
                    if Path(img_path).name == safe_name:
                        records.extend(recs)
                        break

            if not records:
                return web.json_response({"ok": False, "error": "未找到发送记录。\n原因：AstrBot 的 send_message 未返回 message_id，无法执行撤回。\n如需此功能，需要改用 OneBot API 直接发送消息。"})

            # 尝试撤回每条记录
            recalled_count = 0
            errors = []
            for rec in records:
                msg_id = rec.get('message_id', '')
                umo = rec.get('umo', '')
                if not msg_id:
                    continue
                try:
                    # 优先使用存储的 bot 引用（从 event.bot 获取的 aiocqhttp 客户端）
                    if self._bot_ref and hasattr(self._bot_ref, 'delete_msg'):
                        await self._bot_ref.delete_msg(message_id=int(msg_id))
                        recalled_count += 1
                    elif hasattr(self.context, 'bot') and self.context.bot:
                        bot = self.context.bot
                        if hasattr(bot, 'delete_msg'):
                            await bot.delete_msg(message_id=int(msg_id))
                            recalled_count += 1
                        elif hasattr(bot, 'call_api'):
                            await bot.call_api('delete_msg', message_id=int(msg_id))
                            recalled_count += 1
                        else:
                            errors.append("bot 实例不支持 delete_msg")
                    else:
                        errors.append("无可用撤回 API")
                except Exception as e:
                    errors.append(f"撤回消息 {msg_id}: {e}")
                    logger.warning(f"[ComfyUI] 撤回消息失败 {msg_id}: {e}")

            if recalled_count > 0:
                self._sent_images.pop(abs_path, None)
                return web.json_response({"ok": True, "recalled": recalled_count, "errors": errors if errors else None})
            else:
                err_msg = errors[0] if errors else "无法撤回"
                return web.json_response({"ok": False, "error": err_msg})
        except Exception as e:
            logger.error(f"[ComfyUI] 撤回画廊图片失败: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    async def terminate(self):
        logger.info("[ComfyUI] 插件已卸载")
        async with self._task_lock:
            self.task_map.clear()
            self._cancelled_pids.clear()
            self._progress.clear()
            self._prompt_progress.clear()
            self._prompt_node_count.clear()
