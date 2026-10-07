import json
import traceback
import uuid
import asyncio
import aiohttp
import re
import os
import shutil
import time
import hashlib
import base64
from pathlib import Path
from datetime import datetime
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.event.filter import CustomFilter
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, FunctionTool
from astrbot.api.provider import ProviderRequest
from astrbot.api.message_components import Image as AstrImage, At, Plain
from aiohttp import web
from dataclasses import dataclass, field
from .core.anima_data import AnimaDataManager, load_anima_tools_source, _is_anima_source, _ANIMA_SOURCE_NAMES
# v4.3.0 数据分离：大文件（缓存/JS回退源/用户配置）外移到 AstrBot/data/comfyui_allinone_data/
from .core import data_paths
from .core.data_paths import (data_dir_resolver, user_data_dir_resolver,
                              migrate_out as _data_migrate_out, migration_status as _data_migration_status)
from .core.grimoire import GrimoireMixin
from .core.llm_tools import (
    ComfyUITaskError, LarkLooseCommandFilter, LLMToolsMixin,
    ComfyUIDrawTool, ComfyUIListWorkflowsTool, ComfyUISwitchWorkflowTool,
    ComfyUIGetCurrentWorkflowTool, ComfyUIImg2ImgTool, ComfyUIVideoTool,
    ComfyUIRandomTool, ComfyUIListStarsTool, ComfyUIListPresetsTool,
    ComfyUIDeletePresetTool, ComfyUIQueueTool, ComfyUIStopTool,
    ComfyUIExecuteTool, ComfyUIRandomImageTool,
)
from .core.workflow import WorkflowMixin
from .core.generate import GenerateMixin
from .core.webui_server import WebUIMixin


@register("astrbot_plugin_comfyui_local", "BLack_Rin_ROBOT", "连接本地ComfyUI生成图片", "1.0.0")
class ComfyUILocalPlugin(WorkflowMixin, GenerateMixin, WebUIMixin, GrimoireMixin, LLMToolsMixin, Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context, config)
        if config is not None: self.config = config

        # ── 统一用户数据目录 data/user/（必须最先初始化，后续方法依赖它） ──
        # v4.3.0 数据分离：优先外部 comfyui_allinone_data/user/（插件目录瘦身，
        # 满足商店 <18MB）；首次启动自动把插件内旧数据迁移出去；失败回退插件内路径
        _plugin_root = Path(__file__).resolve().parent
        try:
            _mig = _data_migrate_out()
            if _mig.get("moved"):
                logger.info(f"[ComfyUI] 数据分离迁移完成: {_mig['moved']}")
            if _mig.get("errors"):
                logger.warning(f"[ComfyUI] 数据分离迁移部分失败(回退旧路径): {_mig['errors']}")
        except Exception as _mige:
            logger.warning(f"[ComfyUI] 数据分离迁移异常(回退旧路径): {_mige}")
        self._user_data_dir = user_data_dir_resolver()
        self._user_data_dir.mkdir(parents=True, exist_ok=True)

        local_cfg = self._load_local_config()
        self.comfyui_url = local_cfg.get("comfyui_url") or self.config.get("comfyui_url", "127.0.0.1:8188")
        # 目录配置：本地配置存在但键不存在/为空时，不fallback到config默认值，保持为空让前端显示占位符
        out = local_cfg.get("output_dir")
        self.output_dir = Path(out) if out else Path()
        self.webui_port = int(local_cfg.get("webui_port") or self.config.get("webui_port", 8898))
        self.webui_lan = bool(local_cfg.get("webui_lan", False) or self.config.get("webui_lan", False) or False)
        self.webui_ipv6 = bool(local_cfg.get("webui_ipv6", False) or self.config.get("webui_ipv6", False) or False)
        self.upload_dir = self.output_dir / "upload" if self.output_dir.parts else Path()
        if self.output_dir.parts:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.workflow_path = ""
        self.workflow_dir = Path()
        self.current_workflow_name = ""
        self.workflow_list_cache = []
        self._context_workflows = {}
        self._config_lock = asyncio.Lock()   # 保护 workflow_config 的并发写入
        self._ctx_lock = asyncio.Lock()      # 保护 _context_workflows 的并发写入
        import threading as _wf_threading
        self._wf_file_lock = _wf_threading.Lock()  # 保护工作流 JSON 文件并发写（原子写入防损坏）
        # 跨方法锁：保护 data/user/config.json 不因 _save_local_config（同步）与
        # _save_workflow_config（异步）并发写而互相覆盖
        self._config_file_lock = _wf_threading.Lock()
        self.workflow_config = {}
        # Lora 元数据缓存：从 ComfyUI LoRA Manager 获取（含触发词、预览图、标签等）
        self._lora_metadata_cache = {}      # key: lora 全路径(含子目录), value: {trigger_words, preview_url, model_name, tags, file_path}
        self._lora_metadata_fetched = False
        self._object_info_cache = {}        # class_type -> {key: {options/min/max/step/default}}（ComfyUI /object_info，TTL 300s）
        self._object_info_ts = 0
        # Lora 预览图内存缓存：避免每次请求都穿透到 ComfyUI
        self._lora_preview_cache = {}        # key: lora_name, value: {body, content_type}
        self._style_preview_cache = {}       # key: easy-use 预览图 URL, value: {body, content_type}

        # 迁移旧版配置 → data/user/config.json
        old_config = _plugin_root / "plugin_config.json"
        new_config = self._user_data_dir / "config.json"
        if old_config.exists() and not new_config.exists():
            try:
                old_config.rename(new_config)
                logger.info(f"[ComfyUI] 迁移配置: {old_config} → {new_config}")
            except Exception as e:
                logger.warning(f"[ComfyUI] 配置迁移失败: {e}")

        config_path = new_config if new_config.exists() else old_config
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
        self._prompt_start_time = {}  # prompt_id -> float timestamp
        # 魔导书开关（启用后 LLM 画图强制先搜数据库）
        self.grimoire_enabled = self.workflow_config.get('__grimoire_enabled__', False)
        # 魔导书图片缓存目录
        self._pending_grimoire_tasks = 0  # /随机图 尚未提交给 ComfyUI 的任务数
        self._grimoire_cache_dir = self._user_data_dir / "cache"
        self._grimoire_cache_dir.mkdir(parents=True, exist_ok=True)
        # 缓存任务进度 {source: {"running": bool, "total": int, "done": int, "success": int, "failed": int, "message": str}}
        self._cache_progress = {}
        self._ws_client_id = str(uuid.uuid4())  # 固定 client_id，WS 监听器和 prompt 提交使用同一个
        self.current_prompt_id = None
        self.pending_actions = {}  # {user_id: {"action": str, "data": dict, "expires_at": float}}
        # 已发送图片记录 {abs_path: [{"message_id": str, "umo": str, "sent_at": float}, ...]}
        self._sent_images = {}
        self._sent_images_lock = asyncio.Lock()
        # 提示词记录（引用图片查提示词用）
        self._prompt_log = []
        self._prompt_log_lock = asyncio.Lock()
        # 跨线程保护 prompt_log.json 写文件：_backfill_prompt_log_dhash 是 threading 线程，
        # 无法用 asyncio.Lock，追加（async）与补全（threading）并发写会互相覆盖
        import threading as _pl_threading
        self._prompt_log_file_lock = _pl_threading.Lock()
        self._prompt_log_path = self._user_data_dir / "prompt_log.json"
        self._prompt_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.prompt_log_days = int(self.config.get("prompt_log_days", 3))
        # 输出目录保留天数（与 prompt_log_days 同一配置链，可在 AstrBot 管理界面/插件配置设置）
        self.output_keep_days = int(self.config.get("output_keep_days", 7))
        if self._prompt_log_path.exists():
            try:
                with open(str(self._prompt_log_path), 'r', encoding='utf-8') as f:
                    self._prompt_log = json.load(f)
                self._trim_prompt_log()
            except Exception as e:
                logger.warning(f"[ComfyUI] 读取提示词记录失败: {e}")
                self._prompt_log = []
        # 为 dHash 功能上线前的旧记录补算 img_dhash（后台线程，不阻塞启动；
        # __init__ 阶段可能无 running event loop，用 threading 保证必然执行）
        try:
            import threading as _threading
            _threading.Thread(target=self._backfill_prompt_log_dhash, daemon=True).start()
        except Exception:
            pass
        # 扩写后提示词缓存 {文件路径: 扩写文本}，由 _process_and_submit 写入，_send_image_result 消费
        self._expanded_prompt_cache: dict[str, str] = {}
        # v4.9.6: 缓存写入时间戳——与 prompt_log 同一保留天数（prompt_log_days）清理，
        # 附带"源文件已被输出清理删除"的条目也一并回收
        self._expanded_prompt_cache_ts: dict[str, float] = {}
        self._gallery_md5_cache: dict = {}  # (path, mtime) -> md5，画廊查提示词用
        self._gallery_scan_cache = (0, [])  # (扫描时间, 全量列表)——分页画廊的 30s 扫描缓存
        # 最近图片缓存：{umo: (timestamp, [urls])} —— 解决飞书「发图后再发命令」拿不到图的问题
        # （飞书图片是独立消息，命令那条消息没有图片组件；QQ 可同条/紧邻发送所以不受影响）
        self._recent_images: dict[str, tuple] = {}
        self._recent_images_ttl = 3600   # 缓存有效期 1 小时（v4.7.2 起缓存本地副本，不再受平台 URL 时效限制）
        # OneBot bot 引用（从 event.bot 获取，供撤回使用）
        self._bot_ref = None
        # 黑名单群组缓存（从 persona_switcher 读取）
        self._blocked_groups_cache = []
        self._blocked_groups_checked = 0
        # 质量预设（像素密度等级，非固定分辨率；实际宽高由比例动态计算）
        self.quality_presets = {
            "480p": {"name": "SD", "pixels": 399_360},
            "720p": {"name": "标清", "pixels": 921_600},
            "960p": {"name": "高清+", "pixels": 1_638_400},  # 短边≈960（9:16/16:9 下 960/1707）；介于 720p 与 1080p 之间
            "1080p": {"name": "高清", "pixels": 2_073_600},
            "2K": {"name": "超清", "pixels": 3_686_400},
            "4K": {"name": "原画", "pixels": 8_294_400},
        }
        self.default_quality = local_cfg.get("default_quality") or "720p"
        # 图片消息是否附带提示词
        self.show_prompt_on_image = bool(local_cfg.get("show_prompt_on_image", False))
        # 生成结果发送平台: auto=自动识别来源平台(默认) / qq=强制QQ(OneBot) / feishu=强制飞书 / both=两边都发
        self.send_platform = str(local_cfg.get("send_platform", "auto") or "auto").lower()
        if self.send_platform not in ("auto", "qq", "feishu", "both"):
            self.send_platform = "auto"
        # WebUI「生成」按钮的主动推送目标: 平台 + 该平台的ID(QQ号/群号/飞书open_id)
        self.target_platform = str(local_cfg.get("target_platform", "qq") or "qq").lower()
        if self.target_platform not in ("qq", "feishu"):
            self.target_platform = "qq"
        # 兼容旧配置: target_id 为空时回退到历史字段 target_qq
        self.target_id = str(local_cfg.get("target_id", "") or local_cfg.get("target_qq", "") or "").strip()
        # 随机图抽取模式
        rp_mode = local_cfg.get("random_pick_mode", "all") or self.workflow_config.get("random_pick_mode", "all")
        self.workflow_config["random_pick_mode"] = rp_mode
        # 启动时同步：设置面板 k2_compose_mode → __prompt_model__（修复历史遗留不一致）
        # 仅设内存值，后续任意 _save_workflow_config 调用会自然落盘
        k2_mode = local_cfg.get("k2_compose_mode", "anima")
        if k2_mode != self.workflow_config.get("__prompt_model__", "anima"):
            self.workflow_config["__prompt_model__"] = k2_mode
        # 比例列表（纯字符串，宽高由质量动态计算）
        self.aspect_ratios = ["1:1", "3:4", "4:3", "9:16", "16:9", "21:9", "2:3", "3:2"]
        # ComfyUI 官方 ResolutionSelector 节点：插件比例 → 官方选项
        self.official_ratio_map = {
            "1:1": "1:1 (Square)",
            "2:3": "2:3 (Portrait Photo)",
            "3:2": "3:2 (Photo)",
            "3:4": "3:4 (Portrait Standard)",
            "4:3": "4:3 (Standard)",
            "9:16": "9:16 (Portrait Widescreen)",
            "16:9": "16:9 (Widescreen)",
            "21:9": "21:9 (Ultrawide)",
        }
        # 官方选项 → 插件比例（反查）
        self.official_ratio_reverse = {v: k for k, v in self.official_ratio_map.items()}
        self.default_ratio = local_cfg.get("default_ratio") or self.config.get("default_ratio", "9:16")
        # 默认宽高由质量和比例动态计算
        self.default_width, self.default_height = self._calc_resolution(self.default_quality, self.default_ratio)
        self._start_webui()
        wdir = self._get_workflow_dir(); wdir.mkdir(parents=True, exist_ok=True)
        self._refresh_workflow_list()
        # 加载 Anima 数据（v4.3.0：词库目录经 data_dir_resolver 解析，当前恒为插件内 data/）
        data_dir = data_dir_resolver()
        self.anima_data = AnimaDataManager(
            str(data_dir), str(self._user_data_dir),
            artist_limit=int(self.config.get("anima_artist_limit", 0) or 0),
            character_limit=int(self.config.get("anima_character_limit", 0) or 0),
        )
        self.anima_data.load_all()
        self._register_tools()
        # 启动时自动修复 lora 路径（解决用户保存的 lora_name 与实际路径不一致的问题）
        # AstrBot 的 __init__ 在事件循环中执行，使用 get_running_loop
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._auto_fix_lora_paths_on_startup())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            loop.create_task(self._auto_fix_lora_paths_on_startup())
        # 创建后台任务。AstrBot 的 __init__ 在事件循环中执行，使用 get_running_loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        # 保存句柄，terminate() 时统一 cancel，避免插件卸载后循环继续空转
        self._background_tasks = [
            loop.create_task(self._cleanup_upload_loop()),
            loop.create_task(self._cleanup_output_loop()),
            loop.create_task(self._ws_progress_listener()),
        ]
        lan = f"，局域网 http://你的IP:{self.webui_port}" if self.webui_lan else ""
        ipv6 = f"，IPv6 http://[你的IPv6]:{self.webui_port}" if self.webui_ipv6 else ""
        logger.info(f"[ComfyUI] WebUI: http://127.0.0.1:{self.webui_port}{lan}{ipv6} | 工作流目录: {wdir}")

    def _load_local_config(self):
        p = self._user_data_dir / "config.json"
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
        p = self._user_data_dir / "config.json"
        tmp_p = p.with_suffix('.json.tmp')
        with self._config_file_lock:
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
                # 同步更新内存中的 self.workflow_config
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
                     ComfyUIImg2ImgTool(), ComfyUIVideoTool(), ComfyUIRandomTool(),
                     ComfyUIQueueTool(), ComfyUIStopTool(), ComfyUIExecuteTool(), ComfyUIRandomImageTool(),
                     ComfyUIListStarsTool(), ComfyUIListPresetsTool(), ComfyUIDeletePresetTool()]
            self._tool_objs = tools
            for t in tools:
                t._plugin = self
                self.context.add_llm_tools(t)
            self._apply_llm_templates()
            # 诊断：打印注册后的 func_list 内容
            mgr = self.context.provider_manager.llm_tools
            names = [ft.name for ft in mgr.func_list]
            logger.info(f"[ComfyUI] 已注册 {len(tools)} 个 LLM 工具")
            logger.info(f"[ComfyUI] func_list 中的工具: {names}")
        except Exception as e:
            logger.warning(f"[ComfyUI] LLM工具注册失败: {e}")

    def _trim_prompt_log(self):
        """清理超过保留天数的提示词记录"""
        if self.prompt_log_days <= 0:
            return
        cutoff = time.time() - self.prompt_log_days * 86400
        before = len(self._prompt_log)
        self._prompt_log = [r for r in self._prompt_log if r['timestamp'] > cutoff]
        if len(self._prompt_log) < before:
            logger.info(f"[ComfyUI] 提示词记录清理: {before} -> {len(self._prompt_log)} 条")

    async def _append_prompt_log(self, message_id, prompt, img_hash='', img_dhash='', path=''):
        """追加一条提示词记录到日志并写文件（img_hash=内容MD5，img_dhash=感知哈希，
        供重新保存/重编码图片后按内容或感知相似度查询兜底；path=本地文件路径，
        v4.9.6: 飞书发送拿不到 message_id 也照记（message_id 留空），配合
        /提示词 的「会话最近发送图」兜底，修复飞书查询永远失效的问题。"""
        async with self._prompt_log_lock:
            # v4.10.0: 同 path 幂等——提交时先记一次，发送时带 msg_id 更新，不重复堆积
            _p = str(path or '')
            if _p:
                self._prompt_log = [r for r in self._prompt_log if r.get('path') != _p]
            self._prompt_log.append({
                "message_id": str(message_id),
                "prompt": prompt,
                "timestamp": time.time(),
                "img_hash": img_hash or '',
                "img_dhash": img_dhash or '',
                "path": str(path or '')
            })
            self._trim_prompt_log()
            try:
                # 用跨线程锁写文件（与 _backfill_prompt_log_dhash 的补全线程互斥）
                with self._prompt_log_file_lock:
                    with open(str(self._prompt_log_path), 'w', encoding='utf-8') as f:
                        json.dump(self._prompt_log, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning(f"[ComfyUI] 写入提示词记录失败: {e}")

    async def _send_image_result(self, event, text, paths, prompt=''):
        """发图。支持 paths 列表（多图）或单张路径，最多10张。群消息会附带 At @mention，返回 True/False"""
        if isinstance(paths, str):
            paths = [paths]
        if not paths:
            return False
        paths = paths[:10]
        try:
            umo = getattr(event, 'unified_msg_origin', None)
            if not umo:
                return False

            # 如果开启了图片附带提示词，在文本末尾追加最终提示词
            if self.show_prompt_on_image and paths:
                abs_path = str(Path(paths[0]).resolve())
                _cache_val = self._expanded_prompt_cache.get(abs_path, '')
                if not _cache_val:
                    # 键不精确匹配时按文件名兜底（路径形式差异：软链/相对路径/下载路径不一致）
                    try:
                        _fname = Path(abs_path).name
                        for _k, _v in list(self._expanded_prompt_cache.items()):
                            if _k and Path(_k).name == _fname:
                                _cache_val = _v
                                logger.info(f"[ComfyUI] 提示词缓存按文件名兜底命中: {_fname}")
                                break
                    except Exception:
                        pass
                final_prompt = _cache_val or prompt
                logger.info(f"[ComfyUI] 附带提示词检查: 开关={self.show_prompt_on_image} "
                            f"缓存命中={bool(_cache_val)} 传入prompt={bool(prompt)} "
                            f"缓存键数={len(self._expanded_prompt_cache)} key={abs_path[-60:]}")
                if final_prompt:
                    text = f"{text}\n{final_prompt}"
                else:
                    logger.warning("[ComfyUI] 无可用提示词（缓存与传入均为空），消息将不含提示词")

            # 去掉 text 中可能残留的 [CQ:at,...] 前缀
            clean_text = text
            if hasattr(event, 'bot') or 'CQ:' in text:
                clean_text = re.sub(r'\[CQ:at[^\]]*\]', '', text).strip()

            abs_path = str(Path(paths[0]).resolve())
            msg_id = ''
            _onebot_ok = False
            _feishu_ok = False

            # 发送渠道决策：按配置(send_platform) + 来源平台决定走哪些渠道
            _targets = self._resolve_send_targets(event)
            _src_platform = self._detect_event_platform(event)
            logger.info(f"[ComfyUI] 发送渠道: 配置={getattr(self,'send_platform','auto')} 来源={_src_platform} → 目标={sorted(_targets)}")

            # 通过 event.bot 直接调用 OneBot API（可获取 message_id）—— 仅在需要发 QQ 时执行
            bot = getattr(event, 'bot', None)
            if bot and 'qq' in _targets:
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
                        # 构建多图消息：文字 + 多张图片
                        cq_text = clean_text
                        for p in paths:
                            is_video = Path(p).suffix.lower() in {'.mp4', '.mov', '.avi', '.gif'}
                            cq_type = 'video' if is_video else 'image'
                            cq_text += f"\n[CQ:{cq_type},file={p}]"
                        txt_result = await getattr(bot, method_name)(**{params_key: int(target_id), 'message': cq_text})
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
                    # 双号对等架构兜底：多 bot 同时在线时 UnifiedApi 无法路由（ApiNotAvailable），
                    # 按连接直发——优先消息来源号的连接，失败再试其他在线号。
                    try:
                        raw = getattr(getattr(event, 'message_obj', None), 'raw_message', None) or {}
                        src_sid = str(raw.get('self_id') or '')
                        clients = self._get_onebot_ws_clients()
                        order = ([src_sid] if src_sid in clients else []) + [s for s in clients if s != src_sid]
                        for csid in order:
                            try:
                                r = await self._onebot_send_via_ws(
                                    clients[csid], method_name,
                                    **{params_key: int(target_id), 'message': cq_text})
                                if isinstance(r, dict):
                                    msg_id = str(r.get('message_id', ''))
                                elif isinstance(r, (int, str)):
                                    msg_id = str(r)
                                if msg_id:
                                    _onebot_ok = True
                                    logger.info(f"[ComfyUI] 直发成功 via {csid}，msg_id={msg_id}")
                                    break
                            except Exception as e2:
                                logger.warning(f"[ComfyUI] 连接 {csid} 直发失败: {type(e2).__name__}: {e2}")
                    except Exception as e3:
                        logger.warning(f"[ComfyUI] 双号兜底枚举失败: {type(e3).__name__}: {e3}")
            else:
                logger.info(f"[ComfyUI] 跳过 OneBot 直连(来源={_src_platform}, 目标={sorted(_targets)})")

            # 走 AstrBot 标准消息链的场景：
            #   1) 目标是飞书（QQ 的 CQ 码在飞书不可用，必须走标准链，适配器会做素材上传）
            #   2) 需要发 QQ 但 OneBot 直连失败/不可用（保留原有兜底行为）
            if ('feishu' in _targets) or (not _onebot_ok):
                # 飞书分支：适配器对「文字+图片」混合链会把文字吞掉，改为分两条发送
                _is_feishu = 'feishu' in _targets
                if _is_feishu and clean_text:
                    try:
                        await self.context.send_message(umo, MessageChain(chain=[Plain(text=clean_text)]))
                        await asyncio.sleep(0.8)
                        logger.info(f"[ComfyUI] 飞书文字已单独发送: {clean_text[:60]}")
                    except Exception as e:
                        logger.warning(f"[ComfyUI] 飞书文字发送失败: {type(e).__name__}: {e}")
                parts = []
                if not event.is_private_chat() and not _is_feishu:
                    sender_id = event.get_sender_id()
                    parts.append(At(qq=sender_id))
                if clean_text and not _is_feishu:
                    parts.append(Plain(text=clean_text))
                for p in paths:
                    # 飞书分支：超 10MB 的图先压缩（未超限不动原图）
                    _pp = str(Path(p).resolve())
                    if _is_feishu:
                        _shr = self._shrink_for_feishu(_pp)
                        if not _shr:
                            logger.warning(f"[ComfyUI] {Path(_pp).name} 超飞书限制且压缩失败，跳过")
                            continue
                        _pp = _shr
                    parts.append(AstrImage(file=_pp))
                chain = MessageChain(chain=parts)
                result = await self.context.send_message(umo, chain)
                _feishu_ok = 'feishu' in _targets
                logger.info(f"[ComfyUI] 标准链发送完成(目标={sorted(_targets)}): {str(result)[:200]}")
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
                first_abs = str(Path(paths[0]).resolve())
                if msg_id:
                    logger.info(f"[ComfyUI] 记录发送图片: {first_abs} -> msg_id={msg_id}")
                else:
                    logger.info(f"[ComfyUI] 未获取到 message_id，撤回将不可用")
                async with self._sent_images_lock:
                    record = {"message_id": msg_id, "umo": umo, "sent_at": time.time()}
                    if first_abs not in self._sent_images:
                        self._sent_images[first_abs] = []
                    self._sent_images[first_abs].append(record)
                    if len(self._sent_images[first_abs]) > 20:
                        self._sent_images[first_abs] = self._sent_images[first_abs][-20:]
            except Exception as e:
                logger.debug(f"[ComfyUI] 记录发送图片失败: {e}")

            # 记录提示词到 prompt_log（引用图片查提示词用）
            # v4.9.6: 不再以 msg_id 为门槛——飞书发送拿不到 message_id，此前因此
            # 完全不记录，导致飞书里 /提示词 永远查不到；现在照记（message_id 留空，
            # 靠 path/img_hash/img_dhash 匹配），QQ 平台行为不变
            final_prompt = self._expanded_prompt_cache.pop(first_abs, '') or prompt
            if final_prompt:
                try:
                    img_hash = self._calc_file_md5(first_abs)
                    img_dhash = self._calc_image_dhash(first_abs)
                    await self._append_prompt_log(msg_id, final_prompt, img_hash, img_dhash, path=first_abs)
                except Exception as e:
                    logger.debug(f"[ComfyUI] 记录提示词失败: {e}")

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
        # 黑名单检查：封锁群中非管理员禁止使用所有命令
        if self._check_group_blocked(event):
            event.stop_event()
            return
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

    def _check_group_blocked(self, event) -> bool:
        """检查群是否被 persona_switcher 加入黑名单。如果被封锁且用户不是管理员，返回 True。"""
        group_id = event.get_group_id() if hasattr(event, 'get_group_id') else None
        if not group_id:
            return False
        gid = str(group_id)
        # 每 60 秒重新读取一次黑名单
        import time
        now = time.time()
        if now - self._blocked_groups_checked > 60:
            self._blocked_groups_checked = now
            try:
                # 优先固定目录名，找不到时自动发现插件目录下的 blocked_groups.json
                # （避免插件改名/重命名后硬编码路径失效）
                plugins_dir = Path(__file__).resolve().parent.parent
                bp = plugins_dir / "astrbot_plugin_persona_switcher" / "blocked_groups.json"
                if not bp.exists():
                    for cand in sorted(plugins_dir.glob("*/blocked_groups.json")):
                        bp = cand
                        break
                if bp.exists():
                    with open(str(bp), 'r', encoding='utf-8') as f:
                        self._blocked_groups_cache = json.load(f) or []
                else:
                    self._blocked_groups_cache = []
            except Exception:
                self._blocked_groups_cache = []
        if gid in self._blocked_groups_cache:
            # 检查是否是管理员
            try:
                perm = event.get_permission()
                if perm in (filter.PermissionType.ADMIN, filter.PermissionType.SUPER_USER):
                    return False  # 管理员放行
            except Exception:
                pass
            return True
        return False

    async def _ensure_command_workflow(self, event, cmd_name):
        """根据 __wf_categories__ 分类确保当前工作流允许该命令执行。

        分类名与命令名一致（如 文生图、图生图、视频），只有分类匹配的工作流才能用该命令。
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

    def _pref_bot_order(self):
        """双号推送优先级：排前面的号优先用于主动推送/兜底回复。
        从 local_config 读 push_bot_order（列表），缺省按用户使用习惯：204757347 为主、2971882619 备用。"""
        try:
            order = self._load_local_config().get("push_bot_order")
            if isinstance(order, list) and order:
                return [str(x) for x in order]
        except Exception:
            pass
        return ["204757347", "2971882619"]

    def _sort_clients_by_pref(self, clients):
        """把 {self_id: ws} 按推送优先级排序（优先号在前，未列入优先级的排后保持原序）。"""
        order = self._pref_bot_order()
        def key(item):
            sid = str(item[0])
            try:
                return order.index(sid)
            except ValueError:
                return len(order)
        return sorted(clients.items(), key=key)

    def _get_onebot_ws_clients(self):
        """枚举 aiocqhttp 反向 WS 的在线 bot 连接 {self_id(str): ws}。
        双号对等架构下任一号在线都可发送；两号同时在线时优先选 _bot_ref 对应的号。失败返回 {}。"""
        try:
            for inst in self.context.platform_manager.get_insts():
                try:
                    meta = inst.meta()
                    # PlatformMetadata 的类型字段是 name（"aiocqhttp"），不是 type
                    if getattr(meta, 'name', '') != 'aiocqhttp':
                        continue
                except Exception:
                    continue
                bot = getattr(inst, 'bot', None)
                if bot is None and hasattr(inst, 'get_client'):
                    try:
                        bot = inst.get_client()
                    except Exception:
                        bot = None
                clients = getattr(bot, '_wsr_api_clients', None)
                if isinstance(clients, dict) and clients:
                    return {str(k): v for k, v in clients.items()}
        except Exception as e:
            logger.debug(f"[ComfyUI] 枚举 OneBot 在线连接失败: {e}")
        return {}

    async def _onebot_send_via_ws(self, ws, action, **params):
        """直接向指定反向 WS 连接发送 OneBot action 并等待 echo 结果。
        多 bot 同时在线时 UnifiedApi.call_action 会因无法路由而抛 ApiNotAvailable，
        因此绕过它按连接直发（复用 aiocqhttp 的 seq/echo 机制）。"""
        import json as _json
        from aiocqhttp.api_impl import ResultStore, _SequenceGenerator
        seq = _SequenceGenerator.next()
        await ws.send(_json.dumps({'action': action, 'params': params, 'echo': {'seq': seq}}))
        return await ResultStore.fetch(seq, 120)

    async def _send_to_target(self, target_platform, target_id, paths, prompt, group=False):
        """统一主动发送入口：按平台分发到 QQ(OneBot) 或 飞书(标准消息链)。
        target_platform: 'qq' | 'feishu'
        target_id: QQ号/QQ群号 或 飞书 open_id(ou_xxx) / chat_id(oc_xxx)
        group: QQ 群号时为 True（飞书按 id 前缀自动判断群聊）
        """
        tp = str(target_platform or 'qq').lower()
        tid = str(target_id or '').strip()
        if not tid:
            return False
        # v4.9.5: 尊重「图片附带提示词」开关——关闭时主动推送不带提示词文本。
        # 此前飞书/QQ 发送器无条件把 prompt 拼进「✨ 生成完成」，开关形同虚设
        #（聊天路径 _send_image_result 一直有闸门，只有 WebUI 推送这条路漏了）。
        if not self.show_prompt_on_image:
            prompt = ''
        sent = False
        if tp == 'feishu':
            sent = await self._send_image_to_feishu(tid, paths, prompt, group=group)
        else:
            sent = await self._send_image_to_qq(tid, paths, prompt, group=group)
        # v4.9.6: 记录发送（/提示词 的「会话最近图」兜底数据源；WebUI 推送此前零记录）
        try:
            if sent and paths:
                if tp == 'feishu':
                    _umo = f"{self._get_lark_platform_id() or 'lark-main'}:{'GroupMessage' if tid.startswith('oc_') else 'FriendMessage'}:{tid}"
                else:
                    _umo = f"default:{'GroupMessage' if group else 'FriendMessage'}:{tid}"
                _p0 = str(Path(paths[0]).resolve())
                async with self._sent_images_lock:
                    self._sent_images.setdefault(_p0, []).append(
                        {"message_id": "", "umo": _umo, "sent_at": time.time()})
                    if len(self._sent_images[_p0]) > 20:
                        self._sent_images[_p0] = self._sent_images[_p0][-20:]
        except Exception as e:
            logger.debug(f"[ComfyUI] 记录推送图片失败: {e}")
        return sent

    async def _send_image_to_feishu(self, target_id, paths, prompt, group=False):
        """主动发送生成结果到飞书（走 AstrBot 标准消息链，适配器负责素材上传）。
        target_id: ou_xxx(用户 open_id) / oc_xxx(群 chat_id) / 或直接给消息类型前缀。
        UMO 形如 "lark-main:FriendMessage:ou_xxx" 或 "lark-main:GroupMessage:oc_xxx"。"""
        try:
            from astrbot.api.event import MessageChain
            from astrbot.api.message_components import Image as AstrImage, Plain, Video, File, Record
            # 飞书平台实例 id：从 cmd_config.json 里找 type == 'lark' 的 id
            lark_pid = self._get_lark_platform_id() or "lark-main"
            # 消息类型以 ID 前缀为准（飞书 ID 是强类型的，用错会报 230001 invalid receive_id）：
            #   oc_xxx = 群聊 chat_id → GroupMessage
            #   ou_xxx = 用户 open_id → FriendMessage
            # 前缀无法判断时才回退到配置里的「目标是群聊」勾选
            tid = str(target_id).strip()
            if tid.startswith("oc_"):
                is_group = True
            elif tid.startswith("ou_"):
                is_group = False
            else:
                is_group = bool(group)
            mtype = "GroupMessage" if is_group else "FriendMessage"
            umo = f"{lark_pid}:{mtype}:{tid}"
            logger.info(f"[ComfyUI] 飞书发送类型判定: id={tid[:12]}... → {mtype} (配置group={group})")
            chain = MessageChain()
            _text = f"✨ 生成完成" + (f": {prompt[:2000]}" if prompt else "")
            # 飞书：适配器对「文字+图片」混合链会把文字吞掉 → 文字单独先发
            _is_feishu_active = is_group or str(target_id).startswith("ou_") or str(target_id).startswith("oc_")
            if _is_feishu_active:
                try:
                    await self.context.send_message(umo, MessageChain(chain=[Plain(text=_text)]))
                    await asyncio.sleep(0.8)
                    logger.info(f"[ComfyUI] 飞书(主动推送)文字已单独发送: {_text[:60]}")
                except Exception as e:
                    logger.warning(f"[ComfyUI] 飞书(主动推送)文字发送失败: {type(e).__name__}: {e}")
            else:
                chain.message(_text)
            sent_any = False
            for p in paths[:10]:
                if not Path(p).exists():
                    continue
                try:
                    # ★ 平台兼容：飞书分支用「普通路径」构造消息组件。
                    # 不能用 fromFileSystem()——它返回 file:// URI，
                    # 飞书适配器的 MediaResolver 解析不了 → 图片被静默跳过
                    # （日志报"无法打开或上传图片文件"但 send_message 不抛异常，仍显示 True）
                    _p = str(Path(p).resolve())
                    _ext2 = Path(_p).suffix.lower()
                    if _ext2 in ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'):
                        # 飞书图片限制 10MB → 超限自动压缩（未超限不动）
                        _shrunk = self._shrink_for_feishu(_p)
                        if not _shrunk:
                            logger.warning(f"[ComfyUI] {Path(_p).name} 超飞书限制且压缩失败，跳过该图")
                            continue
                        chain.chain.append(AstrImage(file=_shrunk))
                    elif _ext2 in ('.mp4', '.webm', '.mov', '.avi', '.mkv'):
                        # v4.8.0: 飞书视频限 ~10MB，超限 ffmpeg 压缩后再发（耗时操作放线程）
                        _vp = await asyncio.to_thread(self._shrink_video_for_feishu, _p)
                        if not _vp:
                            logger.warning(f"[ComfyUI] {Path(_p).name} 超飞书限制且压缩失败，跳过该视频")
                            continue
                        chain.chain.append(Video(file=_vp))
                    elif _ext2 in ('.wav', '.mp3', '.flac', '.ogg', '.m4a'):
                        chain.chain.append(Record(file=_p))
                    else:
                        # File 的 name 是必填位置参数，不能只传 file
                        chain.chain.append(File(name=Path(_p).name, file=_p))
                    sent_any = True
                except Exception as e:
                    logger.warning(f"[ComfyUI] 飞书追加 {Path(p).name} 失败: {e}")
            if not sent_any and not prompt:
                return False
            # 发送图片：网络波动/上行慢时飞书素材上传可能 WriteTimeout → 重试最多3次
            _last_err = None
            for _try in range(3):
                try:
                    r = await self.context.send_message(umo, chain)
                    logger.info(f"[ComfyUI] 已主动发送到飞书 {target_id} (umo={umo}, 第{_try+1}次): {str(r)[:150]}")
                    return True
                except Exception as e:
                    _last_err = e
                    _ename = type(e).__name__
                    logger.warning(f"[ComfyUI] 飞书发送第{_try+1}次失败({_ename}): {e}")
                    # WriteTimeout/Timeout 类错误才重试，其余直接放弃
                    if 'Timeout' not in _ename and 'timeout' not in str(e).lower():
                        break
                    await asyncio.sleep(2 + _try * 2)
            logger.error(f"[ComfyUI] 飞书发送最终失败(已重试): {type(_last_err).__name__}: {_last_err}")
            return False
        except Exception as e:
            logger.warning(f"[ComfyUI] 主动发送到飞书 {target_id} 失败: {type(e).__name__}: {e}")
            return False

    def _get_lark_platform_id(self):
        """读取 cmd_config.json 中 type=='lark' 的平台实例 id（飞书 UMO 前缀）。"""
        try:
            for ancestor in Path(self._user_data_dir).resolve().parents:
                cand = ancestor / "data" / "cmd_config.json"
                if cand.exists():
                    with open(str(cand), 'r', encoding='utf-8-sig') as f:
                        cfg = json.load(f)
                    for p in cfg.get("platform", []) or []:
                        if str(p.get("type", "")).lower() in ("lark", "feishu"):
                            return p.get("id") or None
                    return None
            cfg_path = Path("/root/AstrBot/data/cmd_config.json")
            if cfg_path.exists():
                with open(str(cfg_path), 'r', encoding='utf-8-sig') as f:
                    cfg = json.load(f)
                for p in cfg.get("platform", []) or []:
                    if str(p.get("type", "")).lower() in ("lark", "feishu"):
                        return p.get("id") or None
        except Exception as e:
            logger.debug(f"[ComfyUI] 读取飞书平台 id 失败: {e}")
        return None

    async def _send_image_to_qq(self, qq, paths, prompt, group=False):
        """主动私聊发送生成结果到指定 QQ（参考隧道插件 master_qq 推送方式）。
        umo 格式必须为 {platform_id}:{message_type.value}:{session_id}。
        platform_id 取 cmd_config.json 的 platform[].id（本机为 "default"），不是适配器 type；
        私聊 message_type.value = "FriendMessage"。
        按扩展名分类型发送：图片→image、视频→video、音频→voice/文件、其他→文件。
        优先走 event.bot 直连 OneBot API（与 QQ 命令链路一致）：CQ 码 file=本地路径，
        由 OneBot 客户端读取本地文件上传；回退 context.send_message（Image 组件会转 base64://，
        多张/大图时 base64 超大易被 QQ 判"图片已过期"）。"""
        try:
            from astrbot.api.event import MessageChain
            # platform_id 取 AstrBot 平台实例 id（cmd_config.json platform[].id），兜底 default
            platform_id = self._get_platform_id() or "default"
            mtype = "GroupMessage" if group else "FriendMessage"
            umo = f"{platform_id}:{mtype}:{qq}"
            # 方式一：OneBot 直连（推荐，图片完整不超时）。
            # 双号对等架构：先试 _bot_ref（最近收消息的号），失败/不在线则遍历所有在线连接兜底——
            # 只要任一号在线，推送就能发出。
            cq_text = (f"✨ 生成完成: {prompt[:2000]}" if prompt else "")
            for p in paths[:10]:
                if not Path(p).exists():
                    continue
                ext = Path(p).suffix.lower()
                is_video = ext in ('.mp4', '.webm', '.mov', '.avi', '.mkv', '.gif')
                cq_type = 'video' if is_video else 'image'
                cq_text += f"\n[CQ:{cq_type},file={p}]"

            clients = self._get_onebot_ws_clients()  # {self_id: ws}
            # 反向WS多号在线时 UnifiedApi 无法路由（len>1 抛 ApiNotAvailable），必须按连接直发。
            # 按优先级排序（主用号 204757347 优先）：任一号在线即可送达，互为哨兵。
            for sid, ws in self._sort_clients_by_pref(clients):
                try:
                    if group:
                        await self._onebot_send_via_ws(ws, 'send_group_msg', group_id=int(qq), message=cq_text)
                        logger.info(f"[ComfyUI] 已主动发送生成结果到 QQ群 {qq}（OneBot直连 via {sid}）")
                    else:
                        await self._onebot_send_via_ws(ws, 'send_private_msg', user_id=int(qq), message=cq_text)
                        logger.info(f"[ComfyUI] 已主动发送生成结果到 QQ {qq}（OneBot直连 via {sid}）")
                    return True
                except Exception as e:
                    logger.warning(f"[ComfyUI] OneBot 连接 {sid} 发送失败: {type(e).__name__}: {e}")
            if clients:
                logger.warning("[ComfyUI] 所有 OneBot 在线连接均发送失败，回退 context.send_message")
            else:
                logger.info("[ComfyUI] 无在线 OneBot 连接，回退 context.send_message")
            # 方式二：回退 context.send_message（chain.file_image → base64://）
            chain = MessageChain()
            if prompt:
                chain.message(f"✨ 生成完成: {prompt[:2000]}")
            from astrbot.api.message_components import Video, File, Record
            for p in paths[:10]:
                if not Path(p).exists():
                    continue
                ext = Path(p).suffix.lower()
                try:
                    if ext in ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'):
                        chain.file_image(str(p))
                    elif ext in ('.mp4', '.webm', '.mov', '.avi', '.mkv'):
                        chain.chain.append(Video.fromFileSystem(str(p)))
                    elif ext in ('.wav', '.mp3', '.flac', '.ogg', '.m4a'):
                        chain.chain.append(Record.fromFileSystem(str(p)))
                    else:
                        chain.chain.append(File.fromFileSystem(str(p)))
                except Exception as e:
                    logger.warning(f"[ComfyUI] 发送 {Path(p).name} 失败，改用文本提示: {e}")
                    chain.message(f" 文件已生成: {Path(p).name}")
            await self.context.send_message(umo, chain)
            logger.info(f"[ComfyUI] 已主动发送生成结果到 {'QQ群' if group else 'QQ'} {qq}")
            return True
        except Exception as e:
            logger.warning(f"[ComfyUI] 主动发送到 {qq} 失败: {e}")
            return False

    def _get_platform_id(self):
        """读取 AstrBot 平台实例 id（cmd_config.json platform[].id），找不到返回 None。
        路径解析：从插件目录逐级向上找 <root>/data/cmd_config.json（兼容
        data/plugins/<plugin>/data/user 结构，不再硬编码 /root/AstrBot）。"""
        try:
            cfg_path = None
            for ancestor in Path(self._user_data_dir).resolve().parents:
                cand = ancestor / "data" / "cmd_config.json"
                if cand.exists():
                    cfg_path = cand
                    break
            if cfg_path is None:
                cfg_path = Path("/root/AstrBot/data/cmd_config.json")
            if cfg_path.exists():
                with open(str(cfg_path), 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                for p in cfg.get("platform", []) or []:
                    if p.get("enable"):
                        return p.get("id") or None
        except Exception as e:
            logger.debug(f"[ComfyUI] 读取平台 id 失败: {e}")
        return None

    def _detect_event_platform(self, event):
        """识别消息来源平台：'qq' | 'feishu' | 其它适配器 type。
        优先读 event 的平台元信息，失败则按 UMO 前缀兜底。"""
        # 1) 平台元信息里的适配器类型（aiocqhttp / lark ...）
        try:
            pm = None
            for attr in ("platform_meta", "platform"):
                pm = getattr(event, attr, None)
                if pm is not None:
                    break
            if pm is None:
                pm = getattr(event, "platform_metadata", None)
            name = ""
            if pm is not None:
                name = str(getattr(pm, "name", "") or getattr(pm, "type", "") or "").lower()
            if name:
                if "aiocqhttp" in name or "onebot" in name:
                    return "qq"
                if "lark" in name or "feishu" in name:
                    return "feishu"
                return name
        except Exception as e:
            logger.debug(f"[ComfyUI] 识别平台失败(元信息): {e}")
        # 2) UMO 前缀兜底：如 "default:FriendMessage:xxx" / "lark-main:FriendMessage:ou_xxx"
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            pid = umo.split(":", 1)[0].lower()
            if pid:
                return "feishu" if "lark" in pid else ("qq" if pid == "default" else pid)
        except Exception:
            pass
        return "unknown"

    def _resolve_send_targets(self, event):
        """按配置 + 来源平台决定实际发送渠道，返回集合，如 {'qq'} / {'feishu'} / {'qq','feishu'}"""
        mode = getattr(self, "send_platform", "auto")
        if mode == "both":
            return {"qq", "feishu"}
        if mode in ("qq", "feishu"):
            return {mode}
        # auto：按来源平台决定
        src = self._detect_event_platform(event)
        return {src} if src in ("qq", "feishu") else {"qq", "feishu"}

    def _workflow_preview_path(self, name):
        """返回工作流预览图路径（不存在返回 None）。命名：<工作流名>.png / .jpg（压缩后）"""
        if not name or '..' in name or '/' in name or '\\' in name:
            return None
        pv_dir = self._user_data_dir / "workflow_previews"
        for ext in ('.png', '.jpg', '.jpeg', '.webp'):
            p = pv_dir / (name + ext)
            if p.exists():
                return p
        return None

    def _compress_preview_bytes(self, raw, max_bytes=1 * 1024 * 1024):
        """把预览图字节压缩到 ≤1MB（JPEG 质量递减 + 必要时缩小尺寸）。
        返回 (bytes, ext)；压缩失败时原样返回 (raw, 'png')。"""
        try:
            from PIL import Image
            import io as _io
            img = Image.open(_io.BytesIO(raw))
            img.load()
            if img.mode in ('RGBA', 'P', 'LA'):
                img = img.convert('RGB')
            # 逐级降低 JPEG 质量
            for q in (92, 85, 78, 70, 60, 50, 40, 30):
                buf = _io.BytesIO()
                img.save(buf, 'JPEG', quality=q)
                if buf.tell() <= max_bytes:
                    return buf.getvalue(), 'jpg'
            # 仍超限：缩小尺寸（最长边按 0.85 递减）
            w, h = img.size
            for _ in range(12):
                w = max(64, int(w * 0.85)); h = max(64, int(h * 0.85))
                buf = _io.BytesIO()
                img.resize((w, h), Image.LANCZOS).save(buf, 'JPEG', quality=70)
                if buf.tell() <= max_bytes:
                    return buf.getvalue(), 'jpg'
            return raw, 'png'
        except Exception as e:
            logger.warning(f"[ComfyUI] 预览图压缩失败（原样保存）: {e}")
            return raw, 'png'

    def _shrink_video_for_feishu(self, path, limit_bytes=9 * 1024 * 1024):
        """飞书视频限 ~10MB：超限时用 ffmpeg 重编码压到限内（proot 自带 ffmpeg）。
        同步函数（ffmpeg 重编码可达数十秒），调用方必须经 asyncio.to_thread 调度。
        成功返回压缩文件路径（原文件保留在画廊），未超限返回原路径，彻底失败返回 None。"""
        p = Path(path)
        try:
            if p.suffix.lower() not in ('.mp4', '.mov', '.webm', '.avi', '.mkv'):
                return str(p)
            if p.stat().st_size <= limit_bytes:
                return str(p)
            ffmpeg = shutil.which('ffmpeg') or '/usr/bin/ffmpeg'
            import subprocess as _sp
            if not Path(ffmpeg).exists():
                logger.warning("[ComfyUI] 视频超飞书限制但 ffmpeg 不可用，原样尝试发送")
                return str(p)
            out = p.with_name(p.stem + '_fs.mp4')
            # 时长：优先 ffprobe 精确取，失败按当前体积粗估（假设 ~1.2MB/s）
            dur = None
            try:
                ffprobe = shutil.which('ffprobe') or '/usr/bin/ffprobe'
                if Path(ffprobe).exists():
                    _r = _sp.run([ffprobe, '-v', 'error', '-show_entries', 'format=duration',
                                  '-of', 'default=nw=1:nk=1', str(p)],
                                 capture_output=True, text=True, timeout=30)
                    dur = float(_r.stdout.strip())
            except Exception:
                dur = None
            if not dur or dur <= 0:
                dur = max(5.0, p.stat().st_size / (1.2 * 1024 * 1024))
            audio_k = 64
            for factor in (1.0, 0.65, 0.4):
                bv = int(limit_bytes * 8 * 0.92 * factor / max(dur, 1.0)) - audio_k * 1024
                if bv < 120 * 1024:
                    bv = 120 * 1024
                cmd = [ffmpeg, '-y', '-i', str(p), '-c:v', 'libx264',
                       '-b:v', str(bv), '-maxrate', str(int(bv * 1.45)), '-bufsize', str(int(bv * 2.9)),
                       '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
                       '-c:a', 'aac', '-b:a', f'{audio_k}k', '-movflags', '+faststart', str(out)]
                try:
                    _r = _sp.run(cmd, capture_output=True, text=True, timeout=900)
                except _sp.TimeoutExpired:
                    logger.warning(f"[ComfyUI] 视频压缩超时: {p.name}")
                    return str(p)
                if _r.returncode == 0 and out.exists() and out.stat().st_size <= limit_bytes:
                    logger.info(f"[ComfyUI] 视频已压缩适配飞书: {p.name} {p.stat().st_size // 1024}KB -> {out.stat().st_size // 1024}KB ({dur:.0f}s, {bv // 1024}kbps)")
                    return str(out)
            logger.warning("[ComfyUI] 视频压缩后仍超飞书限制，发送最后一次压缩结果")
            return str(out) if out.exists() else str(p)
        except Exception as e:
            logger.warning(f"[ComfyUI] 视频压缩失败（原样发送）: {e}")
            return str(p)

    def _shrink_for_feishu(self, path, limit_bytes=9 * 1024 * 1024):
        """飞书图片上传前按需压缩。
        飞书 im/v1/image 限制：图片 ≤ 10MB。这里用 9MB 作为阈值（留余量）。
        ★ 只在超限时才压缩 —— 未超限的图原样发送，保持原画质。
        返回可发送的文件路径（原路径 或 压缩后的新路径）；失败时返回 None 表示无法发送。"""
        try:
            p = Path(path)
            if not p.exists():
                return None
            ext = p.suffix.lower()
            if ext not in ('.png', '.jpg', '.jpeg', '.webp', '.bmp'):
                return str(path)                       # 视频/音频不走此逻辑
            size = p.stat().st_size
            if size <= limit_bytes:
                return str(path)                       # 未超限 → 原图
            logger.info(f"[ComfyUI] 图片 {size/1048576:.1f}MB 超飞书限制，开始压缩: {p.name}")
            from PIL import Image
            import io as _io
            img = Image.open(str(p))
            img.load()
            if img.mode in ('RGBA', 'P', 'LA'):
                img = img.convert('RGB')
            data = None
            # 第一轮：降 JPEG 质量（从 95 起步，尽量保留画质）
            for q in (95, 92, 88, 84, 78, 70, 62, 55, 48, 40, 32, 25):
                buf = _io.BytesIO()
                img.save(buf, 'JPEG', quality=q)
                if buf.tell() <= limit_bytes:
                    data = buf.getvalue()
                    logger.info(f"[ComfyUI] 压缩成功(质量{q}): {size/1048576:.1f}MB -> {len(data)/1048576:.1f}MB")
                    break
            # 第二轮：质量已到底仍超限 → 逐步缩小尺寸（飞书建议 ≤1500x3000，最长边上限 3000）
            if data is None:
                w, h = img.size
                max_side = max(w, h)
                for shrink in (0.85, 0.75, 0.65, 0.55, 0.45, 0.38, 0.30):
                    nw, nh = int(w * shrink), int(h * shrink)
                    # 若最长边仍超 3000，额外按 3000 比例缩
                    if max(nw, nh) > 3000:
                        r2 = 3000 / max(nw, nh)
                        nw, nh = int(nw * r2), int(nh * r2)
                    nw, nh = max(64, nw), max(64, nh)
                    buf = _io.BytesIO()
                    img.resize((nw, nh), Image.LANCZOS).save(buf, 'JPEG', quality=80)
                    if buf.tell() <= limit_bytes:
                        data = buf.getvalue()
                        logger.info(f"[ComfyUI] 压缩成功(缩至{nw}x{nh}): {size/1048576:.1f}MB -> {len(data)/1048576:.1f}MB")
                        break
            if data is None:
                logger.error(f"[ComfyUI] 压缩后仍超限，放弃发送: {p.name}")
                return None
            out = p.with_name(p.stem + '_fs.jpg')
            out.write_bytes(data)
            return str(out)
        except Exception as e:
            logger.warning(f"[ComfyUI] 飞书图片压缩失败: {e}，尝试原图发送")
            return str(path)

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

    async def _ensure_object_info(self):
        """拉取并缓存 ComfyUI /object_info（节点输入定义：下拉选项/数值范围），TTL 300s。
        class_type -> {key: {options, min, max, step, default}}"""
        import time as _t
        now = _t.time()
        if self._object_info_ts and now - self._object_info_ts < 300:
            return
        if not self.comfyui_url:
            return
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://{self.comfyui_url}/object_info", timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status != 200:
                        return
                    data = await r.json()
            cache = {}
            for ct, info in (data or {}).items():
                inp = (info or {}).get('input') or {}
                merged = {}
                for section in ('required', 'optional'):
                    for k, spec in (inp.get(section) or {}).items():
                        if not isinstance(spec, list) or not spec:
                            continue
                        entry = {}
                        # 标准 COMBO: spec[0] 是选项列表
                        if isinstance(spec[0], list):
                            entry['options'] = spec[0]
                        # 非标准 COMBO: spec = ["COMBO", {"options": [...]}]（如 PromptExpand 的 rule/llm_service）
                        elif spec[0] == 'COMBO' and isinstance(spec[-1], dict) and isinstance(spec[-1].get('options'), list):
                            entry['options'] = spec[-1]['options']
                        if isinstance(spec[-1], dict):
                            for f in ('min', 'max', 'step', 'default'):
                                if f in spec[-1]:
                                    entry[f] = spec[-1][f]
                        if entry:
                            merged[k] = entry
                if merged:
                    cache[ct] = merged
            self._object_info_cache = cache
            self._object_info_ts = now
            logger.info(f"[ComfyUI] object_info 缓存 {len(cache)} 个节点类型")
        except Exception as e:
            logger.warning(f"[ComfyUI] 拉取 object_info 失败: {e}")

    def _parse_workflow_params(self, workflow):
        oi = self._object_info_cache or {}
        nodes = []
        for nid, node in workflow.items():
            ct = node.get('class_type', ''); title = node.get('_meta', {}).get('title', ct)
            ct_def = oi.get(ct, {})
            params = []
            for key, val in node.get('inputs', {}).items():
                if key.startswith('_'): continue
                # 跳过连接值（[node_id, slot] 列表）——连接不应暴露为可保存参数，
                # 否则前端保存进 __saved_texts__ 后 _apply_workflow_config 会把连接写回成字符串
                if isinstance(val, list): continue
                if isinstance(val, (str, int, float, bool)):
                    ptype = 'text' if isinstance(val, str) and len(val) > 50 else ('bool' if isinstance(val, bool) else ('string' if isinstance(val, str) else 'number'))
                else: continue
                p = {"key": key, "value": val, "type": ptype}
                # 附加 object_info 元数据：COMBO → options（下拉项），INT/FLOAT → min/max/step
                meta = ct_def.get(key) or {}
                if meta.get('options'):
                    p['options'] = meta['options']
                    # 当前值不在选项里时补进去，避免下拉里没有当前值
                    if isinstance(val, str) and val not in meta['options']:
                        p['options'] = [val] + list(meta['options'])
                for f in ('min', 'max', 'step'):
                    if f in meta:
                        p[f] = meta[f]
                params.append(p)
            # easy stylesSelector 的 select_styles 在 UI 格式里只存在于 widgets_values_named，
            # 不在 inputs 中，这里补暴露出来，让前端能读到当前选择
            if 'stylesselector' in ct.lower().replace(' ', ''):
                wvn = node.get('widgets_values_named') or {}
                for sk in ('styles', 'select_styles'):
                    if sk in wvn and not any(p['key'] == sk for p in params):
                        sv = wvn[sk]
                        if isinstance(sv, (str, int, float, bool)):
                            params.append({"key": sk, "value": sv, "type": 'string' if isinstance(sv, str) else 'number'})
            nodes.append({"id": nid, "title": title, "class_type": ct, "params": params})
        return {"nodes": nodes, "workflow_name": self.current_workflow_name}

    async def _fetch_lora_metadata(self):
        """从 ComfyUI LoRA Manager API 获取所有 Lora 的元数据（触发词、预览图、标签等）。
        缓存到 self._lora_metadata_cache，按 lora 全路径(含子目录)索引。"""
        if self._lora_metadata_fetched:
            return
        try:
            all_items = []
            page = 1
            async with aiohttp.ClientSession() as s:
                while True:
                    url = f"http://{self.comfyui_url}/api/lm/loras/list?page={page}&page_size=200"
                    async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                        if r.status != 200:
                            break
                        data = await r.json()
                        items = data.get('items', [])
                        if not items:
                            break
                        all_items.extend(items)
                        total_pages = data.get('total_pages', 1)
                        if page >= total_pages:
                            break
                        page += 1
            self._lora_metadata_cache.clear()
            for item in all_items:
                fp = item.get('file_path', '')
                fn = item.get('file_name', '')
                # 用 ComfyUI 风格路径（子目录/文件名.safetensors）做 key
                if fp:
                    # 从完整路径中提取 ComfyUI 子目录+文件名
                    # 例如 E:/AIwork/.../loras/ZImage/ZIT/name.safetensors → ZImage/ZIT/name.safetensors
                    normalized = fp.replace('\\', '/')
                    # 大小写不敏感匹配 /loras/ 分割，防止路径含 LORAS/Loras 等变体时 IndexError
                    lc = normalized.lower()
                    if '/loras/' in lc:
                        idx = lc.index('/loras/')
                        rel = normalized[idx + len('/loras/'):]
                    else:
                        rel = fn + '.safetensors' if fn else os.path.basename(fp)
                    self._lora_metadata_cache[rel] = {
                        'file_name': fn,
                        'model_name': item.get('model_name', ''),
                        'trigger_words': item.get('civitai', {}).get('trainedWords', []) or [],
                        'preview_url': item.get('preview_url', ''),
                        'tags': item.get('tags', []) or [],
                        'file_path': fp,
                        'folder': item.get('folder', ''),
                    }
                elif fn:
                    # 无 file_path 时用 file_name 做 key（兼容）
                    key = fn + '.safetensors'
                    self._lora_metadata_cache[key] = {
                        'file_name': fn,
                        'model_name': item.get('model_name', ''),
                        'trigger_words': item.get('civitai', {}).get('trainedWords', []) or [],
                        'preview_url': item.get('preview_url', ''),
                        'tags': item.get('tags', []) or [],
                        'file_path': '',
                        'folder': item.get('folder', ''),
                    }
            # 额外用纯文件名（无扩展名、无路径）做索引，便于前端匹配
            by_basename = {}
            for key, meta in self._lora_metadata_cache.items():
                bn = meta['file_name']
                if bn and bn not in by_basename:
                    by_basename[bn] = meta
            self._lora_metadata_cache.update({f"__bn__{k}": v for k, v in by_basename.items()})
            # 同时缓存多分隔符变体（cache key 是 /，但 ComfyUI lora_name 用 \）
            for key in list(self._lora_metadata_cache.keys()):
                if key.startswith('__bn__') or '/' not in key:
                    continue
                alt_key = key.replace('/', '\\')
                if alt_key not in self._lora_metadata_cache:
                    self._lora_metadata_cache[alt_key] = self._lora_metadata_cache[key]
            self._lora_metadata_fetched = True
            logger.info(f"[ComfyUI] Lora 元数据已加载: {len(all_items)} 个 Lora")
        except Exception as e:
            logger.warning(f"[ComfyUI] 获取 Lora 元数据失败（LoRA Manager 可能未安装）: {e}")

    def _match_lora_meta(self, lora_name):
        """根据 ComfyUI 的 lora_name（如 'ZImage/ZIT/xxx.safetensors'）匹配元数据缓存。
        支持错误路径（如 'Anima风格功能WAK-000046.safetensors' 缺分隔符）兜底。"""
        if not lora_name:
            return None
        # 精确匹配
        if lora_name in self._lora_metadata_cache:
            return self._lora_metadata_cache[lora_name]
        # 标准化路径分隔符
        norm = lora_name.replace('\\', '/')
        if norm in self._lora_metadata_cache:
            return self._lora_metadata_cache[norm]
        # 用 basename 匹配
        bn = os.path.basename(norm)
        name_no_ext = os.path.splitext(bn)[0]
        bn_key = f'__bn__{name_no_ext}'
        if bn_key in self._lora_metadata_cache:
            return self._lora_metadata_cache[bn_key]
        # 模糊匹配：遍历缓存看 key 末尾是否匹配
        for key, meta in self._lora_metadata_cache.items():
            if key.startswith('__bn__'):
                continue
            if key.endswith(bn) or key.endswith(norm):
                return meta
        # 兜底：如果整串没有分隔符（如 'Anima风格功能WAK-000046.safetensors'），
        # 尝试从 bn 中提取 .safetensors 之前的最后一段做匹配
        if '/' not in lora_name and '\\' not in lora_name:
            # 拿去掉扩展名后的最后 30 字符尝试在 __bn__ 索引里搜
            for key, meta in self._lora_metadata_cache.items():
                if not key.startswith('__bn__'):
                    continue
                bn_name = key[6:]  # 去掉 __bn__ 前缀
                if lora_name.endswith(bn_name + '.safetensors') or lora_name.endswith(bn_name):
                    return meta
                # 也试试 key 本身（路径版）以 bn_name 结尾
            for key, meta in self._lora_metadata_cache.items():
                if key.startswith('__bn__'):
                    continue
                if key.endswith('.safetensors'):
                    file_only = os.path.basename(key)
                    if lora_name.endswith(file_only):
                        return meta
        return None

    async def _auto_fix_lora_paths_on_startup(self):
        """插件启动时异步修复用户保存的 lora_name（解决路径不一致问题）。
        对比 ComfyUI 实际 lora 列表，basename 匹配时自动更新到正确路径。"""
        try:
            await asyncio.sleep(2)  # 等其他启动任务先跑
            await self._fetch_lora_metadata()
            valid_loras = set()
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(f"http://{self.comfyui_url}/object_info/LoraLoader", timeout=aiohttp.ClientTimeout(total=5)) as r:
                        if r.status == 200:
                            data = await r.json()
                            valid_loras = set(data.get('LoraLoader', {}).get('input', {}).get('required', {}).get('lora_name', [None])[0] or [])
            except Exception as e:
                logger.warning(f"[ComfyUI] 拉取 lora 列表失败，跳过自动修复: {e}")
                return
            if not valid_loras:
                return
            config_path = self._user_data_dir / "config.json"
            if not config_path.exists():
                return
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
            except Exception as e:
                logger.warning(f"[ComfyUI] 读取 config 失败: {e}")
                return
            wf_configs = cfg.get('__workflow_node_configs__', {}) or {}
            fixed_count = 0
            for wf_name, wf_cfg in list(wf_configs.items()):
                if not isinstance(wf_cfg, dict):
                    continue
                lora_nodes = wf_cfg.get('__lora_nodes__', {}) or {}
                for nid, loras in list(lora_nodes.items()):
                    if not isinstance(loras, list):
                        continue
                    for li, lora in enumerate(loras):
                        if not isinstance(lora, dict):
                            continue
                        ln = lora.get('lora_name', '')
                        if not ln or ln in valid_loras:
                            continue
                        meta = self._match_lora_meta(ln)
                        if not meta or not meta.get('file_path'):
                            continue
                        correct_path = self._extract_comfyui_path(meta['file_path'])
                        if not correct_path or correct_path == ln or correct_path == ln.replace('\\', '/'):
                            continue
                        if correct_path not in valid_loras:
                            alt = correct_path.replace('\\', '/')
                            if alt in valid_loras:
                                correct_path = alt
                            else:
                                continue
                        logger.info(f"[ComfyUI] 自动修复 lora 路径: {wf_name} #{nid} #{li}: {ln!r} -> {correct_path!r}")
                        lora['lora_name'] = correct_path
                        fixed_count += 1
            if fixed_count > 0:
                try:
                    with open(config_path, 'w', encoding='utf-8') as f:
                        json.dump(cfg, f, ensure_ascii=False, indent=2)
                    logger.info(f"[ComfyUI] 启动时自动修复了 {fixed_count} 个 lora 路径")
                except Exception as e:
                    logger.warning(f"[ComfyUI] 保存修复后的 config 失败: {e}")
        except Exception as e:
            logger.warning(f"[ComfyUI] 启动时自动修复 lora 路径失败: {e}")

    def _extract_comfyui_path(self, file_path):
        """从 LoRA Manager 完整路径提取 ComfyUI 风格路径。
        例: E:/.../loras/Anima/风格功能/xxx.safetensors -> Anima\\\\风格功能\\\\xxx.safetensors"""
        if not file_path:
            return None
        normalized = file_path.replace('\\', '/')
        idx = normalized.lower().find('/loras/')
        if idx >= 0:
            return normalized[idx + 7:].replace('/', '\\')
        return None

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
                            "text": (f"[用户发送了图片，服务器路径: {save_path}]"
                                     f"（仅用于调用图生图/编辑工具的 image_urls 参数；"
                                     f"严禁在回复用户时输出该路径或任何服务器文件路径，用户看不到、也不需要）")
                        })
                    else:
                        new_content.append({"type": "text", "text": "[用户发送了图片，但下载失败]"})
                else:
                    new_content.append(item)
            ctx["content"] = new_content
        # 安全兜底：处理 image_urls 中可能未合并入 contexts 的图片（HTTP URL + base64 data URL）
        for i, url in enumerate(list(req.image_urls)):
            save_name = f"llm_input_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_url{i}.png"
            save_path = self._get_image_save_dir() / save_name
            ok = False
            if url.startswith("http"):
                ok = await self._download_image(url, save_path)
            elif url.startswith("data:image/"):
                try:
                    import base64
                    b64 = url.split(",", 1)[-1] if "," in url else url
                    data = base64.b64decode(b64)
                    save_path.write_bytes(data)
                    ok = len(data) > 0 and self._ensure_png(save_path)
                except Exception as e:
                    logger.warning(f"[ComfyUI] 解码 image_urls base64 图片失败: {e}")
            if ok:
                saved_images.append(str(save_path))
        # 提取回复消息的 ID 并注入上下文（供 LLM 查提示词用）
        reply_id = ''
        for comp in event.get_messages():
            d = comp.__dict__ if hasattr(comp, '__dict__') else {}
            if d.get('type') == 'Reply':
                reply_id = str(d.get('id', '') or '')
                break
        if reply_id:
            hint = f"\n[你回复了消息 ID: {reply_id}，如需查看该消息的完整提示词，可调用 comfyui_get_prompt 工具（不传 message_id 即可自动提取）]"
            # 只追加到最后一个 context（用户当前消息），避免重复
            for ctx in reversed(req.contexts):
                content = ctx.get("content")
                if isinstance(content, list):
                    content.append({"type": "text", "text": hint})
                    break

        # 清空图片 URL 列表，防止上层再次组装
        req.image_urls = []
        if saved_images:
            logger.info(f"[ComfyUI] LLM 请求拦截: 已转存 {len(saved_images)} 张图片 -> {saved_images}")

    def _get_image_save_dir(self) -> Path:
        """统一返回 upload_dir 临时保存目录，随后通过 HTTP upload 发送到 ComfyUI。"""
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        return self.upload_dir

    @staticmethod
    def _calc_file_md5(path):
        """计算图片文件内容 MD5（引用图片重新保存后，按内容匹配提示词记录）"""
        try:
            import hashlib
            h = hashlib.md5()
            with open(str(path), 'rb') as f:
                for chunk in iter(lambda: f.read(65536), b''):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return ''

    @staticmethod
    def _calc_image_dhash(path, hash_size=8):
        """计算图片感知哈希（dHash）。图片重编码（PNG↔JPG/压缩）后 MD5 会变，
        但视觉内容不变时 dHash 汉明距离很小，可用于相似匹配兑底。"""
        try:
            from PIL import Image
            with Image.open(str(path)) as img:
                # Pillow 10+ 推荐 Resampling 枚举，避免 Image.LANCZOS 弃用告警
                resample = getattr(Image, 'Resampling', Image).LANCZOS
                img = img.convert('L').resize((hash_size + 1, hash_size), resample)
                pixels = list(img.getdata())
            bits = []
            for row in range(hash_size):
                for col in range(hash_size):
                    bits.append(1 if pixels[row * (hash_size + 1) + col] > pixels[row * (hash_size + 1) + col + 1] else 0)
            return ''.join(str(b) for b in bits)
        except Exception:
            return ''

    @staticmethod
    def _dhash_distance(h1, h2):
        """两个 dHash 字符串的汉明距离（位数不同个数）"""
        if not h1 or not h2 or len(h1) != len(h2):
            return 64
        return sum(a != b for a, b in zip(h1, h2))

    def _backfill_prompt_log_dhash(self):
        """为 prompt_log 中 dHash 功能上线前的旧记录补算 img_dhash：
        扫描图片保存目录（output_dir），按 img_hash(MD5) 匹配记录并回填感知哈希，
        这样旧图被 QQ 重编码后引用也能按内容相似度查到提示词。"""
        try:
            if not self.output_dir.parts or not self._prompt_log:
                return
            need = [r for r in self._prompt_log if r.get('img_hash') and not r.get('img_dhash')]
            if not need:
                return
            md5_to_dhash = {}
            import os
            # 用 with 自动关闭 scandir 迭代器，避免资源泄漏
            with os.scandir(str(self.output_dir)) as scan_it:
                for f in scan_it:
                    if not f.is_file():
                        continue
                    ext = os.path.splitext(f.name)[1].lower()
                    if ext not in ('.png', '.jpg', '.jpeg', '.webp', '.bmp'):
                        continue
                    try:
                        h = self._calc_file_md5(f.path)
                        if h:
                            md5_to_dhash[h] = self._calc_image_dhash(f.path)
                    except Exception:
                        continue
            changed = False
            for r in self._prompt_log:
                if r.get('img_hash') and not r.get('img_dhash') and r['img_hash'] in md5_to_dhash:
                    r['img_dhash'] = md5_to_dhash[r['img_hash']]
                    changed = True
            if changed:
                # 用跨线程锁写文件（与 _append_prompt_log 的追加写互斥，防止互相覆盖）
                with self._prompt_log_file_lock:
                    with open(str(self._prompt_log_path), 'w', encoding='utf-8') as f:
                        json.dump(self._prompt_log, f, ensure_ascii=False, indent=2)
                filled = sum(1 for r in self._prompt_log if r.get('img_dhash'))
                logger.info(f"[ComfyUI] 已补全提示词记录 dHash（当前 {filled}/{len(self._prompt_log)} 条带感知哈希）")
        except Exception as e:
            logger.warning(f"[ComfyUI] 补全提示词记录 dHash 失败: {e}")

    async def _download_image(self, url, save_path):
        # 本地文件路径：直接复制（Windows 盘符路径 = 单个字母 + ':'，支持所有盘符）
        _is_win_drive = (len(url) >= 2 and url[1] == ':' and url[0].isalpha()) if url else False
        if url and (_is_win_drive or url.startswith(('/', '\\'))):
            src = Path(url)
            if src.exists():
                # 本地路径安全限制：仅允许插件保存目录（output_dir/upload_dir）或临时目录下的图片，
                # 防止通过 image_url 参数读取任意本地文件（如 /etc/passwd、私钥等）
                src_r = src.resolve()
                allowed_dirs = []
                for d in (self.output_dir, self.upload_dir):
                    if getattr(d, 'parts', None):
                        allowed_dirs.append(str(d.resolve()))
                in_allowed = any(str(src_r).startswith(d) for d in allowed_dirs if d)
                src_s = str(src_r)
                # 用规范化路径比较，避免 'data/temp' 字符串包含被 ../ 绕过
                in_temp = any(p.name == 'temp' for p in src_r.parents)
                if not (in_allowed or in_temp):
                    logger.warning(f"[ComfyUI] 拒绝复制本地图片（不在允许目录）: {src}")
                    return False
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

    async def _collect_images_from_event(self, event, max_images=10):
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
            # 先尝试从事件组件中提取 At
            for comp in event.get_messages():
                if isinstance(comp, At):
                    qq = getattr(comp, 'qq', None)
                    if qq is not None and str(qq) and str(qq) != str(bot_id):
                        avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={qq}&s=640"
                        if _is_new(avatar_url):
                            image_urls.append(avatar_url)
                            if len(image_urls) >= max_images:
                                return image_urls
            # 兜底：从 event 各种可能的地方提取 @ 目标
            if not image_urls:
                raw = getattr(event, 'message_str', '') or getattr(event, 'text', '')
                at_qqs = set()
                # 调试：打印事件中所有组件的类型和 At 相关信息
                for comp in event.get_messages():
                    if isinstance(comp, At):
                        at_qqs.add(str(getattr(comp, 'qq', '')))
                # 也检查 hasattr 而不是 isinstance（可能 At 是第三方组件）
                if not at_qqs:
                    for comp in event.get_messages():
                        if type(comp).__name__ == 'At' or getattr(comp, 'type', '') == 'At':
                            qq = getattr(comp, 'qq', '') or getattr(comp, 'user_id', '')
                            if qq:
                                at_qqs.add(str(qq))
                # 检查 event 是否有 at_users 等字段
                for attr in ['at_users', 'at', 'mentions', 'mentioned', '_at']:
                    val = getattr(event, attr, None) or getattr(event, attr.replace('_', ''), None)
                    if val:
                        if isinstance(val, (list, tuple)):
                            for v in val:
                                if isinstance(v, (int, str)):
                                    at_qqs.add(str(v))
                                elif isinstance(v, dict):
                                    at_qqs.add(str(v.get('qq', v.get('user_id', ''))))
                        elif isinstance(val, (int, str)):
                            at_qqs.add(str(val))
                        elif isinstance(val, dict):
                            at_qqs.add(str(val.get('qq', val.get('user_id', ''))))
                # 从 raw 文本中提取 QQ
                import re
                for m in re.finditer(r'\[At:(\d+)\]', raw):
                    at_qqs.add(m.group(1))
                # 对每个 At 目标构造头像 URL
                for qq_str in at_qqs:
                    if qq_str and qq_str != str(bot_id):
                        avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={qq_str}&s=640"
                        if _is_new(avatar_url):
                            image_urls.append(avatar_url)
                            if len(image_urls) >= max_images:
                                return image_urls

        # 3. 回退：当前消息与引用都没有图 → 取「最近图片缓存」
        #    解决飞书「先发图 → 再发 /图生图」拿不到图的问题（图片是独立消息）
        if not image_urls:
            try:
                umo = getattr(event, 'unified_msg_origin', None)
                if umo:
                    cached = self._get_recent_images(umo)
                    if cached:
                        logger.info(f"[ComfyUI] 当前消息无图，回退使用最近图片缓存（{len(cached)} 张）")
                        for u in cached[:max_images]:
                            image_urls.append(u)
            except Exception as e:
                logger.debug(f"[ComfyUI] 读取最近图片缓存失败: {e}")

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
        """通过 HTTP 上传文件到 ComfyUI input 目录，返回文件名（按扩展名选 content_type，失败重试一次）"""
        local_path = Path(local_path)
        if not local_path.exists() or local_path.stat().st_size == 0:
            logger.error(f"[ComfyUI] 上传文件不存在或为空: {local_path}")
            return None
        url = f"http://{self.comfyui_url}/upload/image"
        ext = (local_path.suffix or '.png').lower()
        ctype = {
            '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
            '.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.flac': 'audio/flac',
            '.mp4': 'video/mp4', '.webm': 'video/webm', '.mov': 'video/quicktime', '.gif': 'image/gif',
        }.get(ext, 'application/octet-stream')
        for attempt in range(2):
            try:
                timeout = aiohttp.ClientTimeout(total=30)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    with open(local_path, 'rb') as f:
                        data = aiohttp.FormData()
                        data.add_field('image', f, filename=local_path.name, content_type=ctype)
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
            except Exception as e:
                logger.error(f"[ComfyUI] 上传异常: {e}")
            if attempt == 0:
                await asyncio.sleep(0.5)  # 失败重试前短暂等待
        return None

    def _comfy_input_file_exists(self, filename):
        """探测文件是否存在于 ComfyUI input 目录（GET /view 返回 200 即存在）。"""
        if not filename or not self.comfyui_url:
            return False
        import urllib.request
        import urllib.parse
        url = f"http://{self.comfyui_url}/view?filename={urllib.parse.quote(filename)}&type=input"
        try:
            req = urllib.request.Request(url, method='HEAD')
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

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

    def _format_queue_msg(self, total, running=0, pending=0):
        if pending > 0:
            return f" | 运行:{running} 排队:{pending}"
        elif total == 1:
            return " | 队列:1（当前生成中）"
        return ""

    async def _wait_for_history(self, prompt_id, session, timeout=300):
        """轮询 /history/{prompt_id} 直到 outputs 有内容，返回 outputs 字典。
        任务执行报错(status_str=error)时抛出 ComfyUITaskError，避免白等满超时。"""
        start = time.time()
        while time.time() - start < timeout:
            if prompt_id in self._cancelled_pids:
                logger.info(f"[ComfyUI] 任务被取消(新任务替代): {prompt_id}")
                return None
            try:
                async with session.get(f"http://{self.comfyui_url}/history/{prompt_id}") as r:
                    hist = await r.json()
                if prompt_id in hist:
                    entry = hist[prompt_id]
                    st = entry.get('status') or {}
                    # 任务执行报错 → 立即抛出，不再空等
                    if st.get('status_str') == 'error' or (st.get('completed') and not st.get('status_str')):
                        err_msg = ''
                        for m in (st.get('messages') or []):
                            if isinstance(m, list) and len(m) >= 2 and m[0] == 'execution_error' and isinstance(m[1], dict):
                                err_msg = str(m[1].get('message', '') or '')
                                break
                        logger.warning(f"[ComfyUI] 任务执行失败: {prompt_id} err={err_msg[:200]}")
                        raise ComfyUITaskError(f"任务执行失败: {err_msg or '未知错误'}")
                    outputs = entry.get('outputs', {}) or {}
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
            except ComfyUITaskError:
                raise
            except Exception as e:
                logger.debug(f"[ComfyUI] 轮询异常: {e}")
            # 视频(长超时)30s 巡查一次减轻请求压力；图片保持 1s 快速响应
            await asyncio.sleep(30 if timeout >= 3600 else 1)
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

    async def _cleanup_output_loop(self):
        """定期清理输出目录中的过期生成文件（保留天数由 output_keep_days 配置，默认 7 天）。"""
        while True:
            try:
                await asyncio.sleep(3600)
                if not self.output_dir.exists(): continue
                # 保留天数：从 AstrBot 插件配置读取（管理界面可设），默认 7 天
                try:
                    keep_days = int(getattr(self, 'output_keep_days', 7) or 7)
                except (TypeError, ValueError):
                    keep_days = 7
                keep_days = max(1, min(keep_days, 90))
                now = time.time()
                cutoff = now - keep_days * 86400
                deleted = 0
                for f in self.output_dir.iterdir():
                    if f.is_file():
                        try:
                            if f.stat().st_mtime < cutoff:
                                f.unlink(); deleted += 1
                        except Exception:
                            pass
                if deleted:
                    logger.info(f"[ComfyUI] 清理了 {deleted} 个过期输出文件(保留{keep_days}天)")
                # v4.9.6: 提示词内存缓存同步清理——与 prompt_log 同一保留天数（prompt_log_days），
                # 或源文件已被本轮输出清理删除的条目也一并回收（此前该缓存从不清理、无限堆积）
                try:
                    _pc_days = max(1, int(getattr(self, 'prompt_log_days', 3) or 3))
                except (TypeError, ValueError):
                    _pc_days = 3
                _pc_cutoff = now - _pc_days * 86400
                _stale = [k for k in list(self._expanded_prompt_cache.keys())
                          if self._expanded_prompt_cache_ts.get(k, 0) < _pc_cutoff
                          or not Path(k).exists()]
                for k in _stale:
                    self._expanded_prompt_cache.pop(k, None)
                    self._expanded_prompt_cache_ts.pop(k, None)
                if _stale:
                    logger.info(f"[ComfyUI] 清理了 {len(_stale)} 条过期提示词缓存(保留{_pc_days}天)")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[ComfyUI] 清理输出目录异常: {e}")

    async def _ws_progress_listener(self):
        """连接 ComfyUI WebSocket，监听实时进度，写入 self._progress 和 self._prompt_progress。"""
        logger.info("[ComfyUI] WS 进度监听线程启动")
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    # v4.5.7: 必须用驼峰 clientId——ComfyUI 按 query 的 clientId 注册会话，
                    # 执行事件（executing/progress/execution_cached）是点对点发给提交者 sid 的，
                    # 不广播。此前用 client_id（蛇形）未被识别，监听连接拿到随机 sid，
                    # 永远收不到执行事件（进度条 0% / 节点 0/N 不动的根因）。
                    ws_url = f"ws://{self.comfyui_url}/ws?clientId={self._ws_client_id}"
                    async with session.ws_connect(ws_url, timeout=10) as ws:
                        logger.info(f"[ComfyUI] WS 进度监听已连接: {ws_url}")
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = json.loads(msg.data)
                                    msg_type = data.get('type', '')
                                    if msg_type == 'status':
                                        pass  # keep alive
                                    elif msg_type == 'progress':
                                        # 解析执行进度
                                        step = data.get('data', {})
                                        pid = step.get('prompt_id', '')
                                        node = step.get('node', '')
                                        val = step.get('value', 0)
                                        max_val = step.get('max', 0)
                                        if pid:
                                            self._progress[pid] = {'value': val, 'max': max_val, 'node': node}
                                            pp = self._prompt_progress.get(pid)
                                            if pp:
                                                pp['node_value'] = val
                                                pp['node_max'] = max_val
                                                pp['node_name'] = node
                                    elif msg_type == 'executing':
                                        # 解析当前执行节点
                                        ed = data.get('data', {})
                                        pid = ed.get('prompt_id', '')
                                        node = ed.get('node', '')
                                        if pid:
                                            pp = self._prompt_progress.get(pid)
                                            if pp and node:
                                                pp['nodes_done'] = pp.get('nodes_done', 0) + 1
                                                pp['node_name'] = node
                                    elif msg_type == 'execution_cached':
                                        # 缓存的节点（跳过）
                                        cached = data.get('data', {}).get('nodes', [])
                                        pid = data.get('data', {}).get('prompt_id', '')
                                        if pid and cached:
                                            pp = self._prompt_progress.get(pid)
                                            if pp:
                                                pp['nodes_done'] = pp.get('nodes_done', 0) + len(cached)
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

    async def _process_and_submit(self, prompt, ratio, image_path=None, cmd_config=None, user_id=None, quality_override=None, skip_pin_merge=False, notify_umo=None):
        self._refresh_workflow_list()
        if not self.workflow_path: return ("error", "未设置工作流", None)
        # 快照当前工作流：防止并发时（他人切换工作流）本次提交串用别的配置
        wf_name = self.current_workflow_name
        wf_path = self.workflow_path
        # is_video 优先读工作流配置标记 __is_video__（WebUI/配置可显式指定），
        # 未显式配置时用文件名关键词兜底，避免命名不含关键词的长视频被当图片超时
        _cfg_is_video = (self.workflow_config.get('__workflow_node_configs__', {}) or {}).get(wf_name, {}).get('__is_video__')
        is_video = bool(_cfg_is_video) if _cfg_is_video is not None else any(k in wf_name.lower() for k in ['视频', 'wan', 'ltx', 'animate'])
        timeout_sec = 14400 if is_video else 300  # 视频支持长时间大视频(1-3h+)，图片保持 300s
        try:
            # 1. 加载工作流 JSON（直接使用当前工作流文件）
            #    注意：不再 fallback 到 原json/ 目录下的原始备份，因为那可能与 API 版
            #    工作流有不同节点 ID，导致 _apply_workflow_config 无法正确应用保存的参数。
            with open(wf_path, 'r', encoding='utf-8') as f: wf = json.load(f)
            # 4. 内容指纹校验：检测工作流文件是否被替换（即使同名）
            wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
            wf_cfg = wf_configs.get(wf_name, {})
            saved_fp = wf_cfg.get('__fingerprint__', '')
            roles_ok = True
            if saved_fp:
                current_fp = self._compute_fingerprint(wf)
                if current_fp != saved_fp:
                    logger.info(f"[ComfyUI] 工作流 {wf_name} 内容已变更(指纹不匹配)，清除旧配置")
                    if wf_name in wf_configs:
                        del wf_configs[wf_name]
                        self.workflow_config['__workflow_node_configs__'] = wf_configs
                    for k in list(self.workflow_config.keys()):
                        if k.endswith('_text') and not k.startswith('__'):
                            nid = k.split('_')[0]
                            if nid.isdigit():
                                del self.workflow_config[k]
                    self._refresh_workflow_list()
                    await self._save_workflow_config()
                    # 重新加载当前工作流（不含旧配置干扰）
                    with open(wf_path, 'r', encoding='utf-8') as f2: wf = json.load(f2)
                    wf_cfg = {}  # 配置已清空
                    return ("error", "检测到工作流文件已变更，旧配置已清除，请重新设置节点角色", None)
            for role_key in ('__prompt_node__', '__negative_node__', '__resolution_node__'):
                nid = wf_cfg.get(role_key, '')
                if nid and nid not in wf:
                    roles_ok = False
                    break
            if not roles_ok:
                logger.info(f"[ComfyUI] 工作流 {wf_name} 配置的节点 ID 不存在，文件可能已替换，清除配置")
                if wf_name in wf_configs:
                    del wf_configs[wf_name]
                    self.workflow_config['__workflow_node_configs__'] = wf_configs
                    # 同时清掉根级别的旧版 {nodeId}_text（全部清不现实，但清掉这些的能减少泄漏）
                    for k in list(self.workflow_config.keys()):
                        if k.endswith('_text') and not k.startswith('__'):
                            nid = k.split('_')[0]
                            if nid.isdigit():
                                del self.workflow_config[k]
                    self._refresh_workflow_list()
                    await self._save_workflow_config()

            # 2. 从工作流 JSON 读取分辨率节点已保存的宽高
            w, h = None, None
            res_nid = self._find_resolution_node(wf, wf_name=wf_name)
            if res_nid and 'width' in wf[res_nid]['inputs'] and 'height' in wf[res_nid]['inputs']:
                try:
                    w = int(wf[res_nid]['inputs']['width'])
                    h = int(wf[res_nid]['inputs']['height'])
                except (ValueError, TypeError): pass
            # v4.5.7: 无手动节点时，读工作流自带分辨率节点的值（官方选择器/AspectRatioNode）。
            # 这些值是 WebUI 官方面板与 /比例、/分辨率 落盘的"本工作流当前分辨率"，
            # 必须作为提交基准——否则面板刚设的 4:3/1.3MP 每次生成都被全局默认 16:9/720p 踩掉。
            if w is None:
                fw, fh = self._read_workflow_file_resolution(wf)
                if fw and fh:
                    w, h = fw, fh
                    logger.info(f"[ComfyUI] 使用工作流自带分辨率: {w}x{h}")

            # 3. 如果工作流已有明确宽高（如 API 工作流内置 720x1200），直接使用，不覆盖
            #    只有用户通过 /比例 或 /分辨率 命令显式指定时才重新计算
            quality = quality_override if (quality_override and quality_override in self.quality_presets) else self.default_quality
            effective_ratio = ratio or self.default_ratio or "9:16"
            should_set_resolution = False
            # v4.5.7: 本次调用显式指定比例/质量（LLM 工具参数）→ 优先于工作流自带值重新计算；
            # 未指定时保留上面读到的文件值（官方面板与命令路径均已落盘，见 _read_workflow_file_resolution）
            if ratio or (quality_override and quality_override in self.quality_presets):
                w, h = self._calc_resolution(quality, effective_ratio)
                should_set_resolution = True
            if w is not None and h is not None:
                # 工作流已有宽高，直接使用不覆盖
                pass
            else:
                # 工作流无宽高，用质量+比例动态计算
                w, h = self._calc_resolution(quality, effective_ratio)
                should_set_resolution = True
            ratio = effective_ratio

            # 5. 应用配置 + 写入分辨率
            # 关键：传入「本次要注入图片的 LoadImage 节点」，避免清理阶段把它们删掉
            # （否则下面 _set_load_image 注入时节点已不存在 → 图生图无反应）
            _protect = set()
            if image_path:
                try:
                    _pn = self._find_load_image_node(wf)
                    if _pn:
                        _protect.add(str(_pn))
                    for _n in (self._find_all_load_image_nodes(wf) or []):
                        _protect.add(str(_n))
                except Exception as e:
                    logger.debug(f"[ComfyUI] 预取 LoadImage 节点失败: {e}")
            self._apply_workflow_config(wf, wf_name=wf_name, protect_nodes=_protect or None)
            self._apply_group_modes(wf, wf_name=wf_name)
            # 注：占位文件方案已废弃（改用「未上传加载节点移除+断连」），
            # 不再需要 _ensure_placeholder_files 预上传，避免每次生成浪费 ffmpeg + 上传
            try:
                self._apply_loras(wf)
            except Exception as e:
                logger.warning(f"[ComfyUI] Lora 应用失败（已跳过）: {e}")
            try:
                self._apply_style_selector(wf)
            except Exception as e:
                logger.warning(f"[ComfyUI] 风格预设应用失败（已跳过）: {e}")
            # 组禁用已全部由 _apply_group_modes（级联删除）完成——它同时合并了根级
            # __disabled_groups__ 兼容数据。这里不允许再走旧版内联删除路径：
            # 旧路径把下游引用直接置 ""（断头不重连），且与分桶数据可能不一致，
            # 二次删除会留下 ComfyUI 校验错误的图。同理 mode=4 无效，禁用必须物理删节点。
            disabled_nodes = wf_cfg.get('__disabled_nodes__', []) or []
            if disabled_nodes:
                for nid in disabled_nodes:
                    nid_str = str(nid)
                    if nid_str not in wf: continue
                    node = wf[nid_str]
                    node_inputs = node.get('inputs', {})
                    # 收集本节点的"输入端连线"（即上游传来的连接），用于重连下游
                    # 比如 LoraLoader 的 model 输入端是 ["6", 0]，这里就存下 model 对应的上游连接
                    input_connections = {}
                    direct_str_values = {}
                    for k, v in node_inputs.items():
                        if isinstance(v, list) and len(v) == 2:
                            input_connections[k] = v
                        elif isinstance(v, str) and v:
                            direct_str_values[k] = v
                    # 查找哪些节点引用了本节点的输出，尝试重连
                    for onid, onode in list(wf.items()):
                        if not isinstance(onode, dict): continue
                        for k, v in list(onode.get('inputs', {}).items()):
                            if isinstance(v, list) and len(v) == 2 and str(v[0]) == nid_str:
                                # 下游节点引用了本节点的输出 (v[1] 是输出索引)
                                output_idx = v[1]
                                # 尝试找到本节点对应的输入端连接，直通给下游
                                # 规则：输出索引0通常对应model/第一个输入，输出索引1对应clip/第二个输入
                                input_keys = list(input_connections.keys())
                                if output_idx < len(input_keys):
                                    onode['inputs'][k] = input_connections[input_keys[output_idx]]
                                elif direct_str_values:
                                    # 没有对应连线，用第一个字符串值兜底
                                    onode['inputs'][k] = list(direct_str_values.values())[0]
                                else:
                                    onode['inputs'][k] = ""
                    # 删除本节点
                    del wf[nid_str]
            if cmd_config:
                # 只应用 cmd_config 中与 workflow_config 不同的值（避免覆盖已保存的配置）
                for ck, val in cmd_config.items():
                    if ck.startswith('__') or val is None or val == '': continue
                    # 如果 workflow_config 中已有同 key 的值，跳过（保留已保存的配置）
                    if ck in self.workflow_config:
                        continue
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
            # 注意：_apply_workflow_config 可能用 wf_saved_texts 中的旧值覆盖了分辨率节点，
            # 无论 should_set_resolution 是 True 还是 False，都需要在这里重新写入一次
            # 确保 WebUI 中刚设置的比例/质量生效
            if w is not None and h is not None:
                self._set_resolution(wf, w, h)
            if image_path: await self._set_load_image(wf, image_path)
            self._rebuild_jzl_refs(wf)
            if prompt:
                # 合并固定标签到 prompt（有冲突检测），随机图模式跳过（已由 random-pick 合并过）。
                # v4.5.9: 图生图/图生视频（带 image_path 的编辑类任务）同样跳过——
                # 这类提示词是"编辑指令/视频描述"，混入魔导书固定标签/画风标签会污染编辑意图。
                if not skip_pin_merge and not image_path:
                    if self.workflow_config.get('__grimoire_enabled__', False):
                        prompt = self._merge_prompt_with_pins(prompt)
                target_node = cmd_config.get('__prompt_node__') if cmd_config else None
                # 注入前日志：看节点现在的实际值
                nid_before = target_node or self._find_positive_prompt_node(wf, wf_name=wf_name)
                if nid_before and nid_before in wf:
                    for k, v in wf[nid_before].get('inputs', {}).items():
                        if isinstance(v, str):
                            logger.info(f"[ComfyUI] 注入前节点 #{nid_before}.{k} = {v[:200]!r}")
                            break
                        elif isinstance(v, list):
                            logger.info(f"[ComfyUI] 注入前节点 #{nid_before}.{k} 是连线(非文本): {v}")
                else:
                    logger.warning(f"[ComfyUI] 注入前：未找到节点 #{nid_before}")
                if self._inject_prompt(wf, prompt, target_node, wf_name=wf_name):
                    # 日志：打印注入后节点的实际文本
                    nid = target_node or self._find_positive_prompt_node(wf, wf_name=wf_name)
                    if nid and nid in wf:
                        for k, v in wf[nid].get('inputs', {}).items():
                            if isinstance(v, str):
                                logger.info(f"[ComfyUI] 正面节点 #{nid}.{k} 写入后: [{v}]")
                                break
                else:
                    logger.warning(f"[ComfyUI] 正面提示词注入失败！未找到提示词节点或节点无可写文本字段")
                # 注入用户选中的 Lora 触发词到提示词末尾
                self._inject_lora_trigger_words(wf, target_node, wf_name=wf_name)
            # 写入负面提示词（从 __saved_texts__ 或根级别读取）
            # 注意：直接设置（set）而非追加（append），因为 __saved_texts__ 的值本身就是完整内容。
            # 追加模式下会与 _apply_workflow_config 写入的值重复。
            neg_nid = self._find_negative_prompt_node(wf, wf_name=wf_name)
            if neg_nid:
                neg_ck = f"{neg_nid}_text"
                # 优先从 __saved_texts__ 读取，再 fallback 根级别
                wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
                wf_cfg = wf_configs.get(self.current_workflow_name, {})
                wf_saved = wf_cfg.get('__saved_texts__', {}) or {}
                neg_val = wf_saved.get(neg_ck) or self.workflow_config.get(neg_ck)
                if neg_val and neg_nid in wf:
                    for k in wf[neg_nid].get('inputs', {}):
                        if isinstance(wf[neg_nid]['inputs'][k], str):
                            wf[neg_nid]['inputs'][k] = neg_val
                            logger.info(f"[ComfyUI] 设置负面提示词 #{neg_nid}.{k} = {neg_val[:200]}")
                            break
            cid = self._ws_client_id  # 使用固定 client_id 以确保 WS 监听器能收到进度消息
            async with aiohttp.ClientSession() as s:
                total_q, running_q, pending_q = await self._get_queue_status()
                queue_info = self._format_queue_msg(total_q, running_q, pending_q)
                async with s.post(f"http://{self.comfyui_url}/prompt", json={"prompt": wf, "client_id": cid}) as r:
                    rj = await r.json()
                if 'prompt_id' not in rj:
                    # v4.11.1: 不再吞掉 ComfyUI 的拒绝详情（校验错误/节点错误原样透出，方便定位）
                    _err_detail = json.dumps(rj, ensure_ascii=False)[:400]
                    logger.error(f"[ComfyUI] 提交被 ComfyUI 拒绝: {_err_detail}")
                    _msg = ''
                    if isinstance(rj.get('error'), dict):
                        _msg = str(rj['error'].get('message', ''))
                    _node_errs = rj.get('node_errors') or {}
                    if _node_errs:
                        _msg += ' 节点错误: ' + json.dumps(_node_errs, ensure_ascii=False)[:250]
                    return ("error", f"提交失败: {_msg or _err_detail}", None)
                pid = rj['prompt_id']
                # ⚡ 立即预置进度数据，防止 WS 监听器在 async 间隙读到空值
                self.current_prompt_id = pid
                self._prompt_start_time[pid] = time.time()
                self._prompt_node_count[pid] = len(wf)
                self._prompt_progress[pid] = {
                    'nodes_done': 0, 'nodes_total': len(wf),
                    'node_name': '', 'node_value': 0, 'node_max': 0,
                    'running': True,
                    # 节点 ID → class_type 映射：WS 事件只带裸节点 ID，
                    # 进度接口靠它翻译成人类可读的节点类名
                    'node_map': {str(k): (v.get('class_type', '') if isinstance(v, dict) else '') for k, v in wf.items()},
                }
                if user_id:
                    async with self._task_lock:
                        self.task_map[pid] = user_id
                        existing = [p for p, u in self.task_map.items() if u == user_id]
                        if len(existing) > 1:
                            logger.info(f"[ComfyUI] 用户 {user_id} 有 {len(existing)} 个排队任务")
                logger.info(f"[ComfyUI] 任务提交: {pid}, 节点数: {len(wf)}, 等待完成...")
                outputs = await self._wait_for_history(pid, s, timeout=timeout_sec)
                # 读取扩写后文本（如配置了 __expanded_text_node__）
                expanded_text = ''
                if outputs and wf_name:
                    wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
                    wf_cfg = wf_configs.get(wf_name, {})
                    exp_node = wf_cfg.get('__expanded_text_node__', '')
                    if exp_node:
                        logger.info(f"[ComfyUI] 扩写节点已配置: #{exp_node}, outputs含此节点: {exp_node in outputs}")
                    if exp_node and exp_node in outputs:
                        try:
                            exp_out = outputs[exp_node]
                            # 尝试多个可能的 output key
                            text_val = ''
                            for key in ('output', 'string', 'text'):
                                if key in exp_out and isinstance(exp_out[key], list) and len(exp_out[key]) > 0:
                                    text_val = str(exp_out[key][0])
                                    break
                            if not text_val:
                                # 兜底：取 outputs 里第一个字符串值
                                for v in exp_out.values():
                                    if isinstance(v, str) and v.strip():
                                        text_val = v
                                        break
                                    if isinstance(v, list) and len(v) > 0 and isinstance(v[0], str):
                                        text_val = v[0]
                                        break
                            if text_val and text_val.strip():
                                expanded_text = text_val.strip()
                                logger.info(f"[ComfyUI] 扩写后文本(节点#{exp_node}): {expanded_text[:100]}...")
                            else:
                                logger.info(f"[ComfyUI] 扩写节点 #{exp_node} output 格式: { {k: type(v).__name__ for k, v in exp_out.items()} }")
                        except Exception as e:
                            logger.info(f"[ComfyUI] 读取扩写后文本异常: {e}")
                # 无论结果如何，任务已结束，清除进度状态
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
                    # 超时后补查 history：视频生成耗时较长，可能在超时边缘刚完成。
                    # 不补查会误报"生成超时"且丢掉已生成的文件。
                    logger.warning(f"[ComfyUI] 等待超时，补查 history: {pid}")
                    for _try in range(3):
                        try:
                            async with s.get(f"http://{self.comfyui_url}/history/{pid}") as r2:
                                hist2 = await r2.json()
                            if pid in hist2:
                                outputs = hist2[pid].get('outputs', {}) or {}
                                has_files = any(
                                    isinstance(o, dict) and (o.get('images') or o.get('gifs'))
                                    for o in outputs.values()
                                )
                                if has_files:
                                    logger.info(f"[ComfyUI] 超时补查成功，outputs 已就绪: {pid}")
                                    break
                            outputs = None
                        except Exception:
                            outputs = None
                        await asyncio.sleep(5)
                    if outputs is None:
                        logger.warning(f"[ComfyUI] 超时且补查无产出: {pid}")
                        return ("timeout", "生成超时" if not is_video else "生成超时", None)
                logger.info(f"[ComfyUI] 开始下载图片，outputs 节点: {list(outputs.keys())}")
                saved_images = []
                for no_key, no in outputs.items():
                    # 兼容 images 与 gifs（VHS_VideoCombine / MiniMaxH3 等视频输出在 gifs 字段）
                    for img in list(no.get('images', [])) + list(no.get('gifs', [])):
                        if len(saved_images) >= 10:
                            break
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
                                # 根据真实分辨率计算实际比例（映射到最近的标准比例）
                                actual_ratio = self._closest_ratio(real_w, real_h)
                                # 扩写后文本缓存，供 _send_image_result 使用
                                # （v4.9.6: 记录入缓存时间，供按保留天数清理）
                                if expanded_text:
                                    self._expanded_prompt_cache[str(sp)] = expanded_text
                                else:
                                    # 无扩写节点的工作流：缓存本次实际使用的提示词，
                                    # 否则「图片附带提示词」开关取不到内容而静默失效
                                    if prompt:
                                        self._expanded_prompt_cache[str(sp)] = prompt
                                if str(sp) in self._expanded_prompt_cache:
                                    self._expanded_prompt_cache_ts[str(sp)] = time.time()
                                saved_images.append(str(sp))
                            else:
                                logger.warning(f"[ComfyUI] 下载失败 HTTP {ir.status}: {img['filename']}")
                # v4.8.0: 视频任务多输出时（如一采+二采双 VHS_VideoCombine 同前缀的工作流），
                # 历史记录里两段视频都在，outputs 字典顺序决定 out_path[0]——曾把一采低清版
                # 当成成品发给用户。同等时长下文件体积与分辨率正相关，按体积降序排，
                # 高清二采版排最前；图片批量输出不受影响（仅 is_video 生效）。
                if is_video and len(saved_images) > 1:
                    try:
                        saved_images.sort(key=lambda p: Path(p).stat().st_size, reverse=True)
                        sizes = [f"{Path(p).name}:{Path(p).stat().st_size // 1024}KB" for p in saved_images]
                        logger.info(f"[ComfyUI] 视频多输出按体积降序（最大优先交付）: {sizes}")
                    except Exception as e:
                        logger.debug(f"[ComfyUI] 视频输出排序失败: {e}")
                if saved_images:
                    # v4.10.0: 生成完成即记录提示词日志（视频/LLM 工具路径此前不经过发送记录，
                    # 画廊查提示词对这类文件永远"无记录"）
                    try:
                        _gp0 = str(Path(saved_images[0]).resolve())
                        _gih = self._calc_file_md5(_gp0)
                        _gidh = '' if is_video else self._calc_image_dhash(_gp0)
                        await self._append_prompt_log('', expanded_text or prompt or '', _gih, _gidh, path=_gp0)
                    except Exception:
                        pass
                    # 用第一张图计算比例
                    first_sp = Path(saved_images[0])
                    try:
                        from PIL import Image as _PILImage
                        with _PILImage.open(str(first_sp)) as _img:
                            real_w, real_h = _img.size
                    except Exception:
                        try:
                            import struct
                            with open(str(first_sp), 'rb') as _f:
                                _f.read(8)
                                _f.read(4)
                                _f.read(4)
                                real_w, real_h = struct.unpack('>II', _f.read(8))
                        except Exception:
                            real_w, real_h = w, h
                    actual_ratio = self._closest_ratio(real_w, real_h)
                    logger.info(f"[ComfyUI] 下载完成: {len(saved_images)} 张图")
                    return ("ok", f"{actual_ratio} {real_w}x{real_h}", saved_images)
                logger.warning(f"[ComfyUI] outputs 中未找到可下载的文件: {pid}")
            return ("error", "未找到输出文件(history 有记录但无 images)", None)
        except asyncio.CancelledError:
            # 协程被取消（如 AstrBot 框架工具超时）：必须清理进度状态，
            # 否则 _prompt_progress 残留 running=True，WebUI 进度条永远滚动
            _pid = getattr(self, 'current_prompt_id', '')
            if _pid:
                self._progress.pop(_pid, None)
                self._prompt_node_count.pop(_pid, None)
                self._prompt_progress.pop(_pid, None)
                self._prompt_start_time.pop(_pid, None)
            # 视频/长任务被取消时，ComfyUI 可能仍在后台生成：
            # 启动独立 watcher 继续等待并推送结果，避免视频白生成
            if _pid and notify_umo:
                try:
                    asyncio.get_event_loop().create_task(self._watch_and_push_video(_pid, notify_umo))
                    logger.info(f"[ComfyUI] 任务被取消，已启动后台 watcher 继续等待: {_pid}")
                except Exception as _e:
                    logger.warning(f"[ComfyUI] 启动后台 watcher 失败: {_e}")
            raise
        except Exception as e:
            logger.error(traceback.format_exc())
            _pid = getattr(self, 'current_prompt_id', '')
            if _pid:
                self._progress.pop(_pid, None)
                self._prompt_node_count.pop(_pid, None)
                self._prompt_progress.pop(_pid, None)
                self._prompt_start_time.pop(_pid, None)
            return ("error", str(e), None)

    async def _watch_and_push_video(self, pid, umo):
        """后台 watcher：工具协程被取消后，视频仍在 ComfyUI 后台生成时，
        独立等待生成完成并推送到发起人，避免长视频白生成。"""
        try:
            timeout_sec = 14400  # 与视频任务超时一致
            start = time.time()
            async with aiohttp.ClientSession() as s:
                while time.time() - start < timeout_sec:
                    if pid in self._cancelled_pids:
                        logger.info(f"[ComfyUI] watcher 任务被取消: {pid}")
                        return
                    try:
                        async with s.get(f"http://{self.comfyui_url}/history/{pid}") as r:
                            hist = await r.json()
                        if pid in hist:
                            entry = hist[pid]
                            st = entry.get('status') or {}
                            if st.get('status_str') == 'error':
                                logger.warning(f"[ComfyUI] watcher 任务失败: {pid}")
                                return
                            outputs = entry.get('outputs', {}) or {}
                            has_files = False
                            for _no, out in outputs.items():
                                if isinstance(out, dict) and (out.get('images') or out.get('gifs')):
                                    has_files = True
                                    break
                            if has_files:
                                # 下载视频/图片到输出目录
                                saved = []
                                for _no, out in outputs.items():
                                    for img in list(out.get('images', [])) + list(out.get('gifs', [])):
                                        vu = f"http://{self.comfyui_url}/view?filename={img['filename']}&subfolder={img.get('subfolder','')}&type={img.get('type','output')}"
                                        async with s.get(vu, timeout=aiohttp.ClientTimeout(total=60)) as ir:
                                            if ir.status == 200:
                                                sp = self.output_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{img['filename']}"
                                                sp.write_bytes(await ir.read())
                                                saved.append(str(sp))
                                if saved:
                                    logger.info(f"[ComfyUI] watcher 下载完成: {len(saved)} 个文件，推送中...")
                                    try:
                                        from astrbot.api.message_components import Video
                                        chain = MessageChain(chain=[Video(file=saved[0])])
                                        await self.context.send_message(umo, chain)
                                        logger.info(f"[ComfyUI] watcher 已推送结果: {saved[0]}")
                                    except Exception as e:
                                        logger.warning(f"[ComfyUI] watcher 推送失败: {e}")
                                return
                    except Exception as e:
                        logger.debug(f"[ComfyUI] watcher 轮询异常: {e}")
                    await asyncio.sleep(30)
                logger.warning(f"[ComfyUI] watcher 等待超时: {pid}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"[ComfyUI] watcher 异常: {e}")

    # ==================== QQ 命令 ====================

    @filter.command("帮助")
    async def show_help(self, event: AstrMessageEvent):
        await self._ensure_workflow_for_event(event)
        ctx = self._get_context_key(event)
        ctx_wf = self._context_workflows.get(ctx, self.current_workflow_name) if ctx else self.current_workflow_name
        m = f"当前工作流: {self._get_display_name(ctx_wf)}\n\n"
        m += "  /文生图 [比例] 提示词 - 文生图（发文字即可，自动追加固定标签；旧名 /画 仍可用）\n"
        m += "  /随机图 [数量] - 随机出图（默认1张，最多10张）：K2 模式排中文成句，anima 模式从随机池抽标签\n"
        m += "  /图生图 [降噪值] 提示词 - 图生图（直接传图、引用图片 或 @用户获取头像，最多10张；也可仅靠图片生成）\n"
        m += "  /生成视频 - 图生视频（引用图片 或 @用户获取头像，可仅靠图片生成；旧名 /图生视频 仍可用）\n"
        m += "  /执行 提示词 - 执行当前工作流（不限分类，未分类工作流专用）\n"
        m += "  /工作流 [编号/关键词] - 查看/切换工作流\n"
        m += "  /切换 [编号/关键词] - 快速切换工作流\n"
        m += "  /比例 [编号/比例名] - 查看/切换比例\n"
        m += "  /分辨率 [等级] - 设置质量等级（480p/720p/960p/1080p/2K/4K）\n"
        m += "  /提示词 - 引用 bot 发过的图片查询生图提示词\n"
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

    @filter.command("提示词")
    async def get_prompt_by_reply(self, event: AstrMessageEvent):
        """查询一张图片的生图提示词。支持三种方式：
        1) 引用 bot 发过的图片（Reply）；2) 直接发送图片+提示词（无引用，取当前消息图片）；
        3) 引用图或当前图被重编码/重保存后，按内容 MD5/dHash 感知匹配兜底。"""
        reply_id = ''
        reply_urls = []
        for comp in event.get_messages():
            d = comp.__dict__ if hasattr(comp, '__dict__') else {}
            if d.get('type') == 'Reply':
                reply_id = str(d.get('id', '') or '')
                # 收集引用消息中的图片 URL（供按内容 hash 兜底匹配）
                for item in d.get('chain', []):
                    u = None
                    if hasattr(item, 'url'):
                        u = item.url
                    elif hasattr(item, '__dict__') and item.__dict__.get('url'):
                        u = item.__dict__['url']
                    if u:
                        reply_urls.append(u)
                break
            # 无引用时收集当前消息直接携带的图片（直接发图 + 提示词 场景）
            if isinstance(comp, AstrImage):
                u = getattr(comp, 'url', None)
                if u:
                    reply_urls.append(u)
        # 1. 优先按引用消息 message_id 精确匹配
        if reply_id:
            for r in reversed(self._prompt_log):
                if r['message_id'] == reply_id:
                    yield event.plain_result(f"提示词: {r['prompt']}")
                    return
        # 2. 兜底：按图片内容匹配（引用图/直接发图，重新保存/重编码后 message_id 变了）
        #    先试 MD5 精确匹配，再试 dHash 感知相似（汉明距离 <= 10 视为同一张）
        if reply_urls:
            tmp_dir = self._get_image_save_dir()
            for url in reply_urls:
                tmp_path = tmp_dir / f"prompt_ref_{int(time.time() * 1000)}.png"
                try:
                    if await self._download_image(url, tmp_path):
                        h = self._calc_file_md5(tmp_path)
                        dh = self._calc_image_dhash(tmp_path)
                        for r in reversed(self._prompt_log):
                            if r.get('img_hash') and r['img_hash'] == h:
                                yield event.plain_result(f"提示词: {r['prompt']}")
                                return
                            if r.get('img_dhash') and dh and self._dhash_distance(r['img_dhash'], dh) <= 10:
                                yield event.plain_result(f"提示词: {r['prompt']}")
                                return
                except Exception as e:
                    logger.debug(f"[ComfyUI] 引用图片 hash 匹配失败: {e}")
                finally:
                    # 无论下载成功与否、匹配与否，都清理临时文件，防止 upload 目录堆积
                    try:
                        tmp_path.unlink(missing_ok=True)
                    except Exception:
                        pass
        # 3. 飞书等平台回复链不带图 URL 且 message_id 无从匹配：回退到「本会话最近发送的图」
        #    （v4.9.6: 飞书发送此前根本不进日志 + 回复链无图可下载，命令在飞书永远失效；
        #      现取 10 分钟内该会话最近发送的图，按 path/img_hash 匹配 prompt_log）
        umo = getattr(event, 'unified_msg_origin', None)
        if umo:
            try:
                cands = []
                async with self._sent_images_lock:
                    for p_abs, records in self._sent_images.items():
                        for rec in records:
                            if rec.get('umo') == str(umo) and time.time() - rec.get('sent_at', 0) <= 600:
                                cands.append((rec.get('sent_at', 0), p_abs))
                cands.sort(reverse=True)
                for _, p_abs in cands[:3]:
                    for r in reversed(self._prompt_log):
                        if r.get('path') and r['path'] == p_abs:
                            yield event.plain_result(f"提示词: {r['prompt']}")
                            return
                    try:
                        if Path(p_abs).exists():
                            h = self._calc_file_md5(p_abs)
                            for r in reversed(self._prompt_log):
                                if r.get('img_hash') and r['img_hash'] == h:
                                    yield event.plain_result(f"提示词: {r['prompt']}")
                                    return
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"[ComfyUI] 会话最近图兜底失败: {e}")
        yield event.plain_result("已过期或不是我发的图")

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
        """将分辨率同步写入当前工作流 JSON 文件（供 /比例 命令和 Web UI 共用）。
        优先识别官方 ResolutionSelector 节点 + AspectRatioNode（写 aspect_ratio/megapixels/multiple 或宽高），
        无官方节点则按老方案写 width/height 到手动指定节点。"""
        if not self.workflow_path: return
        # 抽卡工作流含内置随机分辨率逻辑，禁止写死，否则随机图每次输出固定比例
        if "抽卡" in self.current_workflow_name:
            logger.info(f"[ComfyUI] 抽卡工作流跳过同步分辨率（保留内置随机逻辑）")
            return
        try:
            wf_path = self.workflow_path  # 直接使用当前工作流文件，不再回退到原版
            with open(wf_path, 'r', encoding='utf-8') as f: wf = json.load(f)
            # 检测官方节点
            detect_wf = wf
            official_ids = self._find_official_resolution_nodes(detect_wf)
            aspect_ids = self._find_aspect_ratio_nodes(detect_wf)
            if official_ids or aspect_ids:
                # 官方 ResolutionSelector 节点：写 aspect_ratio + megapixels + multiple
                official_ratio = self._ratio_to_official(ratio)
                # 质量档位像素数 → 百万像素（官方公式 total_pixels = megapixels * 1024²）
                megapixels = round((width * height) / (1024 * 1024), 2)
                # v4.9.0: 多阶段选择器（文件内 MP 值不同，如 H3 一采0.5MP/二采1.0MP）——
                # 按原比例分配目标像素，只统一宽高比、不抹平阶段差（与 _set_resolution 同语义）
                file_mps = []
                for nid in official_ids:
                    v = wf[nid].get('inputs', {}).get('megapixels')
                    if isinstance(v, (int, float)) and v > 0:
                        file_mps.append(float(v))
                multi_stage = len(set(file_mps)) > 1
                max_mp = max(file_mps) if file_mps else 0.0
                for nid in official_ids:
                    inputs = wf[nid].get('inputs', {})
                    inputs['aspect_ratio'] = official_ratio
                    if multi_stage:
                        v = inputs.get('megapixels')
                        if isinstance(v, (int, float)) and v > 0 and max_mp > 0:
                            inputs['megapixels'] = round(float(v) * megapixels / max_mp, 3)
                        else:
                            inputs['megapixels'] = megapixels
                    else:
                        inputs['megapixels'] = megapixels
                    # multiple 保留工作流现有值，无则给 8
                    if 'multiple' not in inputs:
                        inputs['multiple'] = 8
                # AspectRatioNode：写插件格式比例 + 按比例重算宽高
                # ⚠️ 修复：原代码误将本块嵌套在 for nid in official_ids 循环内，
                # 工作流无官方节点(official_ids=[])时循环体不执行 → 分辨率节点从未被写入，
                # 表现为"WebUI 设置分辨率无效"。现移到官方循环外、if 块内，独立执行。
                if aspect_ids and ':' in ratio:
                    try:
                        ra, rb = map(int, ratio.split(':'))
                    except (ValueError, AttributeError):
                        ra, rb = 9, 16
                    total_pixels = (width * height)
                    x = (total_pixels / (ra * rb)) ** 0.5
                    for nid in aspect_ids:
                        inputs = wf[nid].get('inputs', {})
                        inputs['aspect_ratio'] = ratio
                        div = 8
                        dv = inputs.get('divisible_by', '8')
                        if isinstance(dv, str) and dv.isdigit():
                            div = int(dv)
                        elif isinstance(dv, int):
                            div = dv
                        w = round(ra * x / div) * div
                        h = round(rb * x / div) * div
                        inputs['width'] = w
                        inputs['height'] = h
                self._atomic_write_workflow_json(wf, wf_path)
                # 清理 saved_texts 中的旧分辨率值，防止 /api/workflow-params 用旧值覆盖显示
                cleared = self._clear_saved_resolution_keys(official_ids + aspect_ids)
                if cleared:
                    self._schedule_save_workflow_config()
                logger.info(f"[ComfyUI] /比例 已同步分辨率: {wf_path} {official_ratio} {megapixels}MP 节点{official_ids + aspect_ids}")
                return
            nid = self._find_resolution_node(detect_wf)
            if nid:
                wf[nid]['inputs']['width'] = width
                wf[nid]['inputs']['height'] = height
                self._atomic_write_workflow_json(wf, wf_path)
                logger.info(f"[ComfyUI] /比例 已同步分辨率到工作流: {wf_path} {width}x{height}")
        except Exception as e:
            logger.warning(f"[ComfyUI] /比例 同步分辨率到工作流失败: {e}")

    def _sync_duration_to_workflow(self, duration):
        """将视频时长同步写入当前工作流中的官方时长节点（PrimitiveFloat/Float）：
        返回是否成功写入。兼容 UI 格式（打包子节点）：先展开检测，写回子图内部原始节点。"""
        if not self.workflow_path: return False
        try:
            wf_path = self.workflow_path
            with open(wf_path, 'r', encoding='utf-8') as f: wf = json.load(f)
            official_ids = self._find_official_duration_nodes(wf)
            if not official_ids:
                logger.info(f"[ComfyUI] 工作流无时长节点，跳过时长同步")
                return False
            for nid in official_ids:
                inputs = wf[nid].get('inputs', {})
                if 'value' in inputs:
                    inputs['value'] = float(duration)
            self._atomic_write_workflow_json(wf, wf_path)
            # 清理 __saved_texts__ 旧值，否则刷新后 _apply_workflow_config 会用旧值覆盖新写入的值
            try:
                wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
                wf_cfg = wf_configs.get(self.current_workflow_name, {})
                saved = wf_cfg.get('__saved_texts__', {}) or {}
                keys_to_clear = ['value', 'duration']
                changed = False
                for nid in official_ids:
                    for k in keys_to_clear:
                        ck = f"{nid}_{k}"
                        if ck in saved:
                            del saved[ck]
                            changed = True
                if changed:
                    wf_cfg['__saved_texts__'] = saved
                    wf_configs[self.current_workflow_name] = wf_cfg
                    self.workflow_config['__workflow_node_configs__'] = wf_configs
                    logger.info(f"[ComfyUI] 已清除 saved_texts 旧时长值: 节点{official_ids}")
                    self._schedule_save_workflow_config()
            except Exception as e:
                logger.warning(f"[ComfyUI] 清除 saved_texts 旧时长值失败: {e}")
            logger.info(f"[ComfyUI] 已同步时长到工作流: {wf_path} {duration}s 节点{official_ids}")
            return True
        except Exception as e:
            logger.warning(f"[ComfyUI] 同步时长到工作流失败: {e}")
            return False

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

        # 安全守卫：检查消息是否只包含数字（防止误吞正常聊天中的数字）
        # 只有最近 10 秒内有设置过 pending action 的用户才会触发
        if pa.get('expires_at', 0) < time.time():
            self.pending_actions.pop(user_id, None)
            return

        # pending switch_workflow 时：纯文本分类名也消费（两级导航：/工作流 → 输入分类名 → 显示该分类列表）
        if pa['action'] == 'switch_workflow' and msg in ['文生图', '图生图', '视频', '未分类']:
            async for r in self._switch_workflow_by_msg(event, msg, user_id):
                yield r
            return

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
            data = pa['data']
            stage = data.get('stage', 'category')
            if stage == 'category':
                # 一级编号：选择分类 → 显示该分类工作流列表（分类内二级编号）
                cat_nums = data.get('cat_nums', [])
                if 1 <= idx + 1 <= len(cat_nums):
                    cat = cat_nums[idx]
                    groups = data.get('groups', {}) or {}
                    ungrouped = data.get('ungrouped', []) or []
                    cat_names = ungrouped if cat == '未分类' else (groups.get(cat, []) or [])
                    workflows = data.get('workflows', [])
                    cat_wf_dicts = [w for w in workflows if (w['name'] if isinstance(w, dict) else w) in cat_names]
                    if not cat_wf_dicts:
                        yield event.plain_result(f"❌ 分类 [{cat}] 下暂无工作流")
                        return
                    ctx = data.get('context_key')
                    ctx_wf = self._context_workflows.get(ctx, self.current_workflow_name) if ctx else self.current_workflow_name
                    m = f"分类 [{cat}] 工作流:\n"
                    for i, w in enumerate(cat_wf_dicts, 1):
                        dn = w.get("display_name", w["name"]) if isinstance(w, dict) else self._get_display_name(w)
                        is_cur = (w["name"] if isinstance(w, dict) else w) == ctx_wf
                        m += f"  [{i}] {dn}" + (" ✅\n" if is_cur else "\n")
                    m += "发送编号或关键词切换"
                    # 进入二级：该分类的列表用分类内编号
                    data['stage'] = 'workflow'
                    data['cat_wfs'] = cat_wf_dicts
                    import time as _t
                    pa['expires_at'] = _t.time() + 10
                    yield event.plain_result(m.strip())
                    return
            # 二级：分类内编号直接切换（无 stage=category 时保持原全局编号行为）
            wfs = data.get('cat_wfs') or data.get('workflows', [])
            if 0 <= idx < len(wfs):
                found = wfs[idx]
                ctx = data.get('context_key')
                self._switch_to_workflow(found, context_key=ctx)
                del self.pending_actions[user_id]
                yield event.plain_result(f"✅ 已切换工作流: {found.get('display_name', found['name'])}")

    # ========= 辅助：根据用户输入切换工作流（数字或关键词） =========

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

        # 无参数，显示分类概览（两级导航：先选分类，再选编号/关键词切换）
        ctx = self._get_context_key(event)
        ctx_wf = self._context_workflows.get(ctx, self.current_workflow_name) if ctx else self.current_workflow_name
        _, _, allowed = await self._get_event_bindings(event)
        cur_dn = self._get_display_name(ctx_wf) if ctx_wf else "无"
        target = allowed if allowed else wfs
        # allowed 是字符串列表（如 ["A.json"]），转为完整字典列表确保 _switch_to_workflow 能正确调用
        if allowed and isinstance(allowed[0], str):
            target = [w for w in wfs if w['name'] in allowed]

        # 按分类分组（与 _order_workflows_by_category 同序，编号连续 1..N，与 pending 索引一一对应）
        cats = self.workflow_config.get('__wf_categories__', {}) or {}
        groups = {}  # cat_name -> [wf_dict]
        ungrouped = []
        for w in target:
            wf_name = w["name"] if isinstance(w, dict) else w
            cat = cats.get(wf_name, '')
            if cat:
                groups.setdefault(cat, []).append(w)
            else:
                ungrouped.append(w)

        ordered = self._order_workflows_by_category(target)

        # 分类概览带编号（一级编号选择分类；分类内工作流二级编号在选中分类后显示）
        m = f"当前: {cur_dn}\n"
        m += "\n分类概览:\n"
        cat_order = ['文生图', '图生图', '视频']
        cat_nums = []  # 一级编号 → 分类名（含未分类）
        n = 0
        for cat in cat_order:
            if cat in groups:
                n += 1
                cat_nums.append(cat)
                m += f"  [{n}] {cat}({len(groups[cat])})\n"
        if ungrouped:
            n += 1
            cat_nums.append('未分类')
            m += f"  [{n}] 未分类({len(ungrouped)})\n"
        m += "\n发送分类编号查看该分类工作流；发送关键词直接切换"

        self._set_pending_action(user_id, "switch_workflow", {"workflows": ordered, "context_key": ctx, "stage": "category", "cat_nums": cat_nums, "cats": cats, "groups": {k: [w["name"] if isinstance(w, dict) else w for w in v] for k, v in groups.items()}, "ungrouped": [w["name"] if isinstance(w, dict) else w for w in ungrouped]}, timeout=10)
        yield event.plain_result(m.strip())

    @filter.command("画")
    async def draw_image_alias(self, event: AstrMessageEvent):
        """旧命令别名：等价 /文生图"""
        async for r in self.draw_image(event):
            yield r

    @filter.command("图生视频")
    async def img2vid_alias(self, event: AstrMessageEvent):
        """旧命令别名：等价 /生成视频"""
        async for r in self.img2vid(event):
            yield r

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
    @filter.command("文生图")
    async def draw_image(self, event: AstrMessageEvent):
        await self._ensure_workflow_for_event(event)
        can_exec, needs_sel, matching_wfs = await self._ensure_command_workflow(event, '文生图')
        if needs_sel:
            yield event.plain_result(self._build_wf_selection_menu(event, '文生图', matching_wfs))
            return
        if not can_exec:
            yield event.plain_result("当前无可用文生图工作流")
            return
        msg = event.message_str.replace("/文生图", "").replace("/画", "").strip()
        msg = re.sub(r'\[At:\d+\]', '', msg).strip()
        msg = re.sub(r'@\S+', '', msg).strip()
        if not msg: yield event.plain_result("/文生图 提示词（或使用 /随机图 随机出图）"); return
        prompt = msg
        total_q, running_q, pending_q = await self._get_queue_status()
        queue_msg = self._format_queue_msg(total_q, running_q, pending_q)
        await event.send(event.plain_result(f"生成中...{queue_msg}"))
        cmd_config = self.workflow_config.get('__commands__', {}).get('文生图', {})
        status, text, path = await self._process_and_submit(prompt, None, cmd_config=cmd_config if cmd_config else None, user_id=event.get_sender_id())
        if status == "ok":
            sent = await self._send_image_result(event, f"✨ 生成完成 当前{text}", path, prompt=prompt)
            if not sent:
                yield event.image_result(path[0] if isinstance(path, list) else path)
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
        total_q, running_q, pending_q = await self._get_queue_status()
        queue_msg = self._format_queue_msg(total_q, running_q, pending_q)
        await event.send(event.plain_result(f"执行中...{queue_msg}"))
        status, text, path = await self._process_and_submit(prompt, None, cmd_config=cmd_config, user_id=event.get_sender_id())
        if status == "ok":
            sent = await self._send_image_result(event, f"✨ 执行完成 当前{text}", path, prompt=prompt)
            if not sent:
                yield event.image_result(path[0] if isinstance(path, list) else path)
        else:
            await event.send(event.plain_result(text))

    @filter.command("随机图")
    async def random_image(self, event: AstrMessageEvent):
        """从随机池随机取标签 + 固定标签合并出图，支持 /随机图 N 执行 N 次"""
        await self._ensure_workflow_for_event(event)
        can_exec, needs_sel, matching_wfs = await self._ensure_command_workflow(event, '文生图')
        if needs_sel:
            yield event.plain_result(self._build_wf_selection_menu(event, '文生图', matching_wfs))
            return
        if not can_exec:
            yield event.plain_result("当前无可用文生图工作流")
            return
        # 解析执行次数
        msg = event.message_str.replace("/随机图", "").replace("随机图", "").strip()
        count = 1
        if msg:
            try: count = max(1, min(int(msg), 10))
            except ValueError: pass
        else:
            # 不给数字时默认 1 张
            count = 1
        # 先发提示（event.send 直发，不走 pipeline yield）
        self._pending_grimoire_tasks += count
        try:
            total_q, running_q, pending_q = await self._get_queue_status()
            pending_q += self._pending_grimoire_tasks
            total_q += self._pending_grimoire_tasks
            queue_info = f" | 运行:{running_q} 排队:{pending_q}"
            await event.send(event.plain_result(f"🎲 开始随机出图 ({count}张)...{queue_info}"))
            # 运行任务，收集结果，最后发送图片
            import aiohttp
            tasks = []
            # 提示词模型切到 K2 时，QQ 随机图也改走 K2 引擎组中文成句（与 webui 随机按钮一致，不走 anima random-pick）
            use_k2 = (self.workflow_config.get('__prompt_model__', 'anima') == 'k2')
            for i in range(count):
                if use_k2:
                    # K2 组句为同步 subprocess 调用，放线程池避免阻塞事件循环
                    kres = await asyncio.to_thread(self._k2_compose_prompt)
                    if not kres.get("ok"):
                        if i == 0:
                            yield event.plain_result("❌ " + kres.get("error", "K2 组句失败"))
                        break
                    prompt = kres["text"]
                else:
                    async with aiohttp.ClientSession() as s:
                        async with s.post(f"http://127.0.0.1:{self.webui_port}/api/grimoire/random-pick", json={}) as r:
                            data = await r.json()
                    if not data.get("ok") or not data.get("tags"):
                        if i == 0:
                            yield event.plain_result("随机池为空，请先在魔导书中添加随机池子分类")
                        break
                    prompt = data["tags"]
                status, text, path = await self._process_and_submit(prompt, None, user_id=event.get_sender_id(), skip_pin_merge=True)
                tasks.append((status, text, path, prompt))
            if not tasks:
                return
        finally:
            # 无论循环正常结束还是提前 break/异常，都清零计数器，防止泄漏
            self._pending_grimoire_tasks = 0
        success = 0
        for i, (status, text, path, prompt) in enumerate(tasks):
            if status == "ok":
                await self._send_image_result(event, f"🎲 随机图 ({i+1}/{len(tasks)})", path, prompt=prompt)
                success += 1
        if success > 0:
            await event.send(event.plain_result(f"🎲 随机图完成，共 {success} 张"))

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
        # 支持单图/多图（最多10张）：直接传图、引用图片 或 @用户获取头像
        image_urls = await self._collect_images_from_event(event, max_images=10)
        if image_urls:
            saved_paths = []
            for i, url in enumerate(image_urls):
                save_path = self._get_image_save_dir() / f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{i}.png"
                if await self._download_image(url, save_path):
                    saved_paths.append(save_path)
            if not saved_paths:
                yield event.plain_result("下载图片失败")
                return
            total_q, running_q, pending_q = await self._get_queue_status()
            queue_msg = self._format_queue_msg(total_q, running_q, pending_q)
            denoise_hint = f"（降噪: {denoise}）" if denoise else ""
            await event.send(event.plain_result(f"生成中...{denoise_hint}{queue_msg}"))
            cmd_config = dict(self.workflow_config.get('__commands__', {}).get('图生图', {}))
            if denoise and len(saved_paths) == 1:
                try:
                    with open(self.workflow_path, 'r', encoding='utf-8') as f: wf = json.load(f)
                    sn = self._find_sampler_node(wf)
                    if sn: cmd_config[f'{sn}_denoise'] = denoise
                except Exception as e:
                    logger.warning(f"[ComfyUI] 设置降噪值失败: {e}")
            # 单图传路径字符串（兼容降噪/比例逻辑），多图传路径列表
            submit_imgs = str(saved_paths[0]) if len(saved_paths) == 1 else [str(p) for p in saved_paths]
            status, text, out_path = await self._process_and_submit(prompt, None, submit_imgs, cmd_config=cmd_config if cmd_config else None, user_id=event.get_sender_id())
            # 清理临时下载的图片
            for p in saved_paths:
                try: p.unlink(missing_ok=True)
                except Exception: pass
            if status == "ok":
                sent = await self._send_image_result(event, f"✨ 生成完成 当前{text}", out_path, prompt=prompt)
                if not sent:
                    yield event.image_result(out_path[0])
            else: yield event.plain_result(text)
            return
        yield event.plain_result("请发送图片或 @用户 后使用 /图生图 [降噪] 提示词")

    # ====================================================================
    # 飞书专用：含图片的裸命令（如「[图片] 图生图」）转发到上面的命令实现
    # --------------------------------------------------------------------
    # 为什么需要：AstrBot 的 CommandFilter 用 message_str.startswith("图生图") 判断，
    # 而含图片消息的 message_str 是 "[图片] 图生图" → 命令匹配失败 → 落到 LLM
    # （表现为 AI 乱加戏、念服务器路径）。RegexFilter 用 search 且不受 wake_prefix 制约，
    # 可以容忍占位前缀。此处只在飞书 + 确实带图时转发，避免与 QQ 的 /命令 重复触发。
    # ====================================================================
    @filter.regex(r"^(\[[^\]]{1,24}\]\s*)*图生图(\s|$)")
    async def _lark_img2img_loose(self, event: AstrMessageEvent):
        """飞书：[图片] 图生图 → 转发给 img2img 命令实现（其它平台/场景不处理）"""
        if (event.get_platform_name() or "") != "lark":
            return
        logger.info("[ComfyUI] 飞书宽松匹配命中：图生图（含图片前缀）")
        async for r in self.img2img(event):
            yield r

    @filter.regex(r"^(\[[^\]]{1,24}\]\s*)*(生成视频|图生视频)(\s|$)")
    async def _lark_img2vid_loose(self, event: AstrMessageEvent):
        """飞书：[图片] 图生视频 → 转发给 img2vid 命令实现"""
        if (event.get_platform_name() or "") != "lark":
            return
        logger.info("[ComfyUI] 飞书宽松匹配命中：生成视频/图生视频（含图片前缀）")
        async for r in self.img2vid(event):
            yield r

    @filter.command("生成视频")
    async def img2vid(self, event: AstrMessageEvent):
        await self._ensure_workflow_for_event(event)
        can_exec, needs_sel, matching_wfs = await self._ensure_command_workflow(event, '视频')
        if needs_sel:
            yield event.plain_result(self._build_wf_selection_menu(event, '视频', matching_wfs))
            return
        if not can_exec:
            yield event.plain_result("当前无可用视频工作流")
            return
        image_urls = await self._collect_images_from_event(event, max_images=1)
        image_url = image_urls[0] if image_urls else None
        if image_url:
            # 检查当前工作流是否支持视频生成
            video_kw = ['视频', 'wan', 'ltx', 'animate', 'video', 'WAN']
            if not any(k in self.current_workflow_name for k in video_kw):
                yield event.plain_result(f"当前工作流「{self._get_display_name(self.current_workflow_name)}」不是视频工作流，请先切换到视频工作流再使用 /生成视频")
                return
            save_path = self._get_image_save_dir() / f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            total_q, running_q, pending_q = await self._get_queue_status()
            queue_msg = self._format_queue_msg(total_q, running_q, pending_q)
            yield event.plain_result(f"生成视频中...{queue_msg}")
            if not await self._download_image(image_url, save_path): yield event.plain_result("下载图片失败"); return
            # v4.5.3: 提取命令后的提示词（此前硬编码空串，提示词永远丢失）
            vid_prompt = event.message_str.replace("/生成视频", "").replace("/图生视频", "").strip()
            vid_prompt = re.sub(r'\[[^\]]{1,24}\]', '', vid_prompt).strip()   # 去掉 [图片] 等占位前缀
            vid_prompt = re.sub(r'\[At:\d+\]', '', vid_prompt).strip()
            vid_prompt = re.sub(r'@\S+', '', vid_prompt).strip()
            cmd_config = self.workflow_config.get('__commands__', {}).get('视频', {})
            _umo = getattr(event, 'unified_msg_origin', None)
            vid_prompt, _rule_note = self._enforce_prompt_rule(vid_prompt, 'imgrev')
            status, text, out_path = await self._process_and_submit(vid_prompt, None, str(save_path), cmd_config=cmd_config if cmd_config else None, user_id=event.get_sender_id(), notify_umo=_umo)
            if status == "ok":
                _ok_note = (_rule_note + "\n") if _rule_note else ""
                vid_path = out_path[0] if isinstance(out_path, list) else out_path
                try:
                    from astrbot.api.message_components import Video, At, Plain
                    # v4.8.0: 飞书视频限 ~10MB，超限压缩后再发
                    if self._detect_event_platform(event) == 'feishu':
                        vid_path = await asyncio.to_thread(self._shrink_video_for_feishu, vid_path)
                    # 先发视频
                    yield event.chain_result([Video.fromFileSystem(vid_path)])
                    # 再发 @用户 的完成通知
                    yield event.chain_result([
                        At(qq=event.get_sender_id()),
                        Plain(_ok_note + " 视频生成完毕"),
                    ])
                except Exception as e:
                    logger.error(f"[ComfyUI] 发送视频消息失败: {e}")
                    yield event.plain_result(f"视频已生成，但无法自动发送，文件路径: {vid_path}")
            else: yield event.plain_result(text)
            return
        yield event.plain_result("请引用图片或 @用户 后输入 /生成视频")

    # ── 画廊 API ──────────────────────────────────────────

    # ========================================================================
    # 魔导书 API（数据库管理 + LLM 画图拦截开关）
    # ========================================================================




    # ========= 预设 API =========





















    # ------------------------------------------------------------------
    # 发送消息后保活：防止管道截断 command 的 yield
    # ------------------------------------------------------------------
    @filter.after_message_sent(priority=999)
    async def _keep_alive_after_send(self, event: AstrMessageEvent):
        """消息发送后恢复事件传播，防止 yield 被截断"""
        if event.is_stopped():
            event.continue_event()

    @filter.event_message_type(
        filter.EventMessageType.PRIVATE_MESSAGE | filter.EventMessageType.GROUP_MESSAGE
    )   # EventMessageType 是 enum.Flag，按位或即"私聊+群聊都接收"
    async def _cache_incoming_images(self, event: AstrMessageEvent):
        """捕获用户发来的图片并缓存（按会话），供后续命令在「当前消息无图」时回退取用。

        场景：飞书图片是独立消息，用户「先发图 → 再发 /图生图」时，命令那条消息没有图片组件；
        QQ 可同条/紧邻发送，不受影响。此缓存让两个平台行为一致（10 分钟内有效）。
        """
        try:
            urls = []
            for comp in event.get_messages():
                if isinstance(comp, AstrImage):
                    u = getattr(comp, 'url', None) or getattr(comp, 'file', None)
                    if u:
                        urls.append(str(u))
            if not urls:
                return
            umo = getattr(event, 'unified_msg_origin', None)
            if not umo:
                return
            self._recent_images[str(umo)] = (time.time(), urls)
            logger.info(f"[ComfyUI] 已缓存最近图片 {len(urls)} 张 (umo={umo[-20:]})")
            # v4.7.2: 立即后台落地本地副本——飞书等平台图片 URL 短时效，隔一条消息就失效
            # （"图只能在当场那条消息用"的根因）。下载成功后用本地路径替换 URL 缓存。
            try:
                _t = asyncio.create_task(self._persist_recent_images(str(umo), list(urls)))
                self._background_tasks.add(_t)
                _t.add_done_callback(self._background_tasks.discard)
            except Exception as e:
                logger.debug(f"[ComfyUI] 启动最近图片落地失败: {e}")
        except Exception as e:
            logger.debug(f"[ComfyUI] 缓存最近图片失败: {e}")

    async def _persist_recent_images(self, umo: str, urls: list):
        """把最近图片缓存落地为本地文件（upload_dir/recent_cache/），下载成功后替换 URL 缓存。
        本地路径在 _download_image 的允许目录内，可长期反复取用；该目录由每小时的
        清理循环兜底回收（>1 天），TTL 过期时也会主动删除。"""
        try:
            cache_dir = Path(self.upload_dir) / 'recent_cache'
            cache_dir.mkdir(parents=True, exist_ok=True)
            old = self._recent_images.get(umo)
            old_locals = [u for u in (old[1] if old else []) if 'recent_cache' in str(u)]
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            locals_ = []
            for i, u in enumerate(urls[:10]):
                sp = cache_dir / f"recent_{stamp}_{i}.png"
                if await self._download_image(u, sp):
                    locals_.append(str(sp))
            if not locals_:
                return  # 下载全失败：保留原 URL 缓存（短期内仍可用）
            self._recent_images[umo] = (time.time(), locals_)
            for f in old_locals:
                try:
                    Path(f).unlink(missing_ok=True)
                except Exception:
                    pass
            logger.info(f"[ComfyUI] 最近图片已落地本地 {len(locals_)} 张 (umo={umo[-20:]})")
        except Exception as e:
            logger.debug(f"[ComfyUI] 落地最近图片失败: {e}")

    def _get_recent_images(self, umo):
        """取该会话最近的图片缓存（超过 TTL 返回空，并回收本地副本文件）"""
        try:
            rec = self._recent_images.get(str(umo))
            if not rec:
                return []
            ts, urls = rec
            if time.time() - ts > self._recent_images_ttl:
                self._recent_images.pop(str(umo), None)
                for u in urls:
                    if 'recent_cache' in str(u):
                        try:
                            Path(u).unlink(missing_ok=True)
                        except Exception:
                            pass
                return []
            return list(urls)
        except Exception:
            return []

    # ========================================================================
    # Anima 数据 API & LLM 工具
    # ========================================================================

    def _get_pinned_tags(self) -> str:
        """获取固定的标签（已做冲突去重），返回逗号分隔的标签字符串"""
        pins = self.workflow_config.get('__grimoire_pins__', {})
        if not pins:
            return ""
        tags = []
        seen = set()
        for src, info in pins.items():
            entries = info if isinstance(info, list) else [info]
            for entry in entries:
                t = (entry.get("tags") or "").strip()
                if t:
                    for part in t.split(","):
                        p_stripped = part.strip()
                        p_lower = p_stripped.lower()
                        if p_lower and len(p_lower) > 1 and p_lower not in seen:
                            seen.add(p_lower)
                            tags.append(p_stripped)
        return ", ".join(tags)

    def _merge_prompt_with_pins(self, prompt: str) -> str:
        """合并用户提示词和固定标签，有冲突时固定标签不追加"""
        pinned = self._get_pinned_tags()
        if not pinned:
            return prompt
        # 简单冲突检测：将用户 prompt 分词，看固定标签中是否有重叠
        prompt_lower = prompt.lower()
        pin_parts = [p.strip() for p in pinned.split(",") if p.strip()]
        conflict_keywords = {"1girl", "1boy", "solo", "female", "male", "woman", "man"}
        filtered_pins = []
        for part in pin_parts:
            part_lower = part.lower()
            # 如果用户 prompt 中已经包含该标签的完整内容，跳过
            if part_lower in prompt_lower and len(part_lower) > 3:
                continue
            # 冲突关键词检测：如果用户 prompt 包含性别/人数词且固定标签也包含同类词，跳过
            if any(kw in prompt_lower for kw in conflict_keywords) and \
               any(kw in part_lower for kw in conflict_keywords):
                continue
            # 如果用户 prompt 包含颜色/外貌描述且固定标签也包含同类词，跳过
            if any(kw in part_lower for kw in ["hair", "eye", "color"]) and \
               any(kw in prompt_lower for kw in ["hair", "eye", "color"]):
                continue
            filtered_pins.append(part)
        if filtered_pins:
            result = f"{', '.join(filtered_pins)}, {prompt}"
            logger.info(f"[固定标签] 已合并 {len(filtered_pins)} 个标签到 prompt")
            return result
        return prompt








    # ==================================================================
    # 魔导书 LLM 管理工具：增删改查
    # ==================================================================






    # ----- LLM 工具：增删改查 -----





    # ==================================================================
    # 魔导书图片缓存系统
    # ==================================================================



    async def _run_cache_background(self, source):
        """后台分批下载全部图片"""
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        src_path = source.replace('/', os.sep).replace('\\', os.sep)
        fpath = data_dir / src_path
        if not fpath.suffix:
            fpath = fpath.with_suffix('.json')

        try:
            if fpath.exists() and fpath.stat().st_size > 0:
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    items = json.loads(content) if content else []
            else:
                items = []
            if not isinstance(items, list):
                items = []
        except Exception as e:
            self._cache_progress[source] = {
                "running": False, "total": 0, "done": 0,
                "success": 0, "failed": 0, "message": f"读取数据源失败: {e}"
            }
            return

        # 本地 JSON 不存在 → 尝试从 Anima-Tools JS 加载
        if not items and _is_anima_source(str(fpath.relative_to(data_dir))):
            source_name = fpath.relative_to(data_dir).stem
            items = load_anima_tools_source(source_name)

        # 为 anima 数据源动态生成 image_url（只有有 p 字段的条目才生成）
        for it in items:
            if not it.get("image_url") and it.get("p"):
                p = it.get("p", 1)
                img_id = it.get("id", "")
                if img_id:
                    it["image_url"] = f"https://anima.mooshieblob.com/images/{p}/{img_id}.webp"
            if not it.get("image_url") and it.get("name") and it.get("copyright"):
                raw_name = f"{it['name']}, {it['copyright']}"
                import urllib.parse
                it["image_url"] = f"https://blobs.animadex.net/Outputs/thumbs/{urllib.parse.quote(raw_name)}.webp"

        try:
            # 收集未缓存的图片（读一次缓存目录，用集合查）
            cached_keys = set(f.name for f in self._grimoire_cache_dir.iterdir()) if self._grimoire_cache_dir.exists() else set()
            to_download = []
            for it in items:
                img_url = it.get("image_url") or ""
                if img_url:
                    ck = hashlib.md5(img_url.encode()).hexdigest()
                    if ck not in cached_keys:
                        to_download.append((img_url, ck))

            total = len(to_download)
            self._cache_progress[source]["total"] = total
            if total == 0:
                self._cache_progress[source].update(
                    running=False, done=0, success=0, failed=0,
                    message="所有图片已缓存"
                )
                return

            # 分批下载（每批50，8并发）
            BATCH_SIZE = 50
            MAX_CONCURRENT = 8
            success = 0
            failed = 0
            done = 0

            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=30),
                headers={"User-Agent": "AstrBot-ComfyUI/1.0"}
            ) as session:
                sem = asyncio.Semaphore(MAX_CONCURRENT)

                async def _dl_one(url, ck):
                    async with sem:
                        try:
                            async with session.get(url) as resp:
                                if resp.status == 200:
                                    data = await resp.read()
                                    cache_path = self._grimoire_cache_dir / ck
                                    with open(cache_path, 'wb') as f:
                                        f.write(data)
                                    return True
                                return False
                        except Exception:
                            return False

                for i in range(0, total, BATCH_SIZE):
                    batch = to_download[i:i + BATCH_SIZE]
                    results = await asyncio.gather(*[_dl_one(url, ck) for url, ck in batch])
                    batch_ok = sum(1 for r in results if r)
                    success += batch_ok
                    failed += (len(batch) - batch_ok)
                    done += len(batch)
                    self._cache_progress[source].update(
                        done=done, success=success, failed=failed,
                        message=f"已下载 {done}/{total}"
                    )

            self._cache_progress[source].update(
                running=False,
                message=f"完成: {success} 成功, {failed} 失败"
            )
        except Exception as e:
            logger.error(f"[缓存] 后台下载异常: {e}")
            self._cache_progress[source] = {
                "running": False, "total": 0, "done": 0,
                "success": 0, "failed": 0, "message": f"异常: {e}"
            }


    async def terminate(self):
        logger.info("[ComfyUI] 插件已卸载")
        # 停止 WebUI 服务，避免端口残留
        try:
            if self._webui_site_local:
                await self._webui_site_local.stop()
            if self._webui_site_ipv6:
                await self._webui_site_ipv6.stop()
            if self._webui_runner:
                await self._webui_runner.cleanup()
        except Exception as e:
            logger.warning(f"[ComfyUI] 停止 WebUI 服务时出错: {e}")
        # 取消后台 while True 循环任务（清理循环 / WS 进度监听 / 缓存下载等）
        for _t in list(getattr(self, '_background_tasks', None) or []):
            try:
                _t.cancel()
            except Exception:
                pass
        self._background_tasks = []
        # 取消正在进行的缓存下载任务
        _cache_tasks = getattr(self, '_cache_tasks', None) or {}
        for _t in list(_cache_tasks.values()):
            try:
                _t.cancel()
            except Exception:
                pass
        _cache_tasks.clear()
        async with self._task_lock:
            self.task_map.clear()
            self._cancelled_pids.clear()
            self._progress.clear()
            self._prompt_progress.clear()
            self._prompt_node_count.clear()
