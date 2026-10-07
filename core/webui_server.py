# -*- coding: utf-8 -*-
"""webui_server.py — WebUIMixin（WebUI 伺服、全部 /api 路由、画廊/媒体/工作流参数接口）
v4.13.5 拆分自 main.py（最大的一块），行为不变：Mixin 间仍经 self 运行时互通。
"""
import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import time
import traceback
from datetime import datetime
from pathlib import Path

import aiohttp
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger
from aiohttp import web

from .anima_data import load_anima_tools_source, _ANIMA_SOURCE_NAMES
from .data_paths import data_dir_resolver, migration_status as _data_migration_status

# 插件根目录（本文件在 core/ 子包内）——所有静态资源/前端文件的基准路径
PLUGIN_ROOT = Path(__file__).resolve().parent.parent


class WebUIMixin:
    """WebUI 服务与 API:启动 aiohttp 服务、页面与全部 /api/* 处理器。"""
    # WebUI 直连模式：不再 302 跳转到带 _v 版本参数的 URL，直接返回页面。
    # （原缓存熔断跳转会让 http://192.168.0.102:8898/ 变成 302 → /?_v=…，
    #   用户要求恢复直连地址。缓存仍由下方 Cache-Control 头控制。）
    WEBUI_CACHE_TAG = "v4.0.0"

    async def _serve_webui(self, r):
        resp = web.FileResponse(PLUGIN_ROOT / 'web' / 'webui.html')
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        # 强制清除该源旧缓存：手机浏览器(QQ内置/X5)常无视 no-cache 强缓存旧页面，
        # 每次访问都清一次，确保用户拿到最新版（Clear-Site-Data 仅作用于本页面源，不破坏其它站点）
        resp.headers['Clear-Site-Data'] = '"cache"'
        return resp

    async def _serve_webui_css(self, r):
        resp = web.FileResponse(PLUGIN_ROOT / 'web' / 'webui.css')
        resp.headers['Cache-Control'] = 'no-cache, must-revalidate'
        return resp

    async def _serve_webui_js(self, r):
        resp = web.FileResponse(PLUGIN_ROOT / 'web' / 'webui.js')
        resp.headers['Cache-Control'] = 'no-cache, must-revalidate'
        return resp

    async def _serve_favicon(self, r):
        # v4.3.8: 优先回 AI 生成的多尺寸 PNG ico；无则回 SVG 兜底
        # v4.9.2: 静态图片统一收进 assets/ 子目录（插件根目录结构整理）
        ico = PLUGIN_ROOT / 'assets' / 'favicon.ico'
        if ico.exists():
            resp = web.FileResponse(ico, headers={'Cache-Control': 'public, max-age=86400'})
            return resp
        resp = web.FileResponse(PLUGIN_ROOT / 'assets' / 'favicon.svg', content_type='image/svg+xml')
        resp.headers['Cache-Control'] = 'public, max-age=86400'
        return resp

    async def _serve_favicon_png(self, r):
        """v4.3.8: 伺服 favicon-16x16/32x32/192.png（白名单校验防路径穿越）"""
        size = r.match_info.get('size', '')
        if size not in ('16x16', '32x32', '192', '512'):
            raise web.HTTPNotFound()
        f = PLUGIN_ROOT / 'assets' / f'favicon-{size}.png'
        if not f.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(f, headers={'Cache-Control': 'public, max-age=86400'})

    async def _serve_theme_bg(self, r):
        """v4.4.0: 伺服「心」主题昼夜背景横幅（白名单校验防路径穿越）"""
        mode = r.match_info.get('mode', '')
        if mode not in ('day', 'night') and not mode.startswith('icon-'):
            raise web.HTTPNotFound()
        f = PLUGIN_ROOT / 'assets' / f'theme-xin-{mode}.webp'
        if not f.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(f, headers={'Cache-Control': 'public, max-age=604800', 'Content-Type': 'image/webp'})

    async def _serve_apple_icon(self, r):
        """v4.3.8: iOS 添加到主屏用 180x180 图标"""
        f = PLUGIN_ROOT / 'assets' / 'apple-touch-icon.png'
        if not f.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(f, headers={'Cache-Control': 'public, max-age=86400'})

    async def _webui_k2gen_data(self, request):
        """返回 K2 完整词库数据块（k2gen/data.js 的 UTF-8 源码），供前端 new Function 构造后自动组句"""
        # 优先读插件目录下 k2gen/data.js；不存在则退回 data/k2/ 数据（保证接口可用）
        candidates = [
            PLUGIN_ROOT / "k2gen" / "data.js",
            PLUGIN_ROOT / "data" / "k2",
        ]
        for cand in candidates:
            if cand.is_file():
                try:
                    src = cand.read_text(encoding='utf-8')
                    return web.json_response({"ok": True, "source": src})
                except Exception as e:
                    return web.json_response({"ok": False, "error": f"读取词库失败: {e}"})
        return web.json_response({"ok": False, "error": "k2gen/data.js 不存在"})

    # =========================================================================
    # 【K2 控制】WebUI/QQ 共享的 K2 字段锁定 + NSFW 开关
    #   - __k2_locks__ : {fieldId: {mode:'random'|'fixed'|'off', value:''}}  字段锁定
    #   - __k2_nsfw__  : bool                                             NSFW 开关
    #   前端「魔导书 → K2 字段锁定」面板写入；QQ /随机图 组句(_k2_compose_prompt)读取。
    #   对应路由：GET/POST /api/k2-locks 、 POST /api/k2-nsfw
    # =========================================================================
    async def _webui_get_k2_locks(self, request):
        """返回当前 K2 字段锁定设定（前端面板初始化用）。"""
        locks = self.workflow_config.get("__k2_locks__", {}) or {}
        return web.json_response({"locks": locks})

    async def _webui_save_k2_locks(self, request):
        """保存 K2 字段锁定设定。body: {locks: {fieldId: {mode:'random'|'fixed'|'off', value:''}}}"""
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        locks = data.get("locks", {})
        if not isinstance(locks, dict):
            return web.json_response({"ok": False, "error": "locks 必须为对象"})
        async with self._config_lock:
            self.workflow_config["__k2_locks__"] = locks
        await self._save_workflow_config()
        return web.json_response({"ok": True, "locks": locks})

    async def _webui_save_k2_nsfw(self, request):
        """保存 K2 NSFW 开关状态（前端切换时调用，QQ /随机图 组句遵循）。body: {"nsfw": true/false}"""
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        on = bool(data.get("nsfw", False))
        async with self._config_lock:
            self.workflow_config["__k2_nsfw__"] = on
        # v4.13.1: 同步 anima 模式的 NSFW——界面上可见的 NSFW 开关只有一个，
        # 用户开了就该两个引擎都生效（此前 anima_nsfw 藏在管理后台，开了 K2 的等于没开）
        try:
            self._save_local_config({"anima_nsfw": on})
            self.anima_nsfw = on
        except Exception as e:
            logger.debug(f"[ComfyUI] 同步 anima_nsfw 失败: {e}")
        await self._save_workflow_config()
        return web.json_response({"ok": True, "nsfw": on})

    def _k2_compose_prompt(self, nsfw=False):
        """【K2 控制】后端用 K2 引擎组中文成句（QQ /随机图 K2 模式）。
        遵守 __k2_locks__（字段锁定/排除）与 __k2_nsfw__（NSFW），见上方 K2 控制分区头。
        （复刻 webui k2Compose 的 SFW/NSFW 行为）。

        引擎是纯 JS（k2gen/engine.js + data.js），在 proot 容器内 node 可用，
        故通过 `node k2gen/cli.js --json` 子进程运行已验证引擎组句，
        返回 {ok, text, mode, seed}；失败返回 {ok: False, error}。
        """
        import subprocess
        base = PLUGIN_ROOT
        k2_dir = base / "k2gen"
        if not (k2_dir / "cli.js").is_file():
            k2_dir = base / "data" / "k2"
        cli = k2_dir / "cli.js"
        if not cli.is_file():
            return {"ok": False, "error": "K2 引擎不存在（缺 k2gen/cli.js 或 data/k2/cli.js）"}
        # proot 容器内 /usr/bin/node 通常已在 PATH；兜底到手机 proot 绝对路径
        node = shutil.which("node") or "/data/data/com.termux/files/usr/var/lib/proot-distro/containers/ubuntu/rootfs/usr/bin/node"
        cmd = [node, str(cli), "--json"]
        # NSFW：优先用持久化的 __k2_nsfw__（前端开关同步保存），保证 QQ /随机图 与 WebUI 一致；
        # 调用方显式传 nsfw=True 仍可强制覆盖
        k2_nsfw = self.workflow_config.get("__k2_nsfw__", False)
        if nsfw or k2_nsfw:
            cmd.append("--nsfw")
        # K2 字段锁定设定：fixed → --set fieldId=value（锁定值）；off → --set fieldId=不启用（排除）
        # random 模式的字段不传，保持引擎整体重掷
        locks = self.workflow_config.get("__k2_locks__", {}) or {}
        if isinstance(locks, dict):
            for fid, cfg in locks.items():
                if not isinstance(cfg, dict):
                    continue
                mode = cfg.get("mode")
                if mode == "fixed":
                    val = (cfg.get("value") or "").strip()
                    if val:
                        cmd += ["--set", f"{fid}={val}"]
                elif mode == "off":
                    cmd += ["--set", f"{fid}=不启用"]
        try:
            # 显式 utf-8：node 输出的中文成句是 UTF-8，系统 locale（如 Windows GBK）默认解码会崩
            p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=30, cwd=str(k2_dir))
            if p.returncode != 0:
                return {"ok": False, "error": f"K2 引擎退出码 {p.returncode}: {(p.stderr or '')[-300:]}"}
            out = json.loads(p.stdout)
            if isinstance(out, list):
                out = out[0] if out else {}
            text = (out.get("text") or "").strip()
            if not text:
                return {"ok": False, "error": "K2 引擎组句为空"}
            return {"ok": True, "text": text, "mode": out.get("mode", "SFW"), "seed": out.get("seed")}
        except Exception as e:
            return {"ok": False, "error": f"K2 引擎执行失败: {e}"}

    def _start_webui(self):
        app = web.Application(client_max_size=64 * 1024 * 1024)  # 64MB：工作流预览图 base64 大图（v4.4.0，20MB 曾导致大图保存失败）
        app.router.add_get('/', self._serve_webui)
        app.router.add_get('/webui.css', self._serve_webui_css)
        app.router.add_get('/webui.js', self._serve_webui_js)
        app.router.add_get('/api/k2gen/data', self._webui_k2gen_data)
        # K2 字段锁定设定（前端面板保存，QQ /随机图 后端组句时遵守）
        app.router.add_get('/api/k2-locks', self._webui_get_k2_locks)
        app.router.add_post('/api/k2-locks', self._webui_save_k2_locks)
        # K2 NSFW 开关（前端切换时同步保存，QQ /随机图 后端组句遵循）
        app.router.add_post('/api/k2-nsfw', self._webui_save_k2_nsfw)
        app.router.add_get('/favicon.ico', self._serve_favicon)  # 返回真正的图标文件
        app.router.add_get('/favicon-{size}.png', self._serve_favicon_png)  # v4.3.8 多尺寸 PNG
        app.router.add_get('/apple-touch-icon.png', self._serve_apple_icon)
        app.router.add_get('/theme-xin-{mode}.webp', self._serve_theme_bg)  # v4.4.0 心主题昼夜横幅
        app.router.add_get('/api/config', lambda r: web.json_response({
            "comfyui_url": self.comfyui_url,
            "workflow_dir": str(self.workflow_dir) if self.workflow_dir.parts else "",
            "output_dir": str(self.output_dir) if self.output_dir.parts else "",
            "webui_port": self.webui_port,
            "webui_lan": self.webui_lan,
            "webui_ipv6": self.webui_ipv6,
            "show_prompt_on_image": self.show_prompt_on_image,
            "anima_spec_enhance": self._load_local_config().get("anima_spec_enhance", True),
            "anima_nsfw": self._load_local_config().get("anima_nsfw", False),
            "send_platform": getattr(self, "send_platform", "auto"),
            "color_scheme": self._load_local_config().get("color_scheme", "cyberpunk-orange"),
            "random_pick_mode": self._load_local_config().get("random_pick_mode", "all"),
            "k2_compose_mode": self._load_local_config().get("k2_compose_mode", "anima"),
            "deploy_mode": self._load_local_config().get("deploy_mode", "windows"),
            "target_qq": self._load_local_config().get("target_qq", ""),
            "target_platform": self._load_local_config().get("target_platform", "qq"),
            "target_id": self._load_local_config().get("target_id", "") or self._load_local_config().get("target_qq", ""),
            "target_group": self._load_local_config().get("target_group", False),
        }))
        app.router.add_post('/api/config', self._webui_save_config)
        app.router.add_post('/api/generate', self._webui_generate)
        # v4.3.0 云端数据库同步（GitHub 公开镜像 words/ + anima_cache/）
        app.router.add_get('/api/gitee-sync/status', self._webui_gitee_sync_status)
        app.router.add_post('/api/gitee-sync/run', self._webui_gitee_sync_run)
        app.router.add_get('/api/gitee-sync/progress', self._webui_gitee_sync_progress)
        # v4.3.0 数据分离状态（外部数据目录布局/插件体积）
        app.router.add_get('/api/data-layout', self._webui_data_layout)
        app.router.add_post('/api/deploy-mode', self._webui_set_deploy_mode)
        app.router.add_get('/api/groups', self._webui_get_groups)
        app.router.add_post('/api/groups/auto-apply', self._webui_groups_auto_apply)
        app.router.add_post('/api/debug-group-chain', self._webui_debug_group_chain)
        app.router.add_get('/api/proxy', self._webui_proxy)
        app.router.add_get('/api/workflows', lambda r: web.json_response(self._refresh_workflow_list() or []))
        app.router.add_get('/api/workflows/all', lambda r: web.json_response(self.workflow_list_cache or []))
        app.router.add_get('/api/workflow-preview', self._webui_get_workflow_preview)
        app.router.add_post('/api/workflow-preview', self._webui_save_workflow_preview)
        app.router.add_post('/api/workflows/switch', self._webui_switch_workflow)
        app.router.add_post('/api/workflows/toggle-hidden', self._webui_toggle_hidden)
        app.router.add_get('/api/comfy-models', self._webui_get_models)
        app.router.add_get('/api/loras', self._webui_get_loras)
        app.router.add_get('/api/lora-metadata', self._webui_get_lora_metadata)
        app.router.add_post('/api/lora-metadata/refresh', self._webui_refresh_lora_metadata)
        app.router.add_get('/api/lora-preview', self._webui_lora_preview)
        # Krea / easy-use 风格预设接口（HTML 控制工作流的风格选择器）
        app.router.add_get('/api/style-libs', self._webui_get_style_libs)
        app.router.add_get('/api/style-list', self._webui_get_style_list)
        app.router.add_get('/api/style-preview', self._webui_style_preview)
        app.router.add_get('/api/view-input', self._webui_view_input)
        app.router.add_get('/api/progress', self._webui_get_progress)
        app.router.add_post('/api/open-dir', self._webui_open_dir)
        app.router.add_post('/api/pick-dir', self._webui_pick_dir)
        app.router.add_post('/api/workflow-dir', self._webui_set_workflow_dir)
        app.router.add_get('/api/workflow-params', self._webui_get_workflow_params)
        app.router.add_get('/api/workflow-params-config', self._webui_get_workflow_params_config)
        app.router.add_post('/api/workflow-params', self._webui_save_workflow_params)
        app.router.add_get('/api/llm-templates', self._webui_get_llm_templates)
        app.router.add_post('/api/llm-templates', self._webui_save_llm_templates)
        app.router.add_get('/api/workflow-bind', self._webui_get_bindings)
        app.router.add_post('/api/workflow-bind', self._webui_save_binding)
        app.router.add_post('/api/workflow-bind/delete', self._webui_delete_binding)
        app.router.add_post('/api/wf-category', self._webui_set_wf_category)
        app.router.add_post('/api/wf-category-order', self._webui_save_category_order)
        app.router.add_post('/api/wf-delete', self._webui_delete_workflow)
        app.router.add_post('/api/wf-add', self._webui_add_workflows)
        app.router.add_post('/api/upload-image', self._webui_upload_image)
        app.router.add_post('/api/upload-media', self._webui_upload_media)
        app.router.add_post('/api/clear-node-input', self._webui_clear_node_input)
        app.router.add_post('/api/interrupt', self._webui_interrupt)
        app.router.add_get('/api/context-workflows', self._webui_get_context_workflows)
        app.router.add_post('/api/context-workflow', self._webui_set_context_workflow)
        # WebUI 质量与比例 API
        app.router.add_post('/api/set-quality', self._webui_set_quality)
        app.router.add_post('/api/set-ratio', self._webui_set_ratio)
        app.router.add_post('/api/set-official-res', self._webui_set_official_res)
        app.router.add_post('/api/set-duration', self._webui_set_duration)
        app.router.add_post('/api/reset', self._webui_reset_all)
        # 背景设置 API
        app.router.add_post('/api/bg/save', self._webui_bg_save)
        app.router.add_get('/api/bg/image', self._webui_bg_image)
        # 画廊 API
        app.router.add_get('/api/gallery', self._webui_get_gallery)
        app.router.add_get('/api/gallery/file', self._webui_gallery_file)
        app.router.add_get('/api/gallery/prompt', self._webui_gallery_prompt)  # v4.10.0 画廊查提示词
        app.router.add_post('/api/gallery/delete', self._webui_gallery_delete)
        # Anima 数据搜索 API
        app.router.add_get('/api/anima/search', self._webui_anima_search)
        app.router.add_get('/api/anima/stats', self._webui_anima_stats)
        # 魔导书 API
        app.router.add_get('/api/grimoire/sources', self._webui_grimoire_sources)
        app.router.add_get('/api/grimoire/data', self._webui_grimoire_data)
        app.router.add_get('/api/grimoire/categories', self._webui_grimoire_categories)
        app.router.add_post('/api/grimoire/data', self._webui_grimoire_add)
        app.router.add_put('/api/grimoire/data', self._webui_grimoire_update)
        app.router.add_delete('/api/grimoire/data', self._webui_grimoire_delete)
        app.router.add_post('/api/grimoire/source', self._webui_grimoire_new_source)
        app.router.add_put('/api/grimoire/source', self._webui_grimoire_rename_source)
        app.router.add_delete('/api/grimoire/source', self._webui_grimoire_delete_source)
        app.router.add_post('/api/grimoire/source-order', self._webui_grimoire_save_source_order)
        app.router.add_post('/api/grimoire/dir-order', self._webui_grimoire_save_dir_order)
        app.router.add_post('/api/grimoire/category', self._webui_grimoire_new_category)
        app.router.add_put('/api/grimoire/category', self._webui_grimoire_rename_category)
        app.router.add_delete('/api/grimoire/category', self._webui_grimoire_delete_category)
        app.router.add_get('/api/grimoire/status', self._webui_grimoire_status)
        app.router.add_post('/api/grimoire/status', self._webui_grimoire_toggle)
        app.router.add_post('/api/grimoire/batch-import', self._webui_grimoire_batch_import)
        app.router.add_post('/api/grimoire/batch-delete', self._webui_grimoire_batch_delete)
        app.router.add_get('/api/grimoire/pins', self._webui_grimoire_get_pins)
        app.router.add_post('/api/grimoire/pin', self._webui_grimoire_set_pin)
        app.router.add_get('/api/grimoire/rand-pool', self._webui_grimoire_get_rand_pool)
        app.router.add_post('/api/grimoire/rand-pool', self._webui_grimoire_set_rand_pool)
        app.router.add_post('/api/grimoire/random-pick', self._webui_grimoire_random_pick)
        # 预设 API
        app.router.add_get('/api/grimoire/presets', self._webui_grimoire_get_presets)
        app.router.add_post('/api/grimoire/presets', self._webui_grimoire_save_preset)
        app.router.add_post('/api/grimoire/presets/apply', self._webui_grimoire_apply_preset)
        app.router.add_post('/api/grimoire/presets/delete', self._webui_grimoire_delete_preset)
        # 模型切换 API
        app.router.add_get('/api/grimoire/models', self._webui_grimoire_get_models)
        app.router.add_get('/api/grimoire/model', self._webui_grimoire_get_model)
        app.router.add_post('/api/grimoire/model', self._webui_grimoire_set_model)
        # 收藏（Star）API
        app.router.add_get('/api/grimoire/stars', self._webui_grimoire_get_stars)
        app.router.add_post('/api/grimoire/stars', self._webui_grimoire_set_stars)
        # 魔导书图片缓存 API
        app.router.add_get('/api/grimoire/cache-status', self._webui_grimoire_cache_status)
        app.router.add_post('/api/grimoire/cache-all', self._webui_grimoire_cache_all)
        app.router.add_get('/api/grimoire/cache-progress', self._webui_grimoire_cache_progress)
        app.router.add_get('/cache/{filename}', self._webui_cache_image)
        self._webui_app = app
        self._webui_runner = web.AppRunner(app)
        asyncio.get_event_loop().create_task(self._run_webui(self._webui_runner))

    async def _run_webui(self, runner):
        await runner.setup()
        self._webui_site_local = None
        self._webui_site_ipv6 = None
        if self.webui_lan:
            # 0.0.0.0 已覆盖 127.0.0.1，不需要分开绑
            self._webui_site_local = web.TCPSite(runner, '0.0.0.0', self.webui_port)
            await self._webui_site_local.start()
            logger.info(f"[ComfyUI] WebUI 已启动 (0.0.0.0:{self.webui_port}, 局域网可用)")
        else:
            self._webui_site_local = web.TCPSite(runner, '127.0.0.1', self.webui_port)
            await self._webui_site_local.start()
            logger.info(f"[ComfyUI] WebUI 已启动 (127.0.0.1:{self.webui_port}, 仅本机)")
        if self.webui_ipv6:
            self._webui_site_ipv6 = web.TCPSite(runner, '::', self.webui_port)
            await self._webui_site_ipv6.start()
            logger.info(f"[ComfyUI] IPv6 已启动 (:::{self.webui_port})")

    async def _toggle_lan(self, enable: bool):
        """动态切换本机/局域网：停掉当前 site，重新绑定对应地址"""
        try:
            if self._webui_site_local:
                await self._webui_site_local.stop()
                self._webui_site_local = None
            if enable:
                self._webui_site_local = web.TCPSite(self._webui_runner, '0.0.0.0', self.webui_port)
                await self._webui_site_local.start()
                logger.info(f"[ComfyUI] 局域网访问已开启 (0.0.0.0:{self.webui_port})")
            else:
                self._webui_site_local = web.TCPSite(self._webui_runner, '127.0.0.1', self.webui_port)
                await self._webui_site_local.start()
                logger.info(f"[ComfyUI] 局域网访问已关闭 (仅本机 127.0.0.1:{self.webui_port})")
        except Exception as e:
            logger.warning(f"[ComfyUI] 切换局域网访问失败: {e}")

    async def _toggle_ipv6(self, enable: bool):
        """动态开启/关闭 IPv6 访问"""
        try:
            if self._webui_site_ipv6:
                await self._webui_site_ipv6.stop()
                self._webui_site_ipv6 = None
            if enable:
                self._webui_site_ipv6 = web.TCPSite(self._webui_runner, '::', self.webui_port)
                await self._webui_site_ipv6.start()
                logger.info(f"[ComfyUI] IPv6 访问已开启 (:::{self.webui_port})")
            else:
                logger.info(f"[ComfyUI] IPv6 访问已关闭")
        except Exception as e:
            logger.warning(f"[ComfyUI] 切换 IPv6 访问失败: {e}")

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
        if "webui_port" in d:
            try:
                port = int(d["webui_port"])
                if not (1 <= port <= 65535):
                    return web.json_response({"ok": False, "error": "端口范围 1-65535"})
                self.webui_port = port
                updates["webui_port"] = port
                await self._toggle_lan(self.webui_lan)
                await self._toggle_ipv6(self.webui_ipv6)
            except ValueError:
                return web.json_response({"ok": False, "error": "端口必须是数字"})
        if "show_prompt_on_image" in d:
            val = d["show_prompt_on_image"]
            self.show_prompt_on_image = bool(val)
            updates["show_prompt_on_image"] = self.show_prompt_on_image
        if "anima_spec_enhance" in d:
            val = bool(d["anima_spec_enhance"])
            updates["anima_spec_enhance"] = val
        if "anima_nsfw" in d:
            val = bool(d["anima_nsfw"])
            updates["anima_nsfw"] = val
        if "send_platform" in d:
            val = str(d["send_platform"] or "auto").lower()
            if val not in ("auto", "qq", "feishu", "both"):
                val = "auto"
            self.send_platform = val
            updates["send_platform"] = val
        if "color_scheme" in d:
            updates["color_scheme"] = d["color_scheme"]
        if "random_pick_mode" in d:
            val = d["random_pick_mode"]
            self.workflow_config["random_pick_mode"] = val
            updates["random_pick_mode"] = val
        if "target_qq" in d:
            updates["target_qq"] = str(d["target_qq"] or "").strip()
        if "target_platform" in d:
            val = str(d["target_platform"] or "qq").lower()
            if val not in ("qq", "feishu"):
                val = "qq"
            self.target_platform = val
            updates["target_platform"] = val
        if "target_id" in d:
            val = str(d["target_id"] or "").strip()
            self.target_id = val
            updates["target_id"] = val
        if "target_group" in d:
            val = bool(d["target_group"])
            updates["target_group"] = val
        if "k2_compose_mode" in d:
            val = d["k2_compose_mode"]
            updates["k2_compose_mode"] = val
            # 同步 __prompt_model__，使 QQ /随机图 命令与 WebUI 随机按钮行为一致
            self.workflow_config["__prompt_model__"] = val
            await self._save_workflow_config()
        if updates:
            self._save_local_config(updates)
        return web.json_response({"ok": True})

    # ── v4.3.0 云端数据库同步（Gitee 私有仓库） ─────────────────────────
    def _get_gitee_cfg(self) -> dict:
        """从本地配置读 Gitee 同步参数（token/repo/缓存下载开关）
        v4.3.4: 默认源切换 GitHub 公开镜像（匿名）；旧 gitee repo 配置值全部迁移；
        gitee_repo 配置键保留兼容，新值形如 owner/repo（GitHub 或 Gitee 均可）"""
        from .gitee_sync import DEFAULT_REPO
        lc = self._load_local_config()
        saved_repo = str(lc.get("gitee_repo", "") or "").strip()
        # 历史默认值（gitee 私有/pub 仓库）→ 全部迁到 GitHub 镜像
        if saved_repo in ("heigulin/astrbot-comfyui-data",
                          "heigulin/astrbot-comfyui-data-pub"):
            saved_repo = ""
        return {
            "gitee_token": lc.get("gitee_token", ""),
            "gitee_repo": saved_repo or DEFAULT_REPO,
            "sync_artists": bool(lc.get("sync_artists", True)),
            "sync_characters": bool(lc.get("sync_characters", True)),
        }

    async def _webui_gitee_sync_status(self, request):
        """同步状态（上次结果 + 当前配置掩码 + 数据分离布局 + 实时进度）"""
        from .gitee_sync import GiteeSync, DEFAULT_REPO
        try:
            syncer = GiteeSync(self)
            st = syncer.last_state()
            prog = syncer.progress()
        except Exception:
            st = {}
            prog = {}
        cfg = self._get_gitee_cfg()
        token = cfg.get("gitee_token", "")
        return web.json_response({
            "ok": True,
            "last": st,
            "progress": prog,
            "configured": True,
            "token_masked": (token[:6] + "…" + token[-4:]) if len(token) > 12 else ("已填写" if token else "未填写(公开仓库无需)"),
            "repo": cfg.get("gitee_repo") or DEFAULT_REPO,
            "sync_artists": cfg.get("sync_artists", True),
            "sync_characters": cfg.get("sync_characters", True),
            "layout": _data_migration_status(),
        })

    async def _webui_gitee_sync_progress(self, request):
        """v4.3.7 同步实时进度（前端 1s 轮询）：文件数/字节数/当前文件/网速"""
        from .gitee_sync import GiteeSync
        try:
            prog = GiteeSync(self).progress()
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e), "phase": "idle"})
        return web.json_response({"ok": True, "progress": prog})

    async def _webui_gitee_sync_run(self, request):
        """执行云端同步：POST {sync_artists?, sync_characters?}
        v4.3.6: 数据源固定为内置 GitHub 公开镜像（repo/token 不再接受前端传入——
        自定义源无法保证 manifest/目录结构与镜像匹配，属伪需求）；
        已保存的历史 gitee_token/gitee_repo 配置仍被读取以兼容旧部署。"""
        from .gitee_sync import GiteeSync, DEFAULT_REPO
        try:
            d = await request.json()
        except Exception:
            d = {}
        cfg = self._get_gitee_cfg()
        token = str(cfg.get("gitee_token") or "").strip()
        repo = str(cfg.get("gitee_repo") or "").strip() or DEFAULT_REPO
        # 仅同步开关可配置
        updates = {}
        if "sync_artists" in d:
            updates["sync_artists"] = bool(d["sync_artists"])
        if "sync_characters" in d:
            updates["sync_characters"] = bool(d["sync_characters"])
        if updates:
            self._save_local_config(updates)
        syncer = GiteeSync(self)
        # 防重入 + 后台执行（v4.3.7）：立即返回，前端轮询 /api/gitee-sync/progress
        # 旧版同步阻塞请求直到完成——前端 30s 超时掐断过一次（v4.3.1 教训），
        # 且无法显示进度；现在跑线程池，进度走独立轮询端点
        import threading as _sync_threading
        lock = getattr(self, "_gitee_sync_lock", None)
        if lock is None:
            lock = _sync_threading.Lock()
            self._gitee_sync_lock = lock
        if not lock.acquire(blocking=False):
            return web.json_response({"ok": False, "error": "已有同步任务在进行中，请看进度条"})
        loop = asyncio.get_running_loop()

        def _run_sync():
            try:
                return syncer.sync(
                    token, repo,
                    include_artists=bool(cfg.get("sync_artists", True)),
                    include_characters=bool(cfg.get("sync_characters", True)),
                )
            finally:
                lock.release()

        # 后台线程执行（不 await），进度由 /api/gitee-sync/progress 暴露
        loop.run_in_executor(None, _run_sync)
        return web.json_response({"ok": True, "started": True,
                                  "progress": syncer.progress()})

    async def _webui_data_layout(self, request):
        """数据分离布局状态（插件体积/外部目录/各目录实际生效位置）"""
        return web.json_response({"ok": True, **_data_migration_status()})

    async def _webui_generate(self, r):
        """WebUI 生成按钮：用当前工作流 + 提示词生成；配置了 target_qq 则生成后主动私聊发送。
        支持 count（张数 1~10）与 random（true=从随机池抽标签，替代 prompt）。"""
        try:
            data = await r.json()
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        if not self.workflow_path:
            return web.json_response({"ok": False, "error": "未选中工作流"})
        prompt = (data.get("prompt") or "").strip()
        is_random = bool(data.get("random"))
        try:
            count = max(1, min(int(data.get("count") or 1), 10))
        except (TypeError, ValueError):
            count = 1
        # 图生视频/图生图等允许空提示词（仅靠图片即可生成），不再强制要求 prompt
        quality = data.get("quality") or ""
        logger.info(f"[ComfyUI] WebUI 生成请求: random={is_random} count={count} "
                    f"prompt_len={len(prompt)} quality={quality or '-'} keys={list(data.keys())}")
        qo = quality if quality in self.quality_presets else None
        # 根据当前工作流分类获取 cmd_config，与 QQ 命令行为一致
        cur_cat = (self.workflow_config.get('__wf_categories__', {}) or {}).get(self.current_workflow_name, '')
        cmd_config = dict(self.workflow_config.get('__commands__', {}).get(cur_cat, {})) if cur_cat else None
        all_paths = []
        last_text = ""
        _last_gen_prompt = ""      # 记录最后一次实际使用的提示词（随机模式下是抽出的标签）
        for i in range(count):
            gen_prompt = prompt
            # 随机图模式：从随机池抽标签
            if is_random:
                try:
                    import aiohttp
                    async with aiohttp.ClientSession() as s:
                        async with s.post(f"http://127.0.0.1:{self.webui_port}/api/grimoire/random-pick", json={}) as resp:
                            pk = await resp.json()
                    if not pk.get("ok") or not pk.get("tags"):
                        if i == 0:
                            return web.json_response({"ok": False, "error": "随机池为空，请先在魔导书中添加随机池子分类"})
                        break
                    gen_prompt = pk.get("tags", "")
                except Exception as e:
                    logger.warning(f"[ComfyUI] 随机图抽取失败: {e}")
                    if i == 0:
                        return web.json_response({"ok": False, "error": f"随机图抽取失败: {e}"})
                    break
            _last_gen_prompt = gen_prompt or _last_gen_prompt
            try:
                status, text, out_path = await self._process_and_submit(
                    gen_prompt, None, cmd_config=cmd_config, user_id="webui",
                    quality_override=qo
                )
            except Exception as e:
                logger.warning(f"[ComfyUI] WebUI 生成失败: {e}")
                return web.json_response({"ok": False, "error": str(e)})
            if status != "ok":
                last_text = text
                break
            paths = out_path if isinstance(out_path, list) else [out_path]
            all_paths.extend(paths)
        if not all_paths:
            return web.json_response({"ok": False, "error": last_text or "生成失败"})
        # 配置了发送目标 → 主动推送到该平台（QQ 或 飞书）
        _lc = self._load_local_config()
        tp = str(_lc.get("target_platform", "qq") or "qq").lower()
        if tp not in ("qq", "feishu"):
            tp = "qq"
        target_id = str(_lc.get("target_id", "") or _lc.get("target_qq", "") or "").strip()
        sent = False
        # 提示词兜底链（按可靠性排序）：
        #   1) 本次实际使用的提示词 _last_gen_prompt（随机模式下=抽出的标签，最可靠）
        #   2) 前端传入的 prompt
        #   3) 扩展提示词缓存（按路径，再按文件名兜底）
        _send_prompt = _last_gen_prompt or prompt
        if not _send_prompt and all_paths:
            try:
                _abs = str(Path(all_paths[0]).resolve())
                _send_prompt = self._expanded_prompt_cache.get(_abs, '') or ''
                if not _send_prompt:
                    _fn = Path(_abs).name
                    for _k, _v in list(self._expanded_prompt_cache.items()):
                        if _k and Path(_k).name == _fn:
                            _send_prompt = _v
                            break
            except Exception:
                _send_prompt = ''
        logger.info(f"[ComfyUI] WebUI 推送准备: target={tp}:{target_id[:16]}... "
                    f"随机模式={is_random} prompt={'有('+str(len(_send_prompt))+'字)' if _send_prompt else '空'}")
        if target_id:
            is_group = bool(_lc.get("target_group", False))
            sent = await self._send_to_target(tp, target_id, all_paths, _send_prompt, group=is_group)
            # v4.9.6: WebUI 推送也记入提示词日志——此前只有聊天路径记录，
            # WebUI 出的图在飞书/QQ 里用 /提示词 永远查不到
            try:
                if all_paths:
                    _p0 = str(Path(all_paths[0]).resolve())
                    _ih = self._calc_file_md5(_p0)
                    _idh = self._calc_image_dhash(_p0)
                    await self._append_prompt_log('', _send_prompt or _last_gen_prompt, _ih, _idh, path=_p0)
            except Exception as e:
                logger.debug(f"[ComfyUI] WebUI 推送记录提示词失败: {e}")
        return web.json_response({
            "ok": True, "paths": [str(p) for p in all_paths], "count": len(all_paths),
            "sent": sent, "target_qq": target_id, "target_platform": tp, "target_id": target_id,
            "text": last_text or f"生成 {len(all_paths)} 张"
        })

    async def _webui_open_dir(self, r):
        data = await r.json()
        path = data.get('path', str(self._get_workflow_dir()))
        # 路径安全校验：只允许白名单目录（resolve 规范化后比较，防止符号链接/../ 绕过）
        allowed_dirs = [
            str(Path(self._get_workflow_dir()).resolve()),
            str(Path(self.output_dir).resolve()),
        ]
        resolved = str(Path(path).resolve())
        # 必须完全在白名单目录下（带尾部分隔符防止 /dirA 匹配 /dirABC）
        ok = any(resolved == d or resolved.startswith(d.rstrip('\\/') + os.sep) for d in allowed_dirs if d)
        if not ok:
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

    async def _webui_pick_dir(self, r):
        """弹出系统原生文件夹选择器，返回选中路径。
        手机/headless 环境 tkinter 不可用时返回 need_manual_input=true，
        前端应显示文本输入框让用户手动输入路径。"""
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            path = filedialog.askdirectory(title='选择文件夹')
            root.destroy()
            if path:
                path = path.replace('/', '\\')
                return web.json_response({"ok": True, "path": path})
            return web.json_response({"ok": False, "error": "未选择"})
        except Exception as e:
            # tkinter 不可用时的回退（手机/headless 环境）
            logger.warning(f"[ComfyUI] pick-dir tkinter 不可用(手机/headless环境): {e}")
            return web.json_response({"ok": False, "error": "GUI 选择器不可用，请手动输入路径", "need_manual_input": True})

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

    async def _webui_switch_workflow(self, r):
        try:
            name = (await r.json()).get('name', '')
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        for wf in self.workflow_list_cache:
            if wf['name'] == name: self._switch_to_workflow(wf); return web.json_response({"ok": True})
        return web.json_response({"ok": False, "error": f"未找到工作流: {name}"})

    async def _webui_get_workflow_params(self, request):
        if not self.workflow_path: self._refresh_workflow_list()
        if not self.workflow_path: return web.json_response({"error": "没有工作流"})
        try:
            await self._ensure_object_info()  # 预取节点下拉选项/数值范围（TTL 300s 缓存）
            with open(self.workflow_path, 'r', encoding='utf-8') as f: wf = json.load(f)
            # 兼容 UI 格式（含打包子节点）：自动展开为 API 格式，让前端能识别全部节点
            result = self._parse_workflow_params(wf)
            # 用 __saved_texts__ 覆盖显示值（让前端能看到保存的文本，包括清空后的空值）
            wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
            wf_cfg = wf_configs.get(self.current_workflow_name, {})
            wf_saved = wf_cfg.get('__saved_texts__', {}) or {}
            for node in result["nodes"]:
                for p in node["params"]:
                    ck = f"{node['id']}_{p['key']}"
                    if ck in wf_saved:
                        p["value"] = wf_saved[ck]
            # 包含已保存的 Lora 配置（per-node），并合并工作流 JSON 中已有的 lora 数据
            lora_nodes = wf_cfg.get('__lora_nodes__', {}) or {}
            # 扫描工作流 JSON，提取 PowerLoraLoader 等节点中已有的 lora 输入
            for nid, node in wf.items():
                if not isinstance(node, dict): continue
                ct = node.get('class_type', '')
                # 只处理多 Lora 节点（跳过标准 LoraLoader）
                if ct == 'LoraLoader': continue
                inputs = node.get('inputs', {})
                raw_loras = []
                for key in sorted(inputs.keys()):
                    if not key.startswith('lora_'): continue
                    val = inputs[key]
                    if isinstance(val, dict) and 'lora' in val:
                        raw_loras.append({
                            "on": val.get('on', True),
                            "lora_name": val.get('lora', ''),
                            "strength_model": val.get('strength', 1.0),
                            "strength_clip": val.get('strengthTwo', None),
                        })
                if raw_loras:
                    # 已保存的配置优先，没有则用 JSON 中的
                    if nid not in lora_nodes or not lora_nodes[nid]:
                        lora_nodes[nid] = raw_loras
            result["lora_nodes"] = lora_nodes
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
            if not wf_name:
                return web.json_response({"ok": False, "error": "未选中工作流"})
            # 诊断日志：记录传入数据的关键字段
            logger.info(f"[ComfyUI] _webui_save_workflow_params: wf_name={wf_name!r}, data_keys={list(data.keys())}")
            for rk in ['__prompt_node__', '__resolution_node__', '__load_image_nodes__', '__negative_node__', '__expanded_text_node__']:
                if rk in data:
                    logger.info(f"[ComfyUI]   save role {rk}={data[rk]!r}")
            async with self._config_lock:
                # ★ v4.4.2: 组绑定键重定向到分桶存储（桶键 = 绑定目标 || 当前工作流）。
                # 数据自包含：删除源工作流不再连坐清除。
                # v4.9.4: 空 groups_data 不再删桶——前端 saveParams 无条件上报该字段，
                # 面板一旦无数据（指纹重置/竞态等）就会把用户组绑定静默端掉（自毁循环：
                # 面板空→保存→桶删→面板永远空）。显式解绑走 /api/groups/auto-apply（groups:[]）。
                self._migrate_group_binding_to_store()
                if any(k in data for k in ('__groups_source__', '__bind_target__', '__groups_data__', '__disabled_groups__')):
                    _gstore = self.workflow_config.get('__group_bindings_store__', {}) or {}
                    _bkey = (str(data.get('__bind_target__', '') or '').strip() or wf_name)
                    _gbucket = dict(_gstore.get(_bkey) or {})
                    if '__groups_data__' in data:
                        _gd_in = data.pop('__groups_data__') or []
                        if isinstance(_gd_in, list) and _gd_in:
                            _gbucket['data'] = _gd_in
                            _gbucket['source'] = str(data.pop('__groups_source__', _gbucket.get('source', '')) or '')
                            _gbucket['target'] = _bkey
                            _gbucket['disabled'] = data.pop('__disabled_groups__', _gbucket.get('disabled', {})) or {}
                        # else: 空 groups_data → 保留现有桶原样（仅忽略本次组数据上报）
                    else:
                        if '__groups_source__' in data:
                            _gbucket['source'] = str(data.pop('__groups_source__') or '')
                        if '__bind_target__' in data:
                            _gbucket['target'] = str(data.pop('__bind_target__') or '')
                        if '__disabled_groups__' in data:
                            _gbucket['disabled'] = data.pop('__disabled_groups__') or {}
                    if _gbucket and _gbucket.get('data'):
                        _gstore[_bkey] = _gbucket
                    else:
                        _gstore.pop(_bkey, None)
                    self.workflow_config['__group_bindings_store__'] = _gstore
                for key_name in ['__prompt_node__', '__resolution_node__', '__load_image_node__', '__load_image_nodes__', '__load_audio_nodes__', '__load_video_nodes__', '__empty_load_nodes__', '__negative_node__', '__expanded_text_node__', '__disabled_nodes__', '__lora_nodes__']:
                    if key_name in data:
                        wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
                        wf_configs[wf_name] = wf_configs.get(wf_name, {})
                        wf_configs[wf_name][key_name] = data.pop(key_name)
                        data['__workflow_node_configs__'] = wf_configs
                preserved_keys = ['__local_config__', '__hidden_workflows__', '__groups_data__', '__groups_source__', '__disabled_groups__', '__bind_target__', '__current_workflow__', '__workflow_node_configs__', '__group_bindings__', '__user_bindings__', '__workflow_aliases__', '__wf_categories__']
                for key in preserved_keys:
                    if key not in data and key in self.workflow_config: data[key] = self.workflow_config[key]
                # 清理已迁移到 per-workflow 的根级别旧数据（之前 bug 遗留的）
                for old_key in ['__disabled_nodes__']:
                    self.workflow_config.pop(old_key, None)
                # 将 {nodeId}_{key} 类的键存入工作流专属配置，防止跨工作流泄漏
                wf_configs = data.get('__workflow_node_configs__', {}) or {}
                wf_configs[wf_name] = wf_configs.get(wf_name, {})
                wf_saved_texts = wf_configs[wf_name].get('__saved_texts__', {}) or {}
                # v4.5.7b: 分辨率类键不进 saved_texts，直接写入工作流文件。
                # 官方面板重建后走 saveParams 通道（param_{nid}_* + onchange），旧版
                # /api/set-official-res 的落盘路径已成死代码；若存 saved_texts，
                # 一则陈旧值会反复回填（_clear_saved_resolution_keys 的由来），
                # 二则提交链以文件值为基准时会把它盖掉（面板"改不动"）。指纹只含
                # 节点ID+类型，写文件不影响指纹。
                res_file_updates = {}
                for ck in list(data.keys()):
                    if ck.startswith('__'): continue
                    parts = ck.split('_', 1)
                    if len(parts) == 2 and parts[0].isdigit():
                        if parts[1] in ('aspect_ratio', 'megapixels', 'multiple', 'width', 'height'):
                            res_file_updates.setdefault(parts[0], {})[parts[1]] = data.pop(ck)
                        else:
                            wf_saved_texts[ck] = data.pop(ck)
                if res_file_updates:
                    _rp = self.workflow_path
                    if _rp:
                        try:
                            with open(_rp, 'r', encoding='utf-8') as f:
                                _rwf = json.load(f)
                            _real_ids = {str(_k): _k for _k in _rwf.keys()}
                            for nid_s, kv in res_file_updates.items():
                                node = _rwf.get(_real_ids.get(nid_s))
                                if not isinstance(node, dict): continue
                                inputs = node.get('inputs', {})
                                for k2, v2 in kv.items():
                                    if k2 not in inputs: continue  # 只更新已有输入，绝不新建
                                    orig = inputs[k2]
                                    if isinstance(orig, bool):
                                        v2 = str(v2).lower() in ('true', '1', 'yes')
                                    elif isinstance(orig, (int, float)):
                                        try:
                                            v2 = float(v2) if isinstance(orig, float) else int(float(v2))
                                        except (TypeError, ValueError):
                                            continue
                                    inputs[k2] = v2
                            self._atomic_write_workflow_json(_rwf, _rp)
                            logger.info(f"[ComfyUI] 面板分辨率已写入工作流文件: 节点{list(res_file_updates.keys())} { {k: v for kv in res_file_updates.values() for k, v in kv.items()} }")
                        except Exception as e:
                            logger.warning(f"[ComfyUI] 面板分辨率写入工作流文件失败: {e}")
                    # 无论写文件成败，都清掉该工作流 saved_texts 里的旧分辨率残留
                    _clear_nids = list(res_file_updates.keys())
                    for ck in list(wf_saved_texts.keys()):
                        p2 = ck.split('_', 1)
                        if len(p2) == 2 and p2[0] in _clear_nids and p2[1] in ('aspect_ratio', 'megapixels', 'multiple', 'width', 'height'):
                            del wf_saved_texts[ck]
                for ck in list(data.keys()):
                    if ck.startswith('__'): continue
                    parts = ck.split('_', 1)
                    if len(parts) == 2 and parts[0].isdigit():
                        wf_saved_texts[ck] = data.pop(ck)
                if wf_saved_texts:
                    wf_configs[wf_name]['__saved_texts__'] = wf_saved_texts
                    data['__workflow_node_configs__'] = wf_configs
                # 计算并保存工作流内容指纹
                wf_path_save = self.workflow_path
                if wf_path_save:
                    try:
                        with open(wf_path_save, 'r', encoding='utf-8') as f:
                            fp = self._compute_fingerprint(json.load(f))
                        wf_configs = data.get('__workflow_node_configs__', {}) or {}
                        wf_configs[wf_name] = wf_configs.get(wf_name, {})
                        wf_configs[wf_name]['__fingerprint__'] = fp
                        data['__workflow_node_configs__'] = wf_configs
                    except Exception:
                        pass
                # 更新内存中的 workflow_config，再通过统一函数写文件（含合并逻辑）
                self.workflow_config.update(data)
            # 锁已释放，再调用 _save_workflow_config（它不自带锁，与 asyncio.Lock 的重入危险已解除）
            await self._save_workflow_config()
            logger.info(f"[ComfyUI]   save OK, wf_name={wf_name!r}, node_configs keys: {list(self.workflow_config.get('__workflow_node_configs__', {}).get(wf_name, {}).keys())}")

            # 注意：不再直接写入 API 工作流 .json 文件（已由 workflow_config 统一管理，
            # _apply_workflow_config 会在生成时读取 workflow_config 应用参数）

            return web.json_response({"ok": True, "message": "已保存"})
        except Exception as e:
            logger.warning(f"[ComfyUI] 保存工作流参数失败: {e}\n{traceback.format_exc()}")
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_set_wf_category(self, r):
        """设置工作流分类。
        设非空分类 → 把 .json 自动移动到 工作流目录/<分类>/ 子文件夹（没有则创建）；
        设空(未分类) → 移回根目录。文件按 basename 仍被识别，配置不变。"""
        try:
            data = await r.json()
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        name = data.get('name', '')
        category = data.get('category', '')
        if not name:
            return web.json_response({"ok": False, "error": "缺少工作流名"})
        # 先移动文件（分类 → 子夹 / 未分类 → 根）；失败则不改分类配置
        path, ok, err = await asyncio.to_thread(self._move_workflow_to_category, name, category or '')
        if not ok:
            return web.json_response({"ok": False, "error": err or "移动工作流失败"})
        async with self._config_lock:
            cats = self.workflow_config.get('__wf_categories__', {}) or {}
            if category:
                cats[name] = category
            else:
                cats.pop(name, None)
            self.workflow_config['__wf_categories__'] = cats
        await self._save_workflow_config()
        self._refresh_workflow_list()
        return web.json_response({"ok": True, "categories": cats, "path": str(path)})

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

    def _effective_rule(self, ttype: str, wf_name: str = None) -> str:
        """v4.4.5: 默认全不注入——只有显式绑定规则的工作流才注入（'none'/未绑定/未知名均不注入）。"""
        rule = self._effective_rule_obj(ttype, wf_name)
        return (rule.get('content') or '').strip() if rule else ''

    def _effective_rule_obj(self, ttype: str, wf_name: str = None):
        """返回当前工作流绑定规则的对象（含硬校验开关字段 chk_quality/chk_weights/chk_commas），未绑定返回 None。"""
        wf = wf_name or self.current_workflow_name
        wf_cfg = (self.workflow_config.get('__workflow_node_configs__', {}) or {}).get(wf, {}) or {}
        binding = (wf_cfg.get('__llm_rule__') or '').strip()
        if not binding or binding == 'none':
            return None
        for t in ((self.workflow_config.get('__llm_prompt_templates__', {}) or {}).get(ttype) or []):
            if t.get('name') == binding:
                return t
        return None

    def _enforce_prompt_rule(self, prompt: str, ttype: str):
        """v4.4.6: 提交前按绑定规则的硬校验开关清洗 prompt（兜底拦截，LLM 违规也能救）。
        返回 (清洗后 prompt, 提示文本)。规则未开任何开关时原样返回。"""
        rule = self._effective_rule_obj(ttype)
        if not rule or not isinstance(prompt, str) or not prompt.strip():
            return prompt, ''
        import re as _re
        out = prompt
        notes = []
        # 权重语法先剥离（(xxx:1.3) / （xxx：1.3），支持嵌套一层）——剥离后质量词才好匹配
        if rule.get('chk_weights'):
            pat = _re.compile(r'[（(]([^()：:()（）]{1,40}?)[：:]\s*([0-9]+(?:\.[0-9]+)?)[)）]')
            prev = None
            while prev != out:
                prev = out
                out = pat.sub(r'\1', out)
            if out != prompt:
                notes.append('剥离权重语法')
        # 质量词黑名单（含残留的权重形式）
        if rule.get('chk_quality'):
            pat = _re.compile(r'(?i)[（(]?(?:\s*)(masterpiece|best\s+quality|high\s+quality|normal\s+quality|low\s+quality|worst\s+quality|ultra[-\s]?detailed|absurdres|highres|8k|4k)(?:\s*)(?:[：:][0-9.]+)?[)）]?')
            new = pat.sub('', out)
            if new != out:
                notes.append('移除质量词')
                out = new
        # 清理剥离后的残渣（连续逗号/首尾逗号）
        if out != prompt:
            out = _re.sub(r'(?:\s*[，,]\s*){2,}', ', ', out)
            out = out.strip(' \t，,')
            if not out:
                return prompt, '⚠ 规则清洗后提示词为空，已放弃修正并保留原文'
        # 标签堆砌检测（无法自动改写为长句，只警告）
        if rule.get('chk_commas'):
            try:
                mc = int(rule.get('chk_commas') or 0)
            except (TypeError, ValueError):
                mc = 0
            if mc > 0:
                cnt = out.count(',') + out.count('，')
                if cnt > mc:
                    notes.append(f'疑似标签堆砌（{cnt} 个逗号 > 上限 {mc}），无法自动改写为长句，请人工确认')
        if not notes:
            return prompt, ''
        return out, '⚠ 已按规则「' + str(rule.get('name', '')) + '」自动修正：' + '；'.join(notes)

    def _apply_group_modes(self, workflow, wf_name=None):
        """v4.4.10: 组控制落装——禁用组从图中级联移除（ComfyUI API 执行忽略 mode=4）。
        语义：节点处于禁用组且不在任何启用组 → 移除；跨组共享节点不受影响。
        数据源：分桶存储（当前工作流的 data+disabled），兼容旧 ROOT __disabled_groups__。
        注意：全部输出组被禁用 → ComfyUI 返回 no outputs，属预期反馈。"""
        wf = wf_name or self.current_workflow_name
        store = self.workflow_config.get('__group_bindings_store__', {}) or {}
        bucket = store.get(wf) or {}
        groups = bucket.get('data') or []
        disabled = set(str(k) for k in (bucket.get('disabled') or {}).keys())
        if wf == self.current_workflow_name:
            disabled |= set(str(k) for k in (self.workflow_config.get('__disabled_groups__', {}) or {}).keys())
        if not disabled or not groups:
            self._group_modes_diag = {'wf': wf, '组数': len(groups), '禁用': sorted(disabled), '结果': '跳过'}
            return
        self._group_modes_diag = {'wf': wf, '组数': len(groups), '禁用': sorted(disabled)}
        disabled_nodes = set()
        enabled_nodes = set()
        for g in groups:
            gid = str(g.get('id', ''))
            nodes = [str(n) for n in (g.get('nodes') or [])]
            if gid in disabled:
                disabled_nodes |= set(nodes)
            else:
                enabled_nodes |= set(nodes)
        targets = disabled_nodes - enabled_nodes
        if not targets:
            self._group_modes_diag = {'wf': wf, '组数': len(groups), '禁用': sorted(disabled), '结果': '无命中节点'}
            return
        # v4.4.10: ComfyUI API 执行忽略 mode=4（bypass 是 UI 层概念）——改用级联移除：
        # 移除禁用组节点，其下游若所有输入都来自已移除节点也一并移除，否则仅断开引用。
        before = len(workflow)
        self._remove_workflow_nodes(workflow, list(targets))
        applied = before - len(workflow)
        self._group_modes_diag['移除'] = applied
        if applied:
            logger.info(f"[ComfyUI] 组控制: 已移除 {applied} 个节点（禁用组: {sorted(disabled)}，启用组不受影响）")

    def _rebuild_jzl_refs(self, workflow):
        """v4.5.2: 重建 JZL 参考输入——ref_images.* 里仍指向存活 LoadImage 的链接
        连续重排到 ref_image_0..N-1，消除槽位空洞（ComfyUI 对空洞填 "" 默认值、
        后方引用错位丢失，导致参考图不生效）。无存活引用时全部清除（JZL 跳过空引用）。"""
        try:
            for nid, node in list(workflow.items()):
                if not isinstance(node, dict):
                    continue
                inputs = node.get('inputs', {})
                if not isinstance(inputs, dict):
                    continue
                ref_keys = [k for k in inputs if k.startswith('ref_images.')]
                if not ref_keys:
                    continue
                # v4.5.2b: 工作流键可能是 int（类型归一化后），成员判断必须按 str 归一
                wf_ids = {str(k) for k in workflow.keys()}
                links = []
                for k in ref_keys:
                    v = inputs.get(k)
                    if isinstance(v, list) and len(v) >= 2 and str(v[0]) in wf_ids:
                        links.append(v)
                for k in ref_keys:
                    del inputs[k]
                for i, link in enumerate(links):
                    inputs[f'ref_images.ref_image_{i}'] = link
                if ref_keys:
                    logger.info(f"[ComfyUI] JZL 引用重建: 节点 {nid} 存活引用 {len(links)}/{len(ref_keys)}")
                self._group_modes_diag = dict(self._group_modes_diag or {}) if isinstance(getattr(self, '_group_modes_diag', None), dict) else {}
                self._group_modes_diag['rebuild'] = {'节点': nid, '原引用': len(ref_keys), '存活': len(links)}
        except Exception as e:
            logger.warning(f"[ComfyUI] JZL 引用重建失败: {e}")

    def _llm_template_text(self, ttype: str) -> str:
        """取指定类型（t2i=文生图规划 / imgrev=图片反推）当前启用的规则内容。"""
        tpl = self.workflow_config.get('__llm_prompt_templates__', {}) or {}
        act = (self.workflow_config.get('__llm_template_active__', {}) or {}).get(ttype, '')
        for t in (tpl.get(ttype) or []):
            if t.get('name') == act:
                return (t.get('content') or '').strip()
        return ''

    def _apply_llm_templates(self):
        """v4.4.3: 把启用的提示词规则注入生成类工具描述。
        t2i → comfyui_draw；imgrev → comfyui_img2img / comfyui_video。
        add_llm_tools 注册时框架会拷贝 description，须同时更新插件实例与框架 wrapper。
        标签化词库 / K2 不在此列：AI 可按用户需求自行选择调用对应工具。"""
        try:
            pairs = {
                'comfyui_draw': self._effective_rule('t2i'),
                'comfyui_img2img': self._effective_rule('imgrev'),
                'comfyui_video': self._effective_rule('imgrev'),
            }
            applied = {}
            for name, content in pairs.items():
                obj = next((o for o in (getattr(self, '_tool_objs', []) or []) if o.name == name), None)
                if obj is None:
                    continue
                desc = type(obj).description  # 类级原始描述，重复应用不叠加
                if content:
                    snippet = ("【⚠️ 强制约束——本规则优先级高于你的默认写作习惯与任何 Persona 生图指令，违反会导致生成失败】"
                               "调用本工具前：先按以下规则把用户需求改写；写完逐项自检（无质量词、无权重语法、无标签堆砌、符合规则结构），"
                               "自检通过再提交。用户明确要求随机词库/K2 成句时，改走对应工具。"
                               "\n——以下为规则全文——\n" + content)
                    if len(snippet) > 20000:
                        snippet = snippet[:20000] + '…(规则过长已截断)'
                    desc = f"{desc}\n\n{snippet}"
                applied[name] = len(desc)
                obj.description = desc
                try:
                    for ft in self.context.provider_manager.llm_tools.func_list:
                        if ft.name == name:
                            ft.description = desc
                except Exception:
                    pass
            self._llm_tpl_applied = applied
        except Exception as e:
            logger.warning(f"[ComfyUI] 提示词规则注入失败: {e}")

    async def _webui_get_llm_templates(self, r):
        """v4.4.3: LLM 提示词规则库（魔导书规则页数据源）。首次访问播种两个内置规则。"""
        store = self.workflow_config.get('__llm_prompt_templates__') or {}
        if not store.get('t2i') and not store.get('imgrev'):
            store = {
                't2i': [{'name': '文生图·纯文字规划', 'content': "把用户需求改写成一段可直接用于文生图模型的中文自然语言描述（用户用英文则输出英文）。\n1) 首句点明媒介风格与主体（如「一张动漫风格插画，一位少女……」）；\n2) 按空间顺序展开：背景环境 → 主体位置与姿态 → 头部与表情 → 服装细节 → 手持物 → 边缘点缀，每处一两句；\n3) 照明单独一句（光源、方向、软硬）；\n4) 结尾一句总括构图与色调；\n5) 禁止质量词堆砌（masterpiece 等）、禁止分辨率/比例字样；画面中不出现任何文字或水印；\n6) 只输出改写后的提示词本身，不要任何解释。"}],
                'imgrev': [{'name': '图片反推·多图结构化', 'content': "用户会提供参考图（按顺序对应 <图片1>、<图片2>……），把需求改写为结构化提示词。\n1) <主体 N>：逐个定义主体，外观要素锁定自对应参考图（发型/瞳色/服装/持物/饰件），写明完整保留；\n2) 摘要：一句话概括任务类型（参考生成/动作迁移/风格统一）与画面主线；\n3) 保留分析：逐图说明保留什么、改写什么；\n4) 详细描述：按时间或空间顺序展开镜头（景别、运镜、动作节拍），说明各参考图承担的角色；\n5) 音效与配乐各一段（工作流需要时）；\n6) 画面中不出现任何文字、字幕、水印，不得复刻参考图中的 UI/水印；\n7) 只输出改写后的提示词本身，语言跟随用户输入。"}],
            }
            self.workflow_config['__llm_prompt_templates__'] = store
            await self._save_workflow_config()
        self._apply_llm_templates()
        return web.json_response({
            't2i': store.get('t2i', []),
            'imgrev': store.get('imgrev', []),
            'active': self.workflow_config.get('__llm_template_active__', {}) or {},
            'applied': getattr(self, '_llm_tpl_applied', {}),
            'group_diag': getattr(self, '_group_modes_diag', None),
            'rule_names': {'t2i': [t.get('name') for t in store.get('t2i', [])], 'imgrev': [t.get('name') for t in store.get('imgrev', [])]},
            'workflows': [{'name': w.get('name', ''), 'category': (self.workflow_config.get('__wf_categories__', {}) or {}).get(w.get('name', ''), '')} for w in (self._refresh_workflow_list() or [])],
            'bindings': {wf: wc.get('__llm_rule__', '') for wf, wc in (self.workflow_config.get('__workflow_node_configs__', {}) or {}).items() if isinstance(wc, dict) and wc.get('__llm_rule__')},
        })

    async def _webui_save_llm_templates(self, r):
        """v4.4.3: 保存规则（op=save 整表替换 / op=active 切换启用），保存后立即重新注入工具描述。"""
        try:
            data = await r.json()
            op = data.get('op')
            ttype = data.get('type')
            if op != 'bind' and ttype not in ('t2i', 'imgrev'):
                return web.json_response({"ok": False, "error": "type 必须是 t2i 或 imgrev"})
            store = self.workflow_config.setdefault('__llm_prompt_templates__', {'t2i': [], 'imgrev': []})
            if op == 'save':
                lst = []
                for x in (data.get('list') or []):
                    if isinstance(x, dict) and str(x.get('name', '')).strip():
                        _cc = x.get('chk_commas')
                        try:
                            _cc = max(0, min(60, int(_cc))) if _cc else 0
                        except (TypeError, ValueError):
                            _cc = 0
                        lst.append({'name': str(x['name']).strip()[:60], 'content': str(x.get('content', ''))[:20000],
                                    'chk_quality': bool(x.get('chk_quality')), 'chk_weights': bool(x.get('chk_weights')),
                                    'chk_commas': _cc})
                store[ttype] = lst
                act = self.workflow_config.setdefault('__llm_template_active__', {})
                if act.get(ttype) and not any(t.get('name') == act[ttype] for t in lst):
                    act.pop(ttype, None)
            elif op == 'active':
                name = str(data.get('name', ''))
                if not any(t.get('name') == name for t in store.get(ttype, [])):
                    return web.json_response({"ok": False, "error": "模板不存在"})
                self.workflow_config.setdefault('__llm_template_active__', {})[ttype] = name
            elif op == 'bind':
                # v4.4.4: 工作流级规则绑定——'' 继承分类默认 / 'none' 不注入 / 规则名
                wfname = str(data.get('workflow', '')).strip()
                rule = str(data.get('rule', '')).strip()
                if not wfname:
                    return web.json_response({"ok": False, "error": "workflow 不能为空"})
                if rule and rule != 'none':
                    valid = any(t.get('name') == rule for t in (store.get('t2i', []) + store.get('imgrev', [])))
                    if not valid:
                        return web.json_response({"ok": False, "error": "规则不存在"})
                wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
                wcfg = wf_configs.get(wfname) or {}
                if not isinstance(wcfg, dict):
                    wcfg = {}
                if rule:
                    wcfg['__llm_rule__'] = rule
                else:
                    wcfg.pop('__llm_rule__', None)
                wf_configs[wfname] = wcfg
                self.workflow_config['__workflow_node_configs__'] = wf_configs
                await self._save_workflow_config()
                if wfname == self.current_workflow_name:
                    self._apply_llm_templates()
                return web.json_response({"ok": True, "applied": getattr(self, '_llm_tpl_applied', {})})
            else:
                return web.json_response({"ok": False, "error": "未知操作"})
            self.workflow_config['__llm_prompt_templates__'] = store
            await self._save_workflow_config()
            self._apply_llm_templates()
            return web.json_response({"ok": True, "applied": getattr(self, '_llm_tpl_applied', {})})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})

    def _migrate_group_binding_to_store(self) -> bool:
        """v4.4.2: 旧版 ROOT 三元组（__groups_source__/__bind_target__/__groups_data__/__disabled_groups__）
        迁移到按绑定目标分桶的 __group_bindings_store__。数据自包含：删除源工作流只清 source 引用，
        不再连坐清除组数据（v4.4.1 的孤儿清理误伤绑定数据，为本版修正）。幂等，仅改内存，由调用方落盘。"""
        gd = self.workflow_config.get('__groups_data__')
        if not (isinstance(gd, list) and gd):
            return False
        bt = (self.workflow_config.get('__bind_target__') or '').strip()
        if not bt:
            return False
        store = self.workflow_config.get('__group_bindings_store__', {}) or {}
        prev = store.get(bt) if isinstance(store.get(bt), dict) else {}
        store[bt] = {
            'source': (self.workflow_config.get('__groups_source__') or '').strip(),
            'target': bt,
            'data': gd,
            'disabled': (prev.get('disabled') if prev else None) or self.workflow_config.get('__disabled_groups__') or {},
        }
        self.workflow_config['__group_bindings_store__'] = store
        for k in ('__groups_source__', '__bind_target__', '__groups_data__', '__disabled_groups__'):
            self.workflow_config.pop(k, None)
        logger.info(f"[ComfyUI] 组绑定已迁移到分桶存储: target={bt!r}, groups={len(gd)}")
        return True

    async def _webui_delete_workflow(self, r):
        """删除工作流文件"""
        data = await r.json()
        name = data.get('name', '').strip()
        if not name or '..' in name or '/' in name or '\\' in name:
            return web.json_response({"ok": False, "error": "非法文件名"})
        wdir = self._get_workflow_dir()
        target = self._find_workflow_file_path(name)
        if target is None or not target.exists():
            return web.json_response({"ok": False, "error": "文件不存在"})
        try:
            # 安全检查：确保在 workflow 目录内
            target.resolve().relative_to(wdir.resolve())
            target.unlink()
            logger.info(f"[ComfyUI] 已删除工作流: {name}")
            # 同步清理预览图
            try:
                pv = self._user_data_dir / "workflow_previews" / (name + '.png')
                if pv.exists(): pv.unlink()
            except Exception:
                pass
            async with self._config_lock:
                # 删除相关配置（分类、隐藏、别名）
                for key in ('__wf_categories__', '__hidden_workflows__', '__workflow_aliases__'):
                    d = self.workflow_config.get(key, {})
                    if isinstance(d, dict) and name in d: del d[name]
                    elif isinstance(d, list) and name in d: d.remove(name)
                # 清理内存中会话上下文
                self._context_workflows = {k: v for k, v in self._context_workflows.items() if v != name}
                # 清理组相关配置（v4.4.2: 分桶自包含——删目标删桶；删源仅清 source 引用，组数据保留。
                # v4.4.1 的"删源连坐清除"会误伤已绑定目标的组数据，为本版修正）
                self._migrate_group_binding_to_store()
                _gstore = self.workflow_config.get('__group_bindings_store__', {}) or {}
                for _bt_key in list(_gstore.keys()):
                    _gb = _gstore.get(_bt_key) or {}
                    if _bt_key == name:
                        _gstore.pop(_bt_key, None)
                    elif isinstance(_gb, dict) and _gb.get('source') == name:
                        _gb['source'] = ''
                        _gstore[_bt_key] = _gb
                if _gstore:
                    self.workflow_config['__group_bindings_store__'] = _gstore
                else:
                    self.workflow_config.pop('__group_bindings_store__', None)
                # 清理 __current_workflow__（v4.4.1 新键）
                if self.workflow_config.get('__current_workflow__') == name:
                    self.workflow_config['__current_workflow__'] = ''
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
                # 清理 __workflow_node_configs__ 中该工作流的条目
                wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
                if name in wf_configs:
                    del wf_configs[name]
                    self.workflow_config['__workflow_node_configs__'] = wf_configs
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
        """上传图片到临时目录，后续由 HTTP upload 发送到 ComfyUI。
        若附带 node_id（LoadImage 节点），上传成功后把文件名写回工作流该节点的 image 输入，
        解决「上传了新图但提交的仍是旧文件名」导致的 Invalid image file 校验失败。"""
        try:
            reader = await r.multipart()
            target_dir = self.upload_dir
            if not target_dir.parts:
                return web.json_response({"ok": False, "error": "未设置输出目录"})
            if not target_dir.exists():
                target_dir.mkdir(parents=True, exist_ok=True)
            node_id = None
            uploaded_name = None
            async for part in reader:
                # 普通字段（node_id 等）
                if part.filename is None:
                    fname = (part.name or '')
                    value = (await part.read()).decode('utf-8', errors='replace').strip()
                    if fname == 'node_id':
                        node_id = value
                    continue
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
                uploaded_name = unique_name
            if not uploaded_name:
                return web.json_response({"ok": False, "error": "未找到图片数据"})
            # 上传到 ComfyUI input 目录（LoadImage 节点从这里读文件），返回 ComfyUI 存储名
            comfy_name = None
            try:
                comfy_name = await self._upload_image_remote(dest)
            except Exception as e:
                logger.warning(f"[ComfyUI] 上传到 ComfyUI 失败: {e}")
            # 写回工作流 LoadImage 节点：必须用 ComfyUI 存储名（本地时间戳名 ComfyUI 不认识）
            if node_id and comfy_name:
                self._set_load_image_node(node_id, comfy_name)
            return web.json_response({"ok": True, "filename": uploaded_name, "comfy_name": comfy_name, "node_id": node_id})
        except Exception as e:
            logger.error(f"[ComfyUI] 上传图片异常: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    def _set_load_image_node(self, node_id, filename):
        """把上传的图片文件名写回工作流 JSON 中 LoadImage 节点的 image 输入（原子写入）。
        兼容 UI 格式（打包子节点）：图片存于 widgets_values_named.image。"""
        try:
            if not self.workflow_path or not node_id:
                return
            with open(self.workflow_path, 'r', encoding='utf-8') as f:
                wf = json.load(f)
            node = wf.get(str(node_id))
            if node and isinstance(node.get('inputs'), dict) and 'image' in node['inputs']:
                node['inputs']['image'] = filename
                self._atomic_write_workflow_json(wf, self.workflow_path)
                logger.info(f"[ComfyUI] LoadImage 节点 {node_id} image -> {filename}")
                return
            # UI 格式（打包子节点展开前）：按 id 找节点，写 widgets_values_named.image
            if isinstance(wf, dict) and 'nodes' in wf and 'links' in wf:
                for n in wf.get('nodes', []) or []:
                    if isinstance(n, dict) and str(n.get('id')) == str(node_id):
                        wvn = n.get('widgets_values_named') or {}
                        if isinstance(wvn, dict) and 'image' in wvn:
                            wvn['image'] = filename
                            n['widgets_values_named'] = wvn
                            self._atomic_write_workflow_json(wf, self.workflow_path)
                            logger.info(f"[ComfyUI] LoadImage 节点 {node_id} image -> {filename}（UI 格式）")
                            return
            logger.warning(f"[ComfyUI] LoadImage 节点 {node_id} 无 image 输入，跳过写回")
        except Exception as e:
            logger.warning(f"[ComfyUI] 写回 LoadImage 节点失败: {e}")

    def _set_load_media_node(self, node_id, filename, kind):
        """把上传的音频/视频文件名写回工作流对应节点的输入（audio→audio 输入，video→video 输入）。"""
        try:
            if not self.workflow_path or not node_id:
                return
            with open(self.workflow_path, 'r', encoding='utf-8') as f:
                wf = json.load(f)
            node = wf.get(str(node_id))
            if not node or 'inputs' not in node:
                logger.warning(f"[ComfyUI] 节点 {node_id} 无 inputs，跳过写回")
                return
            # 音频：LoadAudio 节点用 audio 输入；视频：VideoLoader 类节点用 video 输入
            key = 'audio' if kind == 'audio' else 'video'
            if key not in node['inputs']:
                logger.warning(f"[ComfyUI] 节点 {node_id} 无 {key} 输入，尝试第一个字符串字段")
                # 兜底：写入第一个非连接字符串输入
                for k, v in node['inputs'].items():
                    if isinstance(v, str) and k not in ('image',):
                        node['inputs'][k] = filename
                        key = k
                        break
                else:
                    return
            else:
                node['inputs'][key] = filename
            self._atomic_write_workflow_json(wf, self.workflow_path)
            logger.info(f"[ComfyUI] 节点 {node_id} {key} -> {filename}")
        except Exception as e:
            logger.warning(f"[ComfyUI] 写回 {kind} 节点失败: {e}")

    async def _webui_upload_media(self, r):
        """通用媒体上传（kind=image/audio/video），上传到 ComfyUI 并写回对应节点。"""
        try:
            reader = await r.multipart()
            target_dir = self.upload_dir
            if not target_dir.parts:
                return web.json_response({"ok": False, "error": "未设置输出目录"})
            if not target_dir.exists():
                target_dir.mkdir(parents=True, exist_ok=True)
            node_id = None
            kind = 'image'
            uploaded_name = None
            default_ext = '.png'
            async for part in reader:
                if part.filename is None:
                    fname = (part.name or '')
                    value = (await part.read()).decode('utf-8', errors='replace').strip()
                    if fname == 'node_id':
                        node_id = value
                    elif fname == 'kind':
                        kind = value if value in ('image', 'audio', 'video') else 'image'
                    continue
                if part.name != 'media' and part.name != 'image':
                    continue
                fname = part.filename or ('upload' + default_ext)
                safe_name = Path(fname).name
                if not safe_name:
                    return web.json_response({"ok": False, "error": "非法文件名"})
                stem = Path(safe_name).stem
                suffix = Path(safe_name).suffix or default_ext
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                unique_name = f"{stem}_{timestamp}{suffix}"
                dest = target_dir / unique_name
                data = await part.read()
                dest.write_bytes(data)
                logger.info(f"[ComfyUI] 上传{kind}: {unique_name} ({len(data)//1024}KB)")
                uploaded_name = unique_name
            if not uploaded_name:
                return web.json_response({"ok": False, "error": "未找到媒体数据"})
            comfy_name = None
            try:
                comfy_name = await self._upload_image_remote(dest)
            except Exception as e:
                logger.warning(f"[ComfyUI] 上传到 ComfyUI 失败: {e}")
            if node_id and comfy_name:
                if kind == 'image':
                    self._set_load_image_node(node_id, comfy_name)
                else:
                    self._set_load_media_node(node_id, comfy_name, kind)
            return web.json_response({"ok": True, "filename": uploaded_name, "comfy_name": comfy_name, "node_id": node_id, "kind": kind})
        except Exception as e:
            logger.error(f"[ComfyUI] 上传媒体异常: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_clear_node_input(self, r):
        """清空指定加载节点的输入值（图片 X 按钮）：node_id + key → 置空 + 清理 saved_texts 旧值。"""
        try:
            data = await r.json()
            node_id = str(data.get('node_id', '')).strip()
            key = str(data.get('key', '')).strip()
            if not node_id or not key:
                return web.json_response({"ok": False, "error": "缺少 node_id/key"})
            if not self.workflow_path:
                return web.json_response({"ok": False, "error": "未选中工作流"})
            with open(self.workflow_path, 'r', encoding='utf-8') as f:
                wf = json.load(f)
            # 兼容两种格式：
            # - API 格式：node['inputs'][key] = ''
            # - UI 格式（打包子节点）：节点图片存在 widgets_values_named[key]，清空它并写回文件
            node = wf.get(node_id)
            cleared = False
            if isinstance(wf, dict) and 'nodes' in wf and 'links' in wf:
                # UI 格式：按 id 找节点，清空 widgets_values_named 中的 key（如 image）
                for n in wf.get('nodes', []) or []:
                    if isinstance(n, dict) and str(n.get('id')) == node_id:
                        wvn = n.get('widgets_values_named') or {}
                        if isinstance(wvn, dict) and key in wvn:
                            wvn[key] = ''
                            n['widgets_values_named'] = wvn
                            cleared = True
                        break
                if cleared:
                    self._atomic_write_workflow_json(wf, self.workflow_path)
                    logger.info(f"[ComfyUI] 已清空 UI 节点 {node_id} 输入 {key}")
            elif node and isinstance(node.get('inputs'), dict) and key in node['inputs']:
                node['inputs'][key] = ''
                self._atomic_write_workflow_json(wf, self.workflow_path)
                cleared = True
                logger.info(f"[ComfyUI] 已清空节点 {node_id} 输入 {key}")
            # 清理 saved_texts 中的旧值，防止刷新后被旧值覆盖
            try:
                wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
                wf_cfg = wf_configs.get(self.current_workflow_name, {})
                saved = wf_cfg.get('__saved_texts__', {}) or {}
                ck = f"{node_id}_{key}"
                if ck in saved:
                    del saved[ck]
                    wf_cfg['__saved_texts__'] = saved
                    wf_configs[self.current_workflow_name] = wf_cfg
                    self.workflow_config['__workflow_node_configs__'] = wf_configs
                    self._schedule_save_workflow_config()
                    logger.info(f"[ComfyUI] 已清理 saved_texts: {ck}")
            except Exception as e:
                logger.warning(f"[ComfyUI] 清理 saved_texts 失败: {e}")
            return web.json_response({"ok": True, "node_id": node_id, "key": key})
        except Exception as e:
            logger.error(f"[ComfyUI] 清空节点输入异常: {e}")
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
            # 文件不存在或没有组信息时，优先从 per-workflow config 回退读取
            # （前端提取时保存到 __workflow_node_configs__[wf_name]['__groups_data__']，
            #   原工作流/缓存被删后仍可恢复）
            gf = self._read_groups_data_from_config()
            if gf:
                return web.json_response({"groups": gf})
            return web.json_response({"groups": []})
        except Exception as e:
            logger.error(f"[ComfyUI] groups error: {e}")
            # 异常时也从 config 回退
            gf = self._read_groups_data_from_config()
            if gf:
                return web.json_response({"groups": gf})
            return web.json_response({"groups": []})

    def _read_groups_data_from_config(self):
        """从工作流配置读取组数据：优先 per-workflow（__workflow_node_configs__[当前wf]），
        再退分桶存储（v4.4.2 组绑定，当前工作流的桶，与 GET 注入同源），最后根级。"""
        try:
            wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
            wf_cfg = wf_configs.get(self.current_workflow_name, {}) or {}
            per_wf = wf_cfg.get('__groups_data__', []) or []
            if per_wf:
                return per_wf
        except Exception:
            pass
        try:
            bucket = (self.workflow_config.get('__group_bindings_store__', {}) or {}).get(self.current_workflow_name) or {}
            bd = bucket.get('data', []) or []
            if bd:
                return bd
        except Exception:
            pass
        return self.workflow_config.get('__groups_data__', []) or []

    async def _webui_groups_auto_apply(self, r):
        """v4.5.0: 组提取自动应用——组数据写入所有节点结构兼容的工作流（节点 id 全覆盖），
        免手动绑定。空 groups = 清除 source 工作流的组数据。"""
        try:
            data = await r.json()
            source = str(data.get('source', '')).strip()
            raw_groups = data.get('groups')
            if not source:
                return web.json_response({"ok": False, "error": "source 缺失"})
            store = self.workflow_config.setdefault('__group_bindings_store__', {})
            if not (isinstance(raw_groups, list) and raw_groups):
                store.pop(source, None)
                await self._save_workflow_config()
                return web.json_response({"ok": True, "applied": [], "skipped": [], "cleared": source})
            clean = []
            all_ids = set()
            for g in raw_groups:
                if not isinstance(g, dict):
                    continue
                nodes = [str(n) for n in (g.get('nodes') or [])]
                clean.append({'id': str(g.get('id', '')), 'title': str(g.get('title', ''))[:60],
                              'color': str(g.get('color', '')), 'nodes': nodes})
                all_ids |= set(nodes)
            if not clean:
                return web.json_response({"ok": False, "error": "组数据为空"})
            applied, skipped = [], []
            for wr in (self._refresh_workflow_list() or []):
                name = wr.get('name', '')
                try:
                    with open(wr.get('path', ''), 'r', encoding='utf-8') as f:
                        twf = json.load(f)
                    ids = set(str(k) for k in twf.keys())
                except Exception:
                    skipped.append({'name': name, 'reason': '读取失败'})
                    continue
                missing = len(all_ids - ids)
                if missing:
                    skipped.append({'name': name, 'reason': f'缺 {missing} 个节点'})
                    continue
                prev = store.get(name) if isinstance(store.get(name), dict) else {}
                prev_dis = (prev.get('disabled') or {}) if prev else {}
                # 保留用户已切换的组开关（仅存留的组 id）
                store[name] = {'data': clean, 'target': name,
                               'disabled': {k: v for k, v in prev_dis.items() if any(c['id'] == k for c in clean)}}
                applied.append(name)
            self.workflow_config['__group_bindings_store__'] = store
            await self._save_workflow_config()
            return web.json_response({"ok": True, "applied": applied, "skipped": skipped})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_debug_group_chain(self, r):
        """v4.5.2b: 组/图片注入链路干跑——按生成前的真实变换顺序执行，返回每步快照（不提交）。"""
        try:
            data = await r.json()
            wf_name = str(data.get('workflow', '') or self.current_workflow_name)
            image_path = data.get('image_path') or None
            wf_file = self._find_workflow_file_path(wf_name) if hasattr(self, '_find_workflow_file_path') else None
            if not wf_file:
                for wr in (self._refresh_workflow_list() or []):
                    if wr.get('name') == wf_name:
                        wf_file = wr.get('path')
                        break
            if not wf_file:
                return web.json_response({"ok": False, "error": f"找不到工作流 {wf_name}"})
            with open(wf_file, 'r', encoding='utf-8') as f:
                wf = json.load(f)

            def snap(tag):
                lis = {nid: str((n.get('inputs', {}) or {}).get('image', '?'))[:40]
                       for nid, n in wf.items() if isinstance(n, dict) and n.get('class_type') == 'LoadImage'}
                jzl = next((n for n in wf.values() if isinstance(n, dict) and 'ReferenceToVideo' in str(n.get('class_type', ''))), None)
                refs = {}
                if jzl:
                    for k, v in (jzl.get('inputs', {}) or {}).items():
                        if 'ref_images' in k:
                            refs[k.replace('ref_images.', '')] = ('link[' + str(v[0]) + ']') if isinstance(v, list) else repr(v)[:16]
                return {'step': tag, 'load': lis, 'refs': refs}

            snaps = [snap('0-文件原始')]
            # 阶段1：配置应用（含未上传加载节点清理）——与真实流程一致：有图时保护全部 LoadImage
            _protect = set()
            if image_path:
                for _n in (self._find_all_load_image_nodes(wf) or []):
                    _protect.add(str(_n))
            self._apply_workflow_config(wf, wf_name=wf_name, protect_nodes=_protect or None)
            snaps.append(snap('1-配置应用+清理'))
            # 阶段2：组模式（禁用组移除）
            self._apply_group_modes(wf, wf_name=wf_name)
            snaps.append(snap('2-组模式'))
            # 阶段3：图片注入（可选）
            if image_path:
                await self._set_load_image(wf, image_path)
                snaps.append(snap('3-图片注入'))
            # 阶段4：引用重建
            self._rebuild_jzl_refs(wf)
            snaps.append(snap('4-引用重建'))
            return web.json_response({"ok": True, "wf": wf_name, "snaps": snaps})
        except Exception as e:
            import traceback
            return web.json_response({"ok": False, "error": str(e), "tb": traceback.format_exc()[-600:]})

    async def _webui_proxy(self, r):
        """代理 ComfyUI 请求（支持 GET 和 POST）。白名单精确校验 host:port，防止 SSRF 绕过。"""
        if r.method == 'POST':
            body = await r.read()
            content_type = r.content_type or 'application/json'
        target = r.query.get('url', '')
        if not target: return web.Response(text='{"error":"missing url"}', content_type='application/json')
        # 精确校验 host:port（不用 startswith，避免 http://127.0.0.1:8188evil.com 之类绕过）
        try:
            from urllib.parse import urlparse
            parsed = urlparse(target)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname:
                raise ValueError('bad url')
            target_host = parsed.hostname.lower()
            target_port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            allowed = self.comfyui_url or ''
            if '://' in allowed:
                a_parsed = urlparse(allowed)
                allowed_host = (a_parsed.hostname or '').lower()
                allowed_port = a_parsed.port or (443 if a_parsed.scheme == 'https' else 80)
            else:
                if ':' in allowed:
                    ah, ap = allowed.rsplit(':', 1)
                    allowed_host = ah.lower()
                    allowed_port = int(ap)
                else:
                    allowed_host = allowed.lower()
                    allowed_port = 80
            if not allowed_host or target_host != allowed_host or target_port != allowed_port:
                raise ValueError('host mismatch')
        except Exception:
            logger.warning(f"[ComfyUI] 代理请求被拒绝（非白名单地址）: {target}")
            return web.Response(text=json.dumps({"error": "proxy denied"}), content_type='application/json', status=403)
        try:
            async with aiohttp.ClientSession() as s:
                if r.method == 'POST':
                    async with s.post(target, data=body, headers={'Content-Type': content_type}, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        return web.Response(text=await resp.text(), content_type='application/json')
                else:
                    async with s.get(target, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        return web.Response(text=await resp.text(), content_type='application/json')
        except Exception as e:
            logger.warning(f"[ComfyUI] 代理请求失败 {target}: {e}")
            return web.Response(text=json.dumps({"error": "proxy error"}), content_type='application/json')

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
                        # 兼容 UI 格式（含打包子节点）：先展开为 API 格式再扫描节点类型
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
                                  'lora_name', 'control_net_name', 'style_model_name',
                                  'model', 'mmproj', 'chat_handler']

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

    async def _webui_get_loras(self, request):
        """从 ComfyUI 获取所有可用 Lora 列表"""
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://{self.comfyui_url}/object_info/LoraLoader",
                                 timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status == 200:
                        data = await r.json()
                        info = data.get('LoraLoader', {})
                        required = info.get('input', {}).get('required', {})
                        lora_list = required.get('lora_name', [None])[0]
                        if isinstance(lora_list, list):
                            return web.json_response(lora_list)
            return web.json_response([])
        except Exception as e:
            logger.warning(f"[ComfyUI] 获取 Lora 列表失败: {e}")
            return web.json_response([])

    async def _webui_lora_preview(self, request):
        """代理 ComfyUI LoRA Manager 的预览图。
        参数: path (ComfyUI 文件路径) 或 fn (lora 文件名)
        支持内存缓存，避免重复穿透到 ComfyUI。"""
        img_path = request.query.get('path', '')
        cache_key = ''
        if not img_path:
            fn = request.query.get('fn', '')
            if fn:
                cache_key = fn
                # 检查缓存
                if cache_key in self._lora_preview_cache:
                    cached = self._lora_preview_cache[cache_key]
                    return web.Response(body=cached['body'], content_type=cached['content_type'])
                meta = self._match_lora_meta(fn)
                if meta and meta.get('preview_url'):
                    img_path = meta['preview_url']
        if not img_path:
            return web.Response(status=404, text='No preview available')
        try:
            # 如果是相对 URL（/api/lm/previews?...），拼上 ComfyUI 地址
            if img_path.startswith('/'):
                full_url = f"http://{self.comfyui_url}{img_path}"
            else:
                full_url = img_path
            async with aiohttp.ClientSession() as s:
                async with s.get(full_url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        body = await r.read()
                        ct = r.headers.get('Content-Type', 'image/jpeg')
                        # 按 URL 扩展名修正 content-type：LoRA Manager 预览接口可能对
                        # GIF/WebP 返回 image/jpeg，导致浏览器按静态图解析、动画丢失
                        try:
                            path_lower = full_url.lower()
                            ext_guess = path_lower.rsplit('.', 1)[-1] if '.' in path_lower else ''
                            if ext_guess == 'gif':
                                ct = 'image/gif'
                            elif ext_guess == 'webp':
                                ct = 'image/webp'
                            elif ext_guess == 'apng':
                                ct = 'image/apng'
                            elif ext_guess in ('png',):
                                ct = 'image/png'
                            elif ext_guess in ('jpg', 'jpeg'):
                                ct = 'image/jpeg'
                            elif ext_guess in ('mp4', 'webm', 'mov', 'm4v'):
                                ct = 'video/mp4' if ext_guess in ('mp4', 'm4v', 'mov') else 'video/webm'
                        except Exception:
                            pass
                        # 写入缓存（限制最大 256 项，避免内存泄漏）
                        if cache_key:
                            if len(self._lora_preview_cache) >= 256:
                                self._lora_preview_cache.pop(next(iter(self._lora_preview_cache)), None)
                            self._lora_preview_cache[cache_key] = {'body': body, 'content_type': ct}
                        return web.Response(body=body, content_type=ct)
            return web.Response(status=404, text='Preview not found')
        except Exception as e:
            logger.warning(f"[ComfyUI] Lora 预览图获取失败: {e}")
            return web.Response(status=500, text=str(e))

    # ===================== Krea / easy-use 风格预设 =====================
    async def _webui_get_style_libs(self, request):
        """返回所有风格大类（easy stylesSelector 的 styles 选项）。
        直接从已缓存的 ComfyUI object_info 读取，无需额外请求。"""
        try:
            await self._ensure_object_info()
            oi = self._object_info_cache or {}
            # 兼容不同节点类名写法
            ct = None
            for k in ('easy stylesSelector', 'easy_stylesSelector', 'EasyStyleSelector'):
                if k in oi:
                    ct = oi[k]
                    break
            if not ct:
                return web.json_response({"libs": []})
            spec = ct.get('styles') or {}
            libs = spec.get('options') or []
            if isinstance(libs, list):
                libs = [str(x) for x in libs]
            return web.json_response({"libs": libs})
        except Exception as e:
            logger.warning(f"[ComfyUI] 获取风格大类失败: {e}")
            return web.json_response({"libs": [], "error": str(e)})

    async def _webui_get_style_list(self, request):
        """代理 easy-use 的 /easyuse/prompt/styles?name=<大类>，返回该大类下所有具体风格。
        把 thumbnail 相对 URL 改写为本插件代理地址，便于前端（手机浏览器）跨网取图。"""
        lib = request.query.get('lib', '')
        if not lib:
            return web.json_response({"error": "missing lib"}, status=400)
        if not self.comfyui_url:
            return web.json_response({"error": "comfyui 未连接"}, status=400)
        try:
            async with aiohttp.ClientSession() as s:
                url = f"http://{self.comfyui_url}/easyuse/prompt/styles?name={aiohttp.helpers.quote(lib)}"
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status != 200:
                        return web.Response(status=r.status, text=await r.text() or 'styles fetch failed')
                    data = await r.json()
            # 改写 thumbnail 为本插件代理
            for item in (data or []):
                thumb = item.get('thumbnail')
                if isinstance(thumb, str) and '/easyuse/prompt/styles/image' in thumb:
                    # 原路径形如 /easyuse/prompt/styles/image?path=./samples/xxx.jpg
                    rel = thumb.split('?', 1)[-1]
                    item['thumbnail'] = f"/api/style-preview?{rel}"
                elif isinstance(thumb, list):
                    item['thumbnail'] = [
                        f"/api/style-preview?{t.split('?', 1)[-1]}" if isinstance(t, str) and '/easyuse/prompt/styles/image' in t else t
                        for t in thumb
                    ]
            return web.json_response({"styles": data or []})
        except Exception as e:
            logger.warning(f"[ComfyUI] 获取风格列表失败: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def _webui_style_preview(self, request):
        """代理 easy-use 风格预览图 /easyuse/prompt/styles/image?path=...
        支持内存缓存 + content-type 修正，与 /api/lora-preview 一致。"""
        path = request.query.get('path', '')
        name = request.query.get('name', '')
        styles_name = request.query.get('styles_name', '')
        if not path and not (name and styles_name):
            return web.Response(status=400, text='Missing path or name')
        if not self.comfyui_url:
            return web.Response(status=400, text='comfyui 未连接')
        try:
            # 构造 easy-use 原始 URL
            if path:
                eu_url = f"http://{self.comfyui_url}/easyuse/prompt/styles/image?path={aiohttp.helpers.quote(path, safe='')}"
            else:
                eu_url = f"http://{self.comfyui_url}/easyuse/prompt/styles/image?name={aiohttp.helpers.quote(name)}&styles_name={aiohttp.helpers.quote(styles_name)}"
            cache_key = eu_url
            if cache_key in self._style_preview_cache:
                cached = self._style_preview_cache[cache_key]
                return web.Response(body=cached['body'], content_type=cached['content_type'])
            async with aiohttp.ClientSession() as s:
                async with s.get(eu_url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status != 200:
                        return web.Response(status=r.status, text='style preview not found')
                    body = await r.read()
                    ct = r.headers.get('Content-Type', 'image/jpeg')
                    try:
                        low = eu_url.lower()
                        ext = low.rsplit('.', 1)[-1].split('?')[0] if '.' in low.rsplit('/', 1)[-1] else ''
                        if ext == 'gif':
                            ct = 'image/gif'
                        elif ext == 'webp':
                            ct = 'image/webp'
                        elif ext == 'apng':
                            ct = 'image/apng'
                        elif ext == 'png':
                            ct = 'image/png'
                        elif ext in ('jpg', 'jpeg'):
                            ct = 'image/jpeg'
                        elif ext in ('mp4', 'webm', 'mov', 'm4v'):
                            ct = 'video/mp4' if ext in ('mp4', 'm4v', 'mov') else 'video/webm'
                    except Exception:
                        pass
                    if len(self._style_preview_cache) >= 256:
                        self._style_preview_cache.pop(next(iter(self._style_preview_cache)), None)
                    self._style_preview_cache[cache_key] = {'body': body, 'content_type': ct}
                    return web.Response(body=body, content_type=ct)
        except Exception as e:
            logger.warning(f"[ComfyUI] 风格预览图获取失败: {e}")
            return web.Response(status=500, text=str(e))

    async def _webui_view_input(self, request):
        """代理 ComfyUI /view（input 目录文件预览）：前端加载已上传图片时使用。
        参数: filename + 可选 subfolder/type。支持按扩展名修正 content-type。"""
        filename = request.query.get('filename', '')
        subfolder = request.query.get('subfolder', '')
        ftype = request.query.get('type', 'input')
        if not filename:
            return web.Response(status=400, text='Missing filename')
        import urllib.parse as _up
        qs = f"filename={_up.quote(filename)}&type={ftype}"
        if subfolder:
            qs += f"&subfolder={_up.quote(subfolder)}"
        full_url = f"http://{self.comfyui_url}/view?{qs}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(full_url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status == 200:
                        body = await r.read()
                        ct = r.headers.get('Content-Type', 'image/jpeg')
                        # 按扩展名修正 content-type（GIF/WebP/MP4 等）
                        try:
                            path_lower = filename.lower()
                            ext_guess = path_lower.rsplit('.', 1)[-1] if '.' in path_lower else ''
                            if ext_guess == 'gif': ct = 'image/gif'
                            elif ext_guess == 'webp': ct = 'image/webp'
                            elif ext_guess == 'apng': ct = 'image/apng'
                            elif ext_guess in ('png',): ct = 'image/png'
                            elif ext_guess in ('jpg', 'jpeg'): ct = 'image/jpeg'
                            elif ext_guess in ('mp4', 'webm', 'mov', 'm4v'):
                                ct = 'video/mp4' if ext_guess in ('mp4', 'm4v', 'mov') else 'video/webm'
                        except Exception:
                            pass
                        return web.Response(body=body, content_type=ct)
                    return web.Response(status=r.status, text='view failed')
            return web.Response(status=404, text='view not found')
        except Exception as e:
            logger.warning(f"[ComfyUI] /view 代理失败: {e}")
            return web.Response(status=500, text=str(e))

    async def _webui_get_progress(self, request):
        """返回当前正在执行的任务进度（含阶段性进度）。"""
        pid = self.current_prompt_id
        prog = self._progress.get(pid, {})
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://{self.comfyui_url}/queue", timeout=aiohttp.ClientTimeout(total=3)) as r:
                    qd = await r.json()
                    running = len(qd.get('queue_running', []))
                    pending = len(qd.get('queue_pending', [])) + self._pending_grimoire_tasks
        except Exception:
            running = pending = 0
        # 计算已运行时间
        start_ts = self._prompt_start_time.get(pid, 0)
        elapsed = int(time.time() - start_ts) if start_ts else 0
        any_running = any(
            pp.get('running', False) for pp in self._prompt_progress.values()
        ) if self._prompt_progress else False
        is_running = running > 0 or any_running
        # 进度值：优先 WS 的 step 级进度；视频/长任务无 step 时用节点级进度兜底
        pp = self._prompt_progress.get(pid, {}) or {}
        nodes_done = pp.get('nodes_done', 0) or 0
        nodes_total = pp.get('nodes_total', 0) or 0
        # pv/pm = 当前节点的采样步进度（仅采样节点有事件）
        if prog.get('max', 0) and prog.get('max', 0) > 0:
            pv, pm = prog.get('value', 0), prog.get('max', 0)
        else:
            pv, pm = 0, 0
        # v4.5.6: 合成总进度 = (已完成节点数 + 当前节点采样分数) / 总节点数。
        # 旧版直接拿当前节点采样步当百分比：模型加载/非采样节点期间没有任何事件，
        # 进度条长时间钉在 0%，且每个节点之间跳变不单调。
        step_frac = (pv / pm) if pm > 0 else 0.0
        if nodes_total > 0:
            percent = int(min(99, max(0, (nodes_done + step_frac) / nodes_total * 100)))
        elif pm > 0:
            percent = int(min(99, step_frac * 100))
        else:
            percent = 0
        if not is_running:
            percent = 0
        # 节点类名翻译：WS 只给裸 ID
        node_map = pp.get('node_map') or {}
        raw_node = str(pp.get('node_name', '') or '')
        node_label = node_map.get(raw_node) or raw_node
        return web.json_response({
            "prompt_id": pid or "",
            "queue_running": running,
            "queue_pending": pending,
            "running": is_running,
            "elapsed": elapsed,
            "progress_value": pv,
            "progress_max": pm,
            "percent": percent,
            "nodes_done": nodes_done,
            "nodes_total": nodes_total,
            "node_name": pp.get('node_name', ''),
            "node_label": node_label,
            "state": "generating" if any_running else ("queued" if running > 0 else "idle"),
        })

    async def _webui_get_workflow_params_config(self, request):
        # 首次加载时 workflow_path 可能尚未初始化，先刷新工作流列表，
        # 否则下方官方节点检测被 `if self.workflow_path` 跳过 → 前端分辨率面板显示旧版
        if not self.workflow_path:
            self._refresh_workflow_list()
        config_path = self._user_data_dir / "config.json"
        if config_path.exists():
            try:
                with self._config_file_lock:
                    with open(str(config_path), 'r', encoding='utf-8') as f:
                        file_data = json.load(f)
                # 只回填内存中缺失的顶层键，绝不整表覆盖 self.workflow_config，
                # 否则打开面板会丢掉尚未落盘的内存改动（random_pick_mode / __bind_target__ 等）
                if isinstance(file_data, dict):
                    for _k, _v in file_data.items():
                        self.workflow_config.setdefault(_k, _v)
            except Exception as e:
                logger.warning(f"[ComfyUI] 读取工作流参数配置失败: {e}")
        wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
        wf_config_name = self.current_workflow_name
        wf_config = wf_configs.get(wf_config_name, {})
        # 诊断日志
        logger.info(f"[ComfyUI] _webui_get_workflow_params_config: wf={wf_config_name!r}, "
                    f"node_configs_keys={list(wf_configs.keys())}, "
                    f"current_wf_node_roles={ {k: wf_config.get(k) for k in ['__prompt_node__','__resolution_node__','__load_image_nodes__','__negative_node__','__expanded_text_node__']} }")
        # 官方原生节点检测：读取当前工作流文件，识别 ResolutionSelector（分辨率）和 PrimitiveFloat/Float（时长）
        official_res_nodes = []
        official_duration_nodes = []
        try:
            if self.workflow_path:
                with open(self.workflow_path, 'r', encoding='utf-8') as f:
                    wf_now = json.load(f)
                # 兼容 UI 格式（含打包子节点）：先展开为 API 格式再检测官方节点
                official_res_nodes = self._find_official_resolution_nodes(wf_now)
                official_duration_nodes = self._find_official_duration_nodes(wf_now)
        except Exception as e:
            logger.warning(f"[ComfyUI] 官方节点检测失败: {e}")
        # ★ v4.4.2: 组绑定分桶——迁移旧三元组并注入当前工作流的桶（键名保持旧格式，前端无感）
        if self._migrate_group_binding_to_store():
            await self._save_workflow_config()
        _gbucket = (self.workflow_config.get('__group_bindings_store__', {}) or {}).get(self.current_workflow_name) or {}
        return web.json_response({
            "__prompt_node__": wf_config.get("__prompt_node__", "") or self.workflow_config.get("__prompt_node__", ""),
            "__resolution_node__": wf_config.get("__resolution_node__", "") or self.workflow_config.get("__resolution_node__", ""),
            "__load_image_node__": wf_config.get("__load_image_node__", "") or self.workflow_config.get("__load_image_node__", ""),
            "__load_image_nodes__": wf_config.get("__load_image_nodes__", "") or self.workflow_config.get("__load_image_nodes__", ""),
            "__negative_node__": wf_config.get("__negative_node__", "") or self.workflow_config.get("__negative_node__", ""),
            "__commands__": self.workflow_config.get("__commands__", {}),
            "__groups_source__": _gbucket.get("source", ""),
            "__disabled_groups__": _gbucket.get("disabled", {}),
            "__disabled_nodes__": wf_config.get("__disabled_nodes__", []),
            "__bind_target__": _gbucket.get("target", ""),
            "__current_workflow__": self.workflow_config.get("__current_workflow__", ""),
            "__groups_data__": _gbucket.get("data", []),
            "__hidden_workflows__": self.workflow_config.get("__hidden_workflows__", []),
            "__workflow_aliases__": self.workflow_config.get("__workflow_aliases__", {}),
            "__category_order__": self.workflow_config.get("__category_order__", []),
            "__workflow_node_configs__": self.workflow_config.get("__workflow_node_configs__", {}),
            "__wf_categories__": self.workflow_config.get("__wf_categories__", {}),
            # 官方原生节点检测结果（前端据此切换新/老方案显示）
            "official_res_nodes": official_res_nodes,
            "official_duration_nodes": official_duration_nodes,
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

    async def _webui_set_official_res(self, request):
        """WebUI 直接设置官方 ResolutionSelector 节点参数（aspect_ratio / megapixels / multiple）。
        同时同步 AspectRatioNode（比例锁定工具）节点——部分工作流实际用该节点控制分辨率。"""
        try:
            data = await request.json()
            aspect_ratio = data.get("aspect_ratio", "")
            megapixels = data.get("megapixels")
            multiple = data.get("multiple")
            if not self.workflow_path:
                return web.json_response({"ok": False, "error": "未选中工作流"})
            with open(self.workflow_path, 'r', encoding='utf-8') as f:
                wf = json.load(f)
            official_ids = self._find_official_resolution_nodes(wf)
            aspect_ids = self._find_aspect_ratio_nodes(wf)
            if not official_ids and not aspect_ids:
                return web.json_response({"ok": False, "error": "当前工作流无分辨率节点（ResolutionSelector/AspectRatioNode），请用老方案设置"})
            # 参数校验
            if aspect_ratio and aspect_ratio not in self.official_ratio_reverse:
                return web.json_response({"ok": False, "error": f"未知官方比例: {aspect_ratio}"})
            if megapixels is not None:
                try:
                    megapixels = float(megapixels)
                except (TypeError, ValueError):
                    return web.json_response({"ok": False, "error": "megapixels 必须是数字"})
            if multiple is not None:
                try:
                    multiple = int(multiple)
                except (TypeError, ValueError):
                    return web.json_response({"ok": False, "error": "multiple 必须是整数"})
            # 写回分辨率节点：ResolutionSelector 用官方长格式（"9:16 (Portrait Widescreen)"）
            for nid in official_ids:
                inputs = wf[nid].get('inputs', {})
                if aspect_ratio:
                    inputs['aspect_ratio'] = aspect_ratio
                if megapixels is not None: inputs['megapixels'] = megapixels
                if multiple is not None: inputs['multiple'] = multiple
            # AspectRatioNode：写插件格式（aspect_ratio 取冒号部分，如 "9:16 (Portrait Widescreen)" → "9:16"）
            # 并按其 width/height 基准 + divisible_by 重算实际宽高
            if aspect_ids and aspect_ratio:
                plugin_ratio = aspect_ratio.split(' ')[0]  # "9:16 (Portrait Widescreen)" → "9:16"
                try:
                    ra, rb = map(int, plugin_ratio.split(':'))
                except (ValueError, AttributeError):
                    ra, rb = 9, 16
                mp = megapixels if megapixels is not None else 1.0
                total_pixels = mp * 1024 * 1024
                x = (total_pixels / (ra * rb)) ** 0.5
                for nid in aspect_ids:
                    inputs = wf[nid].get('inputs', {})
                    inputs['aspect_ratio'] = plugin_ratio
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
            self._atomic_write_workflow_json(wf, self.workflow_path)
            # 清理 saved_texts 中的旧分辨率值，防止 /api/workflow-params 用旧值覆盖显示
            cleared = self._clear_saved_resolution_keys(official_ids + aspect_ids)
            if cleared:
                await self._save_workflow_config()
            # 同步内存默认值（比例反查 + 像素数估算）
            ratio_plugin = self._official_to_ratio(aspect_ratio) if aspect_ratio else self.default_ratio
            if megapixels is not None and aspect_ratio:
                w_est = int((megapixels * 1024 * 1024) ** 0.5)
                self.default_width, self.default_height = w_est, w_est
            if aspect_ratio and ratio_plugin != self.default_ratio:
                self.default_ratio = ratio_plugin
                self._save_local_config({"default_ratio": ratio_plugin})
            logger.info(f"[ComfyUI] WebUI 已设置官方分辨率: {aspect_ratio} {megapixels}MP x{multiple} 节点{official_ids + aspect_ids}")
            return web.json_response({"ok": True, "nodes": official_ids + aspect_ids, "aspect_ratio": aspect_ratio, "megapixels": megapixels, "multiple": multiple})
        except Exception as e:
            logger.warning(f"[ComfyUI] WebUI 设置官方分辨率失败: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_set_duration(self, request):
        """WebUI 设置视频时长（写入官方 PrimitiveFloat/Float 节点）。"""
        try:
            data = await request.json()
            duration = data.get("duration")
            try:
                duration = float(duration)
            except (TypeError, ValueError):
                return web.json_response({"ok": False, "error": "时长必须是数字（秒）"})
            ok = self._sync_duration_to_workflow(duration)
            if not ok:
                return web.json_response({"ok": False, "error": "当前工作流无官方时长节点（PrimitiveFloat/Float）"})
            return web.json_response({"ok": True, "duration": duration})
        except Exception as e:
            logger.warning(f"[ComfyUI] WebUI 设置时长失败: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_reset_all(self, request):
        """重置所有用户设定：清空 data/user/config.json 中的所有配置，回到全新状态"""
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
            self.webui_port = 8898
            self.webui_lan = False
            self.webui_ipv6 = False
            self.default_quality = "720p"
            self.default_ratio = "9:16"
            self.default_width, self.default_height = self._calc_resolution(self.default_quality, self.default_ratio)
            # 写回一个干净的配置文件（与 清空用户配置.py 一致，不碰 data/ 下的魔导书数据）
            config_path = self._user_data_dir / "config.json"
            # 清除缓存的背景图片
            bg_file = self._user_data_dir / "cache" / "bg.png"
            if bg_file.exists():
                bg_file.unlink()
            clean = {
                "__local_config__": {},
                "__group_bindings__": {},
                "__wf_categories__": {},
                "__disabled_groups__": {},
                "__groups_source__": "",
                "__bind_target__": "",
                "__groups_data__": [],
                "__hidden_workflows__": [],
                "__workflow_aliases__": {},
                "__workflow_node_configs__": {},
                "__workflow_categories__": {},
                "__category_order__": [],
                "__grimoire_enabled__": False,
                "__grimoire_pins__": {},
                "__grimoire_rand_pool__": [],
                "__grimoire_stars__": {},
                "quality_presets": {
                    "480p": {"name": "SD", "pixels": 399360},
                    "720p": {"name": "标清", "pixels": 921600},
                    "1080p": {"name": "高清", "pixels": 2073600},
                    "2K": {"name": "超清", "pixels": 3686400},
                    "4K": {"name": "原画", "pixels": 8294400},
                },
                "aspect_ratios": ["1:1", "3:4", "4:3", "9:16", "16:9", "2:3", "3:2"],
                "current_quality": "720p",
                "current_ratio": "9:16",
                "current_width": 720,
                "current_height": 1280,
            }
            async with self._config_lock:
                with open(str(config_path), 'w', encoding='utf-8') as f:
                    json.dump(clean, f, ensure_ascii=False, indent=2)
            return web.json_response({"ok": True, "message": "✅ 已重置所有用户设定，请刷新页面"})
        except Exception as e:
            logger.warning(f"[ComfyUI] WebUI 重置失败: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_bg_save(self, request):
        """保存背景设置：base64 解码后存为 PNG 文件，config 只存 blur/brightness"""
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        bg_data = data.get('bg', '')
        blur = data.get('blur', '0')
        brightness = data.get('brightness', '100')
        # 存模糊/亮度到 config
        async with self._config_lock:
            self.workflow_config['__bg_blur__'] = blur
            self.workflow_config['__bg_brightness__'] = brightness
        await self._save_workflow_config()
        # base64 解码后存为 PNG 文件
        bg_file = self._user_data_dir / "cache" / "bg.png"
        bg_file.parent.mkdir(parents=True, exist_ok=True)
        if bg_data and ',' in bg_data:
            try:
                import base64
                encoded = bg_data.split(',', 1)[1]
                with open(bg_file, 'wb') as f:
                    f.write(base64.b64decode(encoded))
            except Exception as e:
                logger.warning(f"[ComfyUI] 保存背景图片文件失败: {e}")
        elif not bg_data:
            # 清空背景
            if bg_file.exists():
                bg_file.unlink()
        return web.json_response({"ok": True})

    async def _webui_bg_image(self, request):
        """返回已保存的背景图片文件"""
        bg_file = self._user_data_dir / "cache" / "bg.png"
        if bg_file.exists():
            return web.FileResponse(bg_file)
        return web.Response(status=404)

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

    async def _webui_get_workflow_preview(self, r):
        """返回工作流预览图（GET /api/workflow-preview?name=xxx）"""
        name = (r.query.get('name') or '').strip()
        p = self._workflow_preview_path(name)
        if not p:
            return web.Response(status=404, text='Preview not found')
        try:
            return web.FileResponse(str(p))
        except Exception as e:
            logger.warning(f"[ComfyUI] 读取工作流预览图失败: {e}")
            return web.Response(status=500, text=str(e))

    async def _webui_save_workflow_preview(self, r):
        """保存工作流预览图（POST /api/workflow-preview {name, data(base64 dataURL)}）"""
        try:
            data = await r.json()
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        name = (data.get('name') or '').strip()
        payload = data.get('data') or ''
        if not name or '..' in name or '/' in name or '\\' in name:
            return web.json_response({"ok": False, "error": "非法文件名"})
        pv_dir = self._user_data_dir / "workflow_previews"
        pv_dir.mkdir(parents=True, exist_ok=True)
        target = pv_dir / (name + '.png')
        # 空 payload → 删除预览图
        if not payload:
            try:
                if target.exists(): target.unlink()
                return web.json_response({"ok": True, "deleted": True})
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)})
        # dataURL: data:image/png;base64,xxxx
        if ',' not in payload:
            return web.json_response({"ok": False, "error": "图片数据格式错误"})
        b64 = payload.split(',', 1)[1]
        try:
            raw = base64.b64decode(b64)
        except Exception:
            return web.json_response({"ok": False, "error": "base64 解码失败"})
        if not raw:
            return web.json_response({"ok": False, "error": "图片数据为空"})
        # 无限制大小上传，但最终压缩到 ≤1MB 后保存
        try:
            final_bytes, ext = self._compress_preview_bytes(raw)
        except Exception:
            final_bytes, ext = raw, 'png'
        # 清理旧预览文件（多扩展名），避免残留
        pv_dir.mkdir(parents=True, exist_ok=True)
        for old in pv_dir.glob(name + '.*'):
            try: old.unlink()
            except Exception: pass
        target = pv_dir / (name + '.' + ext)
        target.write_bytes(final_bytes)
        logger.info(f"[ComfyUI] 保存工作流预览图: {name}.{ext} 原始{len(raw)}B -> 压缩{len(final_bytes)}B")
        return web.json_response({"ok": True, "size": len(final_bytes), "ext": ext})

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

    async def _webui_get_lora_metadata(self, request):
        """返回 Lora 元数据缓存（触发词、预览图、标签等）。
        前端通过此 API 获取已选 Lora 的额外信息。"""
        await self._fetch_lora_metadata()
        # 过滤掉内部索引 key（__bn__ 前缀）
        # 保留所有 key（包括 __bn__ 前缀的 basename 索引 + 多分隔符变体），让前端做多策略匹配
        return web.json_response(self._lora_metadata_cache)

    async def _webui_refresh_lora_metadata(self, request):
        """强制清掉缓存并重新拉取 Lora 元数据（用于「刷新元数据」按钮）。"""
        self._lora_metadata_cache.clear()
        self._lora_metadata_fetched = False
        await self._fetch_lora_metadata()
        return web.json_response({
            "ok": True,
            "count": len(self._lora_metadata_cache),
            "trigger_count": sum(1 for v in self._lora_metadata_cache.values() if isinstance(v, dict) and v.get('trigger_words'))
        })

    async def _webui_interrupt(self, request):
        """WebUI 停止按钮：向 ComfyUI 发送 /interrupt 中断当前生成。"""
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(f"http://{self.comfyui_url}/interrupt", timeout=aiohttp.ClientTimeout(total=10))
            logger.info("[ComfyUI] WebUI 已发送停止命令")
            return web.json_response({"ok": True})
        except Exception as e:
            logger.warning(f"[ComfyUI] WebUI 停止命令失败: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_get_gallery(self, r):
        """v4.12.0: 分页画廊——扫描结果内存缓存（30s TTL，force=1 立即重扫），
        服务端完成 type/filter 过滤后按 offset/limit 分页返回。
        旧版每次请求全量递归扫描 + 全量 JSON 返回 + 前端一次性渲染，列表越大越慢。"""
        try:
            output_dir = self.output_dir.resolve()
            if not output_dir.exists():
                return web.json_response({"images": [], "total": 0, "hasMore": False})
            q = r.rel_url.query
            def _int(name, default, lo, hi):
                try:
                    return max(lo, min(hi, int(q.get(name, str(default)))))
                except (TypeError, ValueError):
                    return default
            offset = _int('offset', 0, 0, 100000)
            limit = _int('limit', 60, 1, 200)
            ftype = q.get('type', 'all')
            tfilter = q.get('filter', 'all')
            force = q.get('force') == '1'

            now = time.time()
            cached = getattr(self, '_gallery_scan_cache', None)
            if force or not cached or now - cached[0] > 30:
                allowed_ext = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.mp4', '.mov', '.avi'}
                video_ext = {'.mp4', '.mov', '.avi'}
                from urllib.parse import quote
                full = []
                for f in output_dir.rglob('*'):
                    if not f.is_file() or f.suffix.lower() not in allowed_ext:
                        continue
                    rel = f.relative_to(output_dir)
                    if str(rel).startswith('upload' + os.sep) or str(rel).startswith('upload/'):
                        continue
                    full.append({
                        "path": str(f),
                        "url": f'/api/gallery/file?path={quote(str(rel))}',
                        "name": f.name,
                        "size": f.stat().st_size,
                        "mtime": f.stat().st_mtime,
                        "type": "video" if f.suffix.lower() in video_ext else "image"
                    })
                full.sort(key=lambda x: x["mtime"], reverse=True)
                self._gallery_scan_cache = (now, full)
                cached = self._gallery_scan_cache  # 首扫结果必须回读（旧局部值还是空）
            full = cached[1] if cached else []
            items = full
            if ftype in ('image', 'video'):
                items = [x for x in items if x['type'] == ftype]
            if tfilter == 'today':
                ts = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
                items = [x for x in items if x['mtime'] >= ts]
            elif tfilter == '1day':
                items = [x for x in items if now - x['mtime'] <= 86400]
            elif tfilter == '2day':
                items = [x for x in items if now - x['mtime'] <= 2 * 86400]
            page = items[offset:offset + limit]
            return web.json_response({
                "images": page,
                "total": len(items),
                "hasMore": offset + limit < len(items)
            })
        except Exception as e:
            logger.error(f"[ComfyUI] 获取画廊列表失败: {e}")
            return web.json_response({"images": [], "total": 0, "hasMore": False, "error": str(e)})



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
            # v4.10.1: 补视频 Content-Type（此前 mp4 全是 octet-stream，部分内核播放不出）；
            # Cache-Control 改长缓存——文件名时间戳化天然不可变，no-cache 曾导致每次浏览重复下载
            content_type_map = {
                '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                '.webp': 'image/webp', '.gif': 'image/gif', '.bmp': 'image/bmp',
                '.mp4': 'video/mp4', '.mov': 'video/quicktime', '.avi': 'video/x-msvideo'
            }
            ctype = content_type_map.get(ext, 'application/octet-stream')
            return web.FileResponse(file_path, headers={'Content-Type': ctype, 'Cache-Control': 'public, max-age=86400'})
        except Exception as e:
            logger.error(f"[ComfyUI] 提供画廊文件失败: {e}")
            return web.Response(status=500, text=str(e))

    async def _webui_gallery_prompt(self, request):
        """v4.10.0: 画廊图片 → 查询生成提示词（画廊单图查看器右侧栏 / 手机复制按钮数据源）。
        三级匹配：① prompt_log 的 path 精确命中（v4.9.6+ 生成的图）② 文件 MD5 对 img_hash
        （旧图/平台重编码）③ 无记录返回 matched=none（前端显示「未找到生成记录」）。
        路径安全：resolve 后必须位于输出目录内。MD5 带 (path,mtime) 缓存防重复算大文件。"""
        rel = (request.rel_url.query.get('path', '') or '').strip().replace('\\', '/')
        if not rel:
            return web.json_response({"ok": False, "error": "缺少 path"})
        try:
            output_dir = self.output_dir.resolve()
            f = (output_dir / rel).resolve()
        except Exception:
            return web.json_response({"ok": False, "error": "路径无效"})
        if not str(f).startswith(str(output_dir)):
            return web.json_response({"ok": False, "error": "路径越界"})
        if not f.exists() or not f.is_file():
            return web.json_response({"ok": False, "error": "文件不存在"})
        try:
            import hashlib
            key = (str(f), int(f.stat().st_mtime))
            h = self._gallery_md5_cache.get(key)
            if h is None:
                _h = hashlib.md5()
                with open(str(f), 'rb') as fp:
                    for chunk in iter(lambda: fp.read(1024 * 512), b''):
                        _h.update(chunk)
                h = _h.hexdigest()
                if len(self._gallery_md5_cache) > 500:
                    self._gallery_md5_cache.clear()
                self._gallery_md5_cache[key] = h
        except Exception as e:
            return web.json_response({"ok": False, "error": f"读取失败: {e}"})
        async with self._prompt_log_lock:
            for r in reversed(self._prompt_log):
                if r.get('path') and str(r['path']) == str(f):
                    return web.json_response({"ok": True, "prompt": r.get('prompt', ''),
                                              "ts": r.get('timestamp', 0), "matched": "path"})
            for r in reversed(self._prompt_log):
                if r.get('img_hash') and r['img_hash'] == h:
                    return web.json_response({"ok": True, "prompt": r.get('prompt', ''),
                                              "ts": r.get('timestamp', 0), "matched": "md5"})
        return web.json_response({"ok": True, "prompt": "", "matched": "none"})

    async def _webui_gallery_delete(self, r):
        """删除画廊中的图片文件（按文件名，避免全路径中文编码问题）。
        兼容子目录：name 为纯文件名时若根目录找不到，递归在 output_dir 内查找同名文件删除。"""
        try:
            try:
                data = await r.json()
            except Exception:
                return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
            name_str = data.get('name', '')
            if not name_str:
                return web.json_response({"ok": False, "error": "名为空"})
            # v4.10.1: 优先按相对路径精确删除（画廊数据自带 rel path）——旧版按纯文件名
            # rglob 取第一个同名文件，多子目录同名时会删错。name 兼容保留：多个同名时报错防误删。
            rel = str(data.get('path', '') or '').strip().replace('\\', '/')
            name_str = str(data.get('name', '') or '').strip()
            if not rel and not name_str:
                return web.json_response({"ok": False, "error": "缺少 path/name"})
            out_dir = self.output_dir.resolve()
            target = None
            if rel and '..' not in rel:
                t = (out_dir / rel).resolve()
                try:
                    t.relative_to(out_dir)
                except ValueError:
                    return web.json_response({"ok": False, "error": "拒绝删除：文件不在输出目录中"})
                if t.is_file():
                    target = t
            if target is None and name_str and '..' not in name_str and '/' not in name_str and '\\' not in name_str:
                t = (out_dir / name_str).resolve()
                try:
                    t.relative_to(out_dir)
                except ValueError:
                    t = None
                if t and t.is_file():
                    target = t
                else:
                    matches = [p.resolve() for p in out_dir.rglob(name_str) if p.is_file()]
                    if len(matches) == 1:
                        target = matches[0]
                    elif len(matches) > 1:
                        return web.json_response({"ok": False, "error": f"存在 {len(matches)} 个同名文件，无法确定删除目标"})
            target.unlink()
            logger.info(f"[ComfyUI] 画廊删除文件: {target}")
            # 同时清理发送记录
            self._sent_images.pop(str(target), None)
            try:
                async with self._prompt_log_lock:
                    _before = len(self._prompt_log)
                    self._prompt_log = [x for x in self._prompt_log if x.get('path') != str(target)]
                    _removed = _before - len(self._prompt_log)
                if _removed:
                    with self._prompt_log_file_lock:
                        with open(str(self._prompt_log_path), 'w', encoding='utf-8') as f:
                            json.dump(self._prompt_log, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
            return web.json_response({"ok": True})
        except Exception as e:
            logger.error(f"[ComfyUI] 删除画廊文件失败: {e}")
            return web.json_response({"ok": False, "error": str(e)})

    async def _webui_anima_search(self, request):
        q = request.query.get('q', '').strip()
        try:
            top = int(request.query.get('top', '20'))
        except (ValueError, TypeError):
            top = 20
        if not q:
            return web.json_response({"results": []})
        results = self.anima_data.search(q, top_k=top)
        return web.json_response({"results": results, "total": len(results)})

    async def _webui_anima_stats(self, request):
        return web.json_response(self.anima_data.get_statistics())

    async def _webui_cache_image(self, request):
        """提供本地缓存的图片"""
        filename = request.match_info.get('filename', '')
        if not filename:
            return web.Response(status=400, text="Missing filename")
        # 安全校验：只允许字母数字下划线横线点号
        if not re.match(r'^[a-zA-Z0-9_.-]+$', filename):
            return web.Response(status=400, text="Invalid filename")
        cache_path = self._grimoire_cache_dir / filename
        if not cache_path.exists():
            return web.Response(status=404, text="Not cached")
        content_type = 'image/webp'
        ext = cache_path.suffix.lower()
        if ext in ('.png',):
            content_type = 'image/png'
        elif ext in ('.jpg', '.jpeg'):
            content_type = 'image/jpeg'
        elif ext in ('.gif',):
            content_type = 'image/gif'
        return web.Response(
            body=cache_path.read_bytes(),
            content_type=content_type,
            headers={"Cache-Control": "max-age=86400, public"}
        )

    async def _webui_set_deploy_mode(self, r):
        try:
            d = await r.json()
            mode = d.get('mode', 'windows')
            # 白名单校验，防止写入任意字符串导致前端/路径逻辑错乱
            if mode not in ('windows', 'linux'):
                return web.json_response({'ok': False, 'error': f'无效的部署模式: {mode!r}（仅支持 windows / linux）'})
            self.config['deploy_mode'] = mode
            self._save_local_config({'deploy_mode': mode})
            return web.json_response({'ok': True, 'mode': mode})
        except Exception as e:
            return web.json_response({'ok': False, 'error': str(e)})
