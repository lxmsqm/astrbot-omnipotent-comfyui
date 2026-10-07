# -*- coding: utf-8 -*-
"""workflow.py — WorkflowMixin（工作流切换/分类/解析/组控制等）
v4.13.5 拆分自 main.py，行为不变：Mixin 间仍经 self 运行时互通。
"""
import json
import traceback
from pathlib import Path

from astrbot.api import logger


class WorkflowMixin:
    """工作流管理：目录、列表、切换、配置保存。"""

    def _get_workflow_dir(self):
        # 实例变量优先（用于用户已通过 WebUI 保存过的目录）
        if self.workflow_dir.parts:
            return self.workflow_dir
        p = self._user_data_dir / "config.json"
        try:
            with open(p, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
            d = data.get('__local_config__', {}).get("workflow_dir", "")
            if d: return Path(d)
        except Exception as e:
            logger.warning(f"[ComfyUI] 读取工作流目录配置失败: {e}")
        # 从 AstrBot 插件配置读取，无默认值；用户必须通过 WebUI 或管理界面配置 workflow_dir
        wd = self.config.get("workflow_dir", "")
        return Path(wd) if wd else self._user_data_dir / "workflows"

    def _save_workflow_dir(self, path):
        self.workflow_dir = Path(path)
        self.config['workflow_dir'] = str(path)
        self._save_local_config({"workflow_dir": str(path)})

    def _schedule_save_workflow_config(self):
        """安全调度 _save_workflow_config：有 running loop 用 ensure_future，无 loop 时起线程执行。
        修复同步方法（如 _sync_resolution_to_workflow）里直接 ensure_future 可能抛 RuntimeError 的问题。"""
        import asyncio as _aio
        import threading as _th
        try:
            _aio.get_running_loop()
            _aio.ensure_future(self._save_workflow_config())
        except RuntimeError:
            _th.Thread(target=lambda: _aio.run(self._save_workflow_config()), daemon=True).start()

    async def _save_workflow_config(self):
        """持久化 workflow_config 到文件（线程安全 + 原子写入防并发丢失）"""
        config_path = self._user_data_dir / "config.json"
        # 与 _save_local_config 共用同一把 threading.Lock，防止并发写互相覆盖
        with self._config_file_lock:
            try:
                # 先读取文件最新内容，合并到内存数据中，防止丢失 __local_config__ 等
                if config_path.exists():
                    with open(str(config_path), 'r', encoding='utf-8-sig') as f:
                        file_data = json.load(f)
                    # 仅合并 __local_config__ 等非 workflow_config 持有的键，避免覆盖
                    for _k in ('__local_config__',):
                        if _k in file_data:
                            self.workflow_config[_k] = file_data[_k]
                # 原子写入：先写临时文件再重命名，防止写入中断导致文件损坏
                tmp_path = config_path.with_suffix('.json.tmp')
                with open(str(tmp_path), 'w', encoding='utf-8') as f:
                    json.dump(self.workflow_config, f, ensure_ascii=False, indent=2)
                tmp_path.replace(config_path)
            except Exception as e:
                logger.warning(f"[ComfyUI] 保存工作流配置失败: {e}\n{traceback.format_exc()}")

    def _atomic_write_workflow_json(self, wf, wf_path):
        """原子写入工作流 JSON 文件：带锁 + 临时文件再替换，防止并发写损坏。
        供 /比例、WebUI 分辨率/时长同步等所有写工作流文件的路径共用。"""
        import os as _os
        with self._wf_file_lock:
            tmp_path = str(wf_path) + '.tmp'
            try:
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(wf, f, ensure_ascii=False, indent=2)
                _os.replace(tmp_path, str(wf_path))
            except Exception as e:
                logger.warning(f"[ComfyUI] 原子写入工作流 JSON 失败: {e}")
                try:
                    if _os.path.exists(tmp_path): _os.unlink(tmp_path)
                except Exception:
                    pass

    def _switch_to_workflow(self, wf, context_key=None):
        if context_key:
            self._context_workflows[context_key] = wf['name']
        self.workflow_path = wf['path']
        self.current_workflow_name = wf['name']
        # 持久化 __current_workflow__：切换后写回 config，重启后 _refresh_workflow_list 才能恢复该工作流
        # （否则重启后回退到第一个工作流，官方分辨率/时长节点检测错乱）
        # ★ v4.4.1 修复串台：此前这里写的是 __bind_target__——该键是「组绑定目标」专用，
        #   被切工作流动作反复改写后，任何工作流都会被当成组绑定目标（孤儿绑定串台）。
        #   现拆分：__current_workflow__ 只管「上次选中的工作流」，__bind_target__ 只管组绑定。
        wname = wf.get('name') if isinstance(wf, dict) else wf
        if wname and self.workflow_config.get('__current_workflow__') != wname:
            self.workflow_config['__current_workflow__'] = wname
            self._schedule_save_workflow_config()
        self._refresh_workflow_list()
        # v4.4.4: 切换工作流后按新工作流的规则绑定重注入工具描述（下一轮对话生效）
        self._apply_llm_templates()

    def _refresh_workflow_list(self):
        """扫描工作流：根目录 *.json + 各一级分类子目录 *.json。
        文件可平铺在根目录（未分类 / 用户直接丢），也可在分类子目录（WebUI 设分类后自动归位）。
        工作流以文件名(basename)为唯一标识，path 为实际磁盘位置。"""
        # 首次扫描自动归位：把「已设分类但仍在根目录」的工作流移进对应分类子目录（幂等，仅进程内一次）
        if not getattr(self, '_wf_cat_auto_synced', False):
            self._wf_cat_auto_synced = True
            try:
                cats = self.workflow_config.get('__wf_categories__', {}) or {}
                for wname, cat in list(cats.items()):
                    if not cat:
                        continue
                    try:
                        self._move_workflow_to_category(wname, cat)
                    except Exception:
                        pass
            except Exception:
                pass
        wdir = self._get_workflow_dir(); wdir.mkdir(parents=True, exist_ok=True)
        files = list(wdir.glob("*.json"))
        # 一级分类子目录（画/图生图/...）也纳入扫描，避免归位后文件不可见
        for sub in sorted(wdir.glob("*/")):
            if not sub.is_dir() or sub.name.startswith('.'):
                continue
            files.extend(sub.glob("*.json"))
        files = [f for f in files if not f.name.endswith('.groups.json') and not f.name.startswith('.')]
        # 去重：同名文件若根与子目录并存，优先根（子目录是归位产物）
        seen = {}
        for f in files:
            if f.name not in seen:
                seen[f.name] = f
        files = list(seen.values())
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
                "display_name": display_name,
                "preview": self._workflow_preview_path(f.name) is not None
            })
        # 清理已不存在的旧工作流的残留配置（节点配置、分类、别名等）
        existing_names = {f.name for f in files}
        changed = False
        for cfg_key in ('__workflow_node_configs__', '__wf_categories__', '__workflow_aliases__', '__workflow_categories__'):
            d = self.workflow_config.get(cfg_key, {}) or {}
            for name in list(d.keys()):
                if name not in existing_names:
                    del d[name]
                    changed = True
            if changed:
                self.workflow_config[cfg_key] = d
        if changed:
            # 安全保存（有 running loop 用 ensure_future，无 loop 起线程，避免 RuntimeError）
            self._schedule_save_workflow_config()
        self.workflow_list_cache = all_files
        visible = [w for w in all_files if not w["hidden"]]
        if not self.workflow_path and visible:
            # 优先用 __current_workflow__ 恢复当前工作流（避免重启后首屏指向第一个工作流，
            # 导致官方分辨率/时长节点检测错误、面板不显示）
            # ★ v4.4.1: 兼容旧键 __bind_target__（历史版本把当前工作流存在那里）；
            #   若读到旧键则迁移到新键并清掉旧键，防止组绑定串台残留
            cur_saved = (self.workflow_config.get('__current_workflow__') or '').strip()
            if not cur_saved:
                legacy = (self.workflow_config.get('__bind_target__') or '').strip()
                if legacy and not self.workflow_config.get('__groups_source__'):
                    # 无组绑定时旧键才是"当前工作流"，迁移；有组绑定则保留原义
                    cur_saved = legacy
                    self.workflow_config['__current_workflow__'] = legacy
                    self.workflow_config['__bind_target__'] = ''
                    self._schedule_save_workflow_config()
            cur_match = next((w for w in visible if w['name'] == cur_saved), None) if cur_saved else None
            if cur_match:
                self.workflow_path = cur_match["path"]
                self.current_workflow_name = cur_match["name"]
                cur_match["is_current"] = True
            else:
                self.workflow_path = visible[0]["path"]
                self.current_workflow_name = visible[0]["name"]
                visible[0]["is_current"] = True
        return visible

    def _find_workflow_file_path(self, name):
        """按工作流文件名(basename)定位磁盘文件路径。先查根目录，再查一级分类子目录。找不到返回 None。"""
        if not name:
            return None
        wdir = self._get_workflow_dir()
        p = wdir / name
        if p.is_file():
            return p
        for sub in sorted(wdir.glob("*/")):
            if not sub.is_dir() or sub.name.startswith('.'):
                continue
            sp = sub / name
            if sp.is_file():
                return sp
        return None

    def _move_workflow_to_category(self, name, category):
        """把工作流文件移动到分类子目录（category 非空 → 移入 <分类>/，为空 → 移回根）。
        返回 (新path, ok, err)。文件不存在/已在目标处则返回当前 path+ok。"""
        wdir = self._get_workflow_dir()
        src = self._find_workflow_file_path(name)
        if src is None:
            return None, False, f"找不到工作流文件: {name}"
        # 目标目录
        if category:
            # 去分隔符/非法路径成分，仅允许单层文件夹名
            cat = category.replace('\\', '_').replace('/', '_').strip()
            if not cat:
                return src, False, "非法分类名"
            dest_dir = wdir / cat
        else:
            dest_dir = wdir
        # 已在目标处则无需移动
        if src.parent == dest_dir:
            return src, True, ""
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / name
            if dest.exists():
                return src, False, f"目标已存在同名文件: {dest.name}"
            src.replace(dest)
            # 若移动的是当前工作流，修正 self.workflow_path
            if self.current_workflow_name == name:
                self.workflow_path = dest
            # 若移动的是绑定目标，修正 __bind_target__ 无需(仍按 name)
            logger.info(f"[ComfyUI] 工作流归位: {src} → {dest} (分类={category or '未分类'})")
            return dest, True, ""
        except Exception as e:
            logger.warning(f"[ComfyUI] 工作流归位失败: {e}")
            return src, False, str(e)

    def _get_display_name(self, fname):
        """获取工作流的显示名（别名优先，无别名返回原名）"""
        if not fname:
            return ''
        for w in self.workflow_list_cache:
            if w['name'] == fname:
                return w.get('display_name', fname)
        return fname

    def _build_wf_selection_menu(self, event, cmd_name, matching_wfs):
        """构建工作流选择菜单文本，并设置 pending_action。调用方 yield 该文本后 return。"""
        user_id = event.get_sender_id()
        cmd_icons = {'文生图': '🎨', '图生图': '🖼️', '视频': '🎬'}
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

    def _order_workflows_by_category(self, wfs):
        """按分类排序工作流列表：文生图 → 图生图 → 视频 → 未分类。
        与 /工作流 分组展示的编号顺序保持一致，数字索引可直接对应。"""
        cats = self.workflow_config.get('__wf_categories__', {}) or {}
        groups = {}  # cat_name -> [wf_dict]
        ungrouped = []
        for w in wfs:
            wf_name = w["name"] if isinstance(w, dict) else w
            cat = cats.get(wf_name, '')
            if cat:
                groups.setdefault(cat, []).append(w)
            else:
                ungrouped.append(w)
        ordered = []
        for cat in ('文生图', '图生图', '视频'):
            ordered.extend(groups.get(cat, []))
        ordered.extend(ungrouped)
        return ordered

    async def _switch_workflow_by_msg(self, event, msg, user_id):
        """根据用户输入（数字/关键词/分类名）切换或浏览工作流，供 /工作流 和 /切换 命令共用"""
        wfs = self._refresh_workflow_list()
        target = self._order_workflows_by_category(wfs)

        # 分类名 → 显示该分类的工作流小列表（两级导航：先分类，再编号/关键词切换）
        cats = self.workflow_config.get('__wf_categories__', {}) or {}
        known_cats = ['文生图', '图生图', '视频', '未分类']
        if msg in known_cats:
            if msg == '未分类':
                cat_wfs = [w for w in target if not cats.get(w['name'] if isinstance(w, dict) else w, '')]
            else:
                cat_wfs = [w for w in target if cats.get(w['name'] if isinstance(w, dict) else w, '') == msg]
            if not cat_wfs:
                yield event.plain_result(f"❌ 分类 [{msg}] 下暂无工作流")
                return
            ctx = self._get_context_key(event)
            ctx_wf = self._context_workflows.get(ctx, self.current_workflow_name) if ctx else self.current_workflow_name
            # 分类内重新编号（二级编号），与 handle_numeric_choice 一级编号进入后的行为一致
            m = f"分类 [{msg}] 工作流:\n"
            for i, w in enumerate(cat_wfs, 1):
                dn = w.get("display_name", w["name"]) if isinstance(w, dict) else self._get_display_name(w)
                is_cur = (w["name"] if isinstance(w, dict) else w) == ctx_wf
                m += f"  [{i}] {dn}" + (" ✅\n" if is_cur else "\n")
            m += "发送编号或关键词切换"
            self._set_pending_action(user_id, "switch_workflow", {"workflows": target, "context_key": ctx, "stage": "workflow", "cat_wfs": cat_wfs}, timeout=10)
            yield event.plain_result(m.strip())
            return

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
