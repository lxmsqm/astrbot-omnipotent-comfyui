# -*- coding: utf-8 -*-
"""grimoire.py — 魔导书 Mixin（词库/标签/固定/收藏/K2 锁定等）
v4.13.5 拆分自 main.py（原 4696-6572 行），行为不变：Mixin 间仍经 self 运行时互通。
"""
import asyncio
import hashlib
import json
import os
import shutil

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger
from aiohttp import web
from pathlib import Path

from .anima_data import load_anima_tools_source, _is_anima_source, _ANIMA_SOURCE_NAMES
from .data_paths import data_dir_resolver


class GrimoireMixin:
    """魔导书:数据源/条目/分类/固定/随机池/预设/收藏的 WebUI 与 LLM 接口。"""

    async def _webui_grimoire_sources(self, request):
        """列出所有数据源（按目录分组），缺失的 anima 数据从 Anima-Tools JS 补充"""
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        sources = []
        have_anima_sources = set()
        if data_dir.exists():
            for fpath in sorted(data_dir.rglob("*.json")):
                rel = fpath.relative_to(data_dir)
                # 跳过非数据文件（v4.3.0 注：缓存/JS 源已外移，此处兜底 + 迁移备份目录）
                if rel.parts and rel.parts[0] in ('anima_tools', 'cache', 'prompt_log.json', 'user',
                                                  'anima_tools_migrated_backup', 'user_migrated_backup'):
                    continue
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                        data = json.loads(content) if content else []
                    count = len(data) if isinstance(data, list) else 0
                except Exception:
                    count = 0
                sources.append({
                    "path": str(rel),
                    "name": rel.stem,
                    "dir": str(rel.parent) if str(rel.parent) != '.' else '',
                    "count": count,
                    "size": fpath.stat().st_size,
                    "readonly": False,
                })
                rel_normal = str(rel.parent / rel.stem).replace('\\', '/')
                have_anima_sources.add(rel_normal)

        # 补充缺失的 anima 源（从 Anima-Tools JS）
        for source_path, source_name, label in _ANIMA_SOURCE_NAMES:
            normal_path = source_path.replace('\\', '/')
            if normal_path not in have_anima_sources:
                items = load_anima_tools_source(source_name)
                count = len(items)
                sources.append({
                    "path": source_path + ".json",
                    "name": source_name,
                    "dir": "anima",
                    "count": count,
                    "size": 0,
                    "readonly": True,
                })
                logger.info(f"[魔导书] 补充 Anima-Tools 源: {label} ({count} 条)")

        # 应用大分类排序 + 子分类排序（按目录分组）
        # ⚠️ 修复：旧版只应用了 __grimoire_source_order__（子分类），漏掉 __grimoire_dir_order__
        #     （大分类），导致魔导书大分类拖拽排序后刷新即恢复字母序。现在两者都生效。
        dir_order = self.workflow_config.get('__grimoire_dir_order__', [])
        source_order = self.workflow_config.get('__grimoire_source_order__', {})
        if dir_order or source_order:
            from functools import cmp_to_key
            def source_sort_key(a, b):
                dir_a = a.get('dir', '')
                dir_b = b.get('dir', '')
                if dir_a != dir_b:
                    # 大分类排序：优先按用户拖拽的 dir_order，未出现的大分类按字母序放后
                    ia = dir_order.index(dir_a) if dir_a in dir_order else len(dir_order)
                    ib = dir_order.index(dir_b) if dir_b in dir_order else len(dir_order)
                    if ia != ib:
                        return ia - ib
                    return -1 if dir_a < dir_b else 1
                order_list = source_order.get(dir_a, [])
                if not order_list:
                    return -1 if (a.get('path') or '') < (b.get('path') or '') else 1
                pa = (a.get('path') or '').replace('\\', '/')
                pb = (b.get('path') or '').replace('\\', '/')
                ia = order_list.index(pa) if pa in order_list else len(order_list)
                ib = order_list.index(pb) if pb in order_list else len(order_list)
                return ia - ib
            sources.sort(key=cmp_to_key(source_sort_key))

        # 按当前数据集模型过滤：k2 只显示 data/k2/ 下的源；anima 屏蔽 data/k2/（两者互斥）
        cur_model = self.workflow_config.get('__prompt_model__', 'anima')
        if cur_model == 'k2':
            sources = [s for s in sources if (s.get('path') or '').replace('\\', '/').startswith('k2/')]
        else:
            sources = [s for s in sources if not (s.get('path') or '').replace('\\', '/').startswith('k2/')]

        return web.json_response({"sources": sources})

    async def _webui_grimoire_data(self, request):
        """读取某个数据源的数据（支持分页、搜索和分类过滤）"""
        source = request.query.get('source', '').strip()
        try:
            page = int(request.query.get('page', '1'))
            page_size = int(request.query.get('pageSize', '50'))
        except (ValueError, TypeError):
            page, page_size = 1, 50
        q = request.query.get('q', '').strip()
        category = request.query.get('category', '').strip()
        if not source:
            return web.json_response({"ok": False, "error": "缺少 source 参数"})
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        # 兼容 Windows 路径（前端传正斜杠，需要转成系统分隔符）
        source = source.replace('/', os.sep).replace('\\', os.sep)
        fpath = data_dir / source
        # 尝试加上 .json 后缀（如果 source 不带后缀）
        if not fpath.suffix:
            fpath = fpath.with_suffix('.json')

        # 尝试从本地 JSON 读取
        items = []
        if fpath.exists():
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    items = json.loads(content) if content else []
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)})

        # 本地 JSON 不存在 → 尝试从 Anima-Tools JS 加载（只读源）
        if not items and _is_anima_source(str(fpath.relative_to(data_dir))):
            rel = fpath.relative_to(data_dir)
            source_name = rel.stem  # artists / characters / clothing
            items = load_anima_tools_source(source_name)

        if not isinstance(items, list):
            return web.json_response({"ok": False, "error": "数据格式错误"})
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
        # 记录原始文件下标，供前端编辑/删除使用（只读源标记 readonly）
        is_readonly = not fpath.exists()
        items = [dict(it, index=i, readonly=is_readonly) for i, it in enumerate(items)]
        # 搜索过滤
        if q:
            kw = q.lower().strip()
            items = [it for it in items if
                     kw in (it.get("name") or "").lower() or
                     kw in (it.get("name_cn") or "").lower() or
                     kw in (it.get("tags") or "").lower() or
                     kw in (it.get("note") or "").lower()]
        # 分类过滤（精确匹配 category 字段，支持 ";" 分隔的多分类）
        if category:
            cat_kw = category.strip().lower()
            items = [it for it in items if
                     cat_kw in [p.strip().lower() for p in (it.get("category") or "").split(";")]]
        # 收藏优先：将 preferred 中指定的条目排到最前面（保持原有相对顺序）
        preferred = request.query.get('preferred', '').strip()
        if preferred:
            pref_names = [n.strip().lower() for n in preferred.split(',') if n.strip()]
            if pref_names:
                preferred_items = []
                rest = []
                for it in items:
                    if (it.get("name") or "").lower() in pref_names:
                        preferred_items.append(it)
                    else:
                        rest.append(it)
                items = preferred_items + rest
        total = len(items)
        # 分页
        start = (page - 1) * page_size
        end = start + page_size
        page_items = items[start:end]
        # 缓存状态只在当前页检查（避免 4 万次文件系统查询）
        for it in page_items:
            img_url = it.get("image_url") or ""
            if img_url:
                cache_key = hashlib.md5(img_url.encode()).hexdigest()
                it["cache_key"] = cache_key
                it["cached"] = (self._grimoire_cache_dir / cache_key).exists()
            else:
                it["cached"] = False
        return web.json_response({
            "ok": True,
            "items": page_items,
            "total": total,
            "page": page,
            "pageSize": page_size
        })

    async def _webui_grimoire_categories(self, request):
        """获取某个数据源的所有可用分类（角色只返回热度前 50）"""
        source = request.query.get('source', '').strip()
        if not source:
            return web.json_response({"ok": False, "error": "缺少 source 参数"})
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        source = source.replace('/', os.sep).replace('\\', os.sep)
        fpath = data_dir / source
        if not fpath.suffix:
            fpath = fpath.with_suffix('.json')

        items = []
        if fpath.exists() and fpath.is_file():
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    items = json.loads(content) if content else []
            except Exception:
                items = []

        # 本地 JSON 不存在 → 尝试从 Anima-Tools JS 加载
        if not items and _is_anima_source(str(fpath.relative_to(data_dir))):
            source_name = fpath.relative_to(data_dir).stem
            items = load_anima_tools_source(source_name)

        if not isinstance(items, list):
            items = []
        # 统计每个分类的条目数，按热度排序
        cat_count: dict[str, int] = {}
        cat_cn: dict[str, str] = {}
        for it in items:
            raw = (it.get("category") or "").strip()
            if not raw:
                continue
            parts = [p.strip() for p in raw.split(";") if p.strip()]
            for c in parts:
                cat_count[c] = cat_count.get(c, 0) + 1
                if c not in cat_cn:
                    cat_cn[c] = (it.get("category_cn") or "").strip() or c
        # 角色数据现已全部添加中文翻译，不再限制只返回前 50
        sorted_by_count = sorted(cat_count.items(), key=lambda x: -x[1])
        sorted_cats = [c for c, _ in sorted_by_count]
        categories_with_cn = [{"category": c, "name_cn": cat_cn[c], "count": cat_count[c]} for c in sorted_cats]
        return web.json_response({
            "ok": True, "categories": sorted_cats,
            "categories_cn": categories_with_cn,
            "total": len(cat_count), "shown": len(sorted_cats)
        })

    async def _webui_grimoire_add(self, request):
        """新增条目到指定数据源"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体格式错误"})
        source = body.get('source', '').strip()
        item = body.get('item', {})
        if not source or not item:
            return web.json_response({"ok": False, "error": "缺少 source 或 item 参数"})
        # Anima-Tools 源只读保护
        if _is_anima_source(source):
            return web.json_response({"ok": False, "error": "Anima-Tools 数据源为只读，不可新增"})
        name = item.get('name', '').strip()
        name_cn = item.get('name_cn', '').strip()
        tags = item.get('tags', '').strip()
        note = item.get('note', '').strip()
        style = item.get('style', 'general').strip()
        category = item.get('category', '').strip()
        category_cn = item.get('category_cn', '').strip()
        if not name:
            return web.json_response({"ok": False, "error": "名称不能为空"})
        if not tags:
            # fallback：未填 tags 时用 name 代替（建议用户填写英文标签）
            tags = name
        entry = {"name": name, "tags": tags}
        if name_cn:
            entry["name_cn"] = name_cn
        if note:
            entry["note"] = note
        if style:
            entry["style"] = style
        if category:
            entry["category"] = category
        if category_cn:
            entry["category_cn"] = category_cn
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        source = source.replace('/', os.sep).replace('\\', os.sep)
        fpath = data_dir / source
        if not fpath.suffix:
            fpath = fpath.with_suffix('.json')
        async with self._config_lock:
            try:
                if fpath.exists() and fpath.stat().st_size > 0:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                        items = json.loads(content) if content else []
                else:
                    items = []
                if not isinstance(items, list):
                    items = []
                items.append(entry)
                tmp_p = fpath.with_suffix('.json.tmp')
                with open(tmp_p, 'w', encoding='utf-8') as f:
                    json.dump(items, f, ensure_ascii=False, indent=2)
                tmp_p.replace(fpath)
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)})
        # 重新加载 Anima 数据
        try:
            self.anima_data.load_all()
        except Exception as e:
            logger.warning(f"[魔导书] 重载数据失败: {e}")
        return web.json_response({"ok": True, "item": entry})

    async def _webui_grimoire_batch_import(self, request):
        """批量导入条目到指定数据源"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体格式错误"})
        source = body.get('source', '').strip()
        items_data = body.get('items', [])
        if not source or not items_data:
            return web.json_response({"ok": False, "error": "缺少 source 或 items 参数"})
        # Anima-Tools 源只读保护
        if _is_anima_source(source):
            return web.json_response({"ok": False, "error": "Anima-Tools 数据源为只读，不可批量导入"})
        if not isinstance(items_data, list):
            return web.json_response({"ok": False, "error": "items 必须是数组"})
        # 解析并验证每条数据
        parsed = []
        errors = []
        for i, raw in enumerate(items_data):
            name = (raw.get('name') or '').strip()
            name_cn = (raw.get('name_cn') or '').strip()
            tags = (raw.get('tags') or '').strip()
            if not name:
                errors.append(f"第 {i+1} 条缺少 name")
                continue
            if not tags:
                tags = name
            entry = {"name": name, "tags": tags}
            if name_cn:
                entry["name_cn"] = name_cn
            note = (raw.get('note') or '').strip()
            style = (raw.get('style') or 'general').strip()
            category = (raw.get('category') or '').strip()
            category_cn = (raw.get('category_cn') or '').strip()
            if note:
                entry["note"] = note
            if style:
                entry["style"] = style
            if category:
                entry["category"] = category
            if category_cn:
                entry["category_cn"] = category_cn
            parsed.append(entry)
        if not parsed:
            return web.json_response({"ok": False, "error": "没有有效数据", "errors": errors})
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        source = source.replace('/', os.sep).replace('\\', os.sep)
        fpath = data_dir / source
        if not fpath.suffix:
            fpath = fpath.with_suffix('.json')
        async with self._config_lock:
            try:
                if fpath.exists() and fpath.stat().st_size > 0:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                        items = json.loads(content) if content else []
                else:
                    items = []
                if not isinstance(items, list):
                    items = []
                items.extend(parsed)
                tmp_p = fpath.with_suffix('.json.tmp')
                with open(tmp_p, 'w', encoding='utf-8') as f:
                    json.dump(items, f, ensure_ascii=False, indent=2)
                tmp_p.replace(fpath)
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)})
        try:
            self.anima_data.load_all()
        except Exception as e:
            logger.warning(f"[魔导书] 重载数据失败: {e}")
        return web.json_response({
            "ok": True,
            "imported": len(parsed),
            "errors": errors,
            "total": len(items_data)
        })

    async def _webui_grimoire_batch_delete(self, request):
        """批量删除条目"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体格式错误"})
        source = body.get('source', '').strip()
        indices = body.get('indices', [])
        if not source or not indices:
            return web.json_response({"ok": False, "error": "缺少参数"})
        # Anima-Tools 源只读保护
        if _is_anima_source(source):
            return web.json_response({"ok": False, "error": "Anima-Tools 数据源为只读，不可批量删除"})
        if not isinstance(indices, list):
            return web.json_response({"ok": False, "error": "indices 必须是数组"})
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        source = source.replace('/', os.sep).replace('\\', os.sep)
        fpath = data_dir / source
        if not fpath.suffix:
            fpath = fpath.with_suffix('.json')
        if not fpath.exists():
            return web.json_response({"ok": False, "error": "文件不存在"})
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                items = json.loads(content) if content else []
            if not isinstance(items, list):
                return web.json_response({"ok": False, "error": "数据格式错误"})
            deleted = 0
            for idx in sorted(set(indices), reverse=True):
                if 0 <= idx < len(items):
                    items.pop(idx)
                    deleted += 1
            tmp_p = fpath.with_suffix('.json.tmp')
            with open(tmp_p, 'w', encoding='utf-8') as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            tmp_p.replace(fpath)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})
        try:
            self.anima_data.load_all()
        except Exception as e:
            logger.warning(f"[魔导书] 重载数据失败: {e}")
        return web.json_response({"ok": True, "deleted": deleted})

    async def _webui_grimoire_get_pins(self, request):
        """获取所有固定的标签"""
        pins = self.workflow_config.get('__grimoire_pins__', {})
        # 兼容：旧格式 {源: {name,tags}} → 统一为 {源: [{name,tags}]}
        normalized = {}
        for k, v in pins.items():
            if isinstance(v, dict):
                normalized[k] = [v]
            elif isinstance(v, list):
                normalized[k] = v
            else:
                normalized[k] = []
        return web.json_response({"ok": True, "pins": normalized})

    async def _webui_grimoire_set_pin(self, request):
        """固定/取消固定某条标签"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体格式错误"})
        source = body.get('source', '').strip()
        action = body.get('action', '')  # 'pin' or 'unpin'
        item = body.get('item', {})
        # 统一用正斜杠
        source = source.replace('\\', '/')
        if not source or not action:
            return web.json_response({"ok": False, "error": "缺少参数"})
        pins = {}
        for k, v in (self.workflow_config.get('__grimoire_pins__', {})).items():
            pins[k.replace('\\', '/')] = v  # 统一已有键的格式
        if action == 'unpin':
            if source in pins:
                name = (item or {}).get("name", "")
                if name:
                    # 只移除指定名称的固定标签
                    old_list = pins[source]
                    if isinstance(old_list, list):
                        pins[source] = [x for x in old_list if x.get("name") != name]
                        if not pins[source]:
                            del pins[source]
                    else:
                        # 兼容旧格式（单个对象）
                        del pins[source]
                else:
                    del pins[source]
            # 也尝试匹配旧的带反斜杠的键（清理老数据）
            pins.pop(source.replace('/', '\\'), None)
        elif action == 'pin':
            if not item.get('tags'):
                return web.json_response({"ok": False, "error": "缺少标签"})
            entry = {"name": item.get("name",""), "tags": item.get("tags","")}
            if source not in pins:
                pins[source] = []
            elif isinstance(pins[source], dict):
                # 兼容旧格式：转成数组
                pins[source] = [pins[source]]
            # 检查是否已固定相同名称的标签（避免重复）
            if not any(x.get("name") == entry["name"] for x in pins[source]):
                pins[source].append(entry)
        else:
            return web.json_response({"ok": False, "error": "未知操作"})
        self.workflow_config['__grimoire_pins__'] = pins
        await self._save_workflow_config()
        # 保存后验证（对旧格式单条做兼容检查）
        saved = self.workflow_config.get('__grimoire_pins__', {})
        if action == 'unpin' and isinstance(saved.get(source), dict) and source in saved:
            # 旧格式单条：取消后源不应还在
            logger.warning(f"[魔导书] 取消固定失败: {source} 仍然存在")
            return web.json_response({"ok": False, "error": "保存失败，请重试"})
        return web.json_response({"ok": True, "pins": pins})

    def _grimoire_valid_source_paths(self) -> set:
        """当前真实存在的词库源相对路径集合（与 /api/grimoire/sources 同一扫描口径，含 Anima 补充源）。
        随机池条目必须命中此集合；否则视为垃圾/失效条目（v4.9.3：曾混入 43 条 't/1'..'t/43'
        垃圾路径，徽章计数虚高且随机抽取撞空）。"""
        data_dir = data_dir_resolver()
        valid = set()
        if data_dir.exists():
            for fpath in data_dir.rglob("*.json"):
                rel = fpath.relative_to(data_dir)
                if rel.parts and rel.parts[0] in ('anima_tools', 'cache', 'prompt_log.json', 'user',
                                                  'anima_tools_migrated_backup', 'user_migrated_backup'):
                    continue
                valid.add(str(rel).replace('\\', '/'))
        for source_path, _n, _l in _ANIMA_SOURCE_NAMES:
            valid.add((source_path + '.json').replace('\\', '/'))
        return valid

    async def _webui_grimoire_get_rand_pool(self, request):
        """获取随机池数据源列表（v4.9.3: 剔除失效/垃圾条目并自愈落盘）"""
        old = list(self.workflow_config.get('__grimoire_rand_pool__', []) or [])
        valid = self._grimoire_valid_source_paths()
        pool = [s for s in old if str(s).replace('\\', '/') in valid]
        if len(pool) != len(old):
            self.workflow_config['__grimoire_rand_pool__'] = pool
            await self._save_workflow_config()
            logger.info(f"[魔导书] 随机池自愈: 剔除 {len(old) - len(pool)} 条失效源，剩 {len(pool)} 条")
        return web.json_response({"ok": True, "pool": pool})

    async def _webui_grimoire_set_rand_pool(self, request):
        """添加/移除随机池数据源"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体格式错误"})
        # v4.5.1: 支持批量 sources（全选随机一次往返），单 source 保持兼容
        action = body.get('action', '')
        sources = [str(x).strip().replace('\\', '/') for x in (body.get('sources') or []) if str(x).strip()]
        single = str(body.get('source', '')).strip()
        if single:
            sources.append(single.replace('\\', '/'))
        if not sources or not action:
            return web.json_response({"ok": False, "error": "缺少参数"})
        # v4.9.3: add 操作校验源路径必须真实存在（防垃圾路径混入池子撑爆计数）
        if action == 'add':
            valid = self._grimoire_valid_source_paths()
            bad = [s for s in sources if s not in valid]
            if bad:
                return web.json_response({"ok": False,
                                          "error": f"无效的数据源: {', '.join(bad[:5])}{'…' if len(bad) > 5 else ''}"})
        pool = list(self.workflow_config.get('__grimoire_rand_pool__', []))
        if action == 'add':
            for src in sources:
                if src not in pool:
                    pool.append(src)
        elif action == 'remove':
            pool = [s for s in pool if s not in sources]
        else:
            return web.json_response({"ok": False, "error": "未知操作"})
        self.workflow_config['__grimoire_rand_pool__'] = pool
        await self._save_workflow_config()
        return web.json_response({"ok": True, "pool": pool, "count": len(sources)})

    async def _webui_grimoire_get_stars(self, request):
        """获取收藏标签数据"""
        stars = self.workflow_config.get('__grimoire_stars__', {})
        return web.json_response({"ok": True, "stars": stars})

    async def _webui_grimoire_set_stars(self, request):
        """保存收藏标签数据"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体格式错误"})
        stars = body.get('stars', {})
        self.workflow_config['__grimoire_stars__'] = stars
        await self._save_workflow_config()
        return web.json_response({"ok": True})

    async def _webui_grimoire_get_presets(self, request):
        """获取所有预设列表"""
        presets = self.workflow_config.get('__grimoire_presets__', {})
        names = list(presets.keys())
        return web.json_response({"ok": True, "presets": names})

    async def _webui_grimoire_save_preset(self, request):
        """保存当前固定标签+随机池为预设"""
        try: body = await request.json()
        except Exception: return web.json_response({"ok": False, "error": "请求体格式错误"})
        name = body.get('name', '').strip()
        if not name:
            return web.json_response({"ok": False, "error": "预设名不能为空"})
        presets = self.workflow_config.get('__grimoire_presets__', {})
        presets[name] = {
            "pins": self.workflow_config.get('__grimoire_pins__', {}),
            "pool": self.workflow_config.get('__grimoire_rand_pool__', []),
        }
        self.workflow_config['__grimoire_presets__'] = presets
        await self._save_workflow_config()
        return web.json_response({"ok": True, "presets": list(presets.keys())})

    async def _webui_grimoire_apply_preset(self, request):
        """应用预设（替换当前固定标签和随机池）"""
        try: body = await request.json()
        except Exception: return web.json_response({"ok": False, "error": "请求体格式错误"})
        name = body.get('name', '').strip()
        presets = self.workflow_config.get('__grimoire_presets__', {})
        if name not in presets:
            return web.json_response({"ok": False, "error": f"预设「{name}」不存在"})
        preset = presets[name]
        self.workflow_config['__grimoire_pins__'] = preset.get("pins", {})
        self.workflow_config['__grimoire_rand_pool__'] = preset.get("pool", [])
        await self._save_workflow_config()
        return web.json_response({"ok": True, "pins": preset.get("pins", {}), "pool": preset.get("pool", [])})

    async def _webui_grimoire_delete_preset(self, request):
        """删除预设"""
        try: body = await request.json()
        except Exception: return web.json_response({"ok": False, "error": "请求体格式错误"})
        name = body.get('name', '').strip()
        presets = self.workflow_config.get('__grimoire_presets__', {})
        if name not in presets:
            return web.json_response({"ok": False, "error": f"预设「{name}」不存在"})
        del presets[name]
        self.workflow_config['__grimoire_presets__'] = presets
        await self._save_workflow_config()
        return web.json_response({"ok": True, "presets": list(presets.keys())})

    async def _webui_grimoire_random_pick(self, request):
        """从随机池中每个子分类随机取一条，合并固定标签返回（去重 + 智能排序 + 冲突检测 + NSFW过滤）"""
        from .random_prompt import (shuffle as sc_shuffle, matches_body_group,
                                   check_conflict, get_prompt_section, _is_nsfw_tag,
                                   _is_low_quality)

        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        pool = self.workflow_config.get('__grimoire_rand_pool__', [])
        pins = self.workflow_config.get('__grimoire_pins__', {})

        # ★ NSFW 开关（宽松档：只过滤明确色情标签；开启则不过滤）
        #   K2 模式沿用前端的 K2 NSFW 设置；anima 模式用独立的 anima_nsfw 配置
        try:
            _k2_nsfw = bool(self.workflow_config.get('__k2_nsfw__', False))
        except Exception:
            _k2_nsfw = False
        try:
            _anima_nsfw = bool(self._load_local_config().get('anima_nsfw', False))
        except Exception:
            _anima_nsfw = False
        # v4.13.1: 任一 NSFW 开关开启即放行（两个开关已由保存接口双向同步，此处兜底并集）
        _nsfw_on = _k2_nsfw or _anima_nsfw

        # 已固定的数据源不参与随机（避免同源出重复）
        pinned_sources = set(pins.keys())
        active_pool = [s for s in pool if s not in pinned_sources]

        # ★ NSFW 关闭时：整个「色情动作」类数据源不参与随机（宽松档只过滤内容，不整源剔除其余分类）
        if not _nsfw_on:
            _before = len(active_pool)
            active_pool = [s for s in active_pool
                           if not any(k in s for k in ("色情动作", "erotic", "nsfw"))]
            if len(active_pool) != _before:
                logger.info(f"[随机图] NSFW过滤: 排除 {_before - len(active_pool)} 个色情类数据源")

        # ★ 排除 anima 自带的「图片服装」源（anima/clothing，约 430 条带预览图的服装）
        #   它与文字服装分类（服装配饰/上衣、下装…）重复，同时参与会导致"穿三件衣服"
        _before2 = len(active_pool)
        active_pool = [s for s in active_pool
                       if not (s.startswith("anima/") and
                               ("clothing" in s.lower() or "服装" in s))]
        if len(active_pool) != _before2:
            logger.info(f"[随机图] 排除 {_before2 - len(active_pool)} 个 anima 图片服装源（避免与文字服装重复）")

        import random
        seen_tags = set()
        all_tags = []
        all_tag_objects = []  # 保留标签对象用于后续智能排序

        # 1. 固定标签（总是包含；NSFW 关闭时同样过滤明确色情标签）
        for src, info in pins.items():
            # 兼容：新格式 [{name,tags}] 和旧格式 {name,tags}
            entries = info if isinstance(info, list) else [info]
            for entry in entries:
                t = (entry.get("tags") or "").strip()
                if t:
                    for part in t.split(","):
                        p = part.strip().lower()
                        if not p or len(p) <= 1 or p in seen_tags:
                            continue
                        # ★ NSFW 过滤（固定标签也要过）
                        if not _nsfw_on and _is_nsfw_tag(p):
                            logger.info(f"[随机图] NSFW过滤(固定标签): 跳过 {p}")
                            continue
                        seen_tags.add(p)
                        all_tags.append(part.strip())
                        all_tag_objects.append({
                            "en": part.strip(),
                            "category": "固定",
                            "subcategory": "",
                        })

        # 2. 分组：同组子分类只取一个，避免冲突
        # 组定义：组名下是对应的子分类路径（匹配 data/ 下的实际目录）
        SOURCE_GROUPS = [
            ["艺术风格", ["艺术风格/动漫风格", "艺术风格/画风技法", "艺术风格/渲染技术"]],
            ["画质", ["画质与渲染/品质保证", "画质与渲染/官方规范"]],
            ["光影", ["画质与渲染/光影效果"]],
            ["人物特征", ["人物特征/体型", "人物特征/年龄段"]],
            ["肤质", ["人物特征/肤质"]],
            ["发型", ["发型发色/发型"]],
            ["发色", ["发型发色/发色"]],
            ["面部", ["面部特征/眼色", "面部特征/表情", "面部特征/表情细节"]],
            # ★ 服装：连衣裙独立（与上下装互斥）；上衣/下装/内衣泳装各自独立（可共存）
            #   旧配置把四者放一组「四选一」，导致只能出一件衣服，无法形成完整穿搭
            ["连衣裙", ["服装配饰/连衣裙"]],
            ["材质质感", ["材质质感/布料", "材质质感/皮革", "材质质感/金属",
                          "材质质感/表面", "材质质感/光泽"]],
            # ★ 动作姿态：正常动作 / 色情动作 二选一（具体取哪个由 NSFW 开关决定，见下）
            ["动作姿态", ["动作姿态/正常动作", "动作姿态/色情动作"]],
            ["场景", ["场景构图/自然环境", "场景构图/建筑场景"]],
            ["天气", ["场景构图/天气时间", "特殊效果/天气效果"]],
            ["色彩", ["场景构图/色彩氛围"]],
            ["构图镜头", ["场景构图/构图景别", "场景构图/镜头效果", "场景构图/视角"]],
            ["情绪", ["情绪氛围/氛围", "情绪氛围/表情细节"]],
            ["特效", ["特殊效果/视觉效果"]],
            ["道具", ["道具物品/手持物", "道具物品/武器"]],
        ]
        # 将 active_pool 按组分，每个组随机取一个匹配的子分类
        pool_set = set(active_pool)
        grouped_picks = []  # 最终参与抽选的源路径
        matched = set()
        for group_name, members in SOURCE_GROUPS:
            available = [m for m in members if m in pool_set]
            # v4.12.4: 动作姿态组是核心二选一流程，不因不在池内而跳过（下方分支接管）
            if not available and group_name != "动作姿态":
                continue
            # ★ 动作姿态组：按 NSFW 开关决定只取哪个源
            #   NSFW 关闭 → 只抽「正常动作」（色情动作已在前面被排除）
            #   NSFW 开启 → 只抽「色情动作」（用户要求：开启时只出色情动作）
            if group_name == "动作姿态":
                # v4.13: 已固定动作（固定源在 动作姿态/ 下）→ 不再随机抽动作，以固定为准，
                # 防止固定姿势和随机动作同时出现导致姿势混乱（二元组按目录前缀匹配是安全的）
                if any(str(ps).replace('.json', '').rsplit('/', 1)[0] == '动作姿态'
                       for ps in pinned_sources):
                    logger.info("[随机图] 动作姿态已被固定标签覆盖，跳过随机抽取")
                    continue
                # v4.12.4: 动作姿态二选一是核心流程，完全由 NSFW 开关决定取哪个源，
                # 不受随机池成员限制——池里只勾了正常动作时，NSFW 开启也永远抽不到色情动作
                available = list(members)
                if _nsfw_on:
                    available = [m for m in available if "色情" in m] or available
                else:
                    available = [m for m in available if "色情" not in m] or available
            import random
            chosen = random.choice(available)
            grouped_picks.append(chosen)
            matched.add(chosen)
        # 未分组的源（如 anima 角色、画师等特殊源）限制只抽一个
        anima_tools_sources = []
        other_sources = []
        for src in active_pool:
            if src not in matched:
                if _is_anima_source(src):
                    anima_tools_sources.append(src)
                else:
                    other_sources.append(src)
        # anima_tools 源只随机取一个
        if anima_tools_sources:
            import random
            chosen = random.choice(anima_tools_sources)
            grouped_picks.append(chosen)
            matched.add(chosen)
        # 其余未分组源逐个加入
        for src in other_sources:
            if src not in matched:
                grouped_picks.append(src)

        # 3. 从分组后的源列表中各取一条
        for src in grouped_picks:
            fpath = data_dir / src.replace('/', os.sep).replace('\\', os.sep)
            if not fpath.suffix: fpath = fpath.with_suffix('.json')
            items = []
            if fpath.exists():
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        items = json.loads(f.read().strip() or '[]')
                except Exception:
                    items = []
            # JSON 不存在 → 尝试 Anima-Tools JS 回退
            if not items and _is_anima_source(src):
                source_name = Path(src).stem
                items = load_anima_tools_source(source_name)
            if not isinstance(items, list) or not items:
                continue

            # 随机图抽取模式过滤：根据收藏标签限定范围
            pick_mode = self.workflow_config.get('random_pick_mode', 'all')
            if pick_mode != 'all':
                stars_data = self.workflow_config.get('__grimoire_stars__', {})
                starred_names = set(stars_data.get(src, []))
                if starred_names:
                    if pick_mode == 'starred_only':
                        items = [it for it in items if it.get('name') in starred_names]
                    elif pick_mode == 'exclude_starred':
                        items = [it for it in items if it.get('name') not in starred_names]
                    if not items:
                        continue

            # 用 Fisher-Yates 洗牌替代 random.choice，让每次选取更均匀
            shuffled = sc_shuffle(items)
            picked = None
            for candidate in shuffled:
                t = (candidate.get("tags") or "").strip()
                if not t:
                    continue
                # 拆分成单个标签检查冲突
                parts = [x.strip() for x in t.split(",") if x.strip()]
                # ★ NSFW 过滤（anima_nsfw 关闭时跳过明确色情标签，从同分类继续抽下一个）
                if not _nsfw_on and any(_is_nsfw_tag(p) for p in parts):
                    continue
                # ★ 低质量标签过滤（总是跳过：normal/low/worst quality、score_1~6 等）
                if any(_is_low_quality(p) for p in parts):
                    continue
                # 检查是否有任一标签与已选冲突
                conflict = False
                for part in parts:
                    if part.lower() in seen_tags:
                        conflict = True
                        break
                    # 身体标签组冲突检测（比如选了"small breasts"就不能再选"large breasts"）
                    group = matches_body_group(part)
                    if group:
                        for sel_tag in seen_tags:
                            for gt in group:
                                if gt.lower() == sel_tag:
                                    conflict = True
                                    break
                            if conflict:
                                break
                    if conflict:
                        break
                if not conflict:
                    picked = candidate
                    break

            if picked is None:
                continue

            try:
                t = (picked.get("tags") or "").strip()
                if t:
                    for part in t.split(","):
                        p = part.strip().lower()
                        if p and len(p) > 1 and p not in seen_tags:
                            seen_tags.add(p)
                            all_tags.append(part.strip())
                            # 记录标签对象供后续排序用
                            style = picked.get("style", "")
                            subcat = picked.get("category", "")
                            all_tag_objects.append({
                                "en": part.strip(),
                                "category": style if style and style != "general" else "",
                                "subcategory": subcat,
                                "source": src,  # 记录来源路径
                            })
            except Exception:
                continue

        # 3. "no human" 过滤
        if any("no human" in t.lower() for t in all_tags):
            human_categories = {"发型与发色", "五官与表情", "服装与配饰", "动作姿态", "NSFW", "人物主体"}
            filtered_tags = []
            for i, t in enumerate(all_tags):
                cat = all_tag_objects[i]["category"] if i < len(all_tag_objects) else ""
                if cat in human_categories:
                    continue
                if cat == "人物主体" and all_tag_objects[i].get("subcategory") != "人数与性别":
                    continue
                filtered_tags.append(t)
            all_tags = filtered_tags

        # 3.5 服装-动作冲突检查（防"穿着长袜+脱袜子"这类矛盾）
        if len(all_tags) >= 2:
            conflict_indices = check_conflict(all_tags)
            if conflict_indices:
                removed = [all_tags[i] for i in conflict_indices]
                all_tags = [t for i, t in enumerate(all_tags) if i not in conflict_indices]
                logger.info(f"[随机图] 冲突检测移除: {removed}")

        # ★ 服装数量上限：最多保留 2 件（防"上衣+下装+内衣+配饰"叠一堆）
        #   优先级：连衣裙 > 上衣/下装 > 内衣泳装 > 鞋袜 > 配饰
        try:
            _CLOTH_PRI = [
                (0, ("服装配饰/连衣裙",)),
                (1, ("服装配饰/上衣", "服装配饰/下装")),
                (2, ("服装配饰/内衣泳装",)),
                (3, ("服装配饰/鞋袜",)),
                (4, ("服装配饰/配饰",)),
            ]
            _cloth_idx = []   # [(优先级, 序号)]
            for _i, _o in enumerate(all_tag_objects):
                _src = str(_o.get("source") or "")
                for _pri, _prefixes in _CLOTH_PRI:
                    if any(_src.startswith(_p) for _p in _prefixes):
                        _cloth_idx.append((_pri, _i))
                        break
            if len(_cloth_idx) > 2:
                # 按优先级排序，保留前 2 件
                _cloth_idx.sort()
                _drop = {_i for _pri, _i in _cloth_idx[2:]}
                _dropped_names = [all_tags[_i] for _i in sorted(_drop)]
                all_tags = [t for _i, t in enumerate(all_tags) if _i not in _drop]
                all_tag_objects = [o for _i, o in enumerate(all_tag_objects) if _i not in _drop]
                logger.info(f"[随机图] 服装上限(2件)：移除 {_dropped_names}")
        except Exception as _e:
            logger.debug(f"[ComfyUI] 服装上限处理失败: {_e}")

        # 3.6 动作→服装联动：色情动作时过滤所有服装源标签，只保留裸体/露出标签
        # ★ 连衣裙独占：抽到连衣裙时，移除上衣/下装类标签（避免"连衣裙+裙子"冲突）
        #   连衣裙是连体服装，与上衣/下装天然互斥
        try:
            _has_dress = any(str(o.get("source") or "").startswith("服装配饰/连衣裙")
                             for o in all_tag_objects)
            if _has_dress:
                _keep_t, _keep_o = [], []
                _removed = 0
                for _i, _t in enumerate(all_tags):
                    _o = all_tag_objects[_i] if _i < len(all_tag_objects) else {}
                    _src = str(_o.get("source") or "")
                    if _src.startswith("服装配饰/上衣") or _src.startswith("服装配饰/下装"):
                        _removed += 1
                        continue
                    _keep_t.append(_t)
                    _keep_o.append(_o)
                if _removed:
                    all_tags, all_tag_objects = _keep_t, _keep_o
                    logger.info(f"[随机图] 连衣裙独占：移除 {_removed} 个上衣/下装标签")
        except Exception as _e:
            logger.debug(f"[ComfyUI] 连衣裙独占处理失败: {_e}")

        # 色情动作关键词（匹配 any()，命中任一即触发）
        EROTIC_KEYWORDS = [
            "masturbation", "fingering", "penetration", "sex", "clit", "pussy",
            "dick", "cock", "blowjob", "cum", "orgasm", "nude", "naked",
            "fucking", "anal", "vagina", "nipple", "genitals", "erection",
            "creampie", "ejaculation", "semen", "sperm", "thrusting", "moan",
            "nude", "naked", "completely nude", "topless", "bottomless",
            "no panties", "no bra", "without panties", "without bra",
        ]
        has_erotic = any(any(kw in t.lower() for kw in EROTIC_KEYWORDS) for t in all_tags)
        # 也检测色情动作源（动作姿态/色情动作.json）
        if not has_erotic:
            for obj in all_tag_objects:
                src = obj.get("source", "")
                if "色情动作" in src or "erotic" in src.lower() or "nsfw" in src.lower():
                    has_erotic = True
                    break
        # ★ NSFW 关闭时不做"色情增强"：不丢服装标签、不补裸体标签
        #   （旧逻辑无论开关都会在检测到色情动作时强加 nude/no panties/no bra）
        if has_erotic and not _nsfw_on:
            logger.info("[随机图] NSFW过滤开启，跳过色情动作增强（不补裸体标签）")
            has_erotic = False
        if has_erotic:
            # 服装相关源路径前缀（这些源的标签在色情动作时全部丢弃）
            CLOTHING_SOURCE_PREFIXES = [
                "服装配饰/", "材质质感/", "配饰鞋袜/",
                "anima_tools/clothing",
            ]
            filtered_tags = []
            filtered_objects = []
            for i, t in enumerate(all_tags):
                obj = all_tag_objects[i] if i < len(all_tag_objects) else {}
                src = obj.get("source", "")
                # 检查是否来自服装相关源
                is_clothing = any(src.startswith(prefix) for prefix in CLOTHING_SOURCE_PREFIXES)
                if is_clothing:
                    logger.info(f"[随机图] 色情动作+服装源冲突，移除: {t} (来源: {src})")
                    continue
                filtered_tags.append(t)
                filtered_objects.append(obj)
            all_tags = filtered_tags
            all_tag_objects = filtered_objects
            # 确保裸体标签存在
            NUDE_TAGS = ["nude", "no panties", "no bra"]
            for nt in NUDE_TAGS:
                if not any(nt in t.lower() for t in all_tags):
                    all_tags.append(nt)
                    all_tag_objects.append({
                        "en": nt, "category": "服装", "subcategory": "裸体",
                    })
            logger.info(f"[随机图] 色情动作检测触发，已过滤服装源标签，已补充裸体标签")

        # 3.7 标签总数上限控制：最多 15 个，超出时丢弃非核心标签
        MAX_TAGS = 15
        if len(all_tags) > MAX_TAGS:
            # 核心标签列表（优先保留）
            CORE_KEYWORDS = ["best quality", "high quality", "highres", "masterpiece",
                             "official art", "detailed", "1girl", "1boy", "solo",
                             "nude", "no panties", "no bra", "masturbation", "sex",
                             "cum", "penetration", "creampie", "ejaculation"]
            core_indices = []
            extra_indices = []
            for i, t in enumerate(all_tags):
                t_lower = t.lower()
                if any(kw in t_lower for kw in CORE_KEYWORDS):
                    core_indices.append(i)
                else:
                    extra_indices.append(i)
            # 核心标签全部保留，额外标签随机丢弃到上限
            import random as _rnd
            keep_count = len(core_indices)
            max_extra = MAX_TAGS - keep_count
            if max_extra > 0 and extra_indices:
                _rnd.shuffle(extra_indices)
                keep_extra = extra_indices[:max_extra]
                keep_all = set(core_indices + keep_extra)
            else:
                keep_all = set(core_indices)
            prev_count = len(all_tags)
            all_tags = [t for i, t in enumerate(all_tags) if i in keep_all]
            all_tag_objects = [obj for i, obj in enumerate(all_tag_objects) if i in keep_all]
            dropped = prev_count - len(all_tags)
            if dropped > 0:
                logger.info(f"[随机图] 标签数超上限({prev_count}>{MAX_TAGS})，丢弃 {dropped} 个")

        # 4. 按模型提示词顺序排序
        prompt_model = self.workflow_config.get('__prompt_model__', 'anima')
        if all_tags and all_tag_objects:
            def section_sort_key(item):
                tag_text, idx = item
                obj = all_tag_objects[idx] if idx < len(all_tag_objects) else {}
                src = obj.get("source", "")
                subcat = obj.get("subcategory", obj.get("category", ""))
                sec_idx, sec_name = get_prompt_section(src, subcat, model=prompt_model)
                return (sec_idx, idx)

            tagged = [(t, i) for i, t in enumerate(all_tags)]
            tagged.sort(key=section_sort_key)
            all_tags = [t for t, _ in tagged]

        pick_names = []
        for src in active_pool:
            if src in pinned_sources: continue
            pick_names.append(src.replace('.json','').split('/')[-1])

        logger.info(f"[随机图] 固定标签: {len(pins)}个, 随机池: {len(active_pool)}个源, 合并后 {len(all_tags)} 个标签 (SmartComfy增强)")

        # ============================================================
        # 5. Anima 规范增强（方案B）—— 让随机结果符合 Anima 提示词规范
        #    规范要点（见 anima-prompt-guide.md）：
        #      ① 开头固定 质量/年代/安全：masterpiece, best quality, score_7, safe
        #      ② 必须有主体数：1girl, solo（优先用池子里抽到的，缺失才补默认）
        #      ③ tag + 自然语言混合：至少补 1~2 句自然语言描述
        #    仅 anima 模式生效（K2 模式是中文成句引擎，不套用）
        #    可用配置 anima_spec_enhance 关闭（默认开）
        # ============================================================
        tags_str = ", ".join(all_tags)
        try:
            _lc = self._load_local_config()
            _enhance_on = _lc.get('anima_spec_enhance', True)
            if prompt_model == 'anima' and _enhance_on and all_tags:
                import re as _re
                _lower_all = [t.lower() for t in all_tags]

                # ① 固定前置：质量 + score_7(Base版) + safe
                _prefix = []
                if not any('masterpiece' in t for t in _lower_all):
                    _prefix.append('masterpiece')
                if not any('best quality' in t for t in _lower_all):
                    _prefix.append('best quality')
                if not any(_re.match(r'^score_\d+$', t) for t in _lower_all):
                    _prefix.append('score_7')          # Base 版；Aesthetic 版请关闭本增强
                if not any(t == 'safe' or t.startswith('safe') or t == 'nsfw' for t in _lower_all):
                    # v4.12.4: NSFW 开启时分级用 nsfw（此前硬编码 safe，与色情内容自相矛盾）
                    _prefix.append('safe' if not _nsfw_on else 'nsfw')

                # ② 主体数：从抽到的标签里找，缺失才补
                _subject_words = ('1girl', '2girls', '3girls', '1boy', '2boys',
                                  'multiple girls', 'multiple boys', 'solo', 'no humans', '1other')
                _has_subject = any(any(sw in t for sw in _subject_words) for t in _lower_all)
                if not _has_subject:
                    _prefix.extend(['1girl', 'solo'])

                # ③ 自然语言后缀：基于标签【内容】推断语义（而不是依赖 source 字段——
                #    抽取时不一定记录 source，导致分类全落空、拼接出病句）
                _hair, _eyes, _action, _scene, _light, _shot, _prop, _mood = [], [], [], [], [], [], [], []
                _HAIR_C = ('hair',)
                _EYE_C = ('eyes', 'heterochromia')
                _ACTION_C = ('standing', 'sitting', 'lying', 'walking', 'running', 'jumping',
                             'kneeling', 'dancing', 'leaning', 'crouching', 'squatting',
                             'crossed legs', 'arms crossed', 'hand on', 'hands on',
                             'looking back', 'looking down', 'looking up', 'reaching',
                             'holding ', 'waving', 'pointing', 'salute', 'stretching')
                _SCENE_C = ('forest', 'field', 'beach', 'ocean', 'river', 'lake', 'mountain',
                            'city', 'street', 'room', 'bedroom', 'classroom', 'cafe',
                            'garden', 'castle', 'sky', 'night', 'snow', 'rain', 'sunset',
                            'sunrise', 'moon', 'cherry blossoms', 'flower', 'rooftop',
                            'library', 'temple', 'church', 'ruins', 'desert', 'underwater')
                _LIGHT_C = ('lighting', 'light', 'backlight', 'bokeh', 'glow', 'shadow',
                            'sunbeam', 'ray', 'lens flare', 'bloom', 'contrast')
                _SHOT_C = ('view', 'shot', 'close-up', 'closeup', 'portrait', 'full body',
                           'upper body', 'from above', 'from below', 'from behind',
                           'dutch angle', 'angle', 'perspective', 'depth of field')
                _MOOD_C = ('smile', 'smirk', 'tears', 'crying', 'angry', 'sad', 'happy',
                           'expressionless', 'blush', 'surprised', 'scared', 'bored',
                           'melancholic', 'peaceful', 'relaxed', 'confident', 'shy',
                           'serious', 'sleepy', 'embarrassed', 'determined')
                for _t in all_tags:
                    _lt = _t.lower()
                    if any(k in _lt for k in _HAIR_C) and 'hair' in _lt:
                        _hair.append(_t)
                    elif any(k in _lt for k in _EYE_C):
                        _eyes.append(_t)
                    elif any(_lt.startswith(k) or k in _lt for k in _ACTION_C):
                        _action.append(_t)
                    elif any(k in _lt for k in _SCENE_C):
                        _scene.append(_t)
                    elif any(k in _lt for k in _LIGHT_C):
                        _light.append(_t)
                    elif any(k in _lt for k in _SHOT_C):
                        _shot.append(_t)
                    elif any(k in _lt for k in _MOOD_C):
                        _mood.append(_t)
                    elif _lt.startswith('holding '):
                        _prop.append(_t)

                # ── 主语短语：A girl with silver hair and blue eyes ──
                _subj = "A girl"
                _mods = []
                if _hair:
                    _mods.append("with " + _hair[0])
                if _eyes:
                    _mods.append(_eyes[0] if _eyes[0].lower().endswith('eyes') else _eyes[0] + " eyes")
                if _mood:
                    _mods.append(_mood[0])
                if _mods:
                    _subj = "A girl " + ", ".join(_mods[:2]) + (" " + _mods[2] if len(_mods) > 2 else "")
                if _mods:
                    _subj = "A girl " + ", ".join(_mods)

                # 第1句：主语 + 动作 + 场景
                _s1 = _subj
                if _action:
                    a = _action[0]
                    # 动词化常见动作（避免 "standing" 位置不当）
                    _verb_map = {
                        "standing": "stands", "sitting": "sits", "lying down": "lies down",
                        "walking": "walks", "running": "runs", "jumping": "jumps",
                        "kneeling": "kneels", "dancing": "dances", "leaning forward": "leans forward",
                    }
                    _s1 += " " + _verb_map.get(a.lower(), a)
                if _prop:
                    _s1 += ", holding " + _prop[0].replace("holding ", "")
                if _scene:
                    _s1 += " in a " + _scene[0]
                _sent1 = _s1 + "."

                # 第2句：镜头 + 光照（各自独立成句，避免 "lit by long dress" 这类怪句）
                _s2 = []
                if _shot:
                    _s2.append("The shot is framed as " + _shot[0] + ".")
                if _light and any(k in _light[0].lower() for k in
                                  ('light', 'lighting', 'backlight', 'glow', 'sunbeam',
                                   'ray', 'flare', 'bloom', 'shadow', 'bokeh')):
                    _s2.append("The scene is lit by " + _light[0] + ".")
                if not _s2:
                    _s2.append("Soft, balanced lighting keeps the focus on the character.")
                _sent2 = " ".join(_s2[:2])

                _prefix_str = (", ".join(_prefix) + ", ") if _prefix else ""
                tags_str = (_prefix_str + ", ".join(all_tags) + ". " + _sent1 + " " + _sent2).strip()
                logger.info(f"[随机图] Anima规范增强: 前置[{', '.join(_prefix) or '无'}] "
                            f"自然语言2句 主体数{'已有' if _has_subject else '已补'}")
        except Exception as _e:
            logger.warning(f"[ComfyUI] Anima规范增强失败(用原始拼接): {_e}")

        return web.json_response({"ok": True, "tags": tags_str})

    async def _webui_grimoire_get_models(self, request):
        """返回可用的提示词模型列表"""
        from .random_prompt import PROMPT_SECTION_ORDER
        models = []
        for model_id, cfg in PROMPT_SECTION_ORDER.items():
            models.append({
                "id": model_id,
                "name": model_id,
                "sections": cfg.get("section_order", []),
            })
        return web.json_response({"ok": True, "models": models})

    async def _webui_grimoire_get_model(self, request):
        """返回当前选中的模型"""
        model = self.workflow_config.get('__prompt_model__', 'anima')
        return web.json_response({"ok": True, "model": model})

    async def _webui_grimoire_set_model(self, request):
        """设置当前使用的模型"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体格式错误"})
        model = body.get("model", "anima").strip()
        from .random_prompt import PROMPT_SECTION_ORDER
        if model not in PROMPT_SECTION_ORDER:
            return web.json_response({"ok": False, "error": f"不支持的模型: {model}"})
        self.workflow_config['__prompt_model__'] = model
        await self._save_workflow_config()
        # 双向同步：魔导书切模型也写回设置面板的 k2_compose_mode，保证两处一致
        local_cfg = self._load_local_config()
        if local_cfg.get("k2_compose_mode") != model:
            local_cfg["k2_compose_mode"] = model
            self._save_local_config(local_cfg)
        return web.json_response({"ok": True, "model": model})

    async def _webui_grimoire_update(self, request):
        """编辑指定数据源的某条数据（按索引）"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体格式错误"})
        source = body.get('source', '').strip()
        index = body.get('index', -1)
        item = body.get('item', {})
        if not source or index < 0 or not item:
            return web.json_response({"ok": False, "error": "缺少参数"})
        # Anima-Tools 源只读保护
        if _is_anima_source(source):
            return web.json_response({"ok": False, "error": "Anima-Tools 数据源为只读，不可编辑"})
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        source = source.replace('/', os.sep).replace('\\', os.sep)
        fpath = data_dir / source
        if not fpath.suffix:
            fpath = fpath.with_suffix('.json')
        async with self._config_lock:
            try:
                if fpath.exists() and fpath.stat().st_size > 0:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                        items = json.loads(content) if content else []
                else:
                    items = []
                if not isinstance(items, list) or index >= len(items):
                    return web.json_response({"ok": False, "error": "索引越界"})
                entry = {}
                for key in ['name', 'name_cn', 'tags', 'note', 'style', 'category', 'category_cn']:
                    if key in item:
                        entry[key] = item[key]
                items[index].update(entry)
                tmp_p = fpath.with_suffix('.json.tmp')
                with open(tmp_p, 'w', encoding='utf-8') as f:
                    json.dump(items, f, ensure_ascii=False, indent=2)
                tmp_p.replace(fpath)
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)})
        try:
            self.anima_data.load_all()
        except Exception as e:
            logger.warning(f"[魔导书] 重载数据失败: {e}")
        return web.json_response({"ok": True})

    async def _webui_grimoire_delete(self, request):
        """删除指定数据源的某条数据（按索引）"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体格式错误"})
        source = body.get('source', '').strip()
        index = body.get('index', -1)
        if not source or index < 0:
            return web.json_response({"ok": False, "error": "缺少参数"})
        # Anima-Tools 源只读保护
        if _is_anima_source(source):
            return web.json_response({"ok": False, "error": "Anima-Tools 数据源为只读，不可删除"})
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        source = source.replace('/', os.sep).replace('\\', os.sep)
        fpath = data_dir / source
        if not fpath.suffix:
            fpath = fpath.with_suffix('.json')
        async with self._config_lock:
            try:
                if fpath.exists() and fpath.stat().st_size > 0:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                        items = json.loads(content) if content else []
                else:
                    items = []
                if not isinstance(items, list) or index >= len(items):
                    return web.json_response({"ok": False, "error": "索引越界"})
                deleted = items.pop(index)
                tmp_p = fpath.with_suffix('.json.tmp')
                with open(tmp_p, 'w', encoding='utf-8') as f:
                    json.dump(items, f, ensure_ascii=False, indent=2)
                tmp_p.replace(fpath)
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)})
        try:
            self.anima_data.load_all()
        except Exception as e:
            logger.warning(f"[魔导书] 重载数据失败: {e}")
        return web.json_response({"ok": True, "deleted": deleted})

    async def _webui_grimoire_new_source(self, request):
        """新建子分类（数据源文件），可指定所属总分类"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体格式错误"})
        filename = body.get('filename', '').strip()
        category = body.get('category', 'custom').strip()
        if not filename:
            return web.json_response({"ok": False, "error": "文件名不能为空"})
        if not filename.endswith('.json'):
            filename += '.json'
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        cat_dir = data_dir / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        fpath = cat_dir / filename
        if fpath.exists():
            return web.json_response({"ok": False, "error": "该子分类已存在"})
        try:
            with open(fpath, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False, indent=2)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})
        try:
            self.anima_data.load_all()
        except Exception as e:
            logger.warning(f"[魔导书] 重载数据失败: {e}")
        rel = fpath.relative_to(data_dir)
        return web.json_response({"ok": True, "path": str(rel)})

    async def _webui_grimoire_rename_source(self, request):
        """重命名数据源文件（子分类）"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体格式错误"})
        source = body.get('source', '').strip()
        new_name = body.get('newName', '').strip()
        if not source or not new_name:
            return web.json_response({"ok": False, "error": "缺少参数"})
        if not new_name.endswith('.json'):
            new_name += '.json'
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        src_path = data_dir / source.replace('/', os.sep).replace('\\', os.sep)
        if not src_path.suffix:
            src_path = src_path.with_suffix('.json')
        if not src_path.exists():
            return web.json_response({"ok": False, "error": "数据源不存在"})
        dst_path = src_path.parent / new_name
        if dst_path.exists():
            return web.json_response({"ok": False, "error": "目标文件名已存在"})
        try:
            src_path.rename(dst_path)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})
        try:
            self.anima_data.load_all()
        except Exception as e:
            logger.warning(f"[魔导书] 重载数据失败: {e}")
        # 返回新路径
        rel = dst_path.relative_to(data_dir)
        return web.json_response({"ok": True, "path": str(rel)})

    async def _webui_grimoire_new_category(self, request):
        """新建总分类（目录）"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体格式错误"})
        name = body.get('name', '').strip()
        if not name:
            return web.json_response({"ok": False, "error": "名称不能为空"})
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        cat_dir = data_dir / name
        if cat_dir.exists():
            return web.json_response({"ok": False, "error": "该分类已存在"})
        try:
            cat_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})
        return web.json_response({"ok": True, "path": name})

    async def _webui_grimoire_rename_category(self, request):
        """重命名总分类（目录）"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体格式错误"})
        category = body.get('category', '').strip()
        new_name = body.get('newName', '').strip()
        if not category or not new_name:
            return web.json_response({"ok": False, "error": "缺少参数"})
        # 保护 anima 目录
        if category == 'anima':
            return web.json_response({"ok": False, "error": "不允许修改 Anima 分类名称"})
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        src_dir = data_dir / category
        if not src_dir.exists() or not src_dir.is_dir():
            return web.json_response({"ok": False, "error": "分类不存在"})
        dst_dir = data_dir / new_name
        if dst_dir.exists():
            return web.json_response({"ok": False, "error": "目标分类名已存在"})
        try:
            src_dir.rename(dst_dir)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})
        try:
            self.anima_data.load_all()
        except Exception as e:
            logger.warning(f"[魔导书] 重载数据失败: {e}")
        return web.json_response({"ok": True, "path": new_name})

    async def _webui_grimoire_delete_category(self, request):
        """删除总分类（目录及其下所有文件）"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体格式错误"})
        category = body.get('category', '').strip()
        if not category:
            return web.json_response({"ok": False, "error": "缺少参数"})
        # 保护 anima 目录
        if category == 'anima':
            return web.json_response({"ok": False, "error": "不允许删除 Anima 分类"})
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        cat_dir = data_dir / category
        if not cat_dir.exists() or not cat_dir.is_dir():
            return web.json_response({"ok": False, "error": "分类不存在"})
        try:
            shutil.rmtree(str(cat_dir))
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})
        try:
            self.anima_data.load_all()
        except Exception as e:
            logger.warning(f"[魔导书] 重载数据失败: {e}")
        return web.json_response({"ok": True})

    async def _webui_grimoire_delete_source(self, request):
        """删除数据源文件"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体格式错误"})
        source = body.get('source', '').strip()
        if not source:
            return web.json_response({"ok": False, "error": "缺少 source 参数"})
        # 保护：不允许删除 anima/ 下的原始数据（保护 24000+ 角色数据）
        if source.startswith('anima'):
            return web.json_response({"ok": False, "error": "不允许删除 Anima 原始数据（角色/画师/服装）"})
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        source = source.replace('/', os.sep).replace('\\', os.sep)
        fpath = data_dir / source
        if not fpath.suffix:
            fpath = fpath.with_suffix('.json')
        if not fpath.exists():
            return web.json_response({"ok": False, "error": "文件不存在"})
        try:
            fpath.unlink()
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})
        try:
            self.anima_data.load_all()
        except Exception as e:
            logger.warning(f"[魔导书] 重载数据失败: {e}")
        return web.json_response({"ok": True})

    async def _webui_grimoire_save_source_order(self, request):
        """保存魔导书子分类拖动排序"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        order = body.get('order', {})
        if not isinstance(order, dict):
            return web.json_response({"ok": False, "error": "order 必须为对象"})
        async with self._config_lock:
            self.workflow_config['__grimoire_source_order__'] = order
        await self._save_workflow_config()
        return web.json_response({"ok": True})

    async def _webui_grimoire_save_dir_order(self, request):
        """保存魔导书目录拖动排序"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "无效的 JSON 请求"})
        dir_order = body.get('order', [])
        if not isinstance(dir_order, list):
            return web.json_response({"ok": False, "error": "order 必须为数组"})
        async with self._config_lock:
            self.workflow_config['__grimoire_dir_order__'] = dir_order
        await self._save_workflow_config()
        return web.json_response({"ok": True})

    async def _webui_grimoire_status(self, request):
        """获取魔导书开关状态"""
        return web.json_response({"enabled": self.grimoire_enabled})

    async def _webui_grimoire_toggle(self, request):
        """切换魔导书开关"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体格式错误"})
        enabled = body.get('enabled', not self.grimoire_enabled)
        self.grimoire_enabled = bool(enabled)
        # 持久化到 data/user/config.json
        config_path = self._user_data_dir / "config.json"
        async with self._config_lock:
            try:
                existing = {}
                if config_path.exists():
                    with open(config_path, 'r', encoding='utf-8-sig') as f:
                        existing = json.load(f)
                existing['__grimoire_enabled__'] = self.grimoire_enabled
                self.workflow_config['__grimoire_enabled__'] = self.grimoire_enabled
                tmp_p = config_path.with_suffix('.json.tmp')
                with open(tmp_p, 'w', encoding='utf-8') as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
                tmp_p.replace(config_path)
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)})
        return web.json_response({"ok": True, "enabled": self.grimoire_enabled})

    def _grimoire_sources(self) -> list[dict]:
        """动态扫描 data/ 目录，返回所有可用的魔导书数据源列表"""
        from .random_prompt import get_prompt_section, PROMPT_SECTION_ORDER

        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        sources = []
        dirs_seen = {}
        for fpath in sorted(data_dir.rglob("*.json")):
            rel = fpath.relative_to(data_dir)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    items = json.loads(content) if content else []
                count = len(items) if isinstance(items, list) else 0
            except Exception:
                count = 0
            d = str(fpath.parent.relative_to(data_dir)).replace('\\', '/') if fpath.parent != data_dir else ""
            sources.append({
                "path": str(rel).replace('\\', '/'),
                "name": fpath.stem,
                "dir": d,
                "count": count,
            })
            if d:
                dirs_seen[d] = True
        # 按自定义目录顺序排序（如果有）否则按 Anima section 排序
        dir_order = self.workflow_config.get('__grimoire_dir_order__', [])
        if dir_order:
            # 只保留存在的目录
            valid_order = [d for d in dir_order if d in dirs_seen]
            # 没在自定义顺序中的目录排最后
            remaining = sorted(set(dirs_seen.keys()) - set(valid_order))
            dir_rank = {d: i for i, d in enumerate(valid_order + remaining)}
            def sort_key(s):
                r = dir_rank.get(s["dir"], 999)
                return (0, r, s["name"]) if s["dir"] else (1, 0, s["name"])
        else:
            def sort_key(s):
                sec_idx, _ = get_prompt_section(s["path"], s["name"], model="anima")
                return (0, sec_idx, s["dir"], s["name"]) if s["dir"] else (1, 0, s["name"])
        sources.sort(key=sort_key)
        return sources

    def _grimoire_find_source_path(self, name_or_path: str) -> str | None:
        """通过关键词匹配数据源路径（支持：中文名、文件名、路径片段）"""
        name_or_path = name_or_path.lower().strip()
        sources = self._grimoire_sources()
        # 1. 精确匹配 path
        for s in sources:
            if name_or_path == s["path"].lower():
                return s["path"]
        # 2. 精确匹配 name（含中文名）
        for s in sources:
            if name_or_path == s["path"].split("/")[-1].replace(".json","").lower() or name_or_path == s["name"].lower():
                return s["path"]
        # 3. 关键字匹配
        for s in sources:
            if name_or_path in s["path"].lower() or name_or_path in s["name"].lower():
                return s["path"]
        return None

    def _grimoire_read(self, source_path: str) -> list:
        """读取魔导书 JSON 文件，返回列表。
        损坏时不静默返回空列表——先把损坏文件隔离为 .corrupt（保留现场），
        由调用方决定是否重建；否则下一次保存会用空列表覆盖、整个词库源被无声清空。"""
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        fpath = data_dir / source_path.replace('/', os.sep)
        if not fpath.exists():
            return []
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as e:
            try:
                corrupt = fpath.with_suffix(fpath.suffix + '.corrupt')
                if not corrupt.exists():
                    fpath.replace(corrupt)
                logger.warning(f"[ComfyUI] 词库文件损坏已隔离: {fpath.name} -> {corrupt.name} ({e})")
            except Exception:
                logger.warning(f"[ComfyUI] 词库文件损坏且隔离失败: {source_path} ({e})")
            return []

    def _grimoire_write(self, source_path: str, data: list) -> bool:
        """写入魔导书 JSON 文件（原子写：tmp + replace，防止写一半崩溃损坏词库）"""
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        fpath = data_dir / source_path.replace('/', os.sep)
        fpath.parent.mkdir(parents=True, exist_ok=True)
        tmp = fpath.with_suffix(fpath.suffix + '.tmp')
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(fpath)
            return True
        except Exception as e:
            logger.warning(f"[ComfyUI] 写入魔导书文件失败 {source_path}: {e}")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            return False

    def _grimoire_anima_source_names(self) -> list[str]:
        """返回 Anima-Tools 只读数据源名称"""
        try:
            from .anima_data import _ANIMA_SOURCE_NAMES
            return [s[1] for s in _ANIMA_SOURCE_NAMES]
        except ImportError:
            return []

    @filter.llm_tool(name="comfyui_grimoire_add")
    async def llm_grimoire_add(self, event: AstrMessageEvent, source: str, name: str, tags: str, style: str = "general"):
        """向魔导书指定子分类添加一条新标签。支持动态检测子分类列表。

        Args:
            source (string): 数据源关键词，支持中文名/文件名/路径片段，如 "lighting"、"光影"、"lighting/lighting.json"、"pose_action"、"normal_posture"、"场景"、"environment"、"shot"、"framing"
            name (string): 条目名称，如 "金色黄昏"、"回眸一笑"
            tags (string): 逗号分隔的英文提示词，如 "golden hour, warm glow, rim light"
            style (string): 样式，可选 "general"、"portrait"、"landscape"，默认 "general"
        """
        if not self.grimoire_enabled:
            return "❌ 魔导书已禁用，请先在 WebUI 中启用"

        src_path = self._grimoire_find_source_path(source)
        if not src_path:
            all_srcs = self._grimoire_sources()
            hints = "\n".join(f"  {s['name']} ({s['path']}) [{s['count']}条]" for s in all_srcs[:20])
            return f"❌ 未找到数据源「{source}」，可用数据源:\n{hints}\n(共 {len(all_srcs)} 个数据源)"

        items = self._grimoire_read(src_path)

        # 检查同名
        for it in items:
            if it.get("name") == name:
                return f"❌ 条目「{name}」已在 {src_path} 中存在，如需修改请使用 grimoire_edit"

        new_item = {
            "name": name,
            "tags": tags,
            "style": style,
        }
        items.append(new_item)

        if self._grimoire_write(src_path, items):
            return f"✅ 已添加「{name}」→ {src_path}\n标签: {tags}"
        else:
            return f"❌ 写入失败，请检查文件权限"

    @filter.llm_tool(name="comfyui_grimoire_delete")
    async def llm_grimoire_delete(self, event: AstrMessageEvent, source: str, name: str):
        """从魔导书指定子分类中删除一条标签。

        Args:
            source (string): 数据源关键词，如 "lighting"、"场景"、"shot"、"normal_posture"
            name (string): 要删除的条目名称，如 "黄金时刻"
        """
        if not self.grimoire_enabled:
            return "❌ 魔导书已禁用"

        src_path = self._grimoire_find_source_path(source)
        if not src_path:
            return f"❌ 未找到数据源「{source}」"

        items = self._grimoire_read(src_path)
        before = len(items)
        items = [it for it in items if it.get("name") != name]

        if len(items) == before:
            return f"❌ 在 {src_path} 中未找到「{name}」"

        if self._grimoire_write(src_path, items):
            return f"✅ 已从 {src_path} 删除「{name}」"
        else:
            return f"❌ 删除失败"

    @filter.llm_tool(name="comfyui_grimoire_edit")
    async def llm_grimoire_edit(self, event: AstrMessageEvent, source: str, name: str, new_name: str = "", new_tags: str = "", new_style: str = ""):
        """编辑魔导书中已有的标签。支持修改名称、标签和样式。

        Args:
            source (string): 数据源关键词，如 "lighting"、"场景"、"shot"
            name (string): 要编辑的条目当前名称
            new_name (string): 新名称（如不需修改可不传）
            new_tags (string): 新的逗号分隔英文提示词（如不需修改可不传）
            new_style (string): 新样式（如不需修改可不传）
        """
        if not self.grimoire_enabled:
            return "❌ 魔导书已禁用"

        src_path = self._grimoire_find_source_path(source)
        if not src_path:
            return f"❌ 未找到数据源「{source}」"

        items = self._grimoire_read(src_path)
        found = None
        for it in items:
            if it.get("name") == name:
                found = it
                break

        if not found:
            return f"❌ 在 {src_path} 中未找到「{name}」"

        if new_name:
            found["name"] = new_name
        if new_tags:
            found["tags"] = new_tags
        if new_style:
            found["style"] = new_style

        if self._grimoire_write(src_path, items):
            return f"✅ 已更新「{name}」"
        else:
            return f"❌ 更新失败"

    @filter.llm_tool(name="comfyui_set_prompt_model")
    async def llm_set_prompt_model(self, event: AstrMessageEvent, model: str):
        """切换随机图/魔导书的提示词生成引擎（anima 或 k2）。
        切换后 WebUI 设置面板、魔导书模型下拉、QQ /随机图 命令三方均同步生效。

        Args:
            model (string): 引擎名称，可选 "anima"（原有随机池抽标签方式）或 "k2"（K2 引擎组中文成句）。
                不区分大小写，支持中文别名："anima"/"原有"/"原版"/"随机池"/"默认" → anima；"k2"/"引擎"/"组句" → k2。
        """
        raw = (model or "").strip().lower()
        # 中文别名归一
        if raw in ("k2", "引擎", "组句"):
            target = "k2"
        elif raw in ("anima", "原有", "原版", "随机池", "默认", ""):
            target = "anima"
        else:
            return f"❌ 不支持的引擎「{model}」，仅支持 anima 或 k2"

        from .random_prompt import PROMPT_SECTION_ORDER
        if target not in PROMPT_SECTION_ORDER:
            return f"❌ 不支持的引擎「{target}」"

        # 写 workflow_config.__prompt_model__（QQ /随机图 命令与 WebUI 随机按钮读取此值）
        self.workflow_config['__prompt_model__'] = target
        await self._save_workflow_config()
        # 写 local_config.k2_compose_mode（设置面板显示值，保证两处一致）
        local_cfg = self._load_local_config()
        if local_cfg.get("k2_compose_mode") != target:
            local_cfg["k2_compose_mode"] = target
            self._save_local_config(local_cfg)

        label = "K2 · 引擎组中文成句" if target == "k2" else "Anima · 原有随机池抽标签"
        return f"✅ 已切换提示词生成引擎为：{label}（WebUI 设置、魔导书、QQ /随机图 已同步）"

    async def _webui_grimoire_cache_status(self, request):
        """查询某个数据源的图片缓存状态（含总大小）"""
        source = request.query.get('source', '').strip()
        if not source:
            return web.json_response({"ok": False, "error": "缺少 source 参数"})
        data_dir = data_dir_resolver()  # v4.3.0: 统一经 data_paths 解析
        source = source.replace('/', os.sep).replace('\\', os.sep)
        fpath = data_dir / source
        if not fpath.suffix:
            fpath = fpath.with_suffix('.json')

        # 尝试从本地 JSON 读取
        items = []
        if fpath.exists() and fpath.is_file():
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    items = json.loads(content) if content else []
                if not isinstance(items, list):
                    items = []
            except Exception:
                items = []

        # 本地 JSON 不存在 → 尝试从 Anima-Tools JS 加载
        if not items and _is_anima_source(str(fpath.relative_to(data_dir))):
            source_name = fpath.relative_to(data_dir).stem
            items = load_anima_tools_source(source_name)

        if not items:
            return web.json_response({"ok": False, "error": "数据源为空或不存在"})

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

        total = sum(1 for it in items if it.get("image_url"))
        # 读一次缓存目录，用集合查，避免 4 万次文件系统查询
        cached_keys = set(f.name for f in self._grimoire_cache_dir.iterdir()) if self._grimoire_cache_dir.exists() else set()
        cached = 0
        cache_size = 0
        for it in items:
            img_url = it.get("image_url") or ""
            if img_url:
                cache_key = hashlib.md5(img_url.encode()).hexdigest()
                if cache_key in cached_keys:
                    cached += 1
                    try:
                        cache_size += (self._grimoire_cache_dir / cache_key).stat().st_size
                    except Exception:
                        pass
        return web.json_response({
            "ok": True,
            "total": total,
            "cached": cached,
            "pending": total - cached,
            "cache_size_gb": round(cache_size / (1024**3), 2),
            "cache_size_mb": round(cache_size / (1024**2), 1),
            "source": source
        })

    async def _webui_grimoire_cache_all(self, request):
        """批量缓存某个数据源的全部图片 — 后台启动，立即返回"""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求体必须是 JSON"})
        source = body.get('source', '').strip()
        if not source:
            return web.json_response({"ok": False, "error": "缺少 source 参数"})

        # 如果已有运行中的任务，不重复启动
        existing = self._cache_progress.get(source)
        if existing and existing.get("running"):
            return web.json_response({"ok": True, "message": "已有缓存任务运行中"})

        # 后台启动下载
        self._cache_progress[source] = {
            "running": True,
            "total": 0,
            "done": 0,
            "success": 0,
            "failed": 0,
            "message": "正在扫描..."
        }
        asyncio.create_task(self._run_cache_background(source))
        return web.json_response({"ok": True, "message": "缓存任务已启动"})

    async def _webui_grimoire_cache_progress(self, request):
        """查询当前缓存任务的进度"""
        source = request.query.get('source', '').strip()
        if not source:
            return web.json_response({"ok": False, "error": "缺少 source 参数"})
        prog = self._cache_progress.get(source, {})
        return web.json_response({
            "ok": True,
            "running": prog.get("running", False),
            "total": prog.get("total", 0),
            "done": prog.get("done", 0),
            "success": prog.get("success", 0),
            "failed": prog.get("failed", 0),
            "message": prog.get("message", "")
        })
