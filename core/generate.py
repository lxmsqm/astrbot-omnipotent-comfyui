# -*- coding: utf-8 -*-
"""generate.py — GenerateMixin（生成提交链/提示词处理/分辨率/结果发送等）
v4.13.5 拆分自 main.py，行为不变：Mixin 间仍经 self 运行时互通。
"""
import hashlib
import os
from pathlib import Path

from astrbot.api import logger


class GenerateMixin:
    """生成核心:工作流节点查找、配置应用、提示词注入、分辨率、等待与提交。"""

    def _find_positive_prompt_node(self, workflow, wf_name=None):
        """仅返回 WebUI 中手动指定的正面提示词节点。未指定则返回 None。"""
        wf_name = wf_name or self.current_workflow_name
        wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
        wf_config = wf_configs.get(wf_name, {})
        manual = wf_config.get('__prompt_node__', '') or self.workflow_config.get('__prompt_node__', '')
        if manual and manual in workflow: return manual
        return None

    def _find_negative_prompt_node(self, workflow, wf_name=None):
        """仅返回 WebUI 中手动指定的负面提示词节点。未指定则返回 None。"""
        wf_name = wf_name or self.current_workflow_name
        wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
        wf_config = wf_configs.get(wf_name, {})
        manual = wf_config.get('__negative_node__', '') or self.workflow_config.get('__negative_node__', '')
        if manual and manual in workflow: return manual
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

    def _closest_ratio(self, w: int, h: int) -> str:
        """将实际宽高映射到最近的标准比例。"""
        if not w or not h:
            return self.default_ratio or "9:16"
        best = self.default_ratio or "9:16"
        best_err = float('inf')
        for r_str in self.aspect_ratios:
            try:
                ra, rb = map(int, r_str.split(':'))
                target = ra / rb
                actual = w / h
                err = abs(actual - target)
                if err < best_err:
                    best_err = err
                    best = r_str
            except (ValueError, ZeroDivisionError):
                continue
        return best

    def _find_resolution_node(self, workflow, wf_name=None):
        """仅返回 WebUI 中手动指定的分辨率节点。未指定则返回 None。"""
        wf_name = wf_name or self.current_workflow_name
        wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
        wf_config = wf_configs.get(wf_name, {})
        manual = wf_config.get('__resolution_node__', '') or self.workflow_config.get('__resolution_node__', '')
        if manual and manual in workflow: return manual
        return None

    def _find_official_resolution_nodes(self, workflow):
        """检测工作流中的分辨率节点：
        - 官方 ResolutionSelector（class_type == 'ResolutionSelector'）
        - xbbox/XB 系列参数节点（class_type 含 'XB' 且有 aspect_ratio 输入，
          如 XB_HailuoH3VideoParams / XB_VideoParams）——恢复 XB 分辨率预设显示
        返回节点ID列表；未发现返回空列表。"""
        result = []
        for nid, node in workflow.items():
            if not isinstance(node, dict): continue
            ct = node.get('class_type', '')
            inputs = node.get('inputs', {})
            if ct == 'ResolutionSelector':
                result.append(nid)
            elif 'XB' in ct and isinstance(inputs, dict) and 'aspect_ratio' in inputs:
                result.append(nid)
        return result

    def _find_aspect_ratio_nodes(self, workflow):
        """检测工作流中的 AspectRatioNode（比例锁定工具）节点（class_type == 'AspectRatioNode'）。
        这类节点才是部分工作流实际生效的分辨率节点。返回节点ID列表；未发现返回空列表。"""
        return [nid for nid, node in workflow.items()
                if isinstance(node, dict) and node.get('class_type') == 'AspectRatioNode']

    def _clear_saved_resolution_keys(self, nids):
        """清除 __saved_texts__ 中与分辨率节点相关的旧值（aspect_ratio/megapixels/multiple/width/height）。
        /api/workflow-params 会用 __saved_texts__ 覆盖显示值，若不清理，
        用户通过官方面板设置的新值刷新后会被旧值覆盖。返回是否有清除。"""
        try:
            wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
            wf_cfg = wf_configs.get(self.current_workflow_name, {})
            saved = wf_cfg.get('__saved_texts__', {}) or {}
            keys_to_clear = ['aspect_ratio', 'megapixels', 'multiple', 'width', 'height']
            changed = False
            for nid in nids:
                for k in keys_to_clear:
                    ck = f"{nid}_{k}"
                    if ck in saved:
                        del saved[ck]
                        changed = True
            if changed:
                wf_cfg['__saved_texts__'] = saved
                wf_configs[self.current_workflow_name] = wf_cfg
                self.workflow_config['__workflow_node_configs__'] = wf_configs
                logger.info(f"[ComfyUI] 已清除 saved_texts 旧分辨率值: 节点{nids}")
            return changed
        except Exception as e:
            logger.warning(f"[ComfyUI] 清除 saved_texts 旧分辨率值失败: {e}")
            return False

    def _find_official_duration_nodes(self, workflow):
        """检测工作流中的时长节点：
        - 官方 PrimitiveFloat / Float（class_type == 'PrimitiveFloat' / 'Float'）
        返回节点ID列表；未发现返回空列表。"""
        result = []
        for nid, node in workflow.items():
            if not isinstance(node, dict): continue
            if node.get('class_type') in ('PrimitiveFloat', 'Float'):
                result.append(nid)
        return result

    def _ratio_to_official(self, ratio):
        """插件比例 → 官方 ResolutionSelector 选项；无法映射时返回 '1:1 (Square)'。"""
        return self.official_ratio_map.get(ratio, "1:1 (Square)")

    def _official_to_ratio(self, official):
        """官方 ResolutionSelector 选项 → 插件比例；无法映射时返回 '9:16'。"""
        return self.official_ratio_reverse.get(official, "9:16")

    def _find_load_image_node(self, workflow):
        """仅返回 WebUI 中手动指定的图片载入节点。未指定则返回 None。
        注意：工作流 JSON 的节点键可能是 int 或 str（ComfyUI 导出差异），
        配置里存的是字符串，因此必须做类型归一化比较。"""
        wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
        wf_config = wf_configs.get(self.current_workflow_name, {})
        manual = (wf_config.get('__load_image_node__', '') or self.workflow_config.get('__load_image_node__', '')
                  or wf_config.get('__load_image_nodes__', '') or self.workflow_config.get('__load_image_nodes__', ''))
        if not manual:
            return None
        manual = str(manual).strip()
        # 直接命中
        if manual in workflow:
            return manual
        # 类型归一化命中（int 键 vs str 配置）
        for k in workflow.keys():
            if str(k) == manual:
                logger.info(f"[ComfyUI] 图片节点配置 {manual!r} 与工作流键类型不一致(实际 {type(k).__name__})，已归一化匹配")
                return k
        return None

    def _find_all_load_image_nodes(self, workflow):
        """按节点ID排序，找出工作流中所有 LoadImage 节点"""
        nodes = []
        for nid, node in workflow.items():
            if node.get('class_type') == 'LoadImage':
                nodes.append(nid)
        nodes.sort()
        return nodes

    def _find_image_input_nodes(self, workflow):
        """反推/加载类节点定位：优先带 image 输入的 LoadImage，其次任何含 image 输入的 Load* 节点。
        返回节点ID列表（按ID排序）。"""
        def _sorted(nids):
            try:
                return sorted(nids, key=int)
            except (TypeError, ValueError):
                return sorted(nids)
        primary = [nid for nid, node in workflow.items()
                   if isinstance(node, dict) and node.get('class_type') == 'LoadImage'
                   and 'image' in (node.get('inputs') or {})]
        if primary:
            return _sorted(primary)
        secondary = [nid for nid, node in workflow.items()
                     if isinstance(node, dict)
                     and str(node.get('class_type', '')).startswith('Load')
                     and 'image' in (node.get('inputs') or {})]
        return _sorted(secondary)

    def _find_show_text_nodes(self, workflow):
        """找出工作流中的 ShowText 类文本输出节点（ShowText / ShowText|pysssss 等），返回节点ID列表（按ID排序）。"""
        nodes = [nid for nid, node in workflow.items()
                 if isinstance(node, dict) and 'ShowText' in str(node.get('class_type', ''))]
        try:
            nodes.sort(key=int)
        except (TypeError, ValueError):
            nodes.sort()
        return nodes

    def _find_sampler_node(self, workflow):
        for nid, node in workflow.items():
            if node.get('class_type') in ['KSampler', 'ROCMOptimizedKSampler', 'KSamplerAdvanced']:
                if 'denoise' in node.get('inputs', {}): return nid
        return None

    def _compute_fingerprint(self, workflow) -> str:
        """根据工作流的节点ID和类型计算内容指纹，用于检测工作流文件是否被替换"""
        parts = []
        for nid in sorted(workflow.keys(), key=int):
            node = workflow.get(nid, {})
            if not isinstance(node, dict): continue
            ct = node.get('class_type', '')
            parts.append(f"{nid}:{ct}")
        import hashlib
        return hashlib.md5(",".join(parts).encode()).hexdigest()

    def _apply_workflow_config(self, workflow, wf_name=None, protect_nodes=None):
        """应用工作流保存的参数；并清理未上传的加载节点。

        protect_nodes: 本次生成即将注入图片的节点 ID 集合。
        这些节点即使当前 image 为空也必须保留——否则清理会先删节点、
        后续 _set_load_image 注入时节点已不存在（表现为"图生图没反应"）。
        """
        protect_nodes = set(str(x) for x in (protect_nodes or ()) if x)
        wf_name = wf_name or self.current_workflow_name
        skip_keys = {'rgthree_comparer', 'any', 'any_input'}
        # 获取当前工作流的保存文本（从 __workflow_node_configs__ 读取，每个工作流隔离）
        wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
        wf_cfg = wf_configs.get(wf_name, {})
        wf_saved_texts = wf_cfg.get('__saved_texts__', {}) or {}
        # 应用所有节点保存的参数值（包括主模型、Lora 等），不再限制仅 prompt/negative 节点
        # 注释：以前的 allowed_nodes 限制导致主模型/Lora 切换不生效，现已移除
        for ck in list(wf_saved_texts.keys()) + [k for k in self.workflow_config.keys() if k.endswith('_text') and not k.startswith('__') and k not in wf_saved_texts]:
            val = wf_saved_texts.get(ck)
            if val is None:
                val = self.workflow_config.get(ck)
            if val is None: continue
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
                    # 清理路径前缀：如果是 Windows 盘符路径（如 E:\...），只取文件名部分
                    # 但保留 ComfyUI 子文件夹引用（如 "Anima/角色lora/穗穗.safetensors"）
                    # 只对盘符开头（如 C:）的路径做截断，避免误伤含冒号的参数值（如 16:9、00:00:00）
                    if isinstance(val, str) and len(val) >= 2 and val[1] == ':' and val[0].isalpha():
                        val = os.path.basename(val.replace('\\', '/'))
                    # 保护：如果原值是 dropdown 选择（模型名/Lora 名等），且保存的值与原值不同，
                    # 跳过写回，避免过期的旧值导致 ComfyUI 校验失败。
                    # 注意：只对来自旧版根级配置的值做保护（非 wf_saved_texts），
                    # 用户通过 WebUI 主动保存的值（在 wf_saved_texts 中）始终写入。
                    # 否则用户切换 Lora/模型后，val(新值) != orig(工作流旧值) 会被误拦截。
                    is_model_key = key.endswith('_name') or key in ('llm_service', 'service')
                    if is_model_key and val != orig and ck not in wf_saved_texts:
                        logger.info(f"[ComfyUI] 跳过过期的模型参数: {ck}={val!r} (当前值={orig!r})")
                        continue
                    workflow[nid]['inputs'][key] = val

        # 未上传的加载节点处理：MiniMax H3 主节点 ref 输入全部 optional 且有空值跳过，
        # 因此不再塞占位文件——文件不存在（未上传/引用 PC 本地文件）的加载节点
        # 直接从工作流移除，并断开所有指向它的连接，主节点自动跳过该参考输入。
        # 前端勾选「不使用」的节点（__empty_load_nodes__）即使文件存在也移除。
        try:
            media_rules = (
                ('loadimage', 'image'),
                ('loadaudio', 'audio'),
                ('videoloader', 'video'),
            )
            # 前端「不使用」勾选的节点 id 列表（生成时即使有文件也移除）
            empty_ids = set((wf_cfg.get('__empty_load_nodes__') or '').split(','))
            empty_ids.discard('')
            remove_ids = []
            for nid, node in list(workflow.items()):
                if not isinstance(node, dict):
                    continue
                ct = (node.get('class_type') or '').lower()
                inputs = node.get('inputs', {})
                if not isinstance(inputs, dict):
                    continue
                for ct_key, input_key in media_rules:
                    if ct_key not in ct:
                        continue
                    cur = inputs.get(input_key)
                    # 本次要注入图片的节点：跳过清理（图还没写进去，等 _set_load_image 写入）
                    if str(nid) in protect_nodes:
                        logger.info(f"[ComfyUI] 保留待注入图片的加载节点 {nid}")
                        break
                    has_file = cur and isinstance(cur, str) and cur.strip() and self._comfy_input_file_exists(cur)
                    if str(nid) in empty_ids:
                        remove_ids.append(nid)
                        logger.info(f"[ComfyUI] 加载节点 {nid} 被标记为「不使用」，移除并断开连接")
                    elif not has_file:
                        remove_ids.append(nid)
                        logger.info(f"[ComfyUI] 加载节点 {nid} 无有效文件({cur!r})，移除并断开连接")
                    break
            # 移除节点
            for nid in remove_ids:
                workflow.pop(nid, None)
            # 断开所有指向被移除节点的连接（inputs 值为 [node_id, slot] 形式）
            for node in workflow.values():
                if not isinstance(node, dict):
                    continue
                inputs = node.get('inputs', {})
                if not isinstance(inputs, dict):
                    continue
                for k, v in list(inputs.items()):
                    if isinstance(v, list) and len(v) >= 2 and str(v[0]) in remove_ids:
                        del inputs[k]
            # MiniMax H3 主节点的 first_frame/last_frame 是 optional（可空），
            # 若其 LoadImage 被移除导致值残留（字符串/空/指向已移除节点），
            # 显式删除让主节点走纯文生
            for node in workflow.values():
                if not isinstance(node, dict):
                    continue
                ct = node.get('class_type', '')
                if 'MiniMaxH3' not in ct:
                    continue
                inputs = node.get('inputs', {})
                if not isinstance(inputs, dict):
                    continue
                for kf in ('first_frame', 'last_frame'):
                    vf = inputs.get(kf)
                    # 非连接（list）形式 → 字符串残留，删除以触发 optional 空值走纯文生
                    if vf is not None and not isinstance(vf, list):
                        logger.info(f"[ComfyUI] 清理 {ct} {kf} 残留值({vf!r})，走纯文生")
                        del inputs[kf]
                    # 连接指向已被移除的节点 → 也删除（断连兜底）
                    elif isinstance(vf, list) and len(vf) >= 2 and str(vf[0]) in remove_ids:
                        logger.info(f"[ComfyUI] 清理 {ct} {kf} 指向已移除节点，走纯文生")
                        del inputs[kf]
            # 清理 saved_texts 中的 first_frame/last_frame 旧键，防止下次 _apply_workflow_config 写回字符串
            try:
                wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
                wf_cfg = wf_configs.get(self.current_workflow_name, {})
                saved = wf_cfg.get('__saved_texts__', {}) or {}
                stale_keys = [k for k in saved if k.endswith('_first_frame') or k.endswith('_last_frame')]
                if stale_keys:
                    for k in stale_keys:
                        del saved[k]
                    wf_cfg['__saved_texts__'] = saved
                    wf_configs[self.current_workflow_name] = wf_cfg
                    self.workflow_config['__workflow_node_configs__'] = wf_configs
                    logger.info(f"[ComfyUI] 已清理 saved_texts 首尾帧旧键: {stale_keys}")
                    self._schedule_save_workflow_config()
            except Exception as e:
                logger.warning(f"[ComfyUI] 清理 saved_texts 首尾帧旧键失败: {e}")
            if remove_ids:
                logger.info(f"[ComfyUI] 已移除 {len(remove_ids)} 个未上传加载节点: {remove_ids}")
        except Exception as e:
            logger.warning(f"[ComfyUI] 移除未上传加载节点失败: {e}")

    def _apply_loras(self, workflow):
        """将每个节点保存的 Lora 配置直接写入节点输入参数。

        对 PowerLoraLoader 类节点，将 lora 列表写入 lora_1, lora_2, ... 输入。
        不创建新节点，避免 ComfyUI 校验失败。
        """
        wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
        wf_cfg = wf_configs.get(self.current_workflow_name, {})
        lora_nodes = wf_cfg.get('__lora_nodes__', {}) or {}
        if not lora_nodes:
            return

        for node_id, loras in list(lora_nodes.items()):
            if not isinstance(loras, list) or not loras:
                continue
            if node_id not in workflow:
                continue

            node = workflow[node_id]
            ct = node.get('class_type', '')

            # 跳过标准 LoraLoader（由 _apply_workflow_config 处理）
            if ct == 'LoraLoader':
                continue

            enabled = [l for l in loras if l.get('on', True) and l.get('lora_name')]
            if not enabled:
                continue

            # 直接写入节点的 lora_1, lora_2, ... 输入
            # PowerLoraLoader 期望格式: {on, lora, strength, strengthTwo}
            idx = 1
            # 先清理旧的 lora_* 输入（保留非 lora 输入如 model, clip）
            for key in list(node.get('inputs', {}).keys()):
                if key.startswith('lora_'):
                    del node['inputs'][key]
            # 写入新的 lora 数据
            for lora in enabled:
                lora_entry = {
                    "on": lora.get('on', True),
                    "lora": lora.get('lora_name', ''),
                    "strength": lora.get('strength_model') or 1.0,
                }
                clip_str = lora.get('strength_clip') or lora.get('strength_model') or 1.0
                if clip_str != lora_entry['strength']:
                    lora_entry['strengthTwo'] = clip_str
                node['inputs'][f'lora_{idx}'] = lora_entry
                idx += 1

            logger.info(f"[ComfyUI] 节点 #{node_id} 写入 {len(enabled)} 个 Lora 到输入")

    def _apply_style_selector(self, workflow):
        """将 easy stylesSelector 节点保存的风格选择写回节点。

        easy stylesSelector 的风格由两个 widget 控制：
          - widgets_values[0] = styles（风格库/大类，如 krea2_397styles-3d_render_3D渲染）
          - widgets_values[1] = select_styles（具体风格名，逗号分隔，如 'Mixed Media'）
        这些值在 UI 格式 JSON 里只存在于 widgets_values，未必进 inputs 字典，
        因此不走 _apply_workflow_config 的通用 inputs 写回，这里单独处理。
        同时补写 inputs['styles'] / inputs['select_styles']，保证 API 格式也能生效。
        """
        wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
        wf_cfg = wf_configs.get(self.current_workflow_name, {})
        wf_saved = wf_cfg.get('__saved_texts__', {}) or {}
        # 找到 easy stylesSelector 节点
        style_nid = None
        for nid, node in workflow.items():
            if not isinstance(node, dict):
                continue
            ct = node.get('class_type', '')
            title = (node.get('_meta', {}) or {}).get('title', '')
            if 'stylesselector' in ct.lower().replace(' ', '') or 'stylesselector' in title.lower().replace(' ', ''):
                style_nid = nid
                break
        if not style_nid:
            return
        # 从 saved_texts 取该节点的 styles / select_styles（键形如 {nid}_styles）
        sn = str(style_nid)
        lib = wf_saved.get(f"{sn}_styles")
        sel = wf_saved.get(f"{sn}_select_styles")
        node = workflow[style_nid]
        wvn = node.get('widgets_values_named') or {}
        if 'styles' in wvn or 'select_styles' in wvn:
            # UI 格式：直接写 widgets_values_named
            if lib is not None:
                wvn['styles'] = lib
            if sel is not None:
                wvn['select_styles'] = sel
            node['widgets_values_named'] = wvn
        # 兜底：同时写 widgets_values 列表（按 index 0/1）
        wv = node.get('widgets_values')
        if isinstance(wv, list):
            if lib is not None and len(wv) >= 1:
                wv[0] = lib
            if sel is not None and len(wv) >= 2:
                wv[1] = sel
        # 补写 inputs，确保 API 格式执行时也能读取
        inputs = node.get('inputs')
        if isinstance(inputs, dict):
            if lib is not None:
                inputs['styles'] = lib
            if sel is not None:
                inputs['select_styles'] = sel
        logger.info(f"[ComfyUI] 风格预设已应用: node=#{style_nid} styles={lib!r} select_styles={sel!r}")

    def _inject_prompt(self, workflow, prompt, target_node=None, wf_name=None):
        if not str(prompt or '').strip():
            return False  # 空提示词不注入（避免把节点已有内容覆盖成 "existing, " 尾逗号）
        nid = target_node or self._find_positive_prompt_node(workflow, wf_name=wf_name)
        if nid and nid in workflow:
            inputs = workflow[nid].get('inputs', {})
            # 追加而非覆盖：让 WebUI 保存的提示词作为基础，QQ 命令提示词追加在后面
            for key, val in inputs.items():
                if isinstance(val, str):
                    existing = val.strip()
                    if existing:
                        workflow[nid]['inputs'][key] = f"{existing}, {prompt}"
                    else:
                        workflow[nid]['inputs'][key] = prompt
                    return True
        return False

    def _get_selected_lora_trigger_words(self) -> str:
        """从当前工作流的 __lora_nodes__ 配置中收集用户选中的触发词。
        返回逗号分隔的触发词字符串，无选中时返回空字符串。"""
        wf_configs = self.workflow_config.get('__workflow_node_configs__', {}) or {}
        wf_cfg = wf_configs.get(self.current_workflow_name, {})
        lora_nodes = wf_cfg.get('__lora_nodes__', {}) or {}
        words = []
        for nid, loras in lora_nodes.items():
            if not isinstance(loras, list):
                continue
            for lora in loras:
                if not lora.get('on', True):
                    continue
                selected = lora.get('trigger_words_selected', [])
                if selected:
                    words.extend(selected)
        # 去重并保持顺序
        seen = set()
        unique = []
        for w in words:
            if w not in seen:
                seen.add(w)
                unique.append(w)
        return ', '.join(unique)

    def _inject_lora_trigger_words(self, workflow, target_node=None, wf_name=None):
        """将用户选中的 Lora 触发词注入到提示词节点末尾。"""
        trigger_str = self._get_selected_lora_trigger_words()
        if not trigger_str:
            return False
        nid = target_node or self._find_positive_prompt_node(workflow, wf_name=wf_name)
        if nid and nid in workflow:
            inputs = workflow[nid].get('inputs', {})
            for key, val in inputs.items():
                if isinstance(val, str):
                    existing = val.strip()
                    if existing:
                        workflow[nid]['inputs'][key] = f"{existing}, {trigger_str}"
                    else:
                        workflow[nid]['inputs'][key] = trigger_str
                    logger.info(f"[ComfyUI] Lora 触发词已注入: {trigger_str}")
                    return True
        return False

    def _read_workflow_file_resolution(self, workflow):
        """v4.5.7: 读工作流自带分辨率节点的值（无需手动指定角色）——
        官方 ResolutionSelector（aspect_ratio+megapixels 换算宽高）→ AspectRatioNode（width/height）。
        读不到返回 (None, None)。这些值就是该工作流的"当前分辨率"：
        WebUI 官方面板、/比例、/分辨率 都会落盘到工作流文件，提交时必须以它为基准，
        否则每次生成都会被全局默认比例/质量踩掉（官方面板"改不动"的根因）。"""
        try:
            # v4.9.0: 取 MP 最大的官方节点作为基准——多阶段工作流（如 H3 一采0.5MP/二采1.0MP）
            # 的最终成品分辨率由最大的选择器决定，读第一个会把基准压到一采档
            best = None
            for nid in self._find_official_resolution_nodes(workflow):
                ins = workflow[nid].get('inputs', {})
                mp = ins.get('megapixels', 0)
                if isinstance(mp, (int, float)) and mp > 0 and (best is None or float(mp) > best[1]):
                    best = (nid, float(mp), ins.get('aspect_ratio', ''))
            if best:
                pr = self._official_to_ratio(best[2]) if best[2] else ''
                mp = best[1]
                if pr and ':' in str(pr) and mp > 0:
                    ra, rb = map(int, str(pr).split(':'))
                    total = mp * 1024 * 1024
                    x = (total / (ra * rb)) ** 0.5
                    return int(round(ra * x)), int(round(rb * x))
            for nid in self._find_aspect_ratio_nodes(workflow):
                ins = workflow[nid].get('inputs', {})
                try:
                    return int(ins.get('width')), int(ins.get('height'))
                except (TypeError, ValueError):
                    continue
        except Exception:
            pass
        return None, None

    def _set_resolution(self, workflow, width, height):
        nid = self._find_resolution_node(workflow)
        if not nid:
            # 兜底：未手动指定分辨率节点时，自动探测官方 ResolutionSelector / XB 参数节点 /
            # AspectRatioNode 并全部写入。否则官方选择器工作流（分辨率存在 aspect_ratio+megapixels
            # 输入里，没有 width/height）在提交时比例/质量永远不生效，图里保持文件里的旧值。
            official_ids = self._find_official_resolution_nodes(workflow)
            aspect_ids = self._find_aspect_ratio_nodes(workflow)
            if not official_ids and not aspect_ids:
                return False
            try:
                ratio = self._closest_ratio(width, height)
                megapixels = round((width * height) / (1024 * 1024), 2)
                official = self._ratio_to_official(ratio)
            except Exception:
                return False
            # v4.9.0: 多阶段分辨率选择器（如 H3 一采0.5MP/二采1.0MP）——按文件原 MP 比例
            # 分配目标总像素，只统一宽高比、绝不抹平阶段差。曾把所有选择器盖成同一个 MP，
            # 二采放大目标被压成一采分辨率，放大链白跑（输出永远是一采尺寸）。
            file_mps = []
            for oid in official_ids:
                v = workflow[oid].get('inputs', {}).get('megapixels')
                if isinstance(v, (int, float)) and v > 0:
                    file_mps.append(float(v))
            multi_stage = len(set(file_mps)) > 1
            max_mp = max(file_mps) if file_mps else 0.0
            for oid in official_ids:
                inputs = workflow[oid].get('inputs', {})
                inputs['aspect_ratio'] = official
                if multi_stage:
                    v = inputs.get('megapixels')
                    if isinstance(v, (int, float)) and v > 0 and max_mp > 0:
                        inputs['megapixels'] = round(float(v) * megapixels / max_mp, 3)
                    else:
                        inputs['megapixels'] = megapixels
                else:
                    inputs['megapixels'] = megapixels
                if 'multiple' not in inputs:
                    inputs['multiple'] = 8
            if aspect_ids and ':' in ratio:
                try:
                    ra, rb = map(int, ratio.split(':'))
                    x = ((width * height) / (ra * rb)) ** 0.5
                    for aid in aspect_ids:
                        inputs = workflow[aid].get('inputs', {})
                        inputs['aspect_ratio'] = ratio
                        div = 8
                        dv = inputs.get('divisible_by', 8)
                        div = int(dv) if str(dv).isdigit() else 8
                        inputs['width'] = round(ra * x / div) * div
                        inputs['height'] = round(rb * x / div) * div
                except Exception:
                    pass
            logger.info(f"[ComfyUI] 分辨率兜底写入官方节点: {official_ids + aspect_ids} -> {ratio} {megapixels}MP")
            return True
        inputs = workflow[nid]['inputs']
        ct = workflow[nid].get('class_type', '')
        # 官方 ResolutionSelector 没有 width/height 输入（宽高由 aspect_ratio+megapixels 算出并输出），
        # 硬塞 width/height 无效还会导致 ComfyUI 报未知输入。这里改写 aspect_ratio + megapixels。
        if ct == 'ResolutionSelector':
            try:
                ratio = self._closest_ratio(width, height)
                official = self._ratio_to_official(ratio)
                inputs['aspect_ratio'] = official
                inputs['megapixels'] = round((width * height) / (1024 * 1024), 2)
                if 'multiple' not in inputs:
                    inputs['multiple'] = 8
            except Exception:
                pass
            return True
        # AspectRatioNode（比例锁定工具）同类处理：改写比例字段
        if ct == 'AspectRatioNode':
            try:
                ratio = self._closest_ratio(width, height)
                inputs['aspect_ratio'] = ratio
            except Exception:
                pass
            return True
        # 普通节点（easy-use 等）：直接写 width/height
        inputs['width'] = width
        inputs['height'] = height
        return True

    async def _set_load_image(self, workflow, image_path):
        """设置工作流中的 LoadImage 节点。
        image_path 可以是单个路径字符串，也可以是路径列表（设置多个 LoadImage 节点）。
        ★ 关键：注入前先清空所有 LoadImage 节点的图片引用（含缓存文件名），
          避免多图工作流只传 1 张时，节点 2/3 残留上次的图导致 ComfyUI 用旧图跑出错误结果。
        """
        logger.info(f"[ComfyUI] _set_load_image 被调用: image_path={image_path!r}")
        # 第一步：清空所有图片加载节点的引用（防残留）
        try:
            all_nodes = self._find_all_load_image_nodes(workflow)
            for nid in all_nodes:
                node = workflow.get(nid)
                if isinstance(node, dict) and isinstance(node.get('inputs'), dict):
                    old = node['inputs'].get('image')
                    if old:
                        node['inputs']['image'] = ""
                        logger.info(f"[ComfyUI] 清理节点 {nid} 残留图片引用: {old!r} -> ''")
        except Exception as e:
            logger.warning(f"[ComfyUI] 清空图片节点引用失败: {e}")
        if isinstance(image_path, (list, tuple)):
            logger.info(f"[ComfyUI] 走多图分支, 共 {len(image_path)} 张")
            return await self._set_all_load_images(workflow, list(image_path))
        logger.info(f"[ComfyUI] 走单图分支")
        r = await self._set_single_load_image(workflow, image_path)
        logger.info(f"[ComfyUI] 单图注入结果: {r}")
        return r

    async def _set_single_load_image(self, workflow, image_path):
        """设置单个 LoadImage 节点，并删除工作流中其他多余的 LoadImage 节点"""
        nid = self._find_load_image_node(workflow)
        # 配置未指定时（工作流改名/新导入导致 __load_image_nodes__ 失配）→ 自动扫描实际节点兜底
        if not nid:
            try:
                cands = self._find_image_input_nodes(workflow) or self._find_all_load_image_nodes(workflow)
                nid = str(cands[0]) if cands else None
                if nid:
                    logger.info(f"[ComfyUI] 配置未指定图片节点，自动识别到 LoadImage {nid}")
            except Exception as e:
                logger.debug(f"[ComfyUI] 自动识别 LoadImage 失败: {e}")
        logger.info(f"[ComfyUI] _set_single_load_image: 找到节点={nid!r}, 图片={image_path!r}")
        if not nid:
            logger.error(f"[ComfyUI] 未找到 LoadImage 节点，无法注入图片！工作流节点数={len(workflow)}")
            return False
        # 统一通过 HTTP 上传到 ComfyUI input 目录（无论 local/remote 模式）
        name = await self._upload_image_remote(Path(image_path))
        logger.info(f"[ComfyUI] 上传结果 name={name!r}")
        if not name:
            logger.error(f"[ComfyUI] 上传图片失败: {image_path}")
            return False
        workflow[nid]['inputs']['image'] = name
        logger.info(f"[ComfyUI] LoadImage {nid} <- {name}")
        try:
            Path(image_path).unlink(missing_ok=True)
        except Exception as e:
            logger.debug(f"[ComfyUI] 删除临时文件失败: {e}")
        # 删除其他多余的 LoadImage 节点（多图工作流只传1张时，其余节点必须清掉，
        # 否则会残留上次的图/上次的缓存文件名，导致 ComfyUI 拿旧图跑出错误结果）
        all_nodes = self._find_all_load_image_nodes(workflow)
        nid_s = str(nid)
        extra = [x for x in all_nodes if str(x) != nid_s]   # 类型归一化比较，避免误删已注入节点
        if extra:
            # 先把多余节点的图片引用清空（防止级联删除失败时残留旧图）
            for x in extra:
                node = workflow.get(x)
                if isinstance(node, dict) and isinstance(node.get('inputs'), dict):
                    node['inputs']['image'] = ""
            self._remove_workflow_nodes(workflow, extra)
            logger.info(f"[ComfyUI] 单图模式，清理多余 LoadImage 节点: {extra}")
        return True

    def _remove_workflow_nodes(self, workflow, remove_ids):
        """智能级联删除：删除指定节点。若下游节点的所有输入都来自已删节点则也删除，
        否则按 ComfyUI bypass 语义**直通重连**：下游引用改接到被删节点自己的上游
        （输出序号 → 输入序号启发式），找不到对应上游才退化为 ""。
        v4.9.1: 旧版直接把幸存节点的引用填 ""——若被禁组里有模型链节点（如加速组的
        LoraLoaderModelOnly）而组外还有下游（rgthree LoRA 加载器），下游 model 变字符串，
        ComfyUI 报 'str' object has no attribute 'model'（v4.4.10 前组禁用是空操作所以从未暴露）。
        注意：工作流节点键可能是 int 或 str，统一按 str 比较，但删除时用真实键。"""
        to_remove = set(str(x) for x in remove_ids)
        if not to_remove:
            return

        def _pass_through(removed_nid, out_idx):
            """被删节点自己第 out_idx 个输出对应的上游链接（bypass 直通目标）"""
            node = workflow.get(removed_nid)
            if node is None:
                real = [k for k in workflow if str(k) == str(removed_nid)]
                node = workflow.get(real[0]) if real else None
            if not isinstance(node, dict):
                return None
            link_inputs = [(k, v) for k, v in (node.get('inputs') or {}).items()
                           if isinstance(v, list) and len(v) >= 1]
            if not link_inputs:
                return None
            k, v = link_inputs[out_idx] if out_idx < len(link_inputs) else link_inputs[-1]
            return list(v)

        while True:
            new_removals = set()
            relinked = False
            for nid, node in list(workflow.items()):
                if str(nid) in to_remove or not isinstance(node, dict):
                    continue
                inputs = node.get('inputs', {})
                if not isinstance(inputs, dict):
                    continue
                refs_deleted = [k for k, v in inputs.items()
                                if isinstance(v, list) and len(v) >= 1 and str(v[0]) in to_remove]
                if not refs_deleted:
                    continue
                # 检查该节点的所有输入是否都来自已删节点
                all_inputs = [v for v in inputs.values() if isinstance(v, list) and len(v) >= 1]
                all_from_deleted = all(str(v[0]) in to_remove for v in all_inputs)
                if all_from_deleted and all_inputs:
                    new_removals.add(str(nid))
                else:
                    # 还有活着的输入源 → 直通重连到被删节点的上游（bypass 语义），
                    # 直通目标若也指向已删节点，下一轮循环会继续接力或退化
                    for k in refs_deleted:
                        v = inputs[k]
                        up = _pass_through(str(v[0]), v[1] if len(v) > 1 else 0)
                        if up is not None:
                            inputs[k] = up
                            if str(up[0]) in to_remove:
                                relinked = True
                        else:
                            inputs[k] = ""
            if not new_removals and not relinked:
                break
            to_remove.update(new_removals)
        # 用真实键删除（兼容 int / str 键）
        removed_real = []
        for k in list(workflow.keys()):
            if str(k) in to_remove:
                workflow.pop(k, None)
                removed_real.append(k)
        if removed_real:
            logger.info(f"[ComfyUI] 级联删除: {removed_real}")

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
        # 用实际分配数（may < keep_count，若 image_paths 里有空项）判断，确保未分配的节点被清掉
        assigned = min(keep_count, len(nodes))
        if assigned < len(nodes):
            remove_ids = nodes[assigned:]
            # 先清空引用，防止级联删除失败时残留旧图
            for x in remove_ids:
                node = workflow.get(x)
                if isinstance(node, dict) and isinstance(node.get('inputs'), dict):
                    node['inputs']['image'] = ""
            self._remove_workflow_nodes(workflow, remove_ids)
            logger.info(f"[ComfyUI] 清理未使用的多余图片节点: {remove_ids}（工作流共{len(nodes)}个, 本次用{assigned}个）")
        return True

    def _ensure_png(self, path):
        """将非 PNG 图片转换为 PNG，确保 ComfyUI 能正常读取"""
        try:
            from PIL import Image
            with Image.open(str(path)) as img:
                # 验证图片完整性：尝试加载全部像素数据
                img.load()
                img.save(str(path), 'PNG')
            return True
        except Exception as e:
            logger.warning(f"[ComfyUI] 转 PNG 失败: {path}, {e}")
            # 尝试清理损坏文件
            try: Path(str(path)).unlink(missing_ok=True)
            except Exception as e: logger.debug(f"[ComfyUI] 操作提示发送失败: {e}")
            return False
