# -*- coding: utf-8 -*-
"""llm_tools.py — LLM 函数工具集（FunctionTool 定义）、飞书宽松命令过滤器与任务异常类
v4.13.5 拆分自 main.py（原 29-628 行），行为不变。
"""
import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import aiohttp
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.event.filter import CustomFilter
from astrbot.api import logger, FunctionTool

from .data_paths import data_dir_resolver


class ComfyUITaskError(Exception):
    """ComfyUI 任务执行失败（非超时），用于让等待循环立即中断并向上传递错误信息。"""


# ====================================================================
# 飞书专用「宽松命令匹配」过滤器
# --------------------------------------------------------------------
# 背景：AstrBot 的 CommandFilter 用 message_str.startswith("命令") 判断，
# 而含图片的消息 message_str 会变成 "[图片] 图生图"（图片被 _outline_chain 转成
# "[图片]" 占位符）→ 命令匹配失败 → 消息落到 LLM（AI 乱加戏、念文件路径）。
# QQ 用户习惯「引用图片 + 图生图」，引用内容不进入 message_str 主体，所以不受影响。
#
# 方案：只在飞书平台启用宽松匹配（剥离 [图片]/[表情:x]/[At:x] 等前缀后再判断），
# 其它平台（QQ 等）返回 False → 完全保持原有的 @filter.command 行为，零影响。
# ====================================================================
_PLACEHOLDER_PREFIX_RE = re.compile(r"^(\[[^\]]{1,24}\]\s*)+")


class LarkLooseCommandFilter(CustomFilter):
    """飞书专用：容忍 [图片] 等占位前缀的命令匹配（其它平台不生效）。

    用法：在命令上叠加 @filter.custom_filter(LarkLooseCommandFilter, "命令名")
    注意：custom_filter 会以 (raise_error) 实例化本类，故命令名由类属性传入。
    """

    command_name: str = ""          # 子类通过装饰器参数注入（见 _lark_cmd）

    def __init__(self, raise_error: bool = False) -> None:
        super().__init__(raise_error=raise_error)

    def filter(self, event: AstrMessageEvent, cfg) -> bool:
        try:
            # 只在飞书平台生效，其它平台保持原命令匹配逻辑
            if (event.get_platform_name() or "") != "lark":
                return False
            cmd = (self.command_name or "").strip()
            if not cmd:
                return False
            msg = (event.get_message_str() or "").strip()
            # 剥离开头的 [图片]/[表情:x]/[At:x] 等占位符
            cleaned = _PLACEHOLDER_PREFIX_RE.sub("", msg).strip()
            return cleaned == cmd or cleaned.startswith(cmd + " ")
        except Exception as e:
            logger.debug(f"[ComfyUI] LarkLooseCommandFilter 异常: {e}")
            return False


def _lark_cmd(name: str):
    """生成飞书宽松命令过滤器类：@filter.custom_filter(_lark_cmd('图生图'))"""
    return type(f"LarkLoose_{name}", (LarkLooseCommandFilter,), {"command_name": name})


# ====================================================================
# LLM 工具集（共10个）
# ====================================================================

@dataclass
class ComfyUIDrawTool(FunctionTool):
    name: str = "comfyui_draw"
    description: str = ("使用本地ComfyUI生成图片（文生图，纯提示词出图，不需要输入图片）。"
                        "★ 只能用分类为「画」的工作流；若当前工作流不是「画」类，"
                        "必须先用 comfyui_list_workflows 找到「画」类里合适的工作流，"
                        "再用 comfyui_switch_workflow 切换后再调用本工具。"
                        "不要用「图生图」类工作流做文生图。"
                        "可根据工作流名称关键词自动切换工作流。")
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "提示词（英文或中文），描述要生成的画面"},
            "workflow": {"type": "string", "description": "工作流名称关键词，例如'动漫'、'FLUX'、'真人'。不传则用当前工作流"},
            "ratio": {"type": "string", "description": "图片比例", "enum": ["1:1", "3:4", "4:3", "9:16", "16:9", "21:9", "2:3", "3:2"]},
            "quality": {"type": "string", "description": "质量等级", "enum": ["480p", "720p", "960p", "1080p", "2K", "4K"]}
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
        # LLM 传的 quality 只对当次生效，不覆盖 WebUI 的默认设置
        try:
            umo = event.unified_msg_origin if hasattr(event, 'unified_msg_origin') else None
            if umo:
                from astrbot.api.event import MessageChain
                chain = MessageChain().message("🎨 生成中...")
                await plugin.context.send_message(umo, chain)
        except Exception as e:
            logger.warning(f"[ComfyUI] 发送生成中提示失败: {e}")
        prompt, _rule_note = plugin._enforce_prompt_rule(prompt, 't2i')
        status, text, path = await plugin._process_and_submit(prompt, ratio, user_id=event.get_sender_id(), quality_override=quality)
        if status == "ok":
            _ok_note = (_rule_note + "\n") if _rule_note else ""
            sent = await plugin._send_image_result(event, "✨ 生成完成", path, prompt=prompt)
            if sent: return _ok_note + "✅ 图片已发送"
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
            return "✅ 图片已生成并发送给用户"
        return text


@dataclass
class ComfyUIListWorkflowsTool(FunctionTool):
    name: str = "comfyui_list_workflows"
    description: str = ("查询/列出本地ComfyUI所有可用工作流（含所属分类与用途）。"
                        "当用户想查看、查询、浏览可用工作流，或需要判断'该用哪个工作流'时调用此工具。"
                        "分类含义：文生图=纯提示词出图（用 comfyui_draw）；图生图=需要输入图片改图/转风格"
                        "（用 comfyui_img2img）；视频=图片转视频（用 comfyui_video）。")
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}, "required": []})

    async def run(self, event: AstrMessageEvent):
        plugin = self._plugin
        wfs = plugin._refresh_workflow_list()
        if not wfs: return "当前没有可用工作流"
        cats = plugin.workflow_config.get('__wf_categories__', {}) or {}

        # 分类用途说明（供 LLM 判断该用哪个工作流、调哪个工具）
        cat_usage = {
            '文生图': '文生图——纯提示词出图（对应工具 comfyui_draw）',
            '图生图': '必须提供输入图片——改图/转风格/转真人（对应工具 comfyui_img2img）',
            '视频': '视频——必须提供输入图片，图片转视频（对应工具 comfyui_video）',
        }

        groups = {}
        ungrouped = []
        for w in wfs:
            cat = cats.get(w['name'], '')
            (groups.setdefault(cat, []) if cat else ungrouped).append(w)

        out = "当前可用工作流（按分类分组）：\n"
        cat_order = ['文生图', '图生图', '视频']
        listed = 0
        for cat in cat_order:
            if cat not in groups:
                continue
            out += f"\n【{cat}】{cat_usage.get(cat, '')}\n"
            for w in groups[cat]:
                listed += 1
                dn = w.get('display_name', w['name'])
                out += f"  {listed}. {dn}{' ✅(当前)' if w.get('is_current') else ''}\n"
        # 其它自定义分类
        for cat in sorted([c for c in groups if c not in cat_order]):
            out += f"\n【{cat}】\n"
            for w in groups[cat]:
                listed += 1
                dn = w.get('display_name', w['name'])
                out += f"  {listed}. {dn}{' ✅(当前)' if w.get('is_current') else ''}\n"
        if ungrouped:
            out += "\n【未分类】（用途未知，需按用户意图判断）\n"
            for w in ungrouped:
                listed += 1
                dn = w.get('display_name', w['name'])
                out += f"  {listed}. {dn}{' ✅(当前)' if w.get('is_current') else ''}\n"

        cur = next((w for w in wfs if w.get('is_current')), None)
        if cur:
            cur_cat = cats.get(cur['name'], '未分类')
            out += f"\n当前工作流：{cur.get('display_name', cur['name'])}（分类：{cur_cat}）"
            out += f"\n⚠ 只能执行当前工作流所属分类对应的命令/工具；若用户要做的操作与当前分类不符，"
            out += f"请先用 comfyui_switch_workflow 切到该分类下的工作流再执行。"
        return out


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
                # v4.4.4: 带出目标工作流的规则绑定——LLM 在同一轮就能拿到新工作流的提示词规则
                _r_t2i = plugin._effective_rule('t2i', w['name'])
                _r_rev = plugin._effective_rule('imgrev', w['name'])
                _extra = ''
                if _r_t2i:
                    _extra += f"\n【文生图提示词规则——写 prompt 必须严格遵循】\n{_r_t2i}"
                if _r_rev:
                    _extra += f"\n【图片参考提示词规则——写 prompt 必须严格遵循】\n{_r_rev}"
                _tail = _extra if _extra else "\n（该工作流未绑定提示词规则，自由发挥即可）"
                return f"✅ 已切换到【{w.get('display_name', w['name'])}】{_tail}"
        return f"❌ 未找到包含'{keyword}'的工作流"


@dataclass
class ComfyUIGetCurrentWorkflowTool(FunctionTool):
    name: str = "comfyui_get_current_workflow"
    description: str = ("查询当前正在使用的工作流名称及其所属分类（分类决定能用哪个工具："
                        "画=文生图 comfyui_draw / 图生图=需输入图 comfyui_img2img / "
                        "视频=需输入图 comfyui_video）。"
                        "当用户问'我现在用什么工作流'、'当前画风是什么'，或需要判断能否执行某操作时调用。")
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}, "required": []})

    async def run(self, event: AstrMessageEvent):
        plugin = self._plugin
        name = plugin.current_workflow_name
        if not name:
            return "未设置"
        cats = plugin.workflow_config.get('__wf_categories__', {}) or {}
        cat = cats.get(name, '未分类')
        usage = {
            '文生图': '文生图（纯提示词出图，用 comfyui_draw）',
            '图生图': '图生图（必须提供输入图片，用 comfyui_img2img）',
            '视频': '视频（必须提供输入图片，用 comfyui_video）',
        }.get(cat, '用途未知')
        return f"当前工作流：【{plugin._get_display_name(name)}】（分类：{cat} —— {usage}）"


# ====================================================================
# 新增工具：图生图 / 图生视频 / 编辑 / 队列 / 停止
# ====================================================================

@dataclass
class ComfyUIImg2ImgTool(FunctionTool):
    name: str = "comfyui_img2img"
    description: str = ("使用本地ComfyUI图生图/编辑图片——输入1~10张参考图（逗号分隔），根据提示词修改生成新图片。"
                        "★ 只能用分类为「图生图」的工作流；若当前工作流不是「图生图」类，"
                        "必须先用 comfyui_list_workflows 找到「图生图」类工作流，"
                        "再用 comfyui_switch_workflow 切换后再调用本工具。")
    parameters: dict = field(default_factory=lambda: {
        "type": "object", "properties": {
            "prompt": {"type": "string", "description": "提示词，描述要修改的方向，例如'把背景改成红色'"},
            "image_urls": {"type": "string", "description": "1~10张输入图片的URL或本地文件路径，用英文逗号分隔"},
            "denoise": {"type": "number", "description": "降噪值 0.1~0.8，越低变化越小（仅单图时有效）"},
            "workflow": {"type": "string", "description": "工作流名称关键词，不传则用当前工作流"},
            "ratio": {"type": "string", "description": "图片比例", "enum": ["1:1", "3:4", "4:3", "9:16", "16:9", "21:9", "2:3", "3:2"]}
        }, "required": ["prompt", "image_urls"]
    })

    async def run(self, event: AstrMessageEvent, prompt: str, image_urls: str, denoise: float = None, workflow: str = None, ratio: str = None):
        plugin = self._plugin
        if ratio and ratio not in plugin.aspect_ratios: ratio = None
        if workflow:
            for w in plugin._refresh_workflow_list():
                if workflow.lower() in w['name'].lower():
                    plugin._switch_to_workflow(w); break
        urls = [u.strip() for u in image_urls.split(',') if u.strip()][:10]
        if not urls:
            return "❌ 请提供至少一张图片URL"
        saved_paths = []
        for i, url in enumerate(urls):
            sp = plugin._get_image_save_dir() / f"llm_img_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{i}.png"
            if await plugin._download_image(url, sp):
                saved_paths.append(str(sp))
        if not saved_paths:
            # v4.5.3: 回退对话图片收集（直接图/引用图/@头像/最近图片缓存）
            try:
                ev_urls = await plugin._collect_images_from_event(event, max_images=10)
                for i, evu in enumerate(ev_urls):
                    sp = plugin._get_image_save_dir() / f"llm_img_fb_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{i}.png"
                    if await plugin._download_image(evu, sp):
                        saved_paths.append(str(sp))
            except Exception as e:
                logger.debug(f"[ComfyUI] 对话图片回退失败: {e}")
        if not saved_paths:
            return "❌ 未能获取图片：请在对话中发送图片后再试，或提供图片 URL"
        try:
            umo = event.unified_msg_origin if hasattr(event, 'unified_msg_origin') else None
            if umo:
                from astrbot.api.event import MessageChain
                await plugin.context.send_message(umo, MessageChain().message(f"🖼️ 图生图中...（{len(saved_paths)}张图）"))
        except Exception as e: logger.debug(f"[ComfyUI] 操作提示发送失败: {e}")
        cmd_config = {}
        # 单图且给了降噪 → 写 sampler 降噪
        if len(saved_paths) == 1 and denoise is not None:
            try:
                with open(plugin.workflow_path, 'r', encoding='utf-8') as f: wf = json.load(f)
                sn = plugin._find_sampler_node(wf)
                if sn: cmd_config[f'{sn}_denoise'] = denoise
            except Exception as e: logger.debug(f"[ComfyUI] 操作提示发送失败: {e}")
        # 多图 → 补图生图分类的节点配置（若有）
        if len(saved_paths) > 1:
            edit_cfg = dict(plugin.workflow_config.get('__commands__', {}).get('图生图', {}))
            if edit_cfg: cmd_config.update(edit_cfg)
        # 单图传路径字符串（兼容降噪/比例），多图传路径列表
        submit_imgs = saved_paths[0] if len(saved_paths) == 1 else saved_paths
        prompt, _rule_note = plugin._enforce_prompt_rule(prompt, 'imgrev')
        status, text, out_path = await plugin._process_and_submit(prompt, ratio, submit_imgs, cmd_config=cmd_config or None, user_id=event.get_sender_id())
        # 清理临时下载的图片
        for p in saved_paths:
            try: Path(p).unlink(missing_ok=True)
            except Exception as e: logger.debug(f"[ComfyUI] 操作提示发送失败: {e}")
        if status == "ok":
            sent = await plugin._send_image_result(event, f"✨ 图生图完成 当前{text}", out_path, prompt=prompt)
            if sent: return "✅ 图片已发送"
            try:
                from astrbot.api.message_components import Image; from astrbot.api.event import MessageChain
                umo = event.unified_msg_origin if hasattr(event, 'unified_msg_origin') else None
                if umo:
                    await plugin.context.send_message(umo, MessageChain().message("✨ 图生图完成").file_image(out_path))
                    return "✅ 图片已发送"
            except Exception as e: logger.debug(f"[ComfyUI] 操作提示发送失败: {e}")
            return "✅ 图片已生成并发送给用户"
        return text


@dataclass
class ComfyUIVideoTool(FunctionTool):
    name: str = "comfyui_video"
    description: str = ("使用本地ComfyUI生成视频（图生视频）。需要一张输入图片和视频工作流。"
                        "★ 只能用分类为「视频」的工作流；若当前工作流不是「视频」类，"
                        "必须先用 comfyui_list_workflows 找到「视频」类工作流，"
                        "再用 comfyui_switch_workflow 切换后再调用本工具。"
                        "★ image_url 不传时自动使用用户最近在对话中发送/引用的图片。")
    parameters: dict = field(default_factory=lambda: {
        "type": "object", "properties": {
            "prompt": {"type": "string", "description": "提示词，描述视频内容"},
            "image_url": {"type": "string", "description": "输入图片的URL或本地文件路径；不传则自动使用用户最近发送/引用的图片"},
            "workflow": {"type": "string", "description": "工作流名称关键词，不传则用当前工作流"}
        }, "required": []
    })

    async def run(self, event: AstrMessageEvent, image_url: str = "", prompt: str = "", workflow: str = None):
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
        except Exception as e: logger.debug(f"[ComfyUI] 操作提示发送失败: {e}")
        got_img = await plugin._download_image(image_url, save_path) if image_url else False
        if not got_img:
            # v4.5.3: 回退对话图片收集（直接图/引用图/@头像/最近图片缓存）
            try:
                ev_urls = await plugin._collect_images_from_event(event, max_images=1)
                if ev_urls:
                    got_img = await plugin._download_image(ev_urls[0], save_path)
            except Exception as e:
                logger.debug(f"[ComfyUI] 对话图片回退失败: {e}")
        if not got_img:
            return "❌ 未能获取图片：请在对话中发送图片后再试，或提供图片 URL"
        prompt, _rule_note = plugin._enforce_prompt_rule(prompt, 'imgrev')
        status, text, out_path = await plugin._process_and_submit(prompt, None, str(save_path), user_id=event.get_sender_id(), notify_umo=umo)
        if status == "ok":
            try:
                from astrbot.api.message_components import Video
                # out_path 可能是列表(兼容多图)，取第一个文件发送
                vid_path = out_path[0] if isinstance(out_path, list) and out_path else out_path
                umo = event.unified_msg_origin if hasattr(event, 'unified_msg_origin') else None
                # v4.8.0: 飞书视频限 ~10MB，超限压缩后再发
                if plugin._detect_event_platform(event) == 'feishu':
                    vid_path = await asyncio.to_thread(plugin._shrink_video_for_feishu, vid_path)
                if umo:
                    chain = MessageChain(chain=[Video(file=vid_path)])
                    await plugin.context.send_message(umo, chain)
                return "✅ 视频已生成并发送"
            except Exception as e:
                logger.error(f"[ComfyUI] 发送视频失败: {e}")
            return "✅ 视频已生成并发送给用户"
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
                sent = await plugin._send_image_result(event, f"✨ 抽卡完成 当前{text}", path, prompt='')
                if sent: results.append(f"第{i+1}张: ✅ 已发送")
                else: results.append(f"第{i+1}张: ✅ 已生成")
            else:
                results.append(f"第{i+1}张: ❌ {text}")
            if i < count - 1: await asyncio.sleep(1)
        return f"🎴 抽卡结果（共{count}张）：\n" + "\n".join(results)


@dataclass
class ComfyUIListStarsTool(FunctionTool):
    name: str = "comfyui_list_stars"
    description: str = "查看魔导书收藏的标签——按数据源分组列出所有被⭐收藏的标签。适合想知道收藏了什么标签时使用。"
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}, "required": []})

    async def run(self, event: AstrMessageEvent):
        plugin = self._plugin
        stars = plugin.workflow_config.get('__grimoire_stars__', {})
        if not stars:
            return "📭 没有收藏任何标签"
        lines = ["📋 收藏标签列表："]
        for src, names in sorted(stars.items()):
            if names:
                lines.append(f"  {src}: {', '.join(names[:10])}" + (f"...等{len(names)}个" if len(names) > 10 else ""))
        return "\n".join(lines)


@dataclass
class ComfyUIListPresetsTool(FunctionTool):
    name: str = "comfyui_list_presets"
    description: str = "查看魔导书预设列表——列出所有已保存的预设。适合想知道有哪些预设时使用。"
    parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}, "required": []})

    async def run(self, event: AstrMessageEvent):
        plugin = self._plugin
        presets = plugin.workflow_config.get('__grimoire_presets__', {})
        names = list(presets.keys())
        if not names:
            return "📭 没有预设"
        return "📁 预设列表：\n" + "\n".join(f"  #{i+1} {n}" for i, n in enumerate(names))


@dataclass
class ComfyUIDeletePresetTool(FunctionTool):
    name: str = "comfyui_delete_preset"
    description: str = "删除一个魔导书预设——根据预设名称删除。适合用户说'删除xxx预设'时使用。需要用户提供预设名称。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object", "properties": {
            "name": {"type": "string", "description": "预设名称，例如'测试'、'画师特化'"},
        }, "required": ["name"]
    })

    async def run(self, event: AstrMessageEvent, name: str):
        plugin = self._plugin
        name = name.strip()
        presets = plugin.workflow_config.get('__grimoire_presets__', {})
        if name not in presets:
            return f"❌ 预设「{name}」不存在。现有预设：{', '.join(presets.keys()) if presets else '无'}"
        del presets[name]
        plugin.workflow_config['__grimoire_presets__'] = presets
        await plugin._save_workflow_config()
        return f"✅ 已删除预设「{name}」"


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
        except Exception as e: logger.debug(f"[ComfyUI] 操作提示发送失败: {e}")
        status, text, path = await plugin._process_and_submit(prompt, None, user_id=event.get_sender_id())
        if status == "ok":
            sent = await plugin._send_image_result(event, f"✨ 执行完成 当前{text}", path, prompt=prompt)
            if sent: return "✅ 执行完成，图片已发送"
            try:
                from astrbot.api.message_components import Image; from astrbot.api.event import MessageChain
                umo = event.unified_msg_origin if hasattr(event, 'unified_msg_origin') else None
                if umo:
                    await plugin.context.send_message(umo, MessageChain().message("✨ 执行完成").file_image(path))
                    return "✅ 执行完成，图片已发送"
            except Exception as e: logger.debug(f"[ComfyUI] 操作提示发送失败: {e}")
            return "✅ 执行完成并发送给用户"
        return text


@dataclass
class ComfyUIRandomImageTool(FunctionTool):
    name: str = "comfyui_random_image"
    description: str = "从魔导书随机池中随机抽取标签，结合固定的标签，生成随机图片。每次可指定张数。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object", "properties": {
            "count": {"type": "integer", "description": "生成张数，默认1张，最多10张"}
        }, "required": []
    })

    async def run(self, event: AstrMessageEvent, count: int = 1):
        plugin = self._plugin
        import aiohttp
        count = max(1, min(count, 10))
        pins = plugin.workflow_config.get('__grimoire_pins__', {})
        # 兼容新旧 pin 格式
        pin_names = []
        for info in pins.values():
            entries = info if isinstance(info, list) else [info]
            for e in entries:
                if e.get('name'):
                    pin_names.append(e['name'])
        pin_info = f"，固定了: {', '.join(pin_names)}" if pin_names else ""
        if not plugin.current_workflow_name:
            return "❌ 未选中工作流，请先选择工作流"
        # 检查当前工作流是否属于「画」分类
        wf_cats = plugin.workflow_config.get('__wf_categories__', {}) or {}
        wf_cats.update(plugin.workflow_config.get('__workflow_categories__', {}))
        cur_cat = wf_cats.get(plugin.current_workflow_name, '')
        if cur_cat not in ('文生图', '画'):
            return "❌ 随机图只能在「文生图」分类的工作流上使用，请先切换到画图工作流"
        # 1. 先收集所有提示词
        prompts = []
        for i in range(count):
            async with aiohttp.ClientSession() as s:
                async with s.post(f"http://127.0.0.1:{plugin.webui_port}/api/grimoire/random-pick", json={}) as r:
                    data = await r.json()
            if not data.get("ok") or not data.get("tags"):
                return "❌ 随机池为空，请先在魔导书中添加随机池子分类"
            prompts.append(data.get("tags", ""))
        # 2. 提交全部到 ComfyUI（先全部提交，再逐个等结果）
        user_id = event.get_sender_id()
        batch_results = []
        async def submit_one(prompt, idx):
            status, text, path = await plugin._process_and_submit(prompt, None, user_id=user_id, skip_pin_merge=True)
            if status == "ok":
                sent = await plugin._send_image_result(event, f"🎲 随机图 ({idx+1}/{count}){pin_info}", path, prompt=prompt)
                return f"第{idx+1}张{'已发送' if sent else '生成成功'}"
            return f"第{idx+1}张生成失败: {text}"
        tasks = [submit_one(p, i) for i, p in enumerate(prompts)]
        for coro in asyncio.as_completed(tasks):
            batch_results.append(await coro)
        return f"随机图完成: {'; '.join(batch_results)}{pin_info}"


class LLMToolsMixin:
    """LLM 工具:魔导书搜索/管理/固定/随机池等 llm_* 工具方法。"""

    @filter.llm_tool(name="comfyui_search_tags")
    async def llm_search_tags(self, event: AstrMessageEvent, keyword: str, source: str = ""):
        """搜索魔导书中的所有绘画标签。根据关键词在所有数据源或指定数据源中搜索匹配的提示词标签，返回可用于生图的标签。

        Args:
            keyword (string): 搜索关键词，如"初音未来"、"海滩"、"赛博朋克"、"回眸"、"黄金时刻"
            source (string): 可选，数据源名称，如 artists, characters, clothing, lighting, "Normal posture", "Sex positions", environment, framing。不传则搜索全部数据源
        """
        source = source.strip()
        # 指定了数据源 → 单源搜索（供固定/随机池操作用）
        if source:
            name_map = {
                "artists": "anima/artists.json", "characters": "anima/characters.json",
                "clothing": "anima/clothing.json", "lighting": "lighting/lighting.json",
                "normal posture": "pose_action/Normal posture.json", "sex positions": "pose_action/Sex positions.json",
                "environment": "scene/environment.json", "framing": "shot/framing.json",
            }
            src = name_map.get(source.lower().strip())
            if not src:
                src = self._grimoire_find_source_path(source)  # 动态兜底
            if not src:
                return f"未知数据源: {source}"
            fpath = data_dir_resolver() / src.replace('/', os.sep)  # v4.3.0
            items = []
            if fpath.exists():
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        items = json.load(f)
                except Exception:
                    return f"读取数据源失败: {source}"
            else:
                source_name = src.replace(".json", "").split("/")[-1]
                from .anima_data import load_anima_tools_source, _ANIMA_SOURCE_NAMES
                anima_names = [s[1] for s in _ANIMA_SOURCE_NAMES]
                if source_name in anima_names:
                    items = load_anima_tools_source(source_name)
                else:
                    return f"数据源文件不存在: {source}"
            if not isinstance(items, list):
                return "数据格式错误"
            kw = keyword.lower().strip() if keyword else ""
            matched = []
            for it in items:
                name = (it.get("name") or "").lower()
                name_cn = (it.get("name_cn") or "").lower()
                tags = (it.get("tags") or "").lower()
                if not kw or kw in name or kw in name_cn or kw in tags:
                    matched.append(it)
            if not matched:
                return f"在「{source}」中未找到匹配的条目"
            matched = matched[:20]
            lines = [f"「{source}」中的条目 ({len(matched)} 条):"]
            for it in matched:
                display = it.get("name_cn") or it.get("name") or ""
                tag = it.get("tags") or ""
                lines.append(f"  {display}: {tag[:80]}{'...' if len(tag)>80 else ''}")
            return "\n".join(lines)

        # 没有指定 source → 全局搜索全部数据源
        results = []
        # 1. 搜索 Anima 数据
        if self.anima_data.is_loaded:
            results.extend(self.anima_data.search(keyword, top_k=10))
        # 2. 魔导书启用时才搜索数据文件
        if self.grimoire_enabled:
            data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
            kw = keyword.lower().strip()
            for fpath in data_dir.rglob("*.json"):
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        items = json.load(f)
                    if not isinstance(items, list):
                        continue
                    for it in items:
                        name = (it.get("name") or "").lower()
                        name_cn = (it.get("name_cn") or "").lower()
                        tags = (it.get("tags") or "").lower()
                        note = (it.get("note") or "").lower()
                        if kw in name or kw in name_cn or kw in tags or kw in note:
                            rel_path = str(fpath.relative_to(data_dir.parent))
                            results.append({
                                "category": it.get("category") or fpath.stem,
                                "name": it.get("name_cn") or it.get("name") or "",
                                "tags": it.get("tags") or "",
                                "source": rel_path,
                            })
                except Exception:
                    continue
        # 3. 去重（按 name+tags 去重）
        seen = set()
        unique = []
        for r in results:
            key = (r.get("name",""), r.get("tags",""))
            if key not in seen:
                seen.add(key)
                unique.append(r)
        results = unique[:10]
        if not results:
            return f"未找到与「{keyword}」相关的标签"
        lines = [f"搜索「{keyword}」结果:"]
        for r in results:
            src_parts = (r.get("source") or "").replace("\\","/").split("/")
            src = src_parts[-2] if len(src_parts) >= 2 else (src_parts[-1] if src_parts else r.get("category",""))
            lines.append(f"  [{src}] {r['name']}: {r['tags']}")
        lines.append(f"\n共 {len(results)} 条结果")
        return "\n".join(lines)

    @filter.llm_tool(name="comfyui_list_grimoire")
    async def llm_list_grimoire(self, event: AstrMessageEvent):
        """列出魔导书的随机池子来源和已固定的标签。返回所有可用的随机池子名称和已固定的标签信息。"""
        wc = self.workflow_config
        pool = wc.get('__grimoire_rand_pool__', [])
        pins = wc.get('__grimoire_pins__', {})
        enabled = self.grimoire_enabled
        if not pool and not pins:
            return f"魔导书当前{'已启用' if enabled else '已禁用'}，随机池和固定标签均为空"
        lines = [f"魔导书: {'✅ 已启用' if enabled else '❌ 已禁用'}"]
        all_sources = self._grimoire_sources()
        lines.append(f"\n📦 可用数据源 ({len(all_sources)} 个):")
        for s in all_sources:
            src_path = s["path"]
            name = s["name"]
            in_pool = "⭐ 池中" if src_path in pool else ""
            pinned_name = ""
            for ps, pi in pins.items():
                if ps.replace('\\','/') == src_path:
                    pinned_name = f"📌 {pi.get('name','')}"
                    break
            lines.append(f"  {name}{' ('+s['dir']+')' if s['dir'] else ''}: {s['count']}条 {in_pool} {pinned_name}".strip())
        if pool:
            lines.append(f"\n🎲 随机池中的源 ({len(pool)} 个): {', '.join(s.split('/')[-1].replace('.json','') for s in pool)}")
        if pins:
            lines.append(f"\n📌 固定标签:")
            for ps, pi in pins.items():
                src_name = ps.replace('\\','/').split('/')[-1].replace('.json','')
                lines.append(f"  {src_name} → {pi.get('name','')}: {pi.get('tags','')}")
        return "\n".join(lines)

    @filter.llm_tool(name="comfyui_manage_pool")
    async def llm_manage_pool(self, event: AstrMessageEvent, action: str, source: str):
        """管理魔导书随机池。添加或移除数据源到随机池中，随机池中的数据源会参与随机抽取。

        Args:
            action (string): 操作类型，"add" 表示添加到随机池，"remove" 表示从随机池移除
            source (string): 数据源名称，可选值: artists, characters, clothing, lighting, "Normal posture", "Sex positions", environment, framing
        """
        if not self.grimoire_enabled:
            return "魔导书已禁用，请先在 WebUI 中启用魔导书"
        name_map = {
            "artists": "anima/artists.json", "characters": "anima/characters.json",
            "clothing": "anima/clothing.json", "lighting": "lighting/lighting.json",
            "normal posture": "pose_action/Normal posture.json", "sex positions": "pose_action/Sex positions.json",
            "environment": "scene/environment.json", "framing": "shot/framing.json",
        }
        src = name_map.get(source.lower().strip())
        if not src:
            src = self._grimoire_find_source_path(source)
        if not src:
            return f"未知数据源: {source}，可选: {', '.join(name_map.keys())}"
        pool = list(self.workflow_config.get('__grimoire_rand_pool__', []))
        action = action.lower().strip()
        if action == "add":
            if src in pool:
                return f"「{source}」已在随机池中"
            pool.append(src)
            self.workflow_config['__grimoire_rand_pool__'] = pool
            await self._save_workflow_config()
            return f"✅ 已将「{source}」添加到随机池"
        elif action == "remove":
            if src not in pool:
                return f"「{source}」不在随机池中"
            pool.remove(src)
            self.workflow_config['__grimoire_rand_pool__'] = pool
            await self._save_workflow_config()
            return f"✅ 已将「{source}」从随机池移除"
        else:
            return f"未知操作: {action}，请使用 add 或 remove"

    async def llm_rand_pool_remove(self, event: AstrMessageEvent, source: str):
        """将某个数据源从魔导书随机池中移除。移除后该数据源不再参与随机抽取。

        Args:
            source (string): 数据源名称，可选值: artists, characters, clothing, lighting, "Normal posture", "Sex positions", environment, framing
        """
        if not self.grimoire_enabled:
            return "魔导书已禁用"
        name_map = {
            "artists": "anima/artists.json", "characters": "anima/characters.json",
            "clothing": "anima/clothing.json", "lighting": "lighting/lighting.json",
            "normal posture": "pose_action/Normal posture.json", "sex positions": "pose_action/Sex positions.json",
            "environment": "scene/environment.json", "framing": "shot/framing.json",
        }
        src = name_map.get(source.lower().strip())
        if not src:
            return f"未知数据源: {source}"
        pool = list(self.workflow_config.get('__grimoire_rand_pool__', []))
        if src not in pool:
            return f"「{source}」不在随机池中"
        pool.remove(src)
        self.workflow_config['__grimoire_rand_pool__'] = pool
        await self._save_workflow_config()
        return f"✅ 已将「{source}」从随机池移除"

    @filter.llm_tool(name="comfyui_manage_pin")
    async def llm_manage_pin(self, event: AstrMessageEvent, action: str, source: str, name: str = "", tags: str = ""):
        """管理魔导书的固定标签。固定某个数据源中的一条标签后，该标签会固定出现在每次随机/生图中。

        Args:
            action (string): 操作类型，"pin" 表示固定标签，"unpin" 表示取消固定
            source (string): 数据源名称，如 artists, characters, clothing
            name (string): 要固定的条目名称，取消固定时不需传
            tags (string): 要固定条目对应的英文提示词标签，取消固定时不需传
        """
        if not self.grimoire_enabled:
            return "魔导书已禁用"
        name_map = {
            "artists": "anima/artists.json", "characters": "anima/characters.json",
            "clothing": "anima/clothing.json", "lighting": "lighting/lighting.json",
            "normal posture": "pose_action/Normal posture.json", "sex positions": "pose_action/Sex positions.json",
            "environment": "scene/environment.json", "framing": "shot/framing.json",
        }
        src = name_map.get(source.lower().strip())
        if not src:
            src = self._grimoire_find_source_path(source)
        if not src:
            return f"未知数据源: {source}"
        action = action.lower().strip()
        if action == "pin":
            if not name or not tags:
                return "固定标签需要提供 name 和 tags 参数"
            source_key = src.replace('/', '\\')
            pins = dict(self.workflow_config.get('__grimoire_pins__', {}))
            # 兼容：统一用列表格式
            if source_key not in pins:
                pins[source_key] = []
            elif isinstance(pins[source_key], dict):
                pins[source_key] = [pins[source_key]]
            if not any(e.get('name') == name for e in pins[source_key]):
                pins[source_key].append({"name": name, "tags": tags})
            self.workflow_config['__grimoire_pins__'] = pins
            await self._save_workflow_config()
            return f"✅ 已固定 {source}/{name}: {tags}"
        elif action == "unpin":
            source_key = src.replace('/', '\\')
            pins = dict(self.workflow_config.get('__grimoire_pins__', {}))
            matched = [k for k in pins if k.replace('\\','/') == src]
            if not matched:
                return f"「{source}」没有固定的标签"
            for k in matched:
                pins.pop(k, None)
            self.workflow_config['__grimoire_pins__'] = pins
            await self._save_workflow_config()
            return f"✅ 已取消固定 {source}"
        else:
            return f"未知操作: {action}，请使用 pin 或 unpin"

    async def llm_unpin_tag(self, event: AstrMessageEvent, source: str):
        """取消固定某个数据源的标签。取消后该数据源不再固定出现在随机/生图中。

        Args:
            source (string): 数据源名称，如 artists, characters, clothing
        """
        if not self.grimoire_enabled:
            return "魔导书已禁用"
        name_map = {
            "artists": "anima/artists.json", "characters": "anima/characters.json",
            "clothing": "anima/clothing.json", "lighting": "lighting/lighting.json",
            "normal posture": "pose_action/Normal posture.json", "sex positions": "pose_action/Sex positions.json",
            "environment": "scene/environment.json", "framing": "shot/framing.json",
        }
        src = name_map.get(source.lower().strip())
        if not src:
            return f"未知数据源: {source}"
        source_key = src.replace('/', '\\')
        pins = dict(self.workflow_config.get('__grimoire_pins__', {}))
        matched = [k for k in pins if k.replace('\\','/') == src]
        if not matched:
            return f"「{source}」没有固定的标签"
        for k in matched:
            pins.pop(k, None)
        self.workflow_config['__grimoire_pins__'] = pins
        await self._save_workflow_config()
        return f"✅ 已取消固定 {source}"

    async def llm_search_grimoire_items(self, event: AstrMessageEvent, source: str, keyword: str = ""):
        """在魔导书的某个数据源中搜索条目。返回匹配的条目名称和标签，供固定/添加到随机池用。

        Args:
            source (string): 数据源名称，如 artists, characters, clothing, lighting, "Normal posture", "Sex positions", environment, framing
            keyword (string): 搜索关键词，可选，为空则列出所有条目
        """
        if not self.grimoire_enabled:
            return "魔导书已禁用"
        name_map = {
            "artists": "anima/artists.json", "characters": "anima/characters.json",
            "clothing": "anima/clothing.json", "lighting": "lighting/lighting.json",
            "normal posture": "pose_action/Normal posture.json", "sex positions": "pose_action/Sex positions.json",
            "environment": "scene/environment.json", "framing": "shot/framing.json",
        }
        src = name_map.get(source.lower().strip())
        if not src:
            return f"未知数据源: {source}"
        fpath = data_dir_resolver() / src.replace('/', os.sep)  # v4.3.0
        items = []
        if fpath.exists():
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    items = json.load(f)
            except Exception:
                return f"读取数据源失败: {source}"
        else:
            # JSON 文件不存在 → 尝试从 Anima-Tools JS 加载（只读源）
            source_name = src.replace(".json", "").split("/")[-1]
            from .anima_data import load_anima_tools_source, _ANIMA_SOURCE_NAMES
            anima_names = [s[1] for s in _ANIMA_SOURCE_NAMES]
            if source_name in anima_names:
                items = load_anima_tools_source(source_name)
            else:
                return f"数据源文件不存在: {source}"
        if not isinstance(items, list):
            return "数据格式错误"
        kw = keyword.lower().strip() if keyword else ""
        matched = []
        for it in items:
            name = (it.get("name") or "").lower()
            name_cn = (it.get("name_cn") or "").lower()
            tags = (it.get("tags") or "").lower()
            if not kw or kw in name or kw in name_cn or kw in tags:
                matched.append(it)
        if not matched:
            return f"在「{source}」中未找到匹配的条目"
        matched = matched[:30]
        lines = [f"「{source}」中的条目 ({len(matched)} 条):"]
        for it in matched:
            display = it.get("name_cn") or it.get("name") or ""
            tag = it.get("tags") or ""
            lines.append(f"  {display}: {tag[:80]}{'...' if len(tag)>80 else ''}")
        return "\n".join(lines)

    @filter.llm_tool(name="comfyui_get_prompt")
    async def llm_get_prompt(self, event: AstrMessageEvent, message_id: str = ""):
        """查询某条图片消息对应的生图提示词。用于从已生成的图片中提取完整描述。

        Args:
            message_id (string): 图片消息的 ID。如果不传，会自动从你回复的图片消息中提取。
        """
        if not message_id:
            # 自动从事件上下文中提取回复的消息 ID
            for comp in event.get_messages():
                d = comp.__dict__ if hasattr(comp, '__dict__') else {}
                if d.get('type') == 'Reply':
                    message_id = str(d.get('id', '') or '')
                    break
        if not message_id:
            return "请提供图片消息的 message_id"

        results = []
        for r in reversed(self._prompt_log):
            if r['message_id'] == message_id:
                results.append(f"提示词: {r['prompt']}")
                break

        if not results:
            return f"未找到 message_id 为「{message_id}」的提示词记录（可能已过期，默认保留 {self.prompt_log_days} 天）"
        return "\n".join(results)
