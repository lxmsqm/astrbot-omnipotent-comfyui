// webui.js — 由 webui.html 主 <script> 块抽出（v4.13.5 模块化）。
// 全局作用域不变：内联 onclick 依赖的全局函数都在这里；K2 内联块仍留在 html 中且排在本文件之后。
// ========================================================================
// G. 按钮点击波纹 Ripple
// ========================================================================
document.addEventListener('click', function(e){
  // 移动端不创建波纹 DOM 元素，减少卡顿
  if (window.innerWidth <= 768) return;
  const btn = e.target.closest('button, .btn, .theme-btn, .bn-item, .node-tag-btn, .res-preset-btn, .ratio-btn, .gallery-btn, .gallery-tab, .gallery-filter, .wf-cat-tag, .grm-cat-trigger');
  if (!btn) return;
  const rect = btn.getBoundingClientRect();
  const r = document.createElement('span');
  r.className = 'ripple' + (btn.classList.contains('btn-primary') ? ' purple' : '');
  const size = Math.max(rect.width, rect.height);
  r.style.cssText = `width:${size}px;height:${size}px;left:${e.clientX-rect.left-size/2}px;top:${e.clientY-rect.top-size/2}px;`;
  btn.style.position = 'relative'; btn.style.overflow = 'hidden';
  btn.appendChild(r);
  setTimeout(() => r.remove(), 700);
});

// ========================================================================
// 工具函数
// ========================================================================

// ========================================================================
// 图片缓存管理器（Cache API）— 外网限速环境下避免重复下载图片
// ========================================================================
const IMG_CACHE_NAME = 'comfyui-img-cache';

/** 获取图片 URL：优先走 Cache API，命中返回 blob URL；未命中 fetch 后写缓存；失败回退原始 URL */
async function getCachedImageURL(url) {
  if (!url) return '';
  // 仅缓存 http(s) 与同源 /api/ 图片；data: 等无需缓存
  if (!/^https?:\/\//.test(url) && !url.startsWith('/api/')) return url;
  try {
    if (!('caches' in window)) return url;
    const cache = await caches.open(IMG_CACHE_NAME);
    const hit = await cache.match(url);
    if (hit) {
      const blob = await hit.blob();
      return URL.createObjectURL(blob);
    }
    const resp = await fetch(url);
    if (!resp.ok) return url;
    try {
      await cache.put(url, resp.clone());
    } catch (e) { /* 跨域/不支持则跳过写缓存 */ }
    const blob = await resp.blob();
    return URL.createObjectURL(blob);
  } catch (e) {
    return url; // 失败回退原始 URL（浏览器 HTTP 缓存仍生效）
  }
}

/** 用缓存方式给 <img> 设置 src（渐进替换，不阻塞渲染）
 *  ★ 关键：先用原始 URL 立刻显示（浏览器直接加载，跨域图片无 CORS 限制），
 *    再异步尝试用 Cache API 的结果替换。避免跨域 fetch 卡住导致图片长时间空白。 */
async function setImgWithCache(img, url) {
  if (!img || !url) return;
  // 第一步：立即用原始 URL 渲染（保证可见，不等 fetch）
  try {
    img.src = url;
  } catch (e) { /* noop */ }
  // 第二步：后台尝试缓存版本（成功且有 blob 才替换；失败保持原图不动）
  try {
    const cached = await getCachedImageURL(url);
    if (img.isConnected && cached && cached !== url) img.src = cached;
  } catch (e) { /* noop */ }
}

/** 一键清理本地图片缓存：Cache API + localStorage 中的图片信息 */
async function clearImageCache() {
  try {
    if ('caches' in window) {
      const keys = await caches.keys();
      await Promise.all(keys.filter((k) => k.startsWith('comfyui-')).map((k) => caches.delete(k)));
    }
  } catch (e) { /* noop */ }
  // 清理 localStorage 中的图片相关信息缓存
  const imgKeys = ['custom_bg', 'bg_blur', 'bg_brightness', 'gallery_view', 'sb_cat_collapsed'];
  imgKeys.forEach((k) => { try { localStorage.removeItem(k); } catch (e) {} });
}

/** HTML 转义，防止 XSS */
function esc(str) {
  if (str == null) return '';
  const d = document.createElement('div');
  d.textContent = String(str);
  return d.innerHTML;
}

/** JS 内联字符串转义（用于 onclick 传参）：转义反斜杠和单引号，防止 \N → N 丢失反斜杠 */
function escJsStr(str) {
  if (str == null) return '';
  return String(str)
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'");
}

/** 显示模态框 */
function showModal(id) {
  const el = document.getElementById(id);
  if (el) {
    el.style.display = 'flex';
    el.classList.add('active');
  }
}
/** 隐藏模态框 */
function hideModal(id) {
  const el = document.getElementById(id);
  if (el) {
    el.style.display = 'none';
    el.classList.remove('active');
  }
}

/**
 * 安全 fetch — 统一错误处理和 toast 提示
 * 所有 API 调用都应使用此函数，而非裸 fetch
 */
async function apiFetch(url, options = {}, silent = false) {
  try {
    const isFormData = options.body instanceof FormData;
    // 添加超时（AbortController），防止后端死锁导致永久挂起
    // v4.3.1: 支持调用方用 options.timeoutMs 覆盖（云端同步拉 28MB 缓存需 10 分钟，
    // 旧版写死 30s 导致同步被浏览器掐断、前端报"未知错误"而后端实际成功）
    const timeoutMs = Number(options.timeoutMs) || 30000;
    const { timeoutMs: _omit, ...fetchOpts } = options;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    const r = await fetch(url, {
      ...(isFormData ? {} : { headers: { 'Content-Type': 'application/json' } }),
      signal: controller.signal,
      ...fetchOpts,
    });
    clearTimeout(timeoutId);
    const text = await r.text();
    let d;
    try { d = JSON.parse(text); } catch (e) {
      if (!silent) toast(`API 返回异常 (${r.status}): ${text.slice(0, 100)}`, 'error');
      console.error('apiFetch parse failed:', url, r.status, text.slice(0, 200));
      return null;
    }
    // 只检查明确包含 ok 字段的响应（POST 类端点返回 {ok:true}）
    // 纯 JSON 数组/对象（GET 类端点）直接视为成功
    if (d && d.ok === false && !silent) {
      toast(d.error || '操作失败', 'error');
    }
    return d;
  } catch (e) {
    if (e.name === 'AbortError') {
      if (!silent) toast('请求超时，后端无响应', 'error');
    } else if (!silent) {
      toast(`网络错误: ${  e.message}`, 'error');
    }
    console.error('apiFetch failed:', url, e);
    return null;
  }
}

/**
 * 安全 localStorage 操作 — 自动处理 QuotaExceededError
 */
const Storage = {
  set(key, value) {
    try {
      const serialized = typeof value === 'string' ? value : JSON.stringify(value);
      localStorage.setItem(key, serialized);
      return true;
    } catch (e) {
      if (e.name === 'QuotaExceededError') {
        console.warn('Storage quota exceeded:', key);
      }
      return false;
    }
  },
  get(key, defaultValue = null) {
    try {
      const value = localStorage.getItem(key);
      if (value === null) return defaultValue;
      try { return JSON.parse(value); }
      catch { return value; }
    } catch { return defaultValue; }
  },
  remove(key) {
    try { localStorage.removeItem(key); } catch {}
  },
};

/**
 * DOM 缓存 — 避免重复 document.getElementById 查询
 */
function $id(id) {
  return document.getElementById(id);
}

/**
 * 全局事件委托系统 — 替代内联 onclick
 * HTML 中写 data-action="functionName" 即可自动绑定
 * 例如：<button data-action="toggleSidebar">...</button>
 * 支持 data-args 传递 JSON 参数
 */
(function initActionDelegation() {
  document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-action]');
    if (!el) return;
    const action = el.dataset.action;
    const rawArgs = el.dataset.args;
    const args = rawArgs ? JSON.parse(rawArgs) : [];

    // 特殊处理 stopPropagation（需要事件对象）
    if (action === 'stopPropagation') {
      e.stopPropagation();
      return;
    }

    // 特殊映射：data-action 名 → 实际函数（函数名与 action 不一致时的映射）
    // ⚠️ 核心规则：所有带参数（data-wf / data-wfname / data-role / data-elementid 等）的 action
    // 必须在此声明映射，从 el.dataset 读取参数。因全局函数通过 window[name] 调用时不传参！
    // ⚠️ 优先级：actionMap 高于 window[name]，确保参数传递正确的版本被执行
    const stripQuotes = (s) => (s && typeof s === 'string') ? s.replace(/^['"]|['"]$/g, '') : '';
    const actionMap = {
      'setThemeLight': function() { setTheme('light'); },
      'setThemeDark': function() { setTheme('dark'); },
      'toggleMediaPanel': function() { toggleMediaPanel(); },
      'toggleTheme': function() {
        setTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
        if (document.documentElement.style.getPropertyValue('--custom-bg')) {
          toast('已切换昼夜。当前使用自定义背景，主题横幅不随切换变化；背景设置→重置可恢复', 'info', 4000);
        }
      },
      'setRes480p': function() { setResPreset('480p', el); },
      'setRes720p': function() { setResPreset('720p', el); },
      'setRes960p': function() { setResPreset('960p', el); },
      'setRes1080p': function() { setResPreset('1080p', el); },
      'setRes2K': function() { setResPreset('2K', el); },
      'setRes4K': function() { setResPreset('4K', el); },
      'setRatio': function() { setRatio(el.dataset.r, el); },
      'setBgCropRatio': function() { setBgCropRatio(el.dataset.r); },
      'filterWfByCat': function() { filterWfByCat(el.dataset.cat, el); },
      'pickImage0': function() { const el = document.getElementById('upload_image_input_0'); if (el) el.click(); },
      'pickImage1': function() { const el = document.getElementById('upload_image_input_1'); if (el) el.click(); },
      'pickImage2': function() { const el = document.getElementById('upload_image_input_2'); if (el) el.click(); },
      'pickImage3': function() { const el = document.getElementById('upload_image_input_3'); if (el) el.click(); },
      'pickImage4': function() { const el = document.getElementById('upload_image_input_4'); if (el) el.click(); },
      'pickImage5': function() { const el = document.getElementById('upload_image_input_5'); if (el) el.click(); },
      'pickImage6': function() { const el = document.getElementById('upload_image_input_6'); if (el) el.click(); },
      'pickImage7': function() { const el = document.getElementById('upload_image_input_7'); if (el) el.click(); },
      'pickImage8': function() { const el = document.getElementById('upload_image_input_8'); if (el) el.click(); },
      'pickImage9': function() { const el = document.getElementById('upload_image_input_9'); if (el) el.click(); },
      'pickWfFile': function() { document.getElementById('wf_add_input').click(); },
      'refreshAll': function() { loadWorkflows(); loadParams(); },
      'resetAll': function() { resetAll(); },
      'clearLocalCache': function() { clearLocalCache(); },
      'saveOutputDir': function() { saveOutputDir(); },
      'saveComfyUrl': function() { saveComfyUrl(); },
      'clearAllNodeRoles': function() { clearAllNodeRoles(); },
      'closeBgCrop': function() { closeBgCrop(); },
      'toggleGroupTag': function() { toggleGroupTag(el.dataset.groupid, el); },
      'toggleSidebarCat': function() { toggleSidebarCat(el.dataset.cat, el); },
      'toggleGalleryDropdown': function() { toggleGalleryDropdown(); },
      'toggleAcceptData': function() { toggleAcceptData(); },
      'toggleGalleryBatchDel': function() { toggleGalleryBatchDel(); },
      'pickBgFile': function() { document.getElementById('bg_file_input').click(); },
      // 🔧 以下为参数传递修复：data-action="functionName" 不带 data-args，需从 el.dataset 取值
      'backToWorkflows': function() { if (typeof switchPage === 'function') switchPage('wf'); },
      'switchWF': function() { switchWF(el.dataset.wf || ''); },
      'deleteWf': function() { deleteWf(stripQuotes(el.dataset.wfname)); },
      'clearNodeRole': function() { clearNodeRole(stripQuotes(el.dataset.role)); },
      'showNodeDetail': function() { showNodeDetail(stripQuotes(el.dataset.nodeid)); },
      'setPromptNode': function() { setPromptNode(stripQuotes(el.dataset.nodeid)); },
      'setNegativeNode': function() { setNegativeNode(stripQuotes(el.dataset.nodeid)); },
      'setResNode': function() { setResNode(stripQuotes(el.dataset.nodeid)); },
      'setExpandedTextNode': function() { setExpandedTextNode(stripQuotes(el.dataset.nodeid)); },
      'toggleDisableNode': function() { e.stopPropagation(); toggleDisableNode(stripQuotes(el.dataset.nodeid)); },
      'pickElement': function() {
        const rawId = el.dataset.elementid || '';
        const cleanId = rawId.replace(/^['"]|['"]|\)\.click\(.*$/g, '');
        const target = document.getElementById(cleanId);
        if (target) target.click();
      },
      'showGrimoire': function() { showGrimoire(); },
      'closeSettings': function() { if (document.body.classList.contains('page-settings')) { switchPage('wf'); } else { closeSettingsModal(); } },
      'toggleSettings': function() { toggleSettings(); },
    };

    if (actionMap[action]) {
      e.preventDefault();
      actionMap[action]();
      return;
    }

    // 回退到全局函数（仅无参函数命中此分支）
    if (typeof window[action] === 'function') {
      e.preventDefault();
      window[action].apply(null, args);
      return;
    }
  });
})();

// ========================================================================
// 重置所有用户设定
// ========================================================================
function resetAll() {
  if (!confirm('⚠️ 确定要重置所有用户设定吗？\n\n这将清空所有数据：\n- 工作流分类、别名、节点禁用状态\n- 群/用户绑定\n- 工作流/输出/Input 目录\n- ComfyUI 连接地址\n- 局域网/IPv6 设置\n- 质量/比例偏好\n- 所有本地配置\n\n⚠️ 操作不可撤销！刷新页面后生效。')) return;
  if (!confirm('再次确认：此操作不可撤销！')) return;
  apiFetch('/api/reset', { method: 'POST' }).then(r => {
    if (r && r.ok) {
      alert('✅ ' + (r.message || '已重置，请刷新页面'));
      location.reload();
    } else {
      alert('❌ 重置失败: ' + (r?.error || '未知错误'));
    }
  }).catch(e => {
    alert('❌ 重置失败: ' + e.message);
  });
}

// ========================================================================
// 一键清理本地缓存（Cache API 图片缓存 + localStorage 图片信息）
// ========================================================================
async function clearLocalCache() {
  if (!confirm('确定要一键清理本地缓存吗？\n\n将清空网页缓存过的所有图片信息：\n- 工作流预览图\n- 画廊图片\n- 魔导书画师/角色标签图\n\n清理后首次加载需重新下载图片（外网限速时可能较慢）。')) return;
  await clearImageCache();
  toast('✅ 本地缓存已清理，正在刷新...');
  setTimeout(() => location.reload(), 600);
}

// ========================================================================
// 图片灯箱
// ========================================================================
let _lightboxZoom = 1;
const _lightboxState = { isDragging: false, startX: 0, startY: 0, imgX: 0, imgY: 0 };

function openLightbox(src, title) {
  const lb = $id('lightbox');
  const img = $id('lightbox_img');
  if (!lb || !img) return;
  img.src = src;
  _lightboxZoom = 1;
  img.style.transform = 'scale(1)';
  img.style.left = '0px';
  img.style.top = '0px';
  _lightboxState.imgX = 0;
  _lightboxState.imgY = 0;
  $id('lightbox_info').textContent = title || '';
  lb.classList.add('active');
  document.body.style.overflow = 'hidden';
}

function closeLightbox() {
  const lb = $id('lightbox');
  if (!lb) return;
  lb.classList.remove('active');
  document.body.style.overflow = '';
}

// 缩放控件（DOM 就绪后绑定）
(function() {
  function initLightboxControls() {
    const zi = $id('lightbox_zoom_in');
    const zo = $id('lightbox_zoom_out');
    const zr = $id('lightbox_zoom_reset');
    if (!zi || !zo || !zr) { setTimeout(initLightboxControls, 100); return; }
    zi.addEventListener('click', () => zoomLightbox(0.25));
    zo.addEventListener('click', () => zoomLightbox(-0.25));
    zr.addEventListener('click', () => {
      _lightboxZoom = 1;
      _lightboxState.imgX = 0;
      _lightboxState.imgY = 0;
      const img = $id('lightbox_img');
      img.style.transform = 'scale(1)';
      img.style.left = '0px';
      img.style.top = '0px';
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initLightboxControls);
  } else {
    initLightboxControls();
  }
})();

// 自动绑定 data-lightbox 元素// 自动绑定 data-lightbox 元素
(function initLightboxLinks() {
  document.addEventListener('click', (e) => {
    const img = e.target.closest('[data-lightbox]');
    if (img) {
      e.preventDefault();
      const src = img.dataset.lightbox || img.src || img.href;
      const title = img.dataset.title || img.alt || '';
      if (src) openLightbox(src, title);
    }
  });
})();

// ========================================================================
// 全局状态变量
// ========================================================================
// 背景裁剪状态
let _bgRawData = '', _bgCropRatio = 'free';
const _bgCropState = { x:0, y:0, w:0, h:0, imgW:0, imgH:0, dispW:0, dispH:0 };

// ========================================================================
// 主题初始化
// ========================================================================
(function initTheme() {
  const theme = Storage.get('theme', 'dark');
  document.documentElement.setAttribute('data-theme', theme);
  document.querySelectorAll('.theme-btn').forEach((b) => {
    b.classList.toggle('active', (b.getAttribute('onclick') || '').indexOf(theme) > -1);
  });
  // 加载背景设置（优先从后端 /api/bg/image，后端没有则 fallback localStorage）
  const blur = Storage.get('bg_blur');
  const bright = Storage.get('bg_brightness');
  // 先检查后端是否有背景图片
  fetch('/api/bg/image', { method: 'HEAD' }).then((r) => {
    if (r.ok) {
      document.documentElement.style.setProperty('--custom-bg', 'url(/api/bg/image)');
      if (blur) document.documentElement.style.setProperty('--bg-blur', `${blur}px`);
      if (bright) document.documentElement.style.setProperty('--bg-brightness', bright);
    } else {
      // fallback localStorage
      const bg = Storage.get('custom_bg');
      if (bg) {
        _bgRawData = bg;
        document.documentElement.style.setProperty('--custom-bg', `url(${bg})`);
        if (blur) document.documentElement.style.setProperty('--bg-blur', `${blur}px`);
        if (bright) document.documentElement.style.setProperty('--bg-brightness', bright);
      }
    }
  });
  // 加载透明度
  const op = Storage.get('ui_opacity');
  if (op) {
    const slider = $id('opacity_slider');
    if (slider) slider.value = op;
    document.documentElement.style.setProperty('--ui-opacity', op / 100);
  }
  // 初始化设备检测
  document.documentElement.style.setProperty('--bottom-nav-h', window.innerWidth <= 768 ? '60px' : '0px');
})();

// ========================================================================
// 移动端侧栏开关
// ========================================================================
function toggleSidebar() {
  const sb = $id('sidebar');
  const bd = $id('sidebar_backdrop');
  const isOpen = sb.classList.contains('open');
  if (!isOpen) {
    sb.classList.add('open');
    bd.classList.add('active');
    document.body.style.overflow = 'hidden';
    document.body.style.position = 'fixed';
    document.body.style.width = '100%';
  } else {
    sb.classList.remove('open');
    bd.classList.remove('active');
    document.body.style.overflow = '';
    document.body.style.position = '';
    document.body.style.width = '';
  }
}
function closeSidebar() {
  const sb = $id('sidebar');
  const bd = $id('sidebar_backdrop');
  sb.classList.remove('open');
  bd.classList.remove('active');
  document.body.style.overflow = '';
  document.body.style.position = '';
  document.body.style.width = '';
}

// ========================================================================
// 移动端滚动定位
// ========================================================================
function scrollToPrepPanel() {
  const el = document.getElementById('prep_panel');
  if (el) el.scrollIntoView({behavior:'smooth',block:'start'});
  if (window.innerWidth <= 768) closeSidebar();
}
function scrollToNodes() {
  const el = document.getElementById('search_nodes');
  if (el) el.scrollIntoView({behavior:'smooth',block:'start'});
  if (window.innerWidth <= 768) closeSidebar();
}

// ========================================================================
// 窗口 resize 监听
// ========================================================================
window.addEventListener('resize', () => {
  document.documentElement.style.setProperty('--bottom-nav-h', window.innerWidth <= 768 ? '60px' : '0px');
  if (window.innerWidth > 768) {
    const sb = document.getElementById('sidebar');
    if (sb) sb.classList.remove('open');
    const backdrop = document.getElementById('sidebar_backdrop');
    if (backdrop) backdrop.classList.remove('active');
    document.body.style.overflow = '';
    document.body.style.position = '';
    document.body.style.width = '';
  }
});

// ========================================================================
// 主题
// ========================================================================
function setTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  Storage.set('theme', t);
  const tIcon = document.getElementById('theme_toggle_icon');
  if (tIcon) tIcon.setAttribute('href', t === 'dark' ? '#icon-sun' : '#icon-moon');
  // 更新 meta theme-color
  const mc = document.querySelector('meta[name="theme-color"]');
  if (mc) mc.setAttribute('content', t === 'dark' ? '#120c0c' : '#faf5ec');
}

// ========================================================================
// 透明度（界面透明度，不涉及背景亮度）
// ========================================================================
function changeOpacity(v) {
  const p = v / 100;
  document.documentElement.style.setProperty('--ui-opacity', p);
  // 重置背景亮度滤镜，杜绝透过透明界面看到亮度调整后的背景
  document.documentElement.style.setProperty('--bg-brightness', '100');
  Storage.set('ui_opacity', v);
}

// ========================================================================
// Toast
// ========================================================================
function toast(msg, type = 'success', duration = 3000) {
  const container = document.getElementById('toast_container');
  if (!container) return;
  // 清洗 emoji 前缀（toast 已有 SVG 图标，不需要文本再带一遍）
  msg = msg.replace(/^[✅❌⚠️ℹ️🎉✨🔔]\s*/, '');

  const iconMap = { success: '#icon-check-circle', error: '#icon-alert-circle', info: '#icon-info', warning: '#icon-alert-triangle' };
  const el = document.createElement('div');
  el.className = `toast-item ${type}`;
  el.innerHTML = `<svg class="toast-icon" aria-hidden="true"><use href="${iconMap[type] || '#icon-info'}"/></svg>
    <span>${msg}</span>
    <button class="toast-close" aria-label="关闭"><svg class="icon-sm" aria-hidden="true"><use href="#icon-x"/></svg></button>`;

  // 点击关闭
  el.querySelector('.toast-close').addEventListener('click', (e) => {
    e.stopPropagation();
    dismiss(el);
  });
  // 点击 toast 本身也关闭
  el.addEventListener('click', () => dismiss(el));

  container.appendChild(el);
  // 触发入场动画
  requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('show')));

  // 自动消失
  const timer = setTimeout(() => dismiss(el), duration);
  el._dismissTimer = timer;
}

function dismiss(el) {
  if (el.classList.contains('removing')) return;
  clearTimeout(el._dismissTimer);
  el.classList.remove('show');
  el.classList.add('removing');
  setTimeout(() => { if (el.parentNode) el.parentNode.removeChild(el); }, 300);
}

function setLoading(id, l) {
  const b = document.getElementById(id);
  if (!b) return;
  b.disabled = l;
  b.classList.toggle('btn-loading', l);
}

// ========================================================================
// 背景设置
// ========================================================================
function showBgModal() { document.getElementById('bg_modal').classList.add('active'); loadBgSettings(); }
function closeBgModal() { document.getElementById('bg_modal').classList.remove('active'); }
function loadBgSettings() {
  const blur = localStorage.getItem('bg_blur') || '0';
  const bright = localStorage.getItem('bg_brightness') || '100';
  document.getElementById('bg_blur_slider').value = blur;
  document.getElementById('bg_brightness_slider').value = bright;
  document.getElementById('bg_blur_val').textContent = blur;
  document.getElementById('bg_brightness_val').textContent = bright;
  // 内存变量优先，localStorage 兜底
  const bg = _bgRawData || localStorage.getItem('custom_bg');
  const preview = document.getElementById('bg_preview');
  if (bg) {
    _bgRawData = bg;
    preview.style.backgroundImage = `url(${  bg  })`;
    document.getElementById('bg_preview_text').textContent = '';
    document.getElementById('bg_ratio_row').style.display = 'flex';
    document.getElementById('bg_crop_btn').style.display = '';
  } else {
    preview.style.backgroundImage = '';
    document.getElementById('bg_preview_text').textContent = '点击或拖拽上传图片';
    document.getElementById('bg_ratio_row').style.display = 'none';
    document.getElementById('bg_crop_btn').style.display = 'none';
  }
}
function updateBgPreview() {
  const blur = document.getElementById('bg_blur_slider').value;
  const bright = document.getElementById('bg_brightness_slider').value;
  document.getElementById('bg_blur_val').textContent = blur;
  document.getElementById('bg_brightness_val').textContent = bright;
}
function handleBgFile(e) {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function(ev) {
    _bgRawData = ev.target.result;
    _setBgPreview(ev.target.result);
    Storage.set('custom_bg', ev.target.result);
    document.getElementById('bg_ratio_row').style.display = 'flex';
    document.getElementById('bg_crop_btn').style.display = '';
    setBgCropRatio('free');
  };
  reader.readAsDataURL(file);
}

function _setBgPreview(dataUrl) {
  const preview = document.getElementById('bg_preview');
  preview.style.backgroundImage = `url(${  dataUrl  })`;
  document.getElementById('bg_preview_text').textContent = '';
}
async function saveBg() {
  const blur = document.getElementById('bg_blur_slider').value;
  const bright = document.getElementById('bg_brightness_slider').value;
  // 内存变量优先，localStorage 兜底
  const bg = _bgRawData || localStorage.getItem('custom_bg');
  if (bg) {
    // 保存到后端（存为 PNG 文件）
    await apiFetch('/api/bg/save', {
      method: 'POST',
      body: JSON.stringify({ bg, blur, brightness: bright })
    }, true);
    // 先用 base64 直接设置，保证立即生效（后端 URL 用于刷新后持久化）
    document.documentElement.style.setProperty('--custom-bg', `url(${bg})`);
    try { localStorage.setItem('custom_bg', bg); } catch(e) {}
  } else {
    document.documentElement.style.removeProperty('--custom-bg');
    await apiFetch('/api/bg/save', {
      method: 'POST',
      body: JSON.stringify({ bg: '', blur, brightness: bright })
    }, true);
  }
  document.documentElement.style.setProperty('--bg-blur', `${blur  }px`);
  document.documentElement.style.setProperty('--bg-brightness', bright);
  closeBgModal();
  toast('✅ 背景已应用');
}
function resetBg() {
  localStorage.removeItem('custom_bg');
  localStorage.removeItem('bg_blur');
  localStorage.removeItem('bg_brightness');
  document.documentElement.style.removeProperty('--custom-bg');
  document.documentElement.style.removeProperty('--bg-blur');
  document.documentElement.style.removeProperty('--bg-brightness');
  document.getElementById('bg_blur_slider').value = 0;
  document.getElementById('bg_brightness_slider').value = 100;
  document.getElementById('bg_blur_val').textContent = '0';
  document.getElementById('bg_brightness_val').textContent = '100';
  const preview = document.getElementById('bg_preview');
  preview.style.backgroundImage = '';
  document.getElementById('bg_preview_text').textContent = '点击或拖拽上传图片';
  document.getElementById('bg_file_input').value = '';
  document.getElementById('bg_ratio_row').style.display = 'none';
  document.getElementById('bg_crop_btn').style.display = 'none';
  _bgRawData = '';
  apiFetch('/api/bg/save', {
    method: 'POST',
    body: JSON.stringify({ bg: '', blur: 0, brightness: 100 })
  }, true).catch(() => {});
  toast('✅ 已重置，主题横幅已恢复');
}

// ========================================================================
// 背景裁剪（状态变量已在前面声明）
// ========================================================================

function setBgCropRatio(ratio) {
  _bgCropRatio = ratio;
  document.querySelectorAll('#bg_ratio_row .bg-crop-btn').forEach((b) =>{
    b.classList.toggle('active', b.dataset.ratio === ratio);
  });
  if (ratio !== 'free' && _bgCropState.dispW > 0) {
    const parts = ratio.split(':');
    const r = parseInt(parts[0]) / parseInt(parts[1]);
    const s = _bgCropState;
    let h = s.dispH, w = h * r;
    if (w > s.dispW) { w = s.dispW; h = w / r; }
    if (h > s.dispH) { h = s.dispH; w = h * r; }
    s.x = Math.max(0, (s.dispW - w) / 2);
    s.y = Math.max(0, (s.dispH - h) / 2);
    s.w = Math.min(w, s.dispW);
    s.h = Math.min(h, s.dispH);
    _syncCropBox();
  }
}

function openBgCrop() {
  if (!_bgRawData) return;
  const img = document.getElementById('bg_crop_img');
  img.onload = function() {
    const stage = document.getElementById('bg_crop_stage');
    const w = stage.clientWidth;
    const h = Math.min(w * img.naturalHeight / img.naturalWidth, window.innerHeight * 0.55);
    stage.style.height = `${h  }px`;
    _bgCropState.imgW = img.naturalWidth;
    _bgCropState.imgH = img.naturalHeight;
    _bgCropState.dispW = w;
    _bgCropState.dispH = h;
    const m = 20;
    _bgCropState.x = m; _bgCropState.y = m;
    _bgCropState.w = Math.max(40, w - m * 2);
    _bgCropState.h = Math.max(40, h - m * 2);
    setBgCropRatio(_bgCropRatio);
    _syncCropBox();
  };
  img.src = _bgRawData;
  document.getElementById('bg_crop_modal').classList.add('active');
}

function closeBgCrop() {
  document.getElementById('bg_crop_modal').classList.remove('active');
}

function _syncCropBox() {
  const box = document.getElementById('bg_crop_box');
  const s = _bgCropState;
  box.style.left = `${s.x  }px`;
  box.style.top = `${s.y  }px`;
  box.style.width = `${s.w  }px`;
  box.style.height = `${s.h  }px`;
}

function applyBgCrop() {
  const s = _bgCropState;
  const sx = s.imgW / s.dispW, sy = s.imgH / s.dispH;
  _cropImage(_bgRawData,
    Math.round(s.x * sx), Math.round(s.y * sy),
    Math.round(s.w * sx), Math.round(s.h * sy),
    (cropped) => {
      if (!cropped) { toast('❌ 裁剪失败'); return; }
      _bgRawData = cropped;
      _setBgPreview(cropped);
      try { localStorage.setItem('custom_bg', cropped); } catch(e) {}
      closeBgCrop();
      toast('✅ 裁剪完成');
    });
}

function _cropImage(src, x, y, w, h, cb) {
  const img = new Image();
  img.onload = function() {
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const ctx = c.getContext('2d');
    ctx.drawImage(img, x, y, w, h, 0, 0, w, h);
    let q = 0.85;
    let data = c.toDataURL('image/jpeg', q);
    while (data.length > 500000 && q > 0.2) {
      q -= 0.1;
      data = c.toDataURL('image/jpeg', q);
    }
    cb(data);
  };
  img.onerror = function() { cb(null); };
  img.src = src;
}

// ========================================================================
// 裁剪框拖拽交互（Pointer Events）
// ========================================================================
(function() {
  const box = document.getElementById('bg_crop_box');
  const stage = document.getElementById('bg_crop_stage');
  let activeHandle = null, startX, startY, startState, dragging = false;

  function getPos(e) {
    const rect = stage.getBoundingClientRect();
    const x = (e.clientX || (e.touches && e.touches[0].clientX)) - rect.left;
    const y = (e.clientY || (e.touches && e.touches[0].clientY)) - rect.top;
    return {
      x: Math.max(0, Math.min(x, _bgCropState.dispW)),
      y: Math.max(0, Math.min(y, _bgCropState.dispH))
    };
  }

  function clamp() {
    const s = _bgCropState;
    if (s.x < 0) { s.x = 0; }
    if (s.y < 0) { s.y = 0; }
    if (s.x + s.w > s.dispW) { s.w = s.dispW - s.x; }
    if (s.y + s.h > s.dispH) { s.h = s.dispH - s.y; }
    s.w = Math.max(40, s.w);
    s.h = Math.max(40, s.h);
    _syncCropBox();
  }

  function onDown(e) {
    if (!document.getElementById('bg_crop_modal').classList.contains('active')) return;
    const t = e.target;
    if (t.classList.contains('bg-crop-handle')) {
      activeHandle = t;
    } else if (t === box || box.contains(t)) {
      activeHandle = 'move';
    } else return;
    const p = getPos(e);
    startX = p.x; startY = p.y;
    startState = JSON.parse(JSON.stringify(_bgCropState));
    dragging = true;
    stage.setPointerCapture(e.pointerId);
    e.preventDefault();
  }

  function onMove(e) {
    if (!dragging) return;
    e.preventDefault();
    const p = getPos(e);
    const dx = p.x - startX, dy = p.y - startY;
    const s = JSON.parse(JSON.stringify(startState));
    const ratio = _bgCropRatio !== 'free'
      ? parseInt(_bgCropRatio.split(':')[0]) / parseInt(_bgCropRatio.split(':')[1])
      : null;

    if (activeHandle === 'move') {
      s.x = startState.x + dx;
      s.y = startState.y + dy;
    } else {
      const cls = activeHandle.classList;
      // 统一处理四个角：固定对角方向拉伸
      if (cls.contains('nw')) {
        s.x = startState.x + dx;
        s.y = startState.y + dy;
        s.w = (startState.x + startState.w) - s.x;
        s.h = (startState.y + startState.h) - s.y;
        if (ratio) { s.h = s.w / ratio; s.y = (startState.y + startState.h) - s.h; }
      } else if (cls.contains('ne')) {
        s.y = startState.y + dy;
        s.w = startState.w + dx;
        s.h = (startState.y + startState.h) - s.y;
        if (ratio) { s.h = s.w / ratio; s.y = (startState.y + startState.h) - s.h; }
      } else if (cls.contains('sw')) {
        s.x = startState.x + dx;
        s.w = (startState.x + startState.w) - s.x;
        s.h = startState.h + dy;
        if (ratio) { s.w = s.h * ratio; s.x = startState.x; }
      } else if (cls.contains('se')) {
        s.w = startState.w + dx;
        s.h = startState.h + dy;
        if (ratio) { s.h = s.w / ratio; }
      }
    }

    // 应用并钳制
    _bgCropState.x = s.x; _bgCropState.y = s.y;
    _bgCropState.w = s.w; _bgCropState.h = s.h;
    clamp();
  }

  function onUp() { dragging = false; activeHandle = null; }

  stage.addEventListener('pointerdown', onDown);
  stage.addEventListener('pointermove', onMove);
  stage.addEventListener('pointerup', onUp);
  stage.addEventListener('pointercancel', onUp);
})();

// ========================================================================
// 配置加载
// ========================================================================
let allParamsData = null, promptNodeId = '', resNodeId = '', loadImageNodeIds = ['', '', '', '', '', '', '', '', '', ''], negativeNodeId = '', expandedTextNodeId = '';
let _slotOrderAuto = false; // 图片槽位顺序是否来自自动探测（true 时按标题编号自动对号入座；手动排序保存后转 false）
let loadAudioNodeIds = ['', '', '', '', '', '', '', '', '', ''];
let loadVideoNodeIds = ['', '', '', '', '', '', '', '', '', ''];
let emptyLoadNodes = [];  // 已废弃「不使用」勾选：恒为空，仅保留 saveParams 空值覆盖旧配置的自愈通道
let promptInputKey = '', negativeInputKey = '';
let modelLists = {}, disabledGroups = {}, groupsSource = '', isBound = false, bindTarget = '', allWorkflows = [];
let disabledNodes = [];
// 显式取消标记：用户主动点「取消指定」时置位，saveParams 才允许发送空值覆盖后端
// （避免切换工作流时未初始化的空值误清后端已保存的节点角色）
let _explicitClear = {};
let officialResNodes = [], officialDurationNodes = [];
let _officialResSig = ''; // 官方分辨率面板已构建的节点签名（工作流/节点集变化才重建，避免重渲染打断编辑）
let loraNodesConfig = {};     // 各节点的 Lora 配置 {nodeId: [{on, lora_name, strength_model, strength_clip, trigger_words_selected?}, ...]}
let availableLoras = [];      // 从 ComfyUI 获取的可用 Lora 列表
let loraMetadata = {};        // Lora 元数据缓存 {lora_path: {trigger_words, preview_url, model_name, tags, file_path}}

// 侧栏分类折叠状态
var _sidebarCatCollapsed = {};
var _catOrder = []; // 分类排序
// 拖拽状态
let _dragState = { dragging: false, el: null, startY: 0, origIdx: -1 };
function _loadSidebarCatState() {
  try { _sidebarCatCollapsed = JSON.parse(localStorage.getItem('sb_cat_collapsed') || '{}'); } catch(e) { _sidebarCatCollapsed = {}; }
}
function _saveSidebarCatState() {
  try { localStorage.setItem('sb_cat_collapsed', JSON.stringify(_sidebarCatCollapsed)); } catch(e) {}
}

function getCurrentWF() {
  const e = document.querySelector('.wf-item.active');
  return e ? (e.dataset.wf || e.querySelector('.wf-name').textContent.trim()) : '';
}

function loadCfg() {
  // 初始化生成数量（供 webuiGenerate 使用，避免 select 实时读取失效）
  try {
    const _gc = document.getElementById('gen_count');
    window._genCount = _gc ? (parseInt(_gc.value, 10) || 1) : 1;
  } catch (e) { window._genCount = 1; }
  apiFetch('/api/config', {}, true).then((c) =>{
    if (!c) return;
    $id('cfg_comfyui_url').value = c.comfyui_url || '127.0.0.1:8188';
    $id('cfg_workflow_dir').value = c.workflow_dir || '';
    $id('cfg_output_dir').value = c.output_dir || '';
    const sp = document.getElementById('cfg_show_prompt');
    if (sp) sp.checked = c.show_prompt_on_image === true;
    const ae = document.getElementById('cfg_anima_enhance');
    if (ae) ae.checked = c.anima_spec_enhance !== false;   // 默认开
    const an = document.getElementById('cfg_anima_nsfw');
    if (an) an.checked = c.anima_nsfw === true;            // 默认关
    // 同步魔导书工具栏的 Anima NSFW 开关状态（配置为准，回写 localStorage）
    if (typeof c.anima_nsfw !== 'undefined') {
      window._animaNsfwOn = c.anima_nsfw === true;
      try { localStorage.setItem('anima_nsfw_on', window._animaNsfwOn ? '1' : '0'); } catch (e) {}
      if (typeof _syncAnimaNsfwUI === 'function') _syncAnimaNsfwUI();
    }
    const spf = document.getElementById('send_platform');
    if (spf) spf.value = c.send_platform || 'auto';
    // 加载主题
    const modeSel = document.getElementById('random_pick_mode');
    if (modeSel && c && c.random_pick_mode) modeSel.value = c.random_pick_mode;
    const k2Sel = document.getElementById('k2_compose_mode');
    if (k2Sel && c && c.k2_compose_mode) k2Sel.value = c.k2_compose_mode;
    _k2ComposeMode = (c && c.k2_compose_mode) || 'anima';
    // K2 字段锁定面板：仅 K2 模式显示于魔导书内（grimoire_k2_panel）
    syncGrimoireK2UI();
    if (typeof _syncAnimaNsfwUI === 'function') _syncAnimaNsfwUI();   // 魔导书 Anima NSFW 按钮状态
    if (_k2ComposeMode === 'k2') renderK2LocksPanel();
    // 加载生成结果发送目标（平台 + ID + 是否群聊）
    const tplat = document.getElementById('cfg_target_platform');
    if (tplat) tplat.value = (c && c.target_platform) || 'qq';
    const tid = document.getElementById('cfg_target_id');
    if (tid) tid.value = (c && (c.target_id || c.target_qq)) || '';
    const tgrp = document.getElementById('cfg_target_group');
    if (tgrp) tgrp.checked = !!(c && c.target_group);
    // 加载部署模式
    const dmSel = document.getElementById('cfg_deploy_mode');
    if (dmSel && c && c.deploy_mode) dmSel.value = c.deploy_mode;
    // v4.3.0 云端数据库同步状态
    loadGiteeSyncStatus();
  });
}

/** v4.3.0 云端数据库同步：状态加载 + 执行（v4.3.6 起数据源固定为内置镜像，零配置） */
async function loadGiteeSyncStatus() {
  try {
    const s = await apiFetch('/api/gitee-sync/status', {}, true);
    if (!s || !s.ok) return;
    const taEl = document.getElementById('cfg_sync_artists');
    if (taEl) taEl.checked = s.sync_artists !== false;
    const tcEl = document.getElementById('cfg_sync_characters');
    if (tcEl) tcEl.checked = s.sync_characters !== false;
    const stEl = document.getElementById('gitee_sync_state');
    if (stEl) {
      if (s.last && s.last.finished_at) {
        const okTxt = s.last.ok ? '✅' : '⚠️';
        stEl.textContent = `${okTxt} 上次同步 ${s.last.finished_at}（词库 ${s.last.words_ok || 0} / 缓存 ${s.last.cache_ok || 0}）`;
      } else {
        stEl.textContent = '从未同步过';
      }
    }
  } catch (e) {}
}

async function runGiteeSync() {
  const btn = document.getElementById('btn_gitee_sync');
  const stEl = document.getElementById('gitee_sync_state');
  const taEl = document.getElementById('cfg_sync_artists');
  const tcEl = document.getElementById('cfg_sync_characters');
  const boxEl = document.getElementById('gitee_progress_box');
  const barEl = document.getElementById('gitee_progress_bar');
  const txtEl = document.getElementById('gitee_progress_text');
  // v4.3.6: 数据源固定为内置 GitHub 公开镜像，前端不再传 repo/token
  const body = {
    sync_artists: taEl ? !!taEl.checked : true,
    sync_characters: tcEl ? !!tcEl.checked : true,
  };
  if (btn) { btn.disabled = true; btn.innerHTML = '<svg class="icon-sm" aria-hidden="true"><use href="#icon-loader"/></svg> 同步中…'; }
  if (boxEl) boxEl.style.display = '';
  if (barEl) barEl.style.width = '0%';
  if (stEl) stEl.textContent = '';
  try {
    // v4.3.7: run 立即返回，后台线程执行；前端 1s 轮询进度
    const r = await apiFetch('/api/gitee-sync/run', { method: 'POST', body: JSON.stringify(body) }, true);
    if (!r || !r.ok) {
      const msg = (r && r.error) || '无法启动同步';
      if (boxEl) boxEl.style.display = 'none';
      if (stEl) stEl.textContent = '❌ ' + msg;
      toast('❌ ' + msg, 'error', 8000);
      return;
    }
    const fmtMB = (b) => b >= 1048576 ? (b / 1048576).toFixed(1) + 'MB' : Math.round(b / 1024) + 'KB';
    let lastResult = null;
    while (true) {
      await new Promise(res => setTimeout(res, 1000));
      let p = null;
      try { p = (await apiFetch('/api/gitee-sync/progress', {}, true))?.progress; } catch (e) {}
      if (!p) continue;
      const pct = p.percent ?? 0;
      if (barEl) barEl.style.width = pct + '%';
      const speed = p.speed_bps ? ' · ' + fmtMB(p.speed_bps) + '/s' : '';
      const cur = p.current_file ? p.current_file.split('/').pop() : '';
      const curSize = p.current_total ? ` (${fmtMB(p.current_bytes || 0)}/${fmtMB(p.current_total)})` : '';
      if (txtEl) txtEl.textContent = `${pct}% · ${p.done_files || 0}/${p.total_files || 0} 个文件 · 累计 ${fmtMB(p.downloaded_bytes || 0)}${speed}${cur ? ' · ' + cur + curSize : ''}`;
      if (p.phase === 'done' || p.phase === 'error') { lastResult = p.result || { ok: p.phase === 'done' }; break; }
      if (p.phase === 'idle') break;
    }
    const r2 = lastResult || {};
    if (r2.ok) {
      if (barEl) barEl.style.width = '100%';
      if (stEl) stEl.textContent = `✅ 同步完成 ${r2.finished_at || ''}（词库 ${r2.words_ok} 个${r2.cache_ok ? '，缓存 ' + r2.cache_ok + ' 个' : ''}）`;
      toast(`✅ 云端数据库同步完成：词库 ${r2.words_ok} 个` + (r2.cache_ok ? `，缓存 ${r2.cache_ok} 个` : ''), 'success', 5000);
      // 刷新魔导书数据源列表（词库已重载）
      if (typeof loadGrimoireData === 'function') loadGrimoireData().catch(() => {});
      setTimeout(() => { if (boxEl) boxEl.style.display = 'none'; }, 4000);
    } else {
      const msg = (r2.error || (r2.errors && r2.errors.join('; '))) || '同步失败';
      if (stEl) stEl.textContent = '❌ 同步失败';
      toast('❌ 同步失败: ' + msg, 'error', 8000);
      if (boxEl) boxEl.style.display = 'none';
    }
  } catch (e) {
    if (stEl) stEl.textContent = '❌ 同步异常';
    toast('❌ 同步异常: ' + e, 'error', 8000);
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '<svg class="icon-sm" aria-hidden="true"><use href="#icon-refresh"/></svg> 📥 同步云端数据库'; }
  }
}

/** 保存生成结果发送目标（平台 + ID + 群聊）到后端 */
async function saveTarget() {
  const platEl = document.getElementById('cfg_target_platform');
  const idEl   = document.getElementById('cfg_target_id');
  const grpEl  = document.getElementById('cfg_target_group');
  if (!platEl || !idEl) return;
  const plat = platEl.value || 'qq';
  const val  = idEl.value.trim();
  const grp  = grpEl ? !!grpEl.checked : false;
  const r = await apiFetch('/api/config', { method:'POST', body:JSON.stringify({
    target_platform: plat, target_id: val, target_group: grp
  })}, true);
  if (r && r.ok) {
    const pname = plat === 'feishu' ? '飞书' : 'QQ';
    toast(val ? `✅ 生成结果将发往${pname}${grp ? '群' : ''}: ${val}` : '已关闭生成结果发送');
  } else {
    toast('保存失败', 'error');
  }
}

/** 停止按钮：向 ComfyUI 发送中断命令，停止当前生成 */
function webuiStop() {
  const stopBtn = document.getElementById('stop_btn');
  const orig = stopBtn ? stopBtn.innerHTML : '';
  if (stopBtn) { stopBtn.disabled = true; stopBtn.style.opacity = '0.6'; stopBtn.innerHTML = '<svg class="icon-sm" aria-hidden="true"><use href="#icon-loader"/></svg> 停止中…'; }
  apiFetch('/api/interrupt', { method:'POST', body:'{}' }, true)
    .then((d) => {
      if (d && d.ok) toast('⏹ 已发送停止命令');
      else toast(d?.error || '停止失败', 'error');
    }).catch(e => { console.error('webuiStop error:', e); toast('停止失败', 'error'); })
    .finally(() => {
      if (stopBtn) { stopBtn.disabled = false; stopBtn.style.opacity = ''; stopBtn.innerHTML = orig; }
    });
}

/** 🚀 生成 / 🎲 随机图按钮：用当前工作流生成；isRandom=true 从随机池抽标签；数量取 gen_count
 *  ★ 数量>1 时：直接「模拟连续点击 N 次」——每次独立 K2 组词并提交，出 N 张不同提示词的图。
 *    这样不依赖后端 count 参数传递（避免前端数量读取失效导致只出 1 张）。 */
async function webuiGenerate(isRandom) {
  // ── 数量：优先 window._genCount（onchange 记录），其次实时读 select
  const _cntEl0 = document.getElementById('gen_count');
  let _want = 1;
  if (typeof window._genCount === 'number' && window._genCount >= 1 && window._genCount <= 10) {
    _want = window._genCount;
  } else if (_cntEl0) {
    const _v0 = parseInt(_cntEl0.value, 10);
    if (!isNaN(_v0) && _v0 >= 1 && _v0 <= 10) _want = _v0;
    window._genCount = _want;
  }
  // 数量>1 → 循环调用自身 N 次（每次 count 强制为 1，独立组词）
  if (_want > 1) {
    console.log('[webuiGenerate] 数量=' + _want + ' → 模拟连续点击 ' + _want + ' 次');
    let _ok = 0, _err = '';
    for (let _i = 0; _i < _want; _i++) {
      try {
        await webuiGenerateOnce(isRandom);
        _ok++;
      } catch (e) { _err = e?.message || String(e); break; }
    }
    toast(_ok > 0 ? `✅ 生成完成 ${_ok} 张` : ('❌ ' + (_err || '生成失败')), _ok > 0 ? '' : 'error');
    return;
  }
  // 数量=1 → 正常单次执行
  await webuiGenerateOnce(isRandom);
}

/** 单次生成（原 webuiGenerate 主体） */
async function webuiGenerateOnce(isRandom) {
  // 按压反馈：禁用按钮 + loading 文案，防止重复点击
  const genBtn = document.getElementById('gen_btn');
  const randBtn = document.getElementById('rand_btn');
  const activeBtn = isRandom ? randBtn : genBtn;
  const otherBtn = isRandom ? genBtn : randBtn;
  const origText = activeBtn ? activeBtn.textContent : '';
  if (activeBtn) { activeBtn.disabled = true; activeBtn.style.opacity = '0.65'; activeBtn.textContent = '⏳ 生成中…'; }
  if (otherBtn) { otherBtn.disabled = true; otherBtn.style.opacity = '0.5'; }
  const restore = () => {
    if (activeBtn) { activeBtn.disabled = false; activeBtn.style.opacity = ''; activeBtn.textContent = origText; }
    if (otherBtn) { otherBtn.disabled = false; otherBtn.style.opacity = ''; }
  };
  const posTa = document.getElementById('prompt_textarea');
  // 提示词来源分流：
  //  - K2 随机：由 K2 引擎组临时中文词，不回填输入框，直接提交（random=false，避免后端走 anima random-pick 丢弃 K2 词）
  //  - 生成 / 非K2随机：用输入框当前内容，不触发 K2 重排
  let prompt;
  if (isRandom && _k2ComposeMode === 'k2') {
    prompt = await k2Compose();
    if (!prompt) { prompt = posTa ? posTa.value.trim() : ''; }
  } else {
    prompt = posTa ? posTa.value.trim() : '';
  }
  // 后端 random 标志：K2 随机已在前端组词，后端直接按普通 prompt 处理（避免 anima random-pick 丢弃 K2 词）
  const randomFlag = (isRandom && _k2ComposeMode === 'k2') ? false : !!isRandom;
  // 单次生成：count 恒为 1（多张由外层 webuiGenerate 循环调用实现，每张独立 K2 组词）
  const count = 1;
  if (!isRandom && !prompt) { /* 图生视频/图生图允许空提示词，直接提交 */ }
  const k2MultiRandom = false;   // 外层已循环，这里不再走逐张分支
  try {
    if (k2MultiRandom) {
      // K2 随机多张：每张都重新组一次词（独立随机），逐张提交，避免同词出多张
      let total = 0, lastErr = '';
      for (let i = 0; i < count; i++) {
        const p2 = await k2Compose() || '';
        const r2 = await apiFetch('/api/generate', { method:'POST', body:JSON.stringify({prompt:p2, count:1, random:false}) }, true);
        if (r2 && r2.ok) { total += (r2.count || 1); }
        else { lastErr = r2?.error || '生成失败'; break; }
      }
      toast(total > 0 ? `✅ K2 随机生成完成 ${total} 张` : ('❌ ' + lastErr), total > 0 ? '' : 'error');
    } else {
      const r = await apiFetch('/api/generate', { method:'POST', body:JSON.stringify({prompt, count, random: randomFlag}) }, true);
      if (r && r.ok) {
        const pname = (r.target_platform === 'feishu') ? '飞书' : 'QQ';
        const sentTxt = r.sent ? ` ✅ 已发送到${pname} ${r.target_id || r.target_qq}`
                               : ((r.target_id || r.target_qq) ? ` ⚠️ 发送失败，仅本地生成` : `（未配置发送${pname}目标）`);
        toast(`✅ 生成完成 ${r.count || count} 张` + sentTxt);
      } else {
        toast('❌ ' + (r?.error || '生成失败'), 'error');
      }
    }
  } catch (e) {
    toast('请求失败: ' + e, 'error');
  } finally {
    restore();
  }
}

// ========================================================================
// PC 页面导航（switchPage）：生成台 / 画廊 / 工作流管理 / 设置
// ========================================================================
function switchPage(page) {
  document.querySelectorAll('.page-tab').forEach((t) => {
    t.classList.toggle('active', t.dataset.page === page);
  });
  ['wf', 'gen', 'gallery', 'wfmgr', 'settings'].forEach((p) => {
    document.body.classList.toggle('page-' + p, p === page);
  });
  // 记住页面状态（仅工作流列表/生成台，刷新后恢复生成台用）
  // 画廊/设置/工作流管理是浮层，刷新后不恢复，避免出现无内容的浮层
  try {
    if (page === 'wf' || page === 'gen') {
      localStorage.setItem('ui_last_page', page);
    }
  } catch (_) { /* noop */ }
  // 先关闭所有浮层，避免页面切换时残留（设置/工作流管理/画廊互相切换会互相弹出）
  closeGalleryDropdown();
  closeWfMgrModal();
  closeSettingsModal();
  if (page === 'wf') {
    openWorkflowPage();
  } else if (page === 'gallery') {
    openGalleryPage();
  } else if (page === 'wfmgr') {
    openWfMgrPage();
  } else if (page === 'settings') {
    openSettingsPage();
  }
}

function openWorkflowPage() {
  closeGalleryDropdown();
  closeWfMgrModal();
  closeSettingsModal();
  if (typeof hideGrimoire === 'function') hideGrimoire();
  loadWorkflows(); // 双渲染：侧栏(#wf_list) + 工作流页(#wf_page_grid)
}

function openGalleryPage() {
  const dd = document.getElementById('gallery_dropdown');
  if (!dd) return;
  const wasOpen = dd.classList.contains('show');
  dd.classList.add('show');
  document.getElementById('gallery_backdrop').classList.remove('show');
  if (!wasOpen) loadGalleryImages();
}
function openWfMgrPage() {
  const modal = document.getElementById('wf_mgr_modal');
  if (!modal) return;
  if (!modal.classList.contains('active')) {
    modal.classList.add('active');
    _editListCache = { all: null, cfg: null };
    loadWfEditList();
  }
}
function openSettingsPage() {
  const modal = document.getElementById('settings_modal');
  if (!modal) return;
  if (!modal.classList.contains('active')) {
    modal.classList.add('active');
    modal.style.display = 'flex';
    loadCfg();
  }
}

// 魔导书桌面端拖拽（≥900px 悬浮面板）
(function initGrimoireDrag() {
  const sheet = document.getElementById('grimoire_sheet');
  if (!sheet) return;
  const header = sheet.querySelector('.grimoire-header');
  if (!header) return;
  let dragging = false, sx = 0, sy = 0, ox = 0, oy = 0;
  header.addEventListener('pointerdown', (e) => {
    if (window.innerWidth < 900) return;
    if (e.target.closest('button,select,label,input')) return;
    dragging = true;
    const r = sheet.getBoundingClientRect();
    sx = e.clientX; sy = e.clientY; ox = r.left; oy = r.top;
    header.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  header.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    let nx = ox + (e.clientX - sx), ny = oy + (e.clientY - sy);
    nx = Math.max(8, Math.min(window.innerWidth - 60, nx));
    ny = Math.max(8, Math.min(window.innerHeight - 40, ny));
    sheet.style.left = nx + 'px';
    sheet.style.top = ny + 'px';
    sheet.style.right = 'auto';
  });
  const endDrag = (e) => {
    if (!dragging) return;
    dragging = false;
    try { header.releasePointerCapture(e.pointerId); } catch (_) { /* noop */ }
    // 持久化位置（仅 PC），下次打开回到用户摆放的位置
    try {
      const r = sheet.getBoundingClientRect();
      localStorage.setItem('grimoire_pos', JSON.stringify({ x: Math.round(r.left), y: Math.round(r.top) }));
    } catch (_) { /* noop */ }
  };
  header.addEventListener('pointerup', endDrag);
  header.addEventListener('pointercancel', endDrag);
  // 恢复上次位置（仅 PC）；双击标题栏空白处复位到默认停靠位
  try {
    const savedPos = JSON.parse(localStorage.getItem('grimoire_pos') || 'null');
    if (savedPos && window.innerWidth >= 900) {
      const px = Math.max(8, Math.min(savedPos.x, window.innerWidth - 120));
      const py = Math.max(8, Math.min(savedPos.y, window.innerHeight - 80));
      sheet.style.left = px + 'px';
      sheet.style.top = py + 'px';
      sheet.style.right = 'auto';
    }
  } catch (_) { /* noop */ }
  header.addEventListener('dblclick', (e) => {
    if (window.innerWidth < 900) return;
    if (e.target.closest('button,select,label,input')) return;
    try { localStorage.removeItem('grimoire_pos'); } catch (_) { /* noop */ }
    sheet.style.left = '';
    sheet.style.top = '';
    sheet.style.right = '';
  });
})();

// 魔导书 PC 自由调整大小（右下角手柄拖动，尺寸持久化到 localStorage）
(function initGrimoireResize() {
  const sheet = document.getElementById('grimoire_sheet');
  const handle = document.getElementById('grimoire_resize_handle');
  if (!sheet || !handle) return;
  let resizing = false, rsx = 0, rsy = 0, rw = 0, rh = 0;
  handle.addEventListener('pointerdown', (e) => {
    if (window.innerWidth < 900) return;
    resizing = true;
    const r = sheet.getBoundingClientRect();
    rsx = e.clientX; rsy = e.clientY; rw = r.width; rh = r.height;
    handle.setPointerCapture(e.pointerId);
    e.preventDefault();
    e.stopPropagation();
  });
  handle.addEventListener('pointermove', (e) => {
    if (!resizing) return;
    const w = Math.max(440, Math.min(rw + (e.clientX - rsx), window.innerWidth - 40));
    const h = Math.max(400, Math.min(rh + (e.clientY - rsy), window.innerHeight - 100));
    sheet.style.width = w + 'px';
    sheet.style.height = h + 'px';
  });
  const endResize = (e) => {
    if (!resizing) return;
    resizing = false;
    try { handle.releasePointerCapture(e.pointerId); } catch (_) { /* noop */ }
    try {
      const r = sheet.getBoundingClientRect();
      localStorage.setItem('grimoire_size', JSON.stringify({ w: Math.round(r.width), h: Math.round(r.height) }));
    } catch (_) { /* noop */ }
  };
  handle.addEventListener('pointerup', endResize);
  handle.addEventListener('pointercancel', endResize);
  // 恢复上次尺寸（仅 PC）
  try {
    const saved = JSON.parse(localStorage.getItem('grimoire_size') || 'null');
    if (saved && saved.w && saved.h && window.innerWidth >= 900) {
      sheet.style.width = saved.w + 'px';
      sheet.style.height = saved.h + 'px';
    }
  } catch (_) { /* noop */ }
})();

function toggleSettings() {
  const modal = document.getElementById('settings_modal');
  if (!modal) return;
  const shown = modal.classList.contains('active');
  modal.classList.toggle('active', !shown);
  modal.style.display = shown ? 'none' : 'flex';
  if (!shown) loadCfg();
}
function closeSettingsModal() {
  const modal = document.getElementById('settings_modal');
  if (modal) {
    modal.classList.remove('active');
    modal.style.display = 'none';
  }
}

// ========================================================================
// 目录操作
// ========================================================================
/** 部署模式切换 - 保存到后端 */
async function saveDeployMode() {
  const sel = document.getElementById('cfg_deploy_mode');
  if (!sel) return;
  const r = await apiFetch('/api/deploy-mode', { method:'POST', body:JSON.stringify({mode: sel.value}) }, true);
  if (r && r.ok) toast('部署模式已切换: ' + (sel.value === 'linux' ? 'Linux' : 'Windows'));
}

/** 弹出系统原生的文件夹选择器，选完后自动填入输入框 + 保存到后端 */
async function pickDir(type) {
  const mode = document.getElementById('cfg_deploy_mode').value;
  const input = document.getElementById(type === 'workflow' ? 'cfg_workflow_dir' : 'cfg_output_dir');
  const btn = document.getElementById(type === 'workflow' ? 'btn_save_wf_dir' : 'btn_save_output_dir');
  if (mode === 'linux') {
    if (type === 'workflow') { await setWorkflowDir(); }
    else { await saveOutputDir(); }
    return;
  }
  setLoading(btn, true);
  const result = await apiFetch('/api/pick-dir', { method:'POST' });
  setLoading(btn, false);
  if (result?.ok && result.path) {
    input.value = result.path;
    toast('📁 ' + result.path);
    if (type === 'workflow') { await setWorkflowDir(); }
    else { await saveOutputDir(); }
  } else if (result?.error) {
    toast('❌ ' + result.error);
  }
}
async function openWorkflowDir() {
  const p = document.getElementById('cfg_workflow_dir').value;
  if (!p) { toast('未设置目录', 'error'); return; }
  const d = await apiFetch('/api/open-dir', { method:'POST', body:JSON.stringify({path:p}) });
  toast(d?.ok ? '已打开' : '打开失败');
}
async function openOutputDir() {
  const p = document.getElementById('cfg_output_dir').value;
  if (!p) { toast('未设置目录', 'error'); return; }
  const d = await apiFetch('/api/open-dir', { method:'POST', body:JSON.stringify({path:p}) });
  toast(d?.ok ? '已打开' : '打开失败');
}
async function setWorkflowDir() {
  const p = document.getElementById('cfg_workflow_dir').value;
  if (!p) { toast('请输入路径', 'error'); return; }
  setLoading('btn_save_wf_dir', true);
  const d = await apiFetch('/api/workflow-dir', { method:'POST', body:JSON.stringify({path:p}) });
  if (d?.ok) { loadWorkflows(); toast('✅ 已保存'); if (window.innerWidth <= 768) closeSidebar(); }
  setLoading('btn_save_wf_dir', false);
}
async function saveOutputDir() {
  const p = document.getElementById('cfg_output_dir').value;
  if (!p) { toast('请输入路径', 'error'); return; }
  setLoading('btn_save_output_dir', true);
  await apiFetch('/api/config', { method:'POST', body:JSON.stringify({output_dir:p}) });
  setLoading('btn_save_output_dir', false);
  toast('✅ 已保存');
}
async function saveComfyUrl() {
  const u = document.getElementById('cfg_comfyui_url').value;
  await apiFetch('/api/config', { method:'POST', body:JSON.stringify({comfyui_url:u}) });
  toast('✅ 已保存');
  testComfyUI();
}
async function saveShowPrompt() {
  const val = document.getElementById('cfg_show_prompt').checked;
  await apiFetch('/api/config', { method:'POST', body:JSON.stringify({show_prompt_on_image:val}) });
}
async function saveAnimaEnhance() {
  const el = document.getElementById('cfg_anima_enhance');
  if (!el) return;
  const val = !!el.checked;
  await apiFetch('/api/config', { method:'POST', body:JSON.stringify({anima_spec_enhance:val}) });
  toast(val ? '✅ 已开启 Anima 随机规范增强' : '已关闭（随机结果按原始标签拼接）');
}
async function saveAnimaNsfw() {
  const el = document.getElementById('cfg_anima_nsfw');
  if (!el) return;
  const val = !!el.checked;
  await apiFetch('/api/config', { method:'POST', body:JSON.stringify({anima_nsfw:val}) });
  toast(val ? '✅ 已允许 NSFW（不再过滤）' : '已开启宽松过滤（跳过明确色情标签）');
}
async function saveSendPlatform() {
  const val = document.getElementById('send_platform').value;
  await apiFetch('/api/config', { method:'POST', body:JSON.stringify({send_platform:val}) });
  const names = { auto:'自动（跟随来源平台）', qq:'固定发 QQ', feishu:'固定发飞书', both:'两边都发' };
  toast('✅ 发送平台已设为：' + (names[val] || val));
}


// 主题切换事件
document.addEventListener('change', function(e) {
  if (e.target.id === 'random_pick_mode') {
    const val = e.target.value;
    apiFetch('/api/config', { method:'POST', body:JSON.stringify({random_pick_mode: val}) });
  }
  if (e.target.id === 'k2_compose_mode') {
    const val = e.target.value;
    _k2ComposeMode = val; // 同步前端变量，NSFW 开关显示随模式切换
    apiFetch('/api/config', { method:'POST', body:JSON.stringify({k2_compose_mode: val}) });
    // NSFW 开关只对 K2 引擎有意义（anima 提示词词缀无 NSFW 约束）；K2 面板统一在魔导书内切换显示
    syncGrimoireK2UI();
  }
});

// 加载主题配置（必须在主题函数定义之后）
loadCfg();

// ========================================================================
// ComfyUI 连接
// ========================================================================
async function testComfyUI() {
  const u = document.getElementById('cfg_comfyui_url').value;
  const d = document.getElementById('status_dot'), s = document.getElementById('comfyui_status');
  s.innerHTML = '<span class="status-label">ComfyUI</span> 连接中...'; d.className = 'status-dot';
  try {
    const r = await fetch(`/api/proxy?url=${  encodeURIComponent(`http://${  u  }/system_stats`)}`);
    const v = JSON.parse(await r.text());
    const ver = (v.system && v.system.comfyui_version) || '?';
    s.innerHTML = `<span class="status-label">ComfyUI</span> v${  ver}`;
    d.className = 'status-dot';
    toast(`✅ 连接成功 v${  ver}`);
  } catch(e) {
    s.innerHTML = '<span class="status-label">ComfyUI</span> <span style="color:var(--danger)">断开</span>'; d.className = 'status-dot off';
  }
}

// ========================================================================
// 工作流
// ========================================================================
/** 工作流预览图占位符（无图/加载失败时显示） */
function wfThumbPlaceholder() {
  return '<div class="wf-thumb-ph"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg><span>无预览图</span></div>';
}
/** 工作流预览图上传（侧栏卡片 / 编辑列表共用） */
function uploadWfPreview(name) {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'image/*';
  input.style.display = 'none';
  document.body.appendChild(input);
  input.onchange = async () => {
    const file = input.files && input.files[0];
    input.remove();
    if (!file) return;
    // 无大小限制：任意大小图片均可上传；上传前先本地预压缩（最长边 ≤2048），
    // 减小 base64 传输量与后端压缩耗时，避免 30s 超时
    const reader = new FileReader();
    reader.onload = async () => {
      // v4.4.0: 最长边 2048 保持，但对超高分辨率原图先在画布阶段控制（压缩函数内部处理），
      // 8K 级长图会缩到 2048 长边再输出，PNG 透明图也可控
      const data = await compressImageDataURL(reader.result, 2048, 0.85);
      // v4.4.0: 大图 base64 可达数 MB，慢网 30s 默认超时会掐断 → 加到 120s；
      // 失败时把后端错误透出来（旧版只说"保存失败"查不到原因）
      const d = await apiFetch('/api/workflow-preview', { method:'POST', timeoutMs: 120000, body:JSON.stringify({ name:name, data:data }) });
      if (d && d.ok) { toast('预览图已保存'); loadWorkflows(); loadWfEditList && loadWfEditList(); }
      else {
        const why = d ? (d.error || '未知错误') : '网络超时或数据过大（图太大时换小图重试）';
        toast('预览图保存失败: ' + why, 'error', 8000);
      }
    };
    reader.readAsDataURL(file);
  };
  input.click();
}

/** canvas 预压缩 dataURL 图片：限制最长边 maxSide，JPEG 质量 quality（真有透明才回退 PNG）
 *  v4.4.0 修复：旧版 hasAlpha 判断恒 true（|| true 笔误）→ 所有图都转 PNG，
 *  2048px PNG 动漫图/截图可达 4-10MB，base64 后可超 aiohttp client_max_size(20MB)
 *  → 服务端 JSON 解析失败 →「预览图保存失败」。现在：逐像素查真实 alpha，无透明一律 JPEG
 *  （体积小 3-8 倍），真透明才用 PNG。 */
function compressImageDataURL(dataUrl, maxSide, quality) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      let w = img.naturalWidth, h = img.naturalHeight;
      if (!w || !h) { resolve(dataUrl); return; }
      const scale = Math.min(1, maxSide / Math.max(w, h));
      if (scale >= 1) { resolve(dataUrl); return; }  // 小图无需压缩
      const cw = Math.round(w * scale), ch = Math.round(h * scale);
      const canvas = document.createElement('canvas');
      canvas.width = cw; canvas.height = ch;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0, cw, ch);
      // 真透明检测：抽样 alpha 通道，出现 <255 即认为有透明（抽样步长按面积自适应）
      let hasAlpha = false;
      try {
        const srcIsPng = /data:image\/png/i.test(dataUrl);
        if (srcIsPng) {
          const step = Math.max(1, Math.floor(Math.sqrt(cw * ch / 4096)));
          const d = ctx.getImageData(0, 0, cw, ch).data;
          for (let i = 3; i < d.length; i += 4 * step) {
            if (d[i] < 255) { hasAlpha = true; break; }
          }
        }
      } catch (e) { /* getImageData 受 CORS 限制时按无透明处理 */ }
      try {
        resolve(canvas.toDataURL(hasAlpha ? 'image/png' : 'image/jpeg', quality));
      } catch (e) { resolve(dataUrl); }
    };
    img.onerror = () => resolve(dataUrl);
    img.src = dataUrl;
  });
}

async function loadWorkflows() {
  const d = await apiFetch('/api/workflows', {}, true);
  if (!d || !d.length) { $id('wf_list').innerHTML = '<div class="empty-state">暂无工作流</div>'; return; }
  const c = await apiFetch('/api/workflow-params-config', {}, true);
  if (!c) return;
  _catOrder = c.__category_order__ || [];  // 保存分类排序
  const aliases = c.__workflow_aliases__ || {};
  const cats = Object.assign({}, c.__workflow_categories__ || {}, c.__wf_categories__ || {});
  const div = $id('wf_list');
  // 按分类分组
  const grouped = {};
  d.forEach((w) => {
    const cat = cats[w.name] || '未分类';
    if (!grouped[cat]) grouped[cat] = [];
    grouped[cat].push(w);
  });
  // 未分类工作流默认不在侧栏显示（进「工作流管理-编辑」页才显示未分类），搜索时除外
  const q = (document.getElementById('wf_search')?.value || '').toLowerCase();
  const showUncategorized = q.length > 0;
  if (!showUncategorized) delete grouped['未分类'];
  _loadSidebarCatState();
  // 分类排序：优先用 __category_order__，未出现的分类按字母序放到末尾
  let catKeys = Object.keys(grouped);
  const orderedCats = [];
  if (_catOrder.length) {
    _catOrder.forEach((o) => { if (catKeys.includes(o)) orderedCats.push(o); });
    catKeys.filter((k) => !orderedCats.includes(k)).sort().forEach((k) => orderedCats.push(k));
  } else {
    orderedCats.push(...catKeys.sort());
  }
  let html = '';
  orderedCats.forEach((cat) => {
    const groupWfs = grouped[cat];
    const safeCat = cat.replace(/[^a-zA-Z0-9\u4e00-\u9fff]/g, '_');
    const isCollapsed = _sidebarCatCollapsed[cat] || false;
    html += `<div class="sidebar-cat-header${isCollapsed ? ' collapsed' : ''}" draggable="true" data-action="toggleSidebarCat" data-cat="${esc(cat)}" id="sb_cat_h_${safeCat}">` +
      `<span class="drag-handle">⠿</span>` +
      `<span class="wf-cat-dot"></span>` +
      `<span style="font-size:14px;font-weight:600;color:var(--primary)">${esc(cat)}</span>` +
      `<span class="sidebar-cat-count">(${groupWfs.length})</span>` +
      `<span class="collapse-icon">▾</span></div>` +
      `<div class="sidebar-cat-body${isCollapsed ? ' collapsed' : ''}" id="sb_cat_body_${safeCat}">`;
    groupWfs.forEach((w) => {
      const a = w.is_current ? ' active' : '', badge = w.is_current ? '<span class="wf-badge">当前</span>' : '';
      const hasAlias = aliases[w.name] && aliases[w.name] !== '';
      const disp = hasAlias ? esc(aliases[w.name]) : esc(w.name);
      const sub = hasAlias
        ? `<span style="color:var(--text-sub);font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%">${esc(w.name)}</span>`
        : '';
      const pvUrl = w.preview ? '/api/workflow-preview?name=' + encodeURIComponent(w.name) : '';
      const thumb = pvUrl
        ? `<img data-src="${pvUrl}" alt="" loading="lazy" onerror="this.closest('.wf-thumb').innerHTML=wfThumbPlaceholder()">`
        : wfThumbPlaceholder();
      html += `<div class="wf-item${a}" data-wf="${w.name.replace(/"/g, '&quot;')}" data-action="switchWF" data-wfname="'${w.name.replace(/'/g, "\\'")}'" role="listitem" tabindex="0">` +
        `<div class="wf-thumb">${thumb}` +
        `${badge}</div>` +
        `<div class="wf-item-info"><span class="wf-item-name" title="${esc(w.name)}">${disp}</span></div></div>`;
    });
    html += `</div>`;
  });
  div.innerHTML = html;
  // 双渲染：工作流独立页（PC）网格容器
  const pg = document.getElementById('wf_page_grid');
  if (pg) pg.innerHTML = html;
  // 工作流预览图走缓存加载（data-src → Cache API）
  [div, pg].forEach((root) => {
    if (!root) return;
    root.querySelectorAll('img[data-src]').forEach((img) => {
      const src = img.getAttribute('data-src');
      if (src) {
        setImgWithCache(img, src);
        img.removeAttribute('data-src');
      }
    });
  });
  // 更新工作流页计数徽章
  const wfCnt = document.getElementById('wf_page_count');
  if (wfCnt) wfCnt.textContent = (d ? d.length : 0);
  // 绑定拖拽事件
  initCategoryDrag();
  const cwf = getCurrentWF();
  if (cwf) {
    const cur = d.find((x) => { return x.name === cwf; });
    const dn = (cur && cur.alias) ? cur.alias : cwf;
    document.getElementById('current_wf_title_name').textContent = dn;
  }
  loadBindOverview();
  loadQueue();
}

async function switchWF(n) {
  await apiFetch('/api/workflows/switch', { method:'POST', body:JSON.stringify({name:n}) });
  await loadWorkflows();
  setTimeout(loadParams, 300);
  if (window.innerWidth <= 768) closeSidebar();
  else if (typeof switchPage === 'function') switchPage('gen'); // PC：切完工作流跳转生成台
}

async function toggleHidden(n) {
  await apiFetch('/api/workflows/toggle-hidden', { method:'POST', body:JSON.stringify({name:n}) });
  loadWorkflows();
}

function toggleSidebarCat(cat, el) {
  _sidebarCatCollapsed[cat] = !_sidebarCatCollapsed[cat];
  _saveSidebarCatState();
  // 优先用点击的 header 就近切换（侧栏/工作流页双渲染时 id 会重复，不能依赖 getElementById）
  let header = null;
  if (el) {
    header = el.classList.contains('sidebar-cat-header') ? el : el.closest('.sidebar-cat-header');
  }
  if (!header) {
    const safeCat = cat.replace(/[^a-zA-Z0-9\u4e00-\u9fff]/g, '_');
    header = document.getElementById('sb_cat_h_' + safeCat);
  }
  if (header) {
    const body = header.nextElementSibling;
    header.classList.toggle('collapsed', _sidebarCatCollapsed[cat]);
    if (body && body.classList.contains('sidebar-cat-body')) body.classList.toggle('collapsed', _sidebarCatCollapsed[cat]);
  }
}

// ========================================================================
// 分类拖拽排序（桌面 Drag & Drop + 移动端 Touch）
// ========================================================================
function initCategoryDrag() {
  document.querySelectorAll('.sidebar-cat-header').forEach((header) => {
    header.removeEventListener('dragstart', _onDragStart);
    header.removeEventListener('dragover', _onDragOver);
    header.removeEventListener('drop', _onDrop);
    header.removeEventListener('dragend', _onDragEnd);
    header.addEventListener('dragstart', _onDragStart);
    header.addEventListener('dragover', _onDragOver);
    header.addEventListener('drop', _onDrop);
    header.addEventListener('dragend', _onDragEnd);
    // 移动端 touch 拖拽
    header.removeEventListener('touchstart', _onTouchStart);
    header.removeEventListener('touchmove', _onTouchMove);
    header.removeEventListener('touchend', _onTouchEnd);
    header.addEventListener('touchstart', _onTouchStart, {passive:false});
    header.addEventListener('touchmove', _onTouchMove, {passive:false});
    header.addEventListener('touchend', _onTouchEnd);
  });
}

let _dragEl = null, _touchClone = null, _touchStartY = 0;

function _onDragStart(e) {
  _dragEl = e.target.closest('.sidebar-cat-header');
  if (!_dragEl) return;
  _dragEl.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', _dragEl.dataset.cat || '');
}

function _onDragOver(e) {
  e.preventDefault();
  const target = e.target.closest('.sidebar-cat-header');
  if (!target || target === _dragEl) return;
  document.querySelectorAll('.sidebar-cat-header').forEach((h) => h.classList.remove('drag-over'));
  target.classList.add('drag-over');
}

function _onDrop(e) {
  e.preventDefault();
  const target = e.target.closest('.sidebar-cat-header');
  if (!target || target === _dragEl) return;
  _reorderCategories(_dragEl, target);
}

function _onDragEnd(e) {
  document.querySelectorAll('.sidebar-cat-header').forEach((h) => {
    h.classList.remove('dragging', 'drag-over');
  });
  _dragEl = null;
}

// 移动端 Touch 拖拽
function _onTouchStart(e) {
  const header = e.target.closest('.sidebar-cat-header');
  if (!header) return;
  _dragEl = header;
  _touchStartY = e.touches[0].clientY;
  header.classList.add('dragging');
  // 创建跟随手指的克隆
  _touchClone = header.cloneNode(true);
  _touchClone.style.cssText = 'position:fixed;z-index:9999;pointer-events:none;opacity:0.8;width:' + header.offsetWidth + 'px;background:var(--card);border-radius:var(--radius-sm);box-shadow:0 4px 20px rgba(0,0,0,0.2);padding:8px 12px;';
  _touchClone.style.top = (e.touches[0].clientY - header.offsetHeight / 2) + 'px';
  _touchClone.style.left = header.getBoundingClientRect().left + 'px';
  document.body.appendChild(_touchClone);
}

function _onTouchMove(e) {
  e.preventDefault();
  if (!_touchClone || !_dragEl) return;
  _touchClone.style.top = (e.touches[0].clientY - _dragEl.offsetHeight / 2) + 'px';
  // 查找当前手指位置对应的目标分类
  const touchY = e.touches[0].clientY;
  document.querySelectorAll('.sidebar-cat-header').forEach((h) => h.classList.remove('drag-over'));
  let target = null;
  document.querySelectorAll('.sidebar-cat-header').forEach((h) => {
    if (h === _dragEl) return;
    const rect = h.getBoundingClientRect();
    if (touchY >= rect.top && touchY <= rect.bottom) target = h;
  });
  if (target) target.classList.add('drag-over');
}

function _onTouchEnd(e) {
  if (_touchClone) { _touchClone.remove(); _touchClone = null; }
  document.querySelectorAll('.sidebar-cat-header').forEach((h) => h.classList.remove('dragging', 'drag-over'));
  if (!_dragEl) return;
  // 找到最后一个带有 drag-over 的目标
  let target = null;
  document.querySelectorAll('.sidebar-cat-header').forEach((h) => {
    if (h === _dragEl) return;
    const rect = h.getBoundingClientRect();
    if (e.changedTouches[0].clientY >= rect.top && e.changedTouches[0].clientY <= rect.bottom) target = h;
  });
  if (target) _reorderCategories(_dragEl, target);
  _dragEl = null;
}

// 重新排序分类
async function _reorderCategories(from, to) {
  const fromCat = from.dataset.cat;
  const toCat = to.dataset.cat;
  if (!fromCat || !toCat || fromCat === toCat) return;
  // 获取当前在 DOM 中的所有分类顺序
  const headers = Array.from(document.querySelectorAll('.sidebar-cat-header'));
  // v4.7.1: 以 DOM 实际渲染的分类为准——旧版用 _catOrder 过滤，分类更名后
  // 陈旧名单（如已改名的 画/图生视频）会把新分类全部滤掉，indexOf 得 -1 直接 return，
  // 表现为拖动排序永远无效。未分类不渲染在侧栏（搜索时才显示），保序补到末尾。
  let currentOrder = [];
  headers.forEach((h) => { const c = h.dataset.cat; if (c && !currentOrder.includes(c)) currentOrder.push(c); });
  if (!currentOrder.includes('未分类')) currentOrder.push('未分类');
  // 重排：把 fromCat 移到 toCat 的位置
  const fromIdx = currentOrder.indexOf(fromCat);
  const toIdx = currentOrder.indexOf(toCat);
  if (fromIdx < 0 || toIdx < 0) return;
  currentOrder.splice(fromIdx, 1);
  currentOrder.splice(toIdx < fromIdx ? toIdx : toIdx, 0, fromCat);
  // 保存到后端
  await apiFetch('/api/wf-category-order', { method:'POST', body:JSON.stringify({order:currentOrder}) }, true);
  _catOrder = currentOrder;
  // 重建侧边栏
  loadWorkflows();
}

function filterWorkflows() {
  const q = document.getElementById('wf_search').value.toLowerCase();
  // 有搜索关键词时自动展开所有分类，清空后恢复折叠状态
  if (q) {
    document.querySelectorAll('.sidebar-cat-body').forEach((b) => b.classList.remove('collapsed'));
    document.querySelectorAll('.sidebar-cat-header').forEach((h) => h.classList.remove('collapsed'));
  } else {
    // 恢复折叠状态
    document.querySelectorAll('.sidebar-cat-header').forEach((h) => {
      const cat = h.dataset.cat;
      if (cat && _sidebarCatCollapsed[cat]) { h.classList.add('collapsed');
        const body = document.getElementById('sb_cat_body_' + cat.replace(/[^a-zA-Z0-9\u4e00-\u9fff]/g, '_'));
        if (body) body.classList.add('collapsed');
      }
    });
  }
  document.querySelectorAll('.wf-item').forEach((item) => {
    const name = item.querySelector('.wf-name');
    if (!name) return;
    item.style.display = name.textContent.toLowerCase().indexOf(q) > -1 ? '' : 'none';
  });
}

/** 工作流独立页搜索：过滤分类头 + 卡片 */
function filterWorkflowsPage() {
  const q = (document.getElementById('wf_page_search')?.value || '').toLowerCase();
  document.querySelectorAll('#wf_page_grid .sidebar-cat-header').forEach((h) => {
    const body = h.nextElementSibling;
    if (!body || !body.classList.contains('sidebar-cat-body')) return;
    let visible = 0;
    body.querySelectorAll('.wf-item').forEach((item) => {
      const nm = item.dataset.wf || '';
      const show = !q || nm.toLowerCase().indexOf(q) > -1;
      item.style.display = show ? '' : 'none';
      if (show) visible++;
    });
    h.style.display = visible ? '' : 'none';
    body.style.display = visible ? '' : 'none';
  });
}

// ========================================================================
// 队列
// ========================================================================
async function loadQueue() {
  try {
    const d = await apiFetch('/api/progress', {}, true);
    if (d) {
      const total = (d.queue_running || 0) + (d.queue_pending || 0);
      document.getElementById('queue_count').textContent = total;
    }
  } catch(e) {}
}

// ========================================================================
// 参数/节点
// ========================================================================
async function loadParams() {
  clearTimeout(window._skeletonTimer);
  showSkeletonNodes();
  modelLists = await apiFetch('/api/comfy-models', {}, true) || {};

  const d = await apiFetch('/api/workflow-params', {}, true);
  if (!d || d.error) {
    $id('node_grid').innerHTML = `<div style="grid-column:1/-1;padding:20px;text-align:center;color:var(--text-sub);font-size:12px">⚠️ ${  d?.error || '加载失败'  }</div>`;
    allParamsData = null;
  } else {
    allParamsData = d;
  }

  allWorkflows = await apiFetch('/api/workflows', {}, true) || [];

  const c = await apiFetch('/api/workflow-params-config', {}, true);
  if (c) {
    // 当前工作流名：优先用后端 /api/workflow-params 返回的权威 workflow_name，
    // 回退 DOM active 类。切换工作流时 DOM 更新有延迟，用后端名避免读错工作流配置
    // （曾导致切到生视频工作流后上传区残留/不加载上一个工作流的 LoadImage 配置）。
    const curWF = (d && d.workflow_name) || getCurrentWF();
    const wfC = c.__workflow_node_configs__ || {}, wf = wfC[curWF] || {};
    promptNodeId = wf.__prompt_node__ || '';
    resNodeId = wf.__resolution_node__ || '';
    // 恢复手动指定：不再自动识别老式分辨率节点（否则取消指定后自动检测又填回，
    // 前端看起来"点了没反应"）。未指定时 legacy 预设面板仍显示，用户可直接选 480p~4K
    const imgIds = (wf.__load_image_nodes__ || '').split(',').map(s => s.trim()).filter(Boolean);
    loadImageNodeIds = ['', '', '', '', '', '', '', '', '', ''];
    imgIds.forEach((id, i) => { if (i < 10) loadImageNodeIds[i] = id; });
    _slotOrderAuto = imgIds.length === 0; // 无保存顺序=自动探测，允许按标题编号对号入座
    // 自动识别工作流中所有 LoadImage 节点：无手动配置时按节点顺序自动分配槽位（双重确认：类型名 + image 输入槽）
    if (allParamsData && allParamsData.nodes) {
      const loadImgNodes = allParamsData.nodes.filter(n => /loadimage/i.test(n.class_type || '') && n.params && n.params.some(p => p.key === 'image'));
      if (loadImgNodes.length > 0 && imgIds.length === 0) {
        loadImageNodeIds = ['', '', '', '', '', '', '', '', '', ''];
        loadImgNodes.forEach((n, i) => { if (i < 10) loadImageNodeIds[i] = n.id; });
      }
    }
    // 音频节点：LoadAudio 等（双重确认：类型名 + audio 输入槽）
    const audioIds = (wf.__load_audio_nodes__ || '').split(',').map(s => s.trim()).filter(Boolean);
    loadAudioNodeIds = ['', '', '', '', '', '', '', '', '', ''];
    audioIds.forEach((id, i) => { if (i < 10) loadAudioNodeIds[i] = id; });
    if (allParamsData && allParamsData.nodes) {
      const loadAudioNodes = allParamsData.nodes.filter(n => /loadaudio|audio/i.test(n.class_type || '') && !/videoloader/i.test(n.class_type || '') && n.params && n.params.some(p => p.key === 'audio'));
      if (loadAudioNodes.length > 0 && audioIds.length === 0) {
        loadAudioNodeIds = ['', '', '', '', '', '', '', '', '', ''];
        loadAudioNodes.forEach((n, i) => { if (i < 10) loadAudioNodeIds[i] = n.id; });
      }
    }
    // 视频节点：VHS_LoadVideo / VideoLoader 等（双重确认：类型名 + video 输入槽）
    const videoIds = (wf.__load_video_nodes__ || '').split(',').map(s => s.trim()).filter(Boolean);
    loadVideoNodeIds = ['', '', '', '', '', '', '', '', '', ''];
    videoIds.forEach((id, i) => { if (i < 10) loadVideoNodeIds[i] = id; });
    if (allParamsData && allParamsData.nodes) {
      const loadVideoNodes = allParamsData.nodes.filter(n => /videoloader|loadvideo/i.test(n.class_type || '') && n.params && n.params.some(p => p.key === 'video'));
      if (loadVideoNodes.length > 0 && videoIds.length === 0) {
        loadVideoNodeIds = ['', '', '', '', '', '', '', '', '', ''];
        loadVideoNodes.forEach((n, i) => { if (i < 10) loadVideoNodeIds[i] = n.id; });
      }
    }
    // 「不使用」加载节点：生成时即使文件存在也移除（前端 checkbox 勾选）
    // 「不使用」机制已废弃：空槽（无有效文件）生成时后端自动移除跳过，有图自动加载。
    // 不再读取存量 __empty_load_nodes__，saveParams 以空值覆盖旧配置（自愈清雷）。
    emptyLoadNodes = [];
    negativeNodeId = wf.__negative_node__ || '';
    expandedTextNodeId = wf.__expanded_text_node__ || '';
    // 自动检测正负面提示词的输入字段
    promptInputKey = promptNodeId ? _getNodeFirstStringKey(promptNodeId) : '';
    negativeInputKey = negativeNodeId ? _getNodeFirstStringKey(negativeNodeId) : '';
    disabledGroups = c.__disabled_groups__ || {};
    // v4.4.2: 组数据按绑定目标分桶自包含——源工作流删除后 source 为空，但绑定与组数据保留
    //（v4.4.1 的三元组强校验+孤儿清理会连坐删掉用户绑定数据，为本版修正）。串台由分桶机制根治。
    const _gs = c.__groups_source__ || '';
    const _bt = c.__bind_target__ || '';
    const _gd = c.__groups_data__ || [];
    if (_gd.length && _bt) {
      groupsSource = _gs; bindTarget = _bt;
    } else {
      groupsSource = ''; bindTarget = ''; disabledGroups = {};
    }
    isBound = !!(bindTarget && _gd.length > 0);
    disabledNodes = c.__disabled_nodes__ || [];

    // 先清空 textarea，避免旧工作流的残留数据
    $id('prompt_textarea').value = '';
    $id('neg_textarea').value = '';
    // 优先从配置的保存文本加载（用户手动保存过的值），没有则保持空输入框。
    // 注意：不再用 _loadNodeTextToTextarea 预填节点默认文本——
    // 打包子节点展开后节点的 value 是工作流自带的大段默认内容（如 subject_definitions），
    // 预填会让用户以为输入框已有提示词、直接点生成，导致默认内容被当用户提示词注入（“输入不了”）。
    const wfSaved = (c.__workflow_node_configs__ || {})[curWF] || {};
    const savedTexts = wfSaved.__saved_texts__ || {};
    const promptSavedKey = promptNodeId && `${promptNodeId}_${promptInputKey}`;
    const negSavedKey = negativeNodeId && `${negativeNodeId}_${negativeInputKey}`;
    if (promptSavedKey && promptSavedKey in savedTexts) {
      $id('prompt_textarea').value = savedTexts[promptSavedKey] ?? '';
    }
    if (negSavedKey && negSavedKey in savedTexts) {
      $id('neg_textarea').value = savedTexts[negSavedKey] ?? '';
    }

    // 初始化质量与比例 UI（legacy 面板已移除，元素可能不存在，全部空保护）
    if (c && c.current_quality) {
      const quality = c.current_quality, ratio = c.current_ratio, w = c.current_width, h = c.current_height;
      document.querySelectorAll('#res_presets .res-preset-btn').forEach((b) =>{
        const action = b.getAttribute('data-action');
        b.classList.toggle('active', action && action.includes(quality));
      });
      document.querySelectorAll('#ratio_group .ratio-btn').forEach((b) =>{
        b.classList.toggle('active', b.getAttribute('data-r') === ratio);
      });
      const info = document.getElementById('res_info');
      if (info) {
        info.textContent = `质量: ${quality} · 比例: ${ratio} · ${w}x${h}`;
        info.style.display = 'block';
      }
      const v2k = document.getElementById('vram_warn_2k');
      const v4k = document.getElementById('vram_warn_4k');
      if (v2k) v2k.style.display = (quality === '2K' || quality === '4K') ? 'block' : 'none';
      if (v4k) v4k.style.display = quality === '4K' ? 'block' : 'none';
    }

    // 官方原生节点检测：有 ResolutionSelector → 显示官方面板；有 PrimitiveFloat/Float → 显示时长面板
    officialResNodes = (c && c.official_res_nodes) || [];
    officialDurationNodes = (c && c.official_duration_nodes) || [];
    // 兜底互斥：部分旧手机 WebView 对 inline style="display:none" 控制失效，
    // 导致官方/旧版两面板并存。改用 class + CSS !important 强制隐藏另一面板
    //（普通后代选择器，旧 WebView 均支持，不依赖 :has()）。
    (function () {
      if (!window._resCssInjected) {
        var st = document.createElement('style');
        st.textContent = '#prep_resolution.has-official-res #legacy_res_panel{display:none!important}#prep_resolution.no-official-res #official_res_panel{display:none!important}';
        document.head.appendChild(st);
        window._resCssInjected = true;
      }
      var pr = document.getElementById('prep_resolution');
      var ho = (officialResNodes || []).length > 0;
      if (pr) { pr.classList.toggle('has-official-res', ho); pr.classList.toggle('no-official-res', !ho); }
    })();
    _initOfficialResPanel();
    _initOfficialDurationPanel();
  }

  // 初始化 Lora 配置
  if (d && d.lora_nodes) {
    loraNodesConfig = d.lora_nodes;
  } else {
    loraNodesConfig = {};
  }
  // 异步加载 Lora 列表（供弹窗下拉列表使用）
  loadLoras();

  renderNodes();
  updateNodeDisplay();
  refreshGroupsUI();
}

function showSkeletonNodes() {
  let h = '';
  for (let i = 0; i < 6; i++) {
    h += '<div class="node-card" style="min-height:90px">' +
      '<div class="skeleton skeleton-text" style="width:40%"></div>' +
      '<div class="skeleton skeleton-text" style="margin-top:8px"></div>' +
      '<div class="skeleton skeleton-text short" style="margin-top:4px"></div>' +
      '</div>';
  }
  $id('node_grid').innerHTML = h;
  // 5 秒超时：骨架屏 → "加载失败，点击重试"
  clearTimeout(window._skeletonTimer);
  window._skeletonTimer = setTimeout(() => {
    const grid = $id('node_grid');
    if (!grid) return;
    // 如果骨架屏还在，说明加载卡住了
    const firstChild = grid.firstElementChild;
    if (firstChild && firstChild.classList.contains('skeleton')) {
      grid.innerHTML = `<div class="node-error-fallback">
        <span class="node-error-icon">⚠️</span>
        <span class="node-error-text">节点加载失败</span>
        <button class="btn btn-sm btn-secondary" data-action="loadParams" style="margin-top:8px">🔄 重试</button>
      </div>`;
    }
  }, 5000);
}

function updateNodeDisplay() {
  // 控制上方参数区域的显示/隐藏
  const hasPos = !!promptNodeId, hasNeg = !!negativeNodeId, hasRes = !!resNodeId, hasExp = !!expandedTextNodeId;
  // 官方原生节点：有 ResolutionSelector / PrimitiveFloat 时也显示分辨率面板
  const hasOfficialRes = officialResNodes.length > 0 || officialDurationNodes.length > 0;
  const hasImg = loadImageNodeIds.some(id => !!id);
  const hasDisabled = disabledNodes.length > 0;
  const hasUnet = !!(allParamsData && allParamsData.nodes && allParamsData.nodes.some(n => /UNETLoader/i.test(n.class_type)));
  const hasAny = hasPos || hasNeg || hasRes || hasImg || hasExp || hasDisabled || hasOfficialRes || hasUnet;
  document.getElementById('prep_positive').style.display = hasPos ? 'block' : 'none';
  document.getElementById('prep_negative').style.display = hasNeg ? 'block' : 'none';
  document.getElementById('prep_resolution').style.display = 'block';
  // 分辨率区块始终显示（不再依赖 resNodeId/官方节点是否指定——否则未指定节点时
  // 整块隐藏，用户既看不到 480p~4K 预设、点击指定/取消也因区块消失而无反馈）。
  // 官方/legacy 互斥由下方 JS 内联强制：官方节点存在只显示官方，否则只显示旧版预设。
  // 每次更新都强制执行官方/旧版互斥：避免切换工作流或 c 为空时两套分辨率面板共存
  // 各面板渲染独立 try/catch：任一渲染函数对展开后节点数据抛异常时，
  // 不中断 updateNodeDisplay，保证后续上传区/徽章正常显示（打包工作流含 Lora/UNet 节点曾导致上传区被跳过）
  try { _initOfficialResPanel(); } catch (e) { console.error('res panel err:', e); }
  try { _initOfficialDurationPanel(); } catch (e) { console.error('dur panel err:', e); }
  // 统一面板显隐：官方节点存在→只显示官方；无官方→只显示旧版预设；时长面板独立
  try { updateResPanels(); } catch (e) { console.error('res panels err:', e); }
  // 视频专属：BasicScheduler 基本调度器面板（scheduler/steps/denoise）
  try { renderBasicScheduler(); } catch (e) { console.error('bs panel err:', e); }
  // Lora 面板：检测工作流中是否有 PowerLoraLoader 节点
  const hasLora = _hasLoraNodes();
  document.getElementById('prep_loras').style.display = hasLora ? 'block' : 'none';
  if (hasLora) { try { renderLoraPanel(); } catch (e) { console.error('lora panel err:', e); } }
  // 风格预设面板：检测工作流中是否有 easy stylesSelector 节点
  const hasStyle = _hasStyleNodes();
  document.getElementById('prep_styles').style.display = hasStyle ? 'block' : 'none';
  if (hasStyle) { try { renderStylePanel(); } catch (e) { console.error('style panel err:', e); } }
  // UNet 模型面板：独立显隐（有 UNETLoader 节点才显示，不再依赖 Lora 面板）
  try { renderUnetPanel(); } catch (e) { console.error('unet panel err:', e); }
  document.getElementById('prep_upload_image').style.display = hasImg ? 'block' : 'none';
  document.getElementById('prep_expanded_text').style.display = hasExp ? 'block' : 'none';
  document.getElementById('prep_unassigned_hint').style.display = hasAny ? 'none' : 'block';
  // 更新徽章
  document.getElementById('prep_pos_node_badge').textContent = `节点: ${  promptNodeId || '自动'}`;
  document.getElementById('prep_neg_node_badge').textContent = `节点: ${  negativeNodeId || '自动'}`;
  document.getElementById('prep_res_node_badge').textContent = `节点: ${  resNodeId || '自动'}`;
  const imgBadge = loadImageNodeIds.filter(id => id).map((id, i) => `#${i+1}:${id}`).join(' ') || '自动';
  document.getElementById('prep_img_node_badge').textContent = `节点: ${  imgBadge}`;
  document.getElementById('prep_exp_node_badge').textContent = `节点: ${  expandedTextNodeId || '无'}`;
  // 渲染上传行：自动识别 LoadImage / LoadAudio / 视频加载 节点，每行带序号输入框（填 1~10 指定优先级）
  const uploadContainer = document.getElementById('upload_image_rows');
  if (uploadContainer) {
    const nodes = (allParamsData && allParamsData.nodes) ? allParamsData.nodes : [];
    const groups = [
      { label: '加载图片', cls: /loadimage/i, inputKey: 'image', nodeIds: loadImageNodeIds, orderFn: 'setLoadImageOrder', uploadFn: 'handleImageUpload', accept: 'image/*', prefix: 'image' },
      { label: '加载音频', cls: /loadaudio/i, inputKey: 'audio', nodeIds: loadAudioNodeIds, orderFn: 'setLoadAudioOrder', uploadFn: 'handleAudioUpload', accept: 'audio/*', prefix: 'audio' },
      { label: '加载视频', cls: /videoloader|loadvideo/i, inputKey: 'video', nodeIds: loadVideoNodeIds, orderFn: 'setLoadVideoOrder', uploadFn: 'handleVideoUpload', accept: 'video/*', prefix: 'video' },
    ];
    // 双重确认：类型名匹配 + 确有对应输入槽（params 中存在 image/audio/video 字段）
    const hasInputSlot = (n, key) => !!(n.params && n.params.some(p => p.key === key));
    let uploadHtml = '';
    groups.forEach((g) => {
      const gNodes = nodes.filter(n => g.cls.test(n.class_type || '') && hasInputSlot(n, g.inputKey));
      if (!gNodes.length) return;
      // 自动对号入座：工作流 API 自身已编号（节点标题 图N / 尾部数字）→ 按编号排序并同步槽位顺序，
      // 免手动排序；编号不齐全或用户已有手动排序（_slotOrderAuto=false）时不干预
      if (_slotOrderAuto && g.prefix === 'image' && gNodes.length > 1) {
        const _numOf = (n) => { const m = String(n.title || '').match(/(\d+)\s*$/); return m ? parseInt(m[1], 10) : null; };
        const _nums = gNodes.map(_numOf);
        if (_nums.every(x => x !== null) && new Set(_nums).size === _nums.length) {
          gNodes.sort((a, b) => _numOf(a) - _numOf(b));
          const derived = gNodes.map(n => n.id);
          const curOrder = g.nodeIds.filter(id => id);
          if (JSON.stringify(derived) !== JSON.stringify(curOrder)) {
            for (let i = 0; i < 10; i++) g.nodeIds[i] = (derived[i] !== undefined) ? derived[i] : '';
            _slotOrderAuto = false; // 已对号入座并持久化，之后尊重手动排序
            setTimeout(() => { saveParams().catch(() => {}); }, 80);
          }
        }
      }
      // 当前节点 → 槽位映射（槽位下标 = 序号-1）
      const slotOf = {};
      g.nodeIds.forEach((id, i) => { if (id) slotOf[id] = i; });
      // 按槽位排序渲染：槽位（序号）小的卡片排前面，保证拖动重排后视觉顺序与序号一致
      gNodes.sort((a, b) => {
        const sa = slotOf[a.id] !== undefined ? slotOf[a.id] : 99;
        const sb = slotOf[b.id] !== undefined ? slotOf[b.id] : 99;
        return sa - sb;
      });
      uploadHtml += `<div class="upload-group-title"><svg class="icon-sm" aria-hidden="true"><use href="#${g.prefix === 'image' ? 'icon-image' : (g.prefix === 'video' ? 'icon-video' : 'icon-audio')}"/></svg> ${g.label}<span class="ugt-count">${gNodes.length}</span></div>`;
      gNodes.forEach((n, idx) => {
        const curSlot = slotOf[n.id];
        const orderVal = curSlot !== undefined ? curSlot + 1 : (idx + 1);
        const nodeLabel = esc(n.title || n.class_type || g.label);
        // 检测该节点当前输入值：为空 → 标记「未上传」（生成时该节点会被自动移除并跳过）
        const curParam = n.params && n.params.find(p => p.key === g.inputKey);
        const curVal = curParam ? String(curParam.value || '') : '';
        const isPlaceholder = !curVal;
        // 卡片式上传：图片按实际比例显示（不再固定 9:16）、视频 16:9 横、音频 1:1 方
        // 图片有值时用 ua-ratio-auto（高度随图片比例自适应），占位时才用 9:16 空位
        const ratioCls = g.prefix === 'image'
          ? (isPlaceholder ? ' ua-ratio-916' : ' ua-ratio-auto')
          : (g.prefix === 'video' ? ' ua-ratio-169' : ' ua-ratio-11');
        const iconUse = g.prefix === 'image' ? 'icon-image' : (g.prefix === 'video' ? 'icon-video' : 'icon-audio');
        const noteText = g.prefix === 'image' ? '点击上传' : (g.prefix === 'video' ? '16:9 · 点击上传' : '点击上传音频');
        // 节点已有文件名（上传过/刷新后读回）→ 加载预览图（图片分组）/ 显示文件名（音频/视频）
        const curFname = curVal ? String(curVal).split(/[\\/]/).pop() : '';
        const previewSrc = (!isPlaceholder && g.prefix === 'image')
          ? `/api/view-input?filename=${encodeURIComponent(curVal)}&type=input`
          : '';
        uploadHtml += `<div class="upload-media-card${ratioCls}" id="${g.prefix}_box_${idx}" data-nodeid="${n.id}" draggable="true"
          ondragstart="onUploadCardDragStart(event,'${n.id}')" ondragover="onUploadCardDragOver(event)" ondrop="onUploadCardDrop(event,'${n.id}')" ondragend="onUploadCardDragEnd(event)"
          ontouchstart="onUploadCardTouchStart(event,'${n.id}')" ontouchmove="onUploadCardTouchMove(event,'${n.id}')" ontouchend="onUploadCardTouchEnd(event,'${n.id}')"
          onclick="document.getElementById('${g.prefix}_upload_${idx}').click()" title="${noteText}（长按/拖动可排序）">
          <input type="file" id="${g.prefix}_upload_${idx}" accept="${g.accept}" onchange="${g.uploadFn}(this,'${n.id}',${idx})" style="display:none">
          <img class="ua169-preview" id="${g.prefix}_preview_${idx}" alt="" style="${previewSrc ? '' : 'display:none'}" src="${previewSrc}" onerror="this.style.display='none'" onload="adjustUploadRatio(this)">
          ${!isPlaceholder ? `<button class="ua169-clear" data-nodeid="${n.id}" data-key="${g.inputKey}" onclick="event.stopPropagation();clearNodeMedia('${n.id}','${g.inputKey}')" title="清空此图片">✕</button>` : ''}
          <div class="ua169-empty" id="${g.prefix}_empty_${idx}" style="${previewSrc ? 'display:none' : ''}">
            <svg class="ua169-icon" aria-hidden="true"><use href="#${iconUse}"/></svg>
            <span class="ua169-node">节点 #${n.id}</span>
            <span class="ua169-plus">＋</span>
            <span class="ua169-label">${nodeLabel}</span>
            <span class="ua169-note">${noteText}</span>
            <span class="ua169-fname" id="${g.prefix}_fname_${idx}" style="${isPlaceholder ? 'display:none' : ''}">${curFname}</span>
          </div>
          <div class="ua169-meta">
            <input type="number" class="order-input" id="${g.prefix}_order_${idx}" data-nodeid="${n.id}" min="1" max="10" value="${orderVal}" onclick="event.stopPropagation()" onchange="${g.orderFn}('${n.id}', this.value)" title="优先级序号：1=最先加载，数字越小越优先">
          </div>
        </div>`;
      });
    });
    uploadContainer.innerHTML = uploadHtml || '<div style="padding:8px;color:var(--text-sub2);font-size:12px">未检测到加载节点</div>';
  }
  // 更新清除所有指定按钮的显示
  const clearBtn = document.getElementById('clear_all_roles_btn');
  if (clearBtn) clearBtn.style.display = hasAny ? 'inline-flex' : 'none';
}

/** 检测工作流中是否有 PowerLoraLoader 节点 */
function _hasLoraNodes() {
  if (!allParamsData || !allParamsData.nodes) return false;
  return allParamsData.nodes.some(n => /power\s*lora/i.test(n.class_type) || /power\s*lora/i.test(n.title));
}

/** 获取 Lora 节点的显示名称 */
function _getLoraNodeLabel(node) {
  return node.title || node.class_type || 'Power Lora Loader';
}

// ===================== 风格预设面板（easy stylesSelector） =====================
let styleNodeId = '';          // easy stylesSelector 节点 id
let styleLibs = [];            // 风格大类列表
let styleLibCurrent = '';      // 当前选中的大类
let styleItems = [];           // 当前大类下的具体风格
let selectedStyles = [];       // 已选风格 name（逗号分隔存回 select_styles）
let styleMultiSelect = false;  // 多选叠加模式

function _hasStyleNodes() {
  if (!allParamsData || !allParamsData.nodes) return false;
  const n = allParamsData.nodes.find(n => /easy\s*styles?selector/i.test(n.class_type) || /styles?selector/i.test(n.title));
  if (!n) return false;
  styleNodeId = n.id;
  return true;
}

/** 取当前 easy stylesSelector 节点某参数值 */
function _getStyleNodeParam(key) {
  if (!styleNodeId || !allParamsData || !allParamsData.nodes) return '';
  const node = allParamsData.nodes.find(n => String(n.id) === String(styleNodeId));
  if (!node) return '';
  const p = (node.params || []).find(p => p.key === key);
  return p ? (p.value || '') : '';
}

/** 保存 styles + select_styles 到后端（复用现有 workflow-params 通道） */
function _saveStyleParams() {
  if (!styleNodeId) return;
  const data = {};
  data[`${styleNodeId}_styles`] = styleLibCurrent;
  data[`${styleNodeId}_select_styles`] = selectedStyles.join(',');
  apiFetch('/api/workflow-params', { method: 'POST', body: JSON.stringify(data) }, true)
    .then(() => {
      // 同步回 allParamsData，避免刷新前显示错位
      const node = allParamsData.nodes.find(n => String(n.id) === String(styleNodeId));
      if (node) {
        const ps = node.params || [];
        const setP = (k, v) => { const p = ps.find(p => p.key === k); if (p) p.value = v; };
        setP('styles', styleLibCurrent);
        setP('select_styles', selectedStyles.join(','));
      }
    })
    .catch(() => {});
}

function renderStylePanel() {
  if (!styleNodeId) return;
  const container = document.getElementById('style_panel_content');
  const badge = document.getElementById('prep_style_node_badge');
  if (badge) badge.textContent = `节点: #${styleNodeId}`;
  if (!container) return;
  // 从工作流当前值初始化
  const curLib = _getStyleNodeParam('styles') || '';
  const curSel = (_getStyleNodeParam('select_styles') || '').split(',').map(s => s.trim()).filter(Boolean);
  if (curLib && (!styleLibCurrent || styleLibCurrent !== curLib)) styleLibCurrent = curLib;
  // 多选判定：当前已有多个则用多选模式展示
  if (curSel.length > 1) styleMultiSelect = true;
  selectedStyles = curSel;

  container.innerHTML = '<div class="style-lib-row"><label>库</label><select id="style_lib_sel"></select></div>' +
    '<div class="style-mode-row"><label><input type="checkbox" id="style_multi" ' + (styleMultiSelect ? 'checked' : '') + '> 多选叠加</label><span style="color:var(--text-muted)">已选: <span id="style_sel_count">' + selectedStyles.length + '</span></span></div>' +
    '<div class="style-grid" id="style_grid"><div class="style-empty">加载中…</div></div>';

  const sel = document.getElementById('style_lib_sel');
  sel.onchange = () => { styleLibCurrent = sel.value; loadStyleItems(); };
  document.getElementById('style_multi').onchange = (e) => {
    styleMultiSelect = e.target.checked;
    if (!styleMultiSelect && selectedStyles.length > 1) selectedStyles = selectedStyles.slice(0, 1);
    refreshStyleCards();
    _saveStyleParams();
  };

  // 大类列表（优先用缓存，否则拉取）
  if (styleLibs.length) {
    fillStyleLibOptions(sel, styleLibCurrent);
    if (styleLibCurrent) loadStyleItems(); else container.querySelector('#style_grid').innerHTML = '<div class="style-empty">请选择风格库</div>';
  } else {
    apiFetch('/api/style-libs', {}, true).then(d => {
      styleLibs = (d && d.libs) || [];
      fillStyleLibOptions(sel, styleLibCurrent);
      styleLibCurrent = styleLibCurrent || styleLibs[0] || '';
      sel.value = styleLibCurrent;
      if (styleLibCurrent) loadStyleItems(); else container.querySelector('#style_grid').innerHTML = '<div class="style-empty">无风格库</div>';
    }).catch(() => { container.querySelector('#style_grid').innerHTML = '<div class="style-empty">风格库加载失败</div>'; });
  }
}

function fillStyleLibOptions(sel, current) {
  sel.innerHTML = styleLibs.map(l => `<option value="${esc(l)}"${l === current ? ' selected' : ''}>${esc(l)}</option>`).join('');
}

function loadStyleItems() {
  const grid = document.getElementById('style_grid');
  if (!grid) return;
  grid.innerHTML = '<div class="style-empty">加载中…</div>';
  apiFetch('/api/style-list?lib=' + encodeURIComponent(styleLibCurrent), {}, true).then(d => {
    styleItems = (d && d.styles) || [];
    refreshStyleCards();
  }).catch(() => { grid.innerHTML = '<div class="style-empty">风格列表加载失败</div>'; });
}

function refreshStyleCards() {
  const grid = document.getElementById('style_grid');
  if (!grid) return;
  if (!styleItems.length) { grid.innerHTML = '<div class="style-empty">该库无具体风格</div>'; return; }
  // 重排：已选风格排到最前（与 ComfyUI 原生一致），未选的按原序跟在后面
  const ordered = [];
  const rest = [];
  styleItems.forEach((it, i) => {
    const name = it.name || '';
    if (selectedStyles.includes(name)) ordered.push({ it, i });
    else rest.push({ it, i });
  });
  const renderList = ordered.concat(rest);
  grid.innerHTML = renderList.map(({ it, i }) => {
    const name = it.name || '';
    const cn = it.name_cn || name;
    const active = selectedStyles.includes(name);
    const thumb = it.thumbnail || '';
    const imgHtml = thumb
      ? `<img class="style-thumb" src="${esc(thumb)}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"><span class="style-thumb-placeholder" style="display:none">🎨</span>`
      : `<span class="style-thumb-placeholder">🎨</span>`;
    return `<div class="style-card${active ? ' selected' : ''}" data-idx="${i}" onclick="toggleStyleCard(${i})">
      ${imgHtml}
      <div class="style-check">✓</div>
      <div class="style-name" title="${esc(cn)}">${esc(cn)}</div>
    </div>`;
  }).join('');
  const cnt = document.getElementById('style_sel_count');
  if (cnt) cnt.textContent = selectedStyles.length;
}

function toggleStyleCard(idx) {
  const it = styleItems[idx];
  if (!it) return;
  const name = it.name;
  const pos = selectedStyles.indexOf(name);
  if (styleMultiSelect) {
    if (pos >= 0) selectedStyles.splice(pos, 1); else selectedStyles.push(name);
  } else {
    selectedStyles = (pos >= 0) ? [] : [name];
  }
  refreshStyleCards();
  _saveStyleParams();
}

/** 渲染 UNet 模型名列表（显示在 Lora 管理上方，点击可切换模型） */
function renderUnetPanel() {
  const wrap = document.getElementById('prep_unet_models');
  const list = document.getElementById('unet_model_list');
  if (!wrap || !list) return;
  if (!allParamsData || !allParamsData.nodes) return;
  const unetNodes = allParamsData.nodes.filter(n => /UNETLoader/i.test(n.class_type));
  if (!unetNodes.length) { wrap.style.display = 'none'; return; }
  wrap.style.display = 'block';
  let html = '';
  unetNodes.forEach((node) => {
    const unetParam = node.params && node.params.find(p => p.key === 'unet_name' && typeof p.value === 'string' && p.value);
    const fullName = unetParam ? unetParam.value : '';
    const baseName = fullName ? (fullName.split(/[\\/]/).pop() || fullName) : '— 未选择模型 —';
    const displayName = baseName.length > 46 ? baseName.substring(0, 46) + '…' : baseName;
    const escName = displayName.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    const escFull = (fullName || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    html += `<div class="unet-model-item" data-node="${  node.id  }" title="${  escFull  }" onclick="openUnetModelPicker(event, '${  node.id  }', this)">
      <span class="unet-icon">🧠</span><span class="unet-node-tag">#${  node.id  }</span>
      <span class="unet-model-name">${  escName  }</span><span class="unet-switch-hint">⇅ 切换</span>
    </div>`;
  });
  list.innerHTML = html;
}

/** 点击 UNet 模型行：弹出模型下拉浮层（按文件夹分类，仿 Lora 下拉实现） */
function openUnetModelPicker(event, nid, el) {
  if (event) event.stopPropagation();
  // 先关闭所有 UNet 下拉浮层，避免多个共存
  document.querySelectorAll('.unet-dropdown-panel').forEach(p => p.remove());
  const models = (modelLists && modelLists['UNETLoader_unet_name']) || [];
  if (!models.length) { toast('未获取到模型列表，请检查 ComfyUI 连接', 'error'); return; }
  // 当前选中的模型
  const unetNodes = (allParamsData && allParamsData.nodes) || [];
  const node = unetNodes.find(n => String(n.id) === String(nid));
  const cur = (node && node.params && node.params.find(p => p.key === 'unet_name')) ? node.params.find(p => p.key === 'unet_name').value : '';
  // 构建浮层面板
  const panel = document.createElement('div');
  panel.className = 'unet-dropdown-panel';
  panel.dataset.unetNode = nid;
  panel.innerHTML = `
    <div class="unet-dd-header">🧠 选择 UNet 模型 <span class="unet-dd-close" onclick="closeUnetDropdown()">✕</span></div>
    <div class="unet-dd-search"><input type="text" placeholder="🔍 搜索模型..." oninput="filterUnetList(this, '${nid}')" autocomplete="off"></div>
    <div class="unet-dd-list">${buildUnetTreeHtml(models, cur, nid)}</div>`;
  // 定位在触发元素下方
  const r = el.getBoundingClientRect();
  panel.style.position = 'fixed';
  panel.style.top = (r.bottom + 4) + 'px';
  panel.style.left = Math.max(8, r.left) + 'px';
  panel.style.width = Math.max(300, r.width) + 'px';
  document.body.appendChild(panel);
}

/** 构建 UNet 模型分类树 HTML：顶层文件夹 → 子文件夹 → 模型项 */
function buildUnetTreeHtml(models, cur, nid, expandAll) {
  const tree = {};
  models.forEach((m) => {
    const parts = m.split(/[\\/]/);
    const name = parts.pop();
    const top = parts[0] || '根目录';
    const sub = parts.slice(1).join('/') || '';
    if (!tree[top]) tree[top] = {};
    if (!tree[top][sub]) tree[top][sub] = [];
    tree[top][sub].push({ full: m, name });
  });
  let html = '';
  const tops = Object.keys(tree).sort((a, b) => a.localeCompare(b, 'zh'));
  tops.forEach((top) => {
    const subs = tree[top];
    let total = 0;
    Object.values(subs).forEach(arr => total += arr.length);
    // 顶层分类头（默认折叠，点击展开；搜索时 expandAll 强制展开）
    html += `<div class="unet-dd-cat${expandAll ? '' : ' collapsed'}" onclick="event.stopPropagation(); toggleUnetCat(this)"><span class="unet-dd-arrow">▶</span><span class="unet-dd-cat-icon">📁</span>${top} <span class="unet-dd-count">(${total})</span></div>`;
    html += `<div class="unet-dd-cat-body"${expandAll ? '' : ' style="display:none"'}>`;
    const subKeys = Object.keys(subs).sort((a, b) => {
      if (!a) return 1; if (!b) return -1;
      return a.localeCompare(b, 'zh');
    });
    subKeys.forEach((sub) => {
      const items = subs[sub].sort((a, b) => a.name.localeCompare(b.name, 'zh'));
      if (sub) {
        // 子分类头（可折叠，默认折叠）
        html += `<div class="unet-dd-subcat${expandAll ? '' : ' collapsed'}" onclick="event.stopPropagation(); toggleUnetSubcat(this)"><span class="unet-dd-subcat-arrow">▶</span>📁 ${sub} <span class="unet-dd-subcat-count">(${items.length})</span></div>`;
        html += `<div class="unet-dd-subcat-body"${expandAll ? '' : ' style="display:none"'}>`;
        items.forEach((it) => {
          const escM = it.full.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
          const escJsM = escJsStr(it.full);
          const escB = it.name.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
          const active = it.full === cur ? ' active' : '';
          const mark = it.full === cur ? ' ●' : '';
          html += `<div class="unet-dd-item${active}" onclick="event.stopPropagation(); saveUnetModel('${nid}', '${escJsM}')" title="${escM}"><span class="unet-dd-name">${escB}</span>${mark}</div>`;
        });
        html += '</div>';
      } else {
        // 无子分类：模型项直接挂在顶层分类体下
        items.forEach((it) => {
          const escM = it.full.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
          const escJsM = escJsStr(it.full);
          const escB = it.name.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
          const active = it.full === cur ? ' active' : '';
          const mark = it.full === cur ? ' ●' : '';
          html += `<div class="unet-dd-item${active}" onclick="event.stopPropagation(); saveUnetModel('${nid}', '${escJsM}')" title="${escM}"><span class="unet-dd-name">${escB}</span>${mark}</div>`;
        });
      }
    });
    html += '</div>';
  });
  if (!html) html = '<div class="unet-dd-empty">无匹配模型</div>';
  return html;
}

/** 展开/收起 UNet 顶层分类头 */
function toggleUnetCat(catEl) {
  const arrow = catEl.querySelector('.unet-dd-arrow');
  const body = catEl.nextElementSibling;
  if (!body || !body.classList.contains('unet-dd-cat-body')) return;
  const collapsed = body.style.display === 'none';
  body.style.display = collapsed ? 'block' : 'none';
  if (arrow) arrow.textContent = collapsed ? '▼' : '▶';
  catEl.classList.toggle('collapsed', !collapsed);
}

/** 展开/收起 UNet 子分类头 */
function toggleUnetSubcat(subEl) {
  const arrow = subEl.querySelector('.unet-dd-subcat-arrow');
  const body = subEl.nextElementSibling;
  if (!body || !body.classList.contains('unet-dd-subcat-body')) return;
  const collapsed = body.style.display === 'none';
  body.style.display = collapsed ? 'block' : 'none';
  if (arrow) arrow.textContent = collapsed ? '▼' : '▶';
  subEl.classList.toggle('collapsed', !collapsed);
}

/** 搜索过滤 UNet 模型（跨文件夹匹配，保留分类结构） */
function filterUnetList(input, nid) {
  const val = input.value.toLowerCase().trim();
  const body = input.closest('.unet-dropdown-panel')?.querySelector('.unet-dd-list');
  if (!body) return;
  const models = (modelLists && modelLists['UNETLoader_unet_name']) || [];
  const unetNodes = (allParamsData && allParamsData.nodes) || [];
  const node = unetNodes.find(n => String(n.id) === String(nid));
  const cur = (node && node.params && node.params.find(p => p.key === 'unet_name')) ? node.params.find(p => p.key === 'unet_name').value : '';
  const filtered = !val ? models : models.filter(m => m.toLowerCase().includes(val));
  body.innerHTML = buildUnetTreeHtml(filtered, cur, nid, !!val);
}

/** 关闭所有 UNet 下拉浮层 */
function closeUnetDropdown() {
  document.querySelectorAll('.unet-dropdown-panel').forEach(p => p.remove());
}

// 点击页面其他位置关闭 UNet 下拉浮层
document.addEventListener('click', (e) => {
  if (e.target.closest('.unet-dropdown-panel') || e.target.closest('.unet-model-item')) return;
  closeUnetDropdown();
});

/** 保存 UNet 模型切换（写入 __saved_texts__，生成时自动生效） */
function saveUnetModel(nid, modelName) {
  closeUnetDropdown();
  const payload = {};
  payload[`${nid}_unet_name`] = modelName;
  apiFetch('/api/workflow-params', { method:'POST', body:JSON.stringify(payload) }, true)
    .then((r) => {
      if (r && r.ok !== false) {
        toast(`✅ UNet 模型已切换: ${  modelName}`);
        // 更新内存数据后重渲染
        if (allParamsData && allParamsData.nodes) {
          const node = allParamsData.nodes.find(n => String(n.id) === String(nid));
          if (node && node.params) {
            const p = node.params.find(x => x.key === 'unet_name');
            if (p) p.value = modelName;
          }
        }
        renderUnetPanel();
      } else {
        toast(r?.error || '模型保存失败', 'error');
        renderUnetPanel();
      }
    }).catch((e) => { console.error('saveUnetModel error:', e); toast('模型保存失败', 'error'); renderUnetPanel(); });
}

/** 渲染 Lora 主面板 — 完全复用弹窗 Lora 样式 */
/** 切换 prep-section 折叠（分辨率面板等，与 Lora 管理一致的折叠体验） */
function togglePrepSection(header) {
  const sec = header && header.closest('.prep-section');
  if (!sec) return;
  sec.classList.toggle('collapsed');
}

/** 根据预览文件扩展名生成 img（图片）或 video（mp4 视频）标签 */
function loraPreviewTag(previewUrl, previewSrc, className) {
  if (!previewUrl) return '';
  const isVideo = /\.(mp4|webm|mov|m4v)(\?|#|$)/i.test(previewSrc || '');
  if (isVideo) {
    return `<video class="${className}" src="${previewUrl}" muted autoplay loop playsinline preload="metadata"
      onerror="this.style.display='none'; this.nextElementSibling.style.display='flex'"></video>`;
  }
  return `<img class="${className}" src="${previewUrl}" alt="" loading="lazy"
    onerror="this.style.display='none'; this.nextElementSibling.style.display='flex'">`;
}

function renderLoraPanel() {
  if (!allParamsData || !allParamsData.nodes) return;
  const container = document.getElementById('lora_panel_content');
  if (!container) return;
  const loraNodes = allParamsData.nodes.filter(n => /power\s*lora/i.test(n.class_type) || /power\s*lora/i.test(n.title));
  if (!loraNodes.length) { container.innerHTML = ''; return; }
  const badge = document.getElementById('prep_lora_node_badge');
  if (badge) badge.textContent = `节点: ${loraNodes.map(n => '#' + n.id).join(', ')}`;
  let html = '';
  loraNodes.forEach((node) => {
    const nid = node.id;
    const loras = loraNodesConfig[nid] || [];
    html += `<div class="lora-panel-node-card" data-lora-node="${nid}">`;
    // 默认折叠：无论是否有 Lora 都闭合，点击表头才展开
    const collapsed = ' collapsed';
    html += `<div class="lora-panel-node-header" data-action="toggle-lora-collapse" data-lora-node="${nid}">
      <span class="lora-panel-arrow${collapsed}" style="pointer-events:none">▼</span>
      <svg class="icon-xs" aria-hidden="true" style="flex-shrink:0;pointer-events:none"><use href="#icon-lora"/></svg>
      <span style="pointer-events:none">${_getLoraNodeLabel(node)} #${nid}</span>
      <span style="font-size:10px;color:var(--text-sub);font-weight:400;margin-left:auto;pointer-events:none">${loras.filter(l=>l.on).length}/${loras.length} 启用</span>
    </div>`;
    html += `<div class="lora-panel-node-body${collapsed}">`;
    html += `<div class="section-title" style="margin-bottom:6px"><svg class="icon-sm" aria-hidden="true"><use href="#icon-lora"/></svg> Lora 列表</div>`;
    html += `<div class="lora-list" id="lora_list_panel_${nid}">`;
    if (loras.length === 0) {
      html += '<div class="empty-state" style="padding:8px;text-align:center;color:var(--text-sub2);font-size:11px">暂无 Lora，点击下方「添加」按钮</div>';
    } else {
      loras.forEach((l, li) => {
        const disabledCls = l.on ? '' : ' lora-disabled';
        const loraMeta = matchLoraMeta(l.lora_name || '');
        const previewUrl = loraMeta && loraMeta.preview_url
          ? `/api/lora-preview?fn=${encodeURIComponent(l.lora_name || '')}`
          : '';
        const twAll = loraMeta ? (loraMeta.trigger_words || []) : [];
        const twSelected = l.trigger_words_selected || [];
        const modelLabel = loraMeta ? (loraMeta.model_name || loraMeta.file_name || '') : '';
        const hasPreview = !!previewUrl;
        html += `<div class="lora-item${disabledCls}" data-idx="${li}">
          <div class="lora-top-row">
            <span class="lora-status" onclick="toggleLoraInPanel('${nid}', ${li})" title="${l.on ? '已启用，点击关闭' : '已禁用，点击启用'}"></span>
            ${hasPreview ? loraPreviewTag(previewUrl, loraMeta.preview_url, 'lora-thumb') : `<span class="lora-thumb-placeholder"></span>`}
            <div class="lora-select" onclick="toggleLoraDropdown(event, this, '${nid}', ${li}, true)" title="${modelLabel.replace(/"/g,'&quot;')}">
              ${l.lora_name ? l.lora_name.replace(/^.*[\\\/]/, '') : '— 选择 Lora —'}
            </div>
            <span class="lora-strength-wrap lora-strength-locked" data-lock="1" data-v="${l.strength_model ?? 1.0}">
              <input class="lora-strength" type="range" min="-15" max="15" step="0.05" value="${l.strength_model ?? 1.0}"
                oninput="var w=this.closest('.lora-strength-wrap');if(w.classList.contains('lora-strength-locked')){this.value=w.dataset.v||this.value;return;} w.dataset.v=this.value; updateLoraInPanel('${nid}', ${li}, 'strength_model', parseFloat(this.value) || 0); this.nextElementSibling.value = parseFloat(this.value).toFixed(2)"
                onclick="activateLoraSlider(event, this)"
                ontouchstart="touchStartLoraSlider(event, this)" ontouchend="touchEndLoraSlider(event, this)"
                title="点击激活后拖动调整" aria-label="Lora 权重">
              <input class="lora-strength-val" type="number" min="-15" max="15" step="0.05" value="${(l.strength_model ?? 1.0).toFixed(2)}"
                onchange="updateLoraInPanel('${nid}', ${li}, 'strength_model', parseFloat(this.value) || 0); this.previousElementSibling.value = Math.max(-15, Math.min(15, parseFloat(this.value) || 0))"
                title="直接输入数值" aria-label="Lora 权重数值">
            </span>
            <button class="btn btn-xs btn-danger" onclick="deleteLoraInPanel('${nid}', ${li})" title="移除 Lora" style="flex-shrink:0;padding:2px 6px;font-size:11px">✕</button>
          </div>`;
        // 触发词行
        if (twAll.length > 0) {
          html += `<div class="lora-tw-row">`;
          for (let ti = 0; ti < twAll.length; ti++) {
            const tw = twAll[ti];
            const checked = twSelected.includes(tw) ? ' checked' : '';
            html += `<label class="lora-tw-tag${checked ? ' tw-active' : ''}" title="${tw.replace(/"/g,'&quot;')}">
              <input type="checkbox"${checked} onchange="toggleLoraTwInPanel('${nid}', ${li}, '${escJsStr(tw)}', this.checked)" style="display:none">
              ${tw}
            </label>`;
          }
          html += `</div>`;
        }
        html += `</div>`;
      });
    }
    html += `</div>`;
    html += `<div style="display:flex;gap:6px;margin-top:6px">
      <button class="btn btn-xs btn-primary" onclick="addLoraForNode('${nid}', true);refreshLoraPanelPreserveExpanded()"><svg class="icon-sm" aria-hidden="true"><use href="#icon-plus"/></svg> 添加 Lora</button>
      <button class="btn btn-xs btn-secondary" onclick="toggleAllLorasInPanel('${nid}')" title="全部启用/禁用">⏻ 全部开关</button>
    </div>`;
    html += `</div></div>`;
  });
  container.innerHTML = html;
  // 事件委托：处理卡片折叠/展开
  container.onclick = function(e) {
    const header = e.target.closest('[data-action="toggle-lora-collapse"]');
    if (header) {
      const body = header.nextElementSibling;
      const arrow = header.querySelector('.lora-panel-arrow');
      if (body) body.classList.toggle('collapsed');
      if (arrow) arrow.classList.toggle('collapsed');
    }
  };
}

/** 切换 Lora 节点卡片折叠 */
function toggleLoraNodeCollapse(header) {
  const body = header.nextElementSibling;
  const arrow = header.querySelector('.lora-panel-arrow');
  if (body) body.classList.toggle('collapsed');
  if (arrow) arrow.classList.toggle('collapsed');
}

/** 刷新外部 Lora 管理面板并保留各节点卡片的展开状态（重渲染后不闭合） */
function refreshLoraPanelPreserveExpanded() {
  // 记录当前展开的节点卡片，避免重渲染后全部闭合
  const expanded = [];
  document.querySelectorAll('.lora-panel-node-body:not(.collapsed)').forEach(body => {
    const card = body.closest('.lora-panel-node-card');
    if (card && card.getAttribute('data-lora-node')) expanded.push(card.getAttribute('data-lora-node'));
  });
  renderLoraPanel();
  // 恢复之前展开的节点
  expanded.forEach(nid => {
    const body = document.querySelector(`.lora-panel-node-card[data-lora-node="${nid}"] .lora-panel-node-body`);
    const header = document.querySelector(`.lora-panel-node-card[data-lora-node="${nid}"] .lora-panel-node-header`);
    if (body) body.classList.remove('collapsed');
    const arrow = header && header.querySelector('.lora-panel-arrow');
    if (arrow) arrow.classList.remove('collapsed');
  });
}

/** 面板：切换 Lora 启用/禁用（重渲染后保持节点展开状态，不闭合） */
function toggleLoraInPanel(nodeId, idx) {
  if (loraNodesConfig[nodeId] && loraNodesConfig[nodeId][idx]) {
    loraNodesConfig[nodeId][idx].on = !loraNodesConfig[nodeId][idx].on;
    refreshLoraPanelPreserveExpanded();
    _saveLoraConfig();
  }
}

/** 激活 Lora 权重滑块：点击激活后 3 秒无操作自动重新锁定（防止手机上误触）
 * 锁定状态：首次触摸/滑动只滚动页面不调权重；点击滑块激活后可拖动调整 */
function activateLoraSlider(event, slider) {
  if (event) event.stopPropagation();
  const wrap = slider.closest('.lora-strength-wrap');
  if (!wrap) return;
  wrap.classList.remove('lora-strength-locked');
  if (wrap._lockTimer) clearTimeout(wrap._lockTimer);
  wrap._lockTimer = setTimeout(() => {
    wrap.classList.add('lora-strength-locked');
  }, 3000);
}

// 手机触摸 tap 检测：touchstart 记录坐标，touchend 判断是否"点击"（位移 < 12px 视为点击激活）
function touchStartLoraSlider(event, slider) {
  const wrap = slider.closest('.lora-strength-wrap');
  if (!wrap) return;
  const t = event.touches[0];
  wrap._tx = t.clientX;
  wrap._ty = t.clientY;
}
function touchEndLoraSlider(event, slider) {
  const wrap = slider.closest('.lora-strength-wrap');
  if (!wrap || wrap._tx === undefined || wrap._ty === undefined) return;
  const t = event.changedTouches[0];
  const moved = Math.abs(t.clientX - wrap._tx) + Math.abs(t.clientY - wrap._ty);
  wrap._tx = undefined;
  wrap._ty = undefined;
  // 位移小 = 点击（tap）→ 激活滑块；位移大 = 滑动页面，不激活
  if (moved < 12) activateLoraSlider(event, slider);
}

/** 面板：更新 Lora 参数 + 防抖保存 */
function updateLoraInPanel(nodeId, idx, key, val) {
  if (loraNodesConfig[nodeId] && loraNodesConfig[nodeId][idx]) {
    loraNodesConfig[nodeId][idx][key] = val;
  }
  _scheduleLoraSave();
}

/** 面板：删除 Lora */
function deleteLoraInPanel(nodeId, idx) {
  if (loraNodesConfig[nodeId] && loraNodesConfig[nodeId][idx]) {
    loraNodesConfig[nodeId].splice(idx, 1);
    if (loraNodesConfig[nodeId].length === 0) delete loraNodesConfig[nodeId];
    refreshLoraPanelPreserveExpanded();
    _saveLoraConfig();
  }
}

/** 面板：切换触发词 */
function toggleLoraTwInPanel(nodeId, idx, word, checked) {
  if (!loraNodesConfig[nodeId] || !loraNodesConfig[nodeId][idx]) return;
  const lora = loraNodesConfig[nodeId][idx];
  if (!lora.trigger_words_selected) lora.trigger_words_selected = [];
  if (checked) {
    if (!lora.trigger_words_selected.includes(word)) lora.trigger_words_selected.push(word);
  } else {
    lora.trigger_words_selected = lora.trigger_words_selected.filter(w => w !== word);
  }
  // 更新外部面板中对应触发词标签的高亮样式（不重建整个面板）
  const card = document.querySelector(`.lora-panel-node-card[data-lora-node="${nodeId}"]`);
  if (card) {
    const item = card.querySelector(`.lora-item[data-idx="${idx}"]`);
    if (item) {
      const tags = item.querySelectorAll('.lora-tw-tag');
      tags.forEach(tag => {
        const cb = tag.querySelector('input[type="checkbox"]');
        if (cb && cb.getAttribute('onchange') && cb.getAttribute('onchange').includes(`'${escJsStr(word)}'`)) {
          if (checked) {
            tag.classList.add('tw-active');
            cb.checked = true;
          } else {
            tag.classList.remove('tw-active');
            cb.checked = false;
          }
        }
      });
    }
  }
  _saveLoraConfig();
}

/** 面板：全部开关 */
function toggleAllLorasInPanel(nodeId) {
  const loras = loraNodesConfig[nodeId];
  if (!loras || !loras.length) return;
  const allOn = loras.every(l => l.on);
  loras.forEach(l => { l.on = !allOn; });
  refreshLoraPanelPreserveExpanded();
  _saveLoraConfig();
}

let _loraSaveTimer = null;
/** 防抖保存 Lora 配置（200ms 内多次操作合并为一次） */
function _scheduleLoraSave() {
  if (_loraSaveTimer) clearTimeout(_loraSaveTimer);
  _loraSaveTimer = setTimeout(() => { _loraSaveTimer = null; _saveLoraConfig(); }, 200);
}

/** 删除 Lora（主面板） */
function deleteLoraPanelItem(nodeId, idx) {
  if (loraNodesConfig[nodeId] && loraNodesConfig[nodeId][idx]) {
    loraNodesConfig[nodeId].splice(idx, 1);
    if (loraNodesConfig[nodeId].length === 0) delete loraNodesConfig[nodeId];
    refreshLoraPanelPreserveExpanded();
    // 同时刷新弹窗（如果打开）
    const modalBody = document.getElementById('node_modal_body');
    if (modalBody && modalBody.dataset.nodeId === nodeId) showNodeDetail(nodeId);
    _saveLoraConfig();
  }
}

/** 保存 Lora 配置到后端（只带非空角色字段，避免空值覆盖后端已保存的节点配置） */
function _saveLoraConfig() {
  const saveData = {
    __lora_nodes__: { ...loraNodesConfig },
  };
  // 角色字段只在有值时附带：防止 resNodeId/promptNodeId 等为空时
  // 把后端已保存的分辨率节点/提示词节点等配置覆盖成空，导致刷新后旧版分辨率面板消失
  if (promptNodeId) saveData.__prompt_node__ = promptNodeId;
  if (negativeNodeId) saveData.__negative_node__ = negativeNodeId;
  if (resNodeId) saveData.__resolution_node__ = resNodeId;
  if (expandedTextNodeId) saveData.__expanded_text_node__ = expandedTextNodeId;
  const imgIds = (loadImageNodeIds || []).filter(id => id);
  if (imgIds.length) saveData.__load_image_nodes__ = imgIds.join(',');
  apiFetch('/api/workflow-params', { method: 'POST', body: JSON.stringify(saveData) }, true).catch(() => {});
}

function setPromptNode(n) {
  if (promptNodeId === n) { promptNodeId = ''; _explicitClear = { prompt: true }; updateNodeDisplay(); renderNodes(); setTimeout(() => { saveParams().finally(() => { _explicitClear = {}; }); }, 50); return; }
  // 校验：检查目标节点是否有文本参数
  const key = _getNodeFirstStringKey(n);
  if (!key) { toast('❌ 该节点没有文本输入框，请选择正确的正面提示词节点', 'error'); return; }
  promptInputKey = key;
  // 互斥：清除该节点已有的其他角色
  if (negativeNodeId === n) negativeNodeId = '';
  if (resNodeId === n) resNodeId = '';
  for (let i = 0; i < loadImageNodeIds.length; i++) { if (loadImageNodeIds[i] === n) loadImageNodeIds[i] = ''; }
  if (expandedTextNodeId === n) expandedTextNodeId = '';
  promptNodeId = n;
  updateNodeDisplay(); renderNodes();
  // 读取该节点的现有文本到 textarea
  _loadNodeTextToTextarea(n, key, 'prompt_textarea');
  setTimeout(() => saveParams(), 50);
}
function setNegativeNode(n) {
  if (negativeNodeId === n) { negativeNodeId = ''; _explicitClear = { negative: true }; updateNodeDisplay(); renderNodes(); setTimeout(() => { saveParams().finally(() => { _explicitClear = {}; }); }, 50); return; }
  // 校验：检查目标节点是否有文本参数
  const key = _getNodeFirstStringKey(n);
  if (!key) { toast('❌ 该节点没有文本输入框，请选择正确的负面提示词节点', 'error'); return; }
  negativeInputKey = key;
  // 互斥
  if (promptNodeId === n) promptNodeId = '';
  if (resNodeId === n) resNodeId = '';
  for (let i = 0; i < loadImageNodeIds.length; i++) { if (loadImageNodeIds[i] === n) loadImageNodeIds[i] = ''; }
  if (expandedTextNodeId === n) expandedTextNodeId = '';
  negativeNodeId = n;
  updateNodeDisplay(); renderNodes();
  // 读取该节点的现有文本到 textarea
  _loadNodeTextToTextarea(n, key, 'neg_textarea');
  setTimeout(() => saveParams(), 50);
}
/** 获取指定节点的第一个字符串参数名，没有则返回空字符串 */
function _getNodeFirstStringKey(nodeId) {
  if (!allParamsData || !allParamsData.nodes) return '';
  for (let i = 0; i < allParamsData.nodes.length; i++) {
    const n = allParamsData.nodes[i];
    if (n.id === nodeId) {
      for (let j = 0; j < n.params.length; j++) {
        if (typeof n.params[j].value === 'string') return n.params[j].key;
      }
      return '';
    }
  }
  return '';
}
/** 读取节点文本到指定 textarea */
function _loadNodeTextToTextarea(nodeId, key, textareaId) {
  if (!allParamsData || !allParamsData.nodes) return;
  for (let i = 0; i < allParamsData.nodes.length; i++) {
    const n = allParamsData.nodes[i];
    if (n.id === nodeId) {
      for (let j = 0; j < n.params.length; j++) {
        const p = n.params[j];
        if (p.key === key && typeof p.value === 'string') {
          const el = document.getElementById(textareaId);
          if (el) el.value = p.value;
          return;
        }
      }
    }
  }
}

function setResNode(n) {
  if (resNodeId === n) { resNodeId = ''; _explicitClear = { resolution: true }; }
  else {
    if (promptNodeId === n) promptNodeId = '';
    if (negativeNodeId === n) negativeNodeId = '';
    for (let i = 0; i < loadImageNodeIds.length; i++) { if (loadImageNodeIds[i] === n) loadImageNodeIds[i] = ''; }
    if (expandedTextNodeId === n) expandedTextNodeId = '';
    resNodeId = n;
  }
  updateNodeDisplay(); renderNodes();
  setTimeout(() => { saveParams().finally(() => { _explicitClear = {}; }); }, 50);
}
function setExpandedTextNode(n) {
  if (expandedTextNodeId === n) { expandedTextNodeId = ''; _explicitClear = { expanded_text: true }; updateNodeDisplay(); renderNodes(); setTimeout(() => { saveParams().finally(() => { _explicitClear = {}; }); }, 50); return; }
  if (promptNodeId === n) promptNodeId = '';
  if (negativeNodeId === n) negativeNodeId = '';
  if (resNodeId === n) resNodeId = '';
  for (let i = 0; i < loadImageNodeIds.length; i++) { if (loadImageNodeIds[i] === n) loadImageNodeIds[i] = ''; }
  expandedTextNodeId = n;
  updateNodeDisplay(); renderNodes();
  setTimeout(() => saveParams(), 50);
}
function toggleDisableNode(n) {
  const idx = disabledNodes.indexOf(n);
  if (idx >= 0) {
    disabledNodes.splice(idx, 1);
  } else {
    disabledNodes.push(n);
  }
  renderNodes();
  setTimeout(() => saveParams(), 50);
}
function setLoadImageNode(n, idx) {
  if (idx === undefined) idx = 0;
  if (loadImageNodeIds[idx] === n) { loadImageNodeIds[idx] = ''; _explicitClear = { load_image: true }; }
  else {
    for (let i = 0; i < loadImageNodeIds.length; i++) {
      if (loadImageNodeIds[i] === n && i !== idx) loadImageNodeIds[i] = '';
    }
    if (promptNodeId === n) promptNodeId = '';
    if (negativeNodeId === n) negativeNodeId = '';
    if (resNodeId === n) resNodeId = '';
    if (expandedTextNodeId === n) expandedTextNodeId = '';
    loadImageNodeIds[idx] = n;
  }
  updateNodeDisplay(); renderNodes();
  setTimeout(() => { saveParams().finally(() => { _explicitClear = {}; }); }, 50);
}

/** 序号输入框变更：把节点映射到对应槽位（冲突交换 + 非法回退） */
function setLoadImageOrder(nodeId, val) {
  const order = parseInt(val, 10);
  const input = document.querySelector('.img-order-input[data-nodeid="' + nodeId + '"]');
  if (isNaN(order) || order < 1 || order > 10) {
    const cur = loadImageNodeIds.indexOf(nodeId);
    if (input) input.value = cur >= 0 ? cur + 1 : 1;
    toast('序号需在 1~10 之间', 'error');
    return;
  }
  const idx = order - 1;
  // 该节点已占其他槽位 → 先清掉
  const oldIdx = loadImageNodeIds.indexOf(nodeId);
  if (oldIdx >= 0 && oldIdx !== idx) loadImageNodeIds[oldIdx] = '';
  // 目标槽被其他节点占用 → 交换（被顶的节点放到旧槽）
  if (loadImageNodeIds[idx] && loadImageNodeIds[idx] !== nodeId) {
    const displaced = loadImageNodeIds[idx];
    loadImageNodeIds[idx] = nodeId;
    if (oldIdx >= 0) loadImageNodeIds[oldIdx] = displaced;
    else toast('序号 ' + order + ' 已被占用，已替换', 'error');
  } else {
    loadImageNodeIds[idx] = nodeId;
  }
  updateNodeDisplay(); renderNodes();
  setTimeout(() => saveParams(), 50);
}

/** 音频序号输入框变更：映射到 loadAudioNodeIds 槽位（冲突交换 + 非法回退） */
function setLoadAudioOrder(nodeId, val) {
  const order = parseInt(val, 10);
  const input = document.querySelector('.order-input[data-nodeid="' + nodeId + '"]');
  if (isNaN(order) || order < 1 || order > 10) {
    const cur = loadAudioNodeIds.indexOf(nodeId);
    if (input) input.value = cur >= 0 ? cur + 1 : 1;
    toast('序号需在 1~10 之间', 'error');
    return;
  }
  const idx = order - 1;
  const oldIdx = loadAudioNodeIds.indexOf(nodeId);
  if (oldIdx >= 0 && oldIdx !== idx) loadAudioNodeIds[oldIdx] = '';
  if (loadAudioNodeIds[idx] && loadAudioNodeIds[idx] !== nodeId) {
    const displaced = loadAudioNodeIds[idx];
    loadAudioNodeIds[idx] = nodeId;
    if (oldIdx >= 0) loadAudioNodeIds[oldIdx] = displaced;
    else toast('序号 ' + order + ' 已被占用，已替换', 'error');
  } else {
    loadAudioNodeIds[idx] = nodeId;
  }
  updateNodeDisplay(); renderNodes();
  setTimeout(() => saveParams(), 50);
}

/** 视频序号输入框变更：映射到 loadVideoNodeIds 槽位（冲突交换 + 非法回退） */
function setLoadVideoOrder(nodeId, val) {
  const order = parseInt(val, 10);
  const input = document.querySelector('.order-input[data-nodeid="' + nodeId + '"]');
  if (isNaN(order) || order < 1 || order > 10) {
    const cur = loadVideoNodeIds.indexOf(nodeId);
    if (input) input.value = cur >= 0 ? cur + 1 : 1;
    toast('序号需在 1~10 之间', 'error');
    return;
  }
  const idx = order - 1;
  const oldIdx = loadVideoNodeIds.indexOf(nodeId);
  if (oldIdx >= 0 && oldIdx !== idx) loadVideoNodeIds[oldIdx] = '';
  if (loadVideoNodeIds[idx] && loadVideoNodeIds[idx] !== nodeId) {
    const displaced = loadVideoNodeIds[idx];
    loadVideoNodeIds[idx] = nodeId;
    if (oldIdx >= 0) loadVideoNodeIds[oldIdx] = displaced;
    else toast('序号 ' + order + ' 已被占用，已替换', 'error');
  } else {
    loadVideoNodeIds[idx] = nodeId;
  }
  updateNodeDisplay(); renderNodes();
  setTimeout(() => saveParams(), 50);
}

/* ========== 上传媒体卡片拖动排序（长按/拖动重排，决定加载先后） ========== */
let _dragCardFrom = null;      // 拖动中的 nodeId
let _dragCardTimer = null;     // 长按激活定时器

/** PC 拖动开始 */
function onUploadCardDragStart(event, nodeId) {
  // 序号输入框 / 不使用 / 清空按钮不触发拖动
  if (event.target.closest('.ua169-meta') || event.target.closest('.ua169-clear')) {
    event.preventDefault();
    return;
  }
  _dragCardFrom = nodeId;
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('text/plain', nodeId);
  event.currentTarget.classList.add('dragging');
}
function onUploadCardDragOver(event) {
  event.preventDefault();
  event.dataTransfer.dropEffect = 'move';
  document.querySelectorAll('.upload-media-card.drag-over').forEach(el => el.classList.remove('drag-over'));
  event.currentTarget.classList.add('drag-over');
}
function onUploadCardDrop(event, toNode) {
  event.preventDefault();
  const fromNode = _dragCardFrom || event.dataTransfer.getData('text/plain');
  if (fromNode && fromNode !== toNode) reorderUploadCards(fromNode, toNode);
  _dragCardFrom = null;
}
function onUploadCardDragEnd(event) {
  event.currentTarget.classList.remove('dragging');
  document.querySelectorAll('.upload-media-card.drag-over').forEach(el => el.classList.remove('drag-over'));
  _dragCardFrom = null;
}

/** 移动端：长按 400ms 激活拖拽（卡片跟随手指），松手重排 */
function onUploadCardTouchStart(event, nodeId) {
  if (event.target.closest('.ua169-meta') || event.target.closest('.ua169-clear')) return;
  const card = event.currentTarget;
  _dragCardFrom = nodeId;
  _dragCardTimer = setTimeout(() => {
    card.classList.add('dragging');
  }, 400);
}
function onUploadCardTouchMove(event, nodeId) {
  const card = event.currentTarget;
  if (!card.classList.contains('dragging')) return;
  event.preventDefault();
  const t = event.touches[0];
  card.style.transform = `translate(${t.clientX - card.getBoundingClientRect().left - card.offsetWidth / 2}px, ${t.clientY - card.getBoundingClientRect().top - card.offsetHeight / 2}px)`;
}
function onUploadCardTouchEnd(event, nodeId) {
  clearTimeout(_dragCardTimer);
  const card = event.currentTarget;
  card.classList.remove('dragging');
  card.style.transform = '';
  // 松手：按触摸点命中的目标卡片重排
  if (_dragCardFrom && _dragCardFrom !== nodeId) {
    reorderUploadCards(_dragCardFrom, nodeId);
  }
  _dragCardFrom = null;
}

/** 重排核心：把 fromNode 卡片移到 toNode 之前，重写槽位数组 + DOM 即时重排 + 保存 */
function reorderUploadCards(fromNode, toNode) {
  const arr = loadImageNodeIds.filter(id => id);
  const fi = arr.indexOf(fromNode), ti = arr.indexOf(toNode);
  if (fi < 0 || ti < 0 || fi === ti) return;
  // DOM 即时重排：拖动卡片插入到目标卡片前（视觉立即更新，不等重渲染）
  const fromCard = document.querySelector(`.upload-media-card[data-nodeid="${fromNode}"]`);
  const toCard = document.querySelector(`.upload-media-card[data-nodeid="${toNode}"]`);
  if (fromCard && toCard && fromCard !== toCard) {
    if (fi < ti) toCard.after(fromCard);       // 向后移 → 放到目标后
    else toCard.before(fromCard);              // 向前移 → 放到目标前
  }
  const item = arr.splice(fi, 1)[0];
  arr.splice(ti, 0, item);
  loadImageNodeIds = ['', '', '', '', '', '', '', '', '', ''];
  arr.forEach((id, i) => { if (i < 10) loadImageNodeIds[i] = id; });
  // 刷新各卡片序号输入框（按新顺序 1..N）
  document.querySelectorAll('.upload-media-card[data-nodeid]').forEach((card) => {
    const nid = card.dataset.nodeid;
    const input = card.querySelector('.order-input');
    const newIdx = loadImageNodeIds.indexOf(nid);
    if (input && newIdx >= 0) input.value = newIdx + 1;
  });
  setTimeout(() => saveParams(), 50);
}


/** 清空加载节点的输入（图片 X 按钮）：调后端置空 + 清理 saved_texts + 重渲染 */
function clearNodeMedia(nodeId, key) {
  apiFetch('/api/clear-node-input', { method:'POST', body:JSON.stringify({ node_id: nodeId, key: key }) }, true)
    .then((d) => {
      if (d && d.ok) {
        toast(`✅ 已清空节点 #${nodeId}`);
        // 重新拉取参数（后端已置空节点输入），避免用旧 allParamsData 重渲染导致图片恢复
        refreshParamsAfterDuration();
      } else {
        toast(d?.error || '清空失败', 'error');
      }
    }).catch(e => { console.error('clearNodeMedia error:', e); toast('清空失败', 'error'); });
}

function clearNodeRole(role) {
  // 记录本次是「用户主动取消指定」→ saveParams 时允许发送空值覆盖后端
  _explicitClear = _explicitClear || {};
  _explicitClear[role] = true;
  if (role === 'prompt') promptNodeId = '';
  else if (role === 'negative') negativeNodeId = '';
  else if (role === 'resolution') resNodeId = '';
  else if (role === 'load_image') loadImageNodeIds = ['', '', '', '', '', '', '', '', '', ''];
  else if (role === 'load_audio') loadAudioNodeIds = ['', '', ''];
  else if (role === 'load_video') loadVideoNodeIds = ['', '', ''];
  else if (role === 'expanded_text') expandedTextNodeId = '';
  else if (role === 'disabled') disabledNodes = [];
  updateNodeDisplay(); renderNodes();
  setTimeout(() => { saveParams().finally(() => { _explicitClear = {}; }); }, 50);
}

function clearAllNodeRoles() {
  const hasAny = promptNodeId || negativeNodeId || resNodeId || expandedTextNodeId
    || loadImageNodeIds.some(id => id) || loadAudioNodeIds.some(id => id)
    || loadVideoNodeIds.some(id => id) || disabledNodes.length > 0;
  if (!hasAny) return;
  // 全部角色都标记为显式清空
  _explicitClear = { prompt: true, negative: true, resolution: true, expanded_text: true,
                     load_image: true, load_audio: true, load_video: true };
  promptNodeId = ''; negativeNodeId = ''; resNodeId = ''; expandedTextNodeId = '';
  loadImageNodeIds = ['', '', '', '', '', '', '', '', '', ''];
  loadAudioNodeIds = ['', '', ''];
  loadVideoNodeIds = ['', '', ''];
  disabledNodes = [];
  updateNodeDisplay(); renderNodes();
  setTimeout(() => { saveParams().finally(() => { _explicitClear = {}; }); }, 50);
  toast('已清除所有节点指定');
}

// ========================================================================
const XIN_IC = (k) => `<img class="xin-ic" src="/theme-xin-icon-role-${k}.webp" alt="" loading="lazy">`;
function renderNodes() {
  let h = '';
  if (!allParamsData || !allParamsData.nodes) {
    document.getElementById('node_grid').innerHTML = '<div style="grid-column:1/-1;padding:20px;text-align:center;color:var(--text-sub);font-size:12px">无节点数据</div>';
    return;
  }
  // 已指定角色的节点排前面，Lora 节点紧随其后，其余按原序
  const groupOrder = (n) => {
    if (n.id === promptNodeId) return 0;
    if (n.id === negativeNodeId) return 1;
    if (loadImageNodeIds.indexOf(n.id) >= 0) return 2;
    if (n.id === resNodeId) return 3;
    if (n.id === expandedTextNodeId) return 4;
    if (disabledNodes.indexOf(n.id) >= 0) return 0;
    // 多 Lora 节点自动检测（如 Power Lora Loader），排在指定节点之后、普通节点之前
    if (/power\s*lora/i.test(n.class_type) || /power\s*lora/i.test(n.title)) return 4.5;
    return 5;
  };
  const sortedNodes = [...allParamsData.nodes].sort((a, b) => groupOrder(a) - groupOrder(b));
  sortedNodes.forEach((n) => {
    // 该节点占用的图片槽位索引（未占用为 -1）
    const iLIndex = loadImageNodeIds.indexOf(n.id);
    const iLany = iLIndex >= 0;
    const iP = n.id === promptNodeId, iR = n.id === resNodeId, iN = n.id === negativeNodeId, iE = n.id === expandedTextNodeId;
    const isAssigned = iP || iR || iLany || iN || iE;
    const isDisabled = disabledNodes.indexOf(n.id) >= 0;
    const isLoraCard = !isAssigned && !isDisabled && (/power\s*lora/i.test(n.class_type) || /power\s*lora/i.test(n.title));
    let cls = 'node-card';
    if (iP) cls += ' prompt-active';
    if (iR) cls += ' res-active';
    if (iLany) cls += ' load-active';
    if (iN) cls += ' neg-active';
    if (iE) cls += ' exp-active';
    if (isDisabled) cls += ' node-disabled';
    if (isLoraCard) cls += ' lora-active';
    const icon = isDisabled ? XIN_IC('disabled') : (iN ? XIN_IC('negative') : (iP ? XIN_IC('prompt') : (iLany ? XIN_IC('load') : (iR ? XIN_IC('res') : (iE ? XIN_IC('exp') : (isLoraCard ? XIN_IC('lora') : XIN_IC('node')))))));
    // 角色标签
    let roleLabel = '';
    if (iP) roleLabel = `<span class="node-role-label role-prompt">${XIN_IC('prompt')} 正面</span>`;
    else if (iN) roleLabel = `<span class="node-role-label role-negative">${XIN_IC('negative')} 负面</span>`;
    else if (iLany) roleLabel = `<span class="node-role-label role-load">${XIN_IC('load')} 图片</span>`;
    else if (iR) roleLabel = `<span class="node-role-label role-res">${XIN_IC('res')} 分辨率</span>`;
    else if (iE) roleLabel = `<span class="node-role-label role-exp">${XIN_IC('exp')} 扩写文本</span>`;
    else if (isDisabled) roleLabel = `<span class="node-role-label role-disabled">${XIN_IC('disabled')} 已禁用</span>`;
    else if (isLoraCard) roleLabel = `<span class="node-role-label role-lora">${XIN_IC('lora')} Lora</span>`;
    // 互斥按钮
    const posDisabled = (promptNodeId && !iP) || (isAssigned && !iP) ? ' disabled' : '';
    const negDisabled = (negativeNodeId && !iN) || (isAssigned && !iN) ? ' disabled' : '';
    const resDisabled = (isAssigned && !iR) ? ' disabled' : '';
    const expDisabled = (expandedTextNodeId && !iE) || (isAssigned && !iE) ? ' disabled' : '';
    // 图片槽位按钮：该节点已被其他角色占用时禁用
    const imgDisabled = (isAssigned && !iLany) ? ' disabled' : '';
    let tags = '';
    tags += `<button class="node-tag-btn${  iP ? ' active-success' : ''  }"${  posDisabled  } data-action="setPromptNode" data-nodeid="'${  n.id  }'" aria-label="设为正面提示词节点"><svg class="icon-xs" aria-hidden="true"><use href="#icon-check"/></svg></button>`;
    tags += `<button class="node-tag-btn${  iN ? ' active-negative' : ''  }"${  negDisabled  } data-action="setNegativeNode" data-nodeid="'${  n.id  }'" aria-label="设为负面提示词节点"><svg class="icon-xs" aria-hidden="true"><use href="#icon-prohibited"/></svg></button>`;
    tags += `<button class="node-tag-btn${  iR ? ' active-warning' : ''  }"${  resDisabled  } data-action="setResNode" data-nodeid="'${  n.id  }'" aria-label="设为分辨率节点"><svg class="icon-xs" aria-hidden="true"><use href="#icon-ruler"/></svg></button>`;
    tags += `<button class="node-tag-btn${  iE ? ' active-accent' : ''  }"${  expDisabled  } data-action="setExpandedTextNode" data-nodeid="'${  n.id  }'" aria-label="设为扩写后文本节点"><svg class="icon-xs" aria-hidden="true"><use href="#icon-document"/></svg></button>`;
    // 禁用/启用切换按钮（用于 Lora 等节点，⛔ 区别于负面 🚫）
    tags += `<button class="node-tag-btn${  isDisabled ? ' active-danger' : ''  }" data-action="toggleDisableNode" data-nodeid="'${  n.id  }'" aria-label="禁用/启用此节点"><svg class="icon-xs" aria-hidden="true"><use href="#icon-ban"/></svg></button>`;
    h += `<div class="${  cls  }" data-search="${  (`${n.title  } ${  n.id  } ${  n.class_type}`).toLowerCase()  }" data-action="showNodeDetail" data-nodeid="'${  n.id  }'" tabindex="0" role="button" onkeydown="if(event.key==='Enter')showNodeDetail('${  n.id  }')" aria-label="节点 ${  n.title  }">`;
    h += roleLabel;
    h += `<div><span class="node-icon">${  icon  }</span><span class="node-id">#${  n.id  }</span></div>`;
    h += `<div class="node-title">${  n.title  }</div>`;
    h += `<div class="node-type">${  n.class_type  }</div>`;
    // 显示提示词参数预览（自动取第一个字符串参数）
    const firstStrParam = n.params && n.params.find(p => typeof p.value === 'string' && p.value);
    if (firstStrParam) {
      const preview = firstStrParam.value.length > 50 ? firstStrParam.value.substring(0, 50) + '…' : firstStrParam.value;
      const escaped = preview.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      h += `<div class="node-text-preview">${  escaped  }</div>`;
    }
    h += `<div class="node-tags">${  tags  }</div>`;
    h += '</div>';
  });
  document.getElementById('node_grid').innerHTML = h || '<div style="grid-column:1/-1;padding:20px;text-align:center;color:var(--text-sub)">无节点</div>';
  // 重建后重新应用搜索过滤，保持当前搜索关键词
  filterNodes();
}

// ========================================================================
// 魔导书·LLM 提示词规则页（文生图规划 / 图片反推·多图结构化）
// ========================================================================
let _grimRules = { t2i: [], imgrev: [] };
let _grimRulesActive = {};
let _grimRulesTab = 't2i';
let _grimRulesEditIdx = null;
let _grimRulesOpen = false;
let _grimRuleWorkflows = [], _grimRuleBindings = {}, _grimRuleNames = { t2i: [], imgrev: [] };

async function toggleGrimoireRules() {
  _grimRulesOpen = !_grimRulesOpen;
  const panel = document.getElementById('grimoire_rules_panel');
  const srcArea = document.querySelector('.grimoire-sources');
  const detailArea = document.querySelector('.grimoire-detail');
  const k2Panel = document.getElementById('grimoire_k2_panel');
  if (panel) panel.style.display = _grimRulesOpen ? 'block' : 'none';
  const hideBody = _grimRulesOpen ? 'none' : '';
  if (srcArea) srcArea.style.display = hideBody;
  if (detailArea) detailArea.style.display = hideBody;
  if (k2Panel && _k2ComposeMode === 'k2') k2Panel.style.display = _grimRulesOpen ? 'none' : 'block';
  if (_grimRulesOpen) await loadGrimRules();
}

async function loadGrimRules() {
  try {
    const d = await apiFetch('/api/llm-templates', {}, true);
    _grimRules.t2i = d.t2i || [];
    _grimRules.imgrev = d.imgrev || [];
    _grimRulesActive = d.active || {};
    _grimRuleWorkflows = d.workflows || [];
    _grimRuleBindings = d.bindings || {};
    _grimRuleNames = d.rule_names || { t2i: [], imgrev: [] };
    grimRulesRender();
  } catch (e) { toast('规则加载失败', 'error'); }
}

function grimRulesTab(t) {
  _grimRulesTab = t;
  _grimRulesEditIdx = null;
  const btnIds = { t2i: 'grm_rules_tab_t2i', imgrev: 'grm_rules_tab_imgrev', bind: 'grm_rules_tab_bind' };
  Object.entries(btnIds).forEach(([key, id]) => {
    const b = document.getElementById(id);
    if (b) b.className = 'btn btn-xs ' + (t === key ? 'btn-primary' : 'btn-secondary');
  });
  const ed = document.getElementById('grm_rules_editor');
  if (ed) ed.style.display = 'none';
  grimRulesRender();
}

function grimRulesRender() {
  const list = document.getElementById('grm_rules_list');
  if (!list) return;
  if (_grimRulesTab === 'bind') {
    const wfs = _grimRuleWorkflows || [];
    if (!wfs.length) { list.innerHTML = '<div style="font-size:11px;color:var(--text-sub2);padding:8px">无工作流</div>'; return; }
    list.innerHTML = wfs.map((wf) => {
      const val = _grimRuleBindings[wf.name] || '';
      const bound = val && val !== 'none' ? val : '';
      const names = wf.category === '文生图' ? (_grimRuleNames.t2i || []) : (_grimRuleNames.imgrev || []); // 名字字符串数组（_grimRules.* 是 {name,content} 对象数组，直接 map 会渲染成 [object Object]）
      const opts = ['<option value="none"' + (!bound ? ' selected' : '') + '>不注入</option>']
        .concat(names.map((n) => '<option value="' + esc(n) + '"' + (bound === n ? ' selected' : '') + '>' + esc(n) + '</option>')).join('');
      const wSafe = String(wf.name).replace(/'/g, "\\'");
      return `<div style="display:flex;align-items:center;gap:8px;font-size:11px;padding:6px 8px;border:1px solid var(--card-border);border-radius:var(--radius-sm);background:var(--input-bg)">
        <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(wf.name)} <span style="color:var(--text-sub2)">（${esc(wf.category || '未分类')}）</span></span>
        <select onchange="grimRulesBind('${wSafe}', this.value)" style="padding:3px 6px;border-radius:6px;border:1px solid var(--card-border);background:var(--input-bg);color:var(--text);font-size:11px;max-width:180px">${opts}</select>
      </div>`;
    }).join('');
    return;
  }
  const arr = _grimRules[_grimRulesTab] || [];
  if (!arr.length) {
    list.innerHTML = '<div style="font-size:11px;color:var(--text-sub2);padding:8px">暂无规则，点「＋新增规则」创建</div>';
    return;
  }
  list.innerHTML = arr.map((t, i) => {
    const preview = (t.content || '').replace(/\n/g, ' ').slice(0, 60);
    return `<div style="display:flex;align-items:center;gap:8px;font-size:11px;padding:6px 8px;border:1px solid var(--card-border);border-radius:var(--radius-sm);background:var(--input-bg)">
      <span style="flex:1;min-width:0;display:flex;align-items:center;gap:6px;overflow:hidden" title="${esc(preview)}">
        <span style="font-weight:600;white-space:nowrap">${esc(t.name)}</span>
        ${t.chk_quality ? '<span style="font-size:9px;padding:1px 4px;border-radius:5px;background:color-mix(in oklch, var(--primary) 15%, transparent);color:var(--primary)">质</span>' : ''}
        ${t.chk_weights ? '<span style="font-size:9px;padding:1px 4px;border-radius:5px;background:color-mix(in oklch, var(--primary) 15%, transparent);color:var(--primary)">权</span>' : ''}
        ${t.chk_commas ? `<span style="font-size:9px;padding:1px 4px;border-radius:5px;background:color-mix(in oklch, var(--primary) 15%, transparent);color:var(--primary)">逗≤${t.chk_commas}</span>` : ''}
        <span style="color:var(--text-sub2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(preview)}</span>
      </span>
      <button class="btn btn-xs btn-secondary" onclick="grimRulesEdit(${i})">编辑</button>
      <button class="btn btn-xs btn-secondary" onclick="grimRulesDel(${i})">删</button>
    </div>`;
  }).join('');
}

async function grimRulesPost(payload) {
  const r = await apiFetch('/api/llm-templates', { method: 'POST', body: JSON.stringify(payload) }, true);
  if (!r || r.ok === false) { toast((r && r.error) || '操作失败', 'error'); return false; }
  return true;
}

async function grimRulesBind(wfName, rule) {
  if (await grimRulesPost({ op: 'bind', workflow: wfName, rule: rule })) {
    if (rule) _grimRuleBindings[wfName] = rule; else delete _grimRuleBindings[wfName];
    toast(rule === 'none' ? '✅ 已设为不注入' : (rule ? '✅ 已绑定规则' : '✅ 已恢复继承默认'));
  }
}

async function grimRulesSetActive(i) {
  const t = (_grimRules[_grimRulesTab] || [])[i];
  if (!t) return;
  if (await grimRulesPost({ op: 'active', type: _grimRulesTab, name: t.name })) {
    _grimRulesActive[_grimRulesTab] = t.name;
    toast('✅ 已启用，下次对话生成即生效');
    grimRulesRender();
  }
}

function grimRulesAdd() {
  _grimRulesEditIdx = -1;
  const ed = document.getElementById('grm_rules_editor');
  document.getElementById('grm_rule_name').value = '';
  document.getElementById('grm_rule_content').value = '';
  document.getElementById('grm_rule_chk_quality').checked = false;
  document.getElementById('grm_rule_chk_weights').checked = false;
  document.getElementById('grm_rule_chk_commas').value = 0;
  if (ed) ed.style.display = 'flex';
}

function grimRulesEdit(i) {
  const t = (_grimRules[_grimRulesTab] || [])[i];
  if (!t) return;
  _grimRulesEditIdx = i;
  document.getElementById('grm_rule_name').value = t.name;
  document.getElementById('grm_rule_content').value = t.content || '';
  document.getElementById('grm_rule_chk_quality').checked = !!t.chk_quality;
  document.getElementById('grm_rule_chk_weights').checked = !!t.chk_weights;
  document.getElementById('grm_rule_chk_commas').value = t.chk_commas || 0;
  const ed = document.getElementById('grm_rules_editor');
  if (ed) ed.style.display = 'flex';
  ed && ed.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function grimRulesCancel() {
  _grimRulesEditIdx = null;
  const ed = document.getElementById('grm_rules_editor');
  if (ed) ed.style.display = 'none';
}

async function grimRulesSave() {
  const name = document.getElementById('grm_rule_name').value.trim();
  const content = document.getElementById('grm_rule_content').value;
  if (!name || !content.trim()) { toast('名称和内容都要填', 'error'); return; }
  const arr = [...(_grimRules[_grimRulesTab] || [])];
  const entry = { name, content,
    chk_quality: document.getElementById('grm_rule_chk_quality').checked,
    chk_weights: document.getElementById('grm_rule_chk_weights').checked,
    chk_commas: parseInt(document.getElementById('grm_rule_chk_commas').value, 10) || 0 };
  if (_grimRulesEditIdx !== null && _grimRulesEditIdx >= 0) arr[_grimRulesEditIdx] = entry;
  else {
    if (arr.some(t => t.name === name)) { toast('同名规则已存在', 'error'); return; }
    arr.push(entry);
  }
  if (await grimRulesPost({ op: 'save', type: _grimRulesTab, list: arr })) {
    _grimRules[_grimRulesTab] = arr;
    _grimRulesEditIdx = null;
    document.getElementById('grm_rules_editor').style.display = 'none';
    toast('✅ 规则已保存并注入');
    grimRulesRender();
  }
}

async function grimRulesDel(i) {
  const t = (_grimRules[_grimRulesTab] || [])[i];
  if (!t || !confirm(`确定删除规则「${t.name}」？`)) return;
  const arr = (_grimRules[_grimRulesTab] || []).filter((x, j) => j !== i);
  if (await grimRulesPost({ op: 'save', type: _grimRulesTab, list: arr })) {
    _grimRules[_grimRulesTab] = arr;
    if (_grimRulesActive[_grimRulesTab] === t.name) delete _grimRulesActive[_grimRulesTab];
    toast('已删除');
    grimRulesRender();
  }
}

// ========================================================================
// 媒体资产侧边栏（ComfyUI 风格：生成完即可见）
// ========================================================================
let _mpTab = 'image';
let _mpLimit = 40; // 首屏限量渲染，避免大列表一次建几百个 DOM
function toggleMediaPanel() {
  const p = document.getElementById('media_panel');
  if (!p) return;
  const show = !p.classList.contains('show');
  p.classList.toggle('show', show);
  // v4.13.4: 面板出现时左侧导航自动让位（悬停左缘可唤回）
  document.body.classList.toggle('media-panel-open', show);
  if (show) loadMediaPanel();
}
function setMpTab(t) {
  _mpTab = t;
  _mpLimit = 40;
  document.querySelectorAll('.mp-tab').forEach((b) => b.classList.toggle('active', b.dataset.mp === t));
  loadMediaPanel(true); // v4.12.2: 切 tab 必须按新类型重新取数（_mpData 按 tab 类型从服务端拉取）
}
let _mpData = []; // v4.12.0: 媒体面板独立数据（分页后不再与画廊共享全量列表）
async function loadMediaPanel(force = false) {
  if (!force && _mpData && _mpData.length) { renderMediaPanel(); return; }
  try {
    const d = await apiFetch('/api/gallery?limit=60&type=' + encodeURIComponent(_mpTab), {}, true);
    _mpData = (d && d.images) ? d.images : [];
    renderMediaPanel();
  } catch (e) { /* 忽略：面板开着时静默失败 */ }
}
function mpFmtTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const p2 = (n) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`;
}
function renderMediaPanel() {
  const list = document.getElementById('media_panel_list');
  const search = document.getElementById('mp_search');
  if (!list) return;
  const q = ((search && search.value) || '').toLowerCase();
  const items = (_mpData || []).filter((i) => (!q || (i.name || '').toLowerCase().includes(q)));
  if (!items.length) { list.innerHTML = '<div class="mp-empty">暂无资产</div>'; return; }
  const shown = items.slice(0, _mpLimit);
  const more = items.length > shown.length ? `<div class="mp-more" onclick="_mpLimit=9999;renderMediaPanel()">显示全部 ${items.length} 张</div>` : '';
  list.innerHTML = shown.map((img) => {
    if (img.type === 'video') {
      return `
    <div class="mp-item">
      <video class="mp-item-thumb" src="${img.url || ''}" muted preload="metadata" playsinline controls onerror="this.style.opacity=.3"></video>
      <div class="mp-item-meta"><div class="mp-item-name">${img.name || ''}</div><div class="mp-item-sub">${mpFmtTime(img.mtime)}</div></div>
    </div>`;
    }
    return `
    <div class="mp-item" onclick="openLightbox('${img.url || ''}', '${(img.name || '').replace(/'/g, '')}')">
      <img class="mp-item-thumb" loading="lazy" src="${img.url || ''}" alt="" onerror="this.style.opacity=.3">
      <div class="mp-item-meta"><div class="mp-item-name">${img.name || ''}</div><div class="mp-item-sub">${mpFmtTime(img.mtime)}</div></div>
    </div>`;
  }).join('') + more;
}
document.addEventListener('input', (e) => { if (e.target && e.target.id === 'mp_search') { _mpLimit = 40; renderMediaPanel(); } });

// 画廊导航交互（仅 PC）：单击开媒体面板，双击进完整画廊
let _galleryTapTimer = null;
function galleryTabTap() {
  if (window.innerWidth < 900) { switchPage('gallery'); return; }
  if (_galleryTapTimer) { clearTimeout(_galleryTapTimer); _galleryTapTimer = null; return; }
  _galleryTapTimer = setTimeout(() => { _galleryTapTimer = null; toggleMediaPanel(); }, 260);
}
function galleryTabDbl() {
  if (_galleryTapTimer) { clearTimeout(_galleryTapTimer); _galleryTapTimer = null; }
  if (window.innerWidth < 900) return;
  switchPage('gallery');
}

// ========================================================================
// Lora 管理
// ========================================================================
async function loadLoras() {
  availableLoras = await apiFetch('/api/loras', {}, true) || [];
  // 等待元数据加载完成，避免首次选择 Lora 时 metadata 还是空的
  await loadLoraMetadata();
}

async function loadLoraMetadata() {
  try {
    loraMetadata = await apiFetch('/api/lora-metadata', {}, true) || {};
    // 元数据加载完成后刷新 Lora 面板（保留展开状态，避免重渲染后全部折叠）
    refreshLoraPanelPreserveExpanded();
  } catch(e) {
    console.warn('Lora 元数据加载失败:', e);
    loraMetadata = {};
  }
}

/**
 * 根据 lora_name 匹配元数据
 * @param {string} loraName - ComfyUI 风格的路径如 "ZImage/ZIT/xxx.safetensors"
 * @returns {object|null} {trigger_words, preview_url, model_name, tags, file_path}
 */
function matchLoraMeta(loraName) {
  if (!loraName || !loraMetadata) return null;
  // 精确匹配
  if (loraMetadata[loraName]) return loraMetadata[loraName];
  // 标准化路径分隔符
  const norm = loraName.replace(/\\/g, '/');
  if (loraMetadata[norm]) return loraMetadata[norm];
  // 互转的分隔符版本
  const altSep = norm.replace(/\//g, '\\');
  if (loraMetadata[altSep]) return loraMetadata[altSep];
  // 按 basename 匹配
  const bn = norm.split('/').pop();
  const nameNoExt = bn.replace(/\.(safetensors|ckpt|pt|pth)$/i, '');
  const bnKey = '__bn__' + nameNoExt;
  if (loraMetadata[bnKey]) return loraMetadata[bnKey];
  // 模糊匹配：遍历缓存看末尾是否匹配
  for (const [key, meta] of Object.entries(loraMetadata)) {
    if (key.startsWith('__bn__')) continue;
    if (key.endsWith(bn)) return meta;
  }
  // 兜底：整串无分隔符（如 'Anima风格功能WAK-000046.safetensors'），
  // 尝试在 __bn__ 索引里找以 bn_name + .safetensors 结尾的项
  if (!loraName.includes('/') && !loraName.includes('\\')) {
    for (const [key, meta] of Object.entries(loraMetadata)) {
      if (!key.startsWith('__bn__')) continue;
      const bnName = key.substring(6);
      if (loraName.endsWith(bnName + '.safetensors') || loraName.endsWith(bnName)) {
        return meta;
      }
    }
    // 也试试 path 版 key 以文件名结尾的
    const lowerName = loraName.toLowerCase();
    for (const [key, meta] of Object.entries(loraMetadata)) {
      if (key.startsWith('__bn__')) continue;
      if (key.toLowerCase().endsWith(lowerName) || key.toLowerCase().endsWith(lowerName.replace('.safetensors', ''))) {
        return meta;
      }
    }
  }
  console.warn(`[Lora] 未匹配到元数据: ${loraName} (bn=${nameNoExt}, cache keys: ${Object.keys(loraMetadata).length})`);
  return null;
}

/**
 * 从 LoRA Manager 的完整 file_path 提取 ComfyUI 风格的 lora_name
 * 例: E:/AIwork/.../loras/Anima/风格功能/wanx-000015.safetensors
 *   → Anima\风格功能\wanx-000015.safetensors
 */
function extractComfyUIPath(filePath) {
  if (!filePath) return null;
  const normalized = filePath.replace(/\\/g, '/');
  const lower = normalized.toLowerCase();
  const idx = lower.indexOf('/loras/');
  if (idx >= 0) {
    return normalized.substring(idx + 7).replace(/\//g, '\\');
  }
  return null;
}

// ========================================================================
// Lora 管理（节点弹窗内）
// ========================================================================
let _loraTouchTimer = null;
let _loraTouchNodeId = null;
let _loraTouchIdx = -1;
let _loraTouchMoved = false;

function onLoraTouchStart(event, nodeId, idx) {
  _loraTouchMoved = false;
  _loraTouchNodeId = nodeId;
  _loraTouchIdx = idx;
  _loraTouchTimer = setTimeout(() => {
    if (!_loraTouchMoved) {
      showLoraContextMenuInModal(nodeId, idx, event);
    }
    _loraTouchTimer = null;
  }, 500);
}
function onLoraTouchEnd(event, nodeId, idx) {
  if (_loraTouchTimer) { clearTimeout(_loraTouchTimer); _loraTouchTimer = null; }
}
function onLoraTouchMove() {
  _loraTouchMoved = true;
  if (_loraTouchTimer) { clearTimeout(_loraTouchTimer); _loraTouchTimer = null; }
}

// ========================================================================
// Lora 下拉面板（搜索在面板顶部）
// ========================================================================
/** 移除全部 Lora 下拉面板并恢复页面滚动（面板打开时锁定滚动，保证选择框固定在视口不随页面下滑） */
function _cleanupLoraPanels() {
  document.querySelectorAll('.lora-dropdown-panel').forEach(p => p.remove());
  document.querySelectorAll('.lora-select').forEach(el => el._ldpAttached = false);
  if (!document.querySelector('.lora-dropdown-panel')) {
    document.body.style.overflow = '';
  }
}

function toggleLoraDropdown(event, trigger, nodeId, idx, fromPanel) {
  event.stopPropagation();
  // 如果已有面板，先关掉所有（避免多个下拉共存）
  const allPanels = document.querySelectorAll('.lora-dropdown-panel');
  // 检查点的是不是当前 trigger 关联的面板（通过 trigger 上的状态位判断）
  if (trigger._ldpAttached) {
    _cleanupLoraPanels();
    return;
  }
  // 关闭所有其他面板
  _cleanupLoraPanels();

  const curVal = (loraNodesConfig[nodeId]?.[idx]?.lora_name) || '';

  // 构建分类树
  const tree = {};
  availableLoras.forEach(lr => {
    const parts = lr.split(/[\\\/]/);
    const name = parts.pop();
    const top = parts[0] || '根目录';
    const sub = parts.slice(1).join('/') || '';
    if (!tree[top]) tree[top] = {};
    if (!tree[top][sub]) tree[top][sub] = [];
    tree[top][sub].push({ full: lr, name });
  });

  const panel = document.createElement('div');
  panel.className = 'lora-dropdown-panel';
  panel.innerHTML = `
    <div class="ldp-search"><input type="text" placeholder="Filter list" oninput="filterLdp(this, '${nodeId}', ${idx})" autocomplete="off"></div>
    <div class="ldp-body">${buildLdpTree(tree, curVal, nodeId, idx, false, fromPanel)}</div>`;

  trigger.parentElement.appendChild(panel);
  // 定位到 trigger 正下方（viewport 坐标）
  const tRect = trigger.getBoundingClientRect();
  panel.style.left = tRect.left + 'px';
  panel.style.top = (tRect.bottom + 2) + 'px';
  panel.style.width = Math.max(340, tRect.width) + 'px';  // 加宽容纳预览图
  panel.style.maxHeight = '380px';  // 加高
  trigger._ldpAttached = true;
  document.body.appendChild(panel);
  // 打开面板时锁定页面滚动：手机上下滑页面不会把选择框一起带走（固定在视口）
  document.body.style.overflow = 'hidden';
  // 不再自动聚焦搜索框：手机上打开面板即弹输入键盘，影响先浏览分类/列表再搜索的体验。
  // 用户主动点击搜索框时才聚焦（配合面板定位：打开时不抢占键盘）。
}

function buildLdpItemHtml(lr, curVal, nodeId, idx, fromPanel) {
  const sel = lr.full === curVal ? ' selected' : '';
  const meta = matchLoraMeta(lr.full);
  const previewUrl = (meta && meta.preview_url)
    ? `/api/lora-preview?fn=${encodeURIComponent(lr.full)}`
    : '';
  const thumb = previewUrl
    ? loraPreviewTag(previewUrl, meta && meta.preview_url, 'ldp-thumb')
    : '';
  const placeholder = previewUrl
    ? '<span class="ldp-thumb-placeholder" style="display:none"></span>'
    : '<span class="ldp-thumb-placeholder"></span>';
  const jsFromPanel = fromPanel ? ', true' : '';
  return `<div class="ldp-item${sel}" onclick="selectLdpItem('${nodeId}', ${idx}, '${escJsStr(lr.full)}'${jsFromPanel})">
    ${thumb}${placeholder}<span class="ldp-name" title="${lr.full.replace(/"/g,'&quot;')}">${lr.name}</span>
  </div>`;
}

function buildLdpTree(tree, curVal, nodeId, idx, expandAll, fromPanel) {
  let html = '';
  const tops = Object.keys(tree).sort((a, b) => a.localeCompare(b, 'zh'));
  tops.forEach(top => {
    const subs = tree[top];
    let total = 0;
    Object.values(subs).forEach(arr => total += arr.length);
    // 顶层分类头（默认折叠，点击展开；搜索时 expandAll 强制展开）
    html += `<div class="ldp-cat${expandAll ? '' : ' collapsed'}" onclick="event.stopPropagation(); toggleLdpCat(this)"><span class="ldp-cat-arrow">▶</span><span class="ldp-cat-icon">📁</span>${top} <span class="ldp-cat-count">(${total})</span></div>`;
    html += `<div class="ldp-cat-body"${expandAll ? '' : ' style="display:none"'}>`;
    const subKeys = Object.keys(subs).sort((a, b) => {
      if (!a) return 1; if (!b) return -1;
      return a.localeCompare(b, 'zh');
    });
    subKeys.forEach(sub => {
      const items = subs[sub].sort((a, b) => a.name.localeCompare(b.name, 'zh'));
      if (sub) {
        // 子分类头（可折叠，默认折叠）
        html += `<div class="ldp-subcat${expandAll ? '' : ' collapsed'}" onclick="event.stopPropagation(); toggleLdpSubcat(this)"><span class="ldp-subcat-arrow">▶</span>📁 ${sub} <span class="ldp-subcat-count">(${items.length})</span></div>`;
        html += `<div class="ldp-subcat-body"${expandAll ? '' : ' style="display:none"'}>`;
        items.forEach(lr => {
          html += buildLdpItemHtml(lr, curVal, nodeId, idx, fromPanel);
        });
        html += '</div>';
      } else {
        // 无子分类：模型项直接挂在顶层分类体下
        items.forEach(lr => {
          html += buildLdpItemHtml(lr, curVal, nodeId, idx, fromPanel);
        });
      }
    });
    html += '</div>';
  });
  if (!html) html = '<div class="ldp-empty">无匹配</div>';
  return html;
}

/** 展开/收起 Lora 顶层分类头 */
function toggleLdpCat(catEl) {
  const arrow = catEl.querySelector('.ldp-cat-arrow');
  const body = catEl.nextElementSibling;
  if (!body || !body.classList.contains('ldp-cat-body')) return;
  const collapsed = body.style.display === 'none';
  body.style.display = collapsed ? 'block' : 'none';
  if (arrow) arrow.textContent = collapsed ? '▼' : '▶';
  catEl.classList.toggle('collapsed', !collapsed);
}

/** 展开/收起 Lora 子分类头 */
function toggleLdpSubcat(subEl) {
  const arrow = subEl.querySelector('.ldp-subcat-arrow');
  const body = subEl.nextElementSibling;
  if (!body || !body.classList.contains('ldp-subcat-body')) return;
  const collapsed = body.style.display === 'none';
  body.style.display = collapsed ? 'block' : 'none';
  if (arrow) arrow.textContent = collapsed ? '▼' : '▶';
  subEl.classList.toggle('collapsed', !collapsed);
}

function filterLdp(input, nodeId, idx) {
  const val = input.value.toLowerCase().trim();
  const body = input.closest('.lora-dropdown-panel')?.querySelector('.ldp-body');
  if (!body) return;
  // 判断来源：外部 Lora 管理面板的搜索框（在 .lora-panel-node-card 内）→ fromPanel=true
  const fromPanel = !!input.closest('.lora-panel-node-card');
  const curVal = (loraNodesConfig[nodeId]?.[idx]?.lora_name) || '';
  const filtered = !val ? availableLoras : availableLoras.filter(lr => lr.toLowerCase().includes(val));
  const tree = {};
  filtered.forEach(lr => {
    const parts = lr.split(/[\\\/]/);
    const name = parts.pop();
    const top = parts[0] || '根目录';
    const sub = parts.slice(1).join('/') || '';
    if (!tree[top]) tree[top] = {};
    if (!tree[top][sub]) tree[top][sub] = [];
    tree[top][sub].push({ full: lr, name });
  });
  body.innerHTML = buildLdpTree(tree, curVal, nodeId, idx, !!val, fromPanel);
}

function selectLdpItem(nodeId, idx, val, fromPanel) {
  // 如果 Lora 变了，清空旧触发词选择
  const oldName = loraNodesConfig[nodeId]?.[idx]?.lora_name;
  if (oldName !== val && loraNodesConfig[nodeId] && loraNodesConfig[nodeId][idx]) {
    loraNodesConfig[nodeId][idx].trigger_words_selected = [];
  }
  updateLoraForNode(nodeId, idx, 'lora_name', val);
  // 关闭所有下拉面板 + 清标记（同时恢复页面滚动）
  _cleanupLoraPanels();
  document.querySelectorAll('.lora-select').forEach(el => { el._ldpAttached = false; el.textContent = val.replace(/^.*[\\\/]/, ''); });
  // 外部面板场景不弹节点弹窗，只刷新外部 Lora 管理面板
  if (!fromPanel) {
    // 重新渲染弹窗以显示新 Lora 的触发词
    showNodeDetail(nodeId);
  }
  // 同步刷新外部 Lora 管理面板（保留展开状态，不闭合节点卡片）
  refreshLoraPanelPreserveExpanded();
}

// 点击外部关闭所有 Lora 面板（同时恢复页面滚动）
document.addEventListener('click', (e) => {
  if (!e.target.closest('.lora-dropdown-panel') && !e.target.closest('.lora-select')) {
    _cleanupLoraPanels();
  }
});

// ========================================================================
// Lora 触发词管理
// ========================================================================
function toggleLoraTriggerWord(nodeId, idx, word, checked) {
  if (!loraNodesConfig[nodeId] || !loraNodesConfig[nodeId][idx]) return;
  const lora = loraNodesConfig[nodeId][idx];
  if (!lora.trigger_words_selected) lora.trigger_words_selected = [];
  if (checked) {
    if (!lora.trigger_words_selected.includes(word)) {
      lora.trigger_words_selected.push(word);
    }
  } else {
    lora.trigger_words_selected = lora.trigger_words_selected.filter(w => w !== word);
  }
  // 更新 UI 标签样式（不重建整个面板）
  const panel = document.getElementById('lora_list_' + nodeId);
  if (panel) {
    const item = panel.querySelector(`[data-idx="${idx}"]`);
    if (item) {
      const tags = item.querySelectorAll('.lora-tw-tag');
      tags.forEach(tag => {
        const cb = tag.querySelector('input[type="checkbox"]');
        if (cb && cb.getAttribute('onchange').includes(`'${escJsStr(word)}'`)) {
          if (checked) {
            tag.classList.add('tw-active');
            cb.checked = true;
          } else {
            tag.classList.remove('tw-active');
            cb.checked = false;
          }
        }
      });
    }
  }
  // 即时保存到后端
  const saveData = { __lora_nodes__: { ...loraNodesConfig }, __prompt_node__: promptNodeId, __negative_node__: negativeNodeId, __resolution_node__: resNodeId, __expanded_text_node__: expandedTextNodeId, __load_image_nodes__: (loadImageNodeIds || []).filter(id => id).join(',') };
  apiFetch('/api/workflow-params', { method: 'POST', body: JSON.stringify(saveData) }, true).catch(() => {});
}

async function refreshLoraMeta() {
  try {
    // 强制后端清掉缓存并重新拉取（包括多分隔符变体和 __bn__ 索引）
    const res = await apiFetch('/api/lora-metadata/refresh', { method: 'POST' }, true);
    // 重新加载前端缓存
    loraMetadata = await apiFetch('/api/lora-metadata', {}, true) || {};
    // 重新渲染当前节点详情
    const body = document.getElementById('node_modal_body');
    const nid = body && body.dataset.nodeId;
    if (nid) {
      showNodeDetail(nid);
    }
    toast(`Lora 元数据已刷新: 共 ${res?.count || loraMetadata ? Object.keys(loraMetadata).length : 0} 条索引，${res?.trigger_count ?? 0} 个有触发词`, 'success');
  } catch(e) {
    toast('刷新失败: ' + e.message, 'error');
  }
}

function addLoraForNode(nodeId, skipModal) {
  if (!availableLoras.length) {
    toast('⚠️ 正在加载 Lora 列表，请稍后重试', 'warning');
    return;
  }
  if (!loraNodesConfig[nodeId]) loraNodesConfig[nodeId] = [];
  loraNodesConfig[nodeId].push({ on: true, lora_name: availableLoras[0] || '', strength_model: 1.0, strength_clip: null });
  // 立即持久化，防止刷新网页后新增的 Lora 丢失
  _scheduleLoraSave();
  if (!skipModal) {
    // 重新渲染弹窗
    showNodeDetail(nodeId);
  }
}

function toggleAllLoras(nodeId) {
  const loras = loraNodesConfig[nodeId];
  if (!loras || !loras.length) return;
  // 判断当前状态：全部开？全部关？混合？
  const allOn = loras.every(l => l.on);
  const newState = !allOn; // 全开→全关，其他情况→全开
  loras.forEach(l => { l.on = newState; });
  showNodeDetail(nodeId);
}

// ========================================================================
// Lora 拖拽排序
// ========================================================================
let _dragFromIdx = -1;
let _dragNodeId = '';

function onLoraDragStart(event, nodeId, idx) {
  // 阻止滑块/数值输入触发 lora 位置拖拽
  if (event.target.closest('.lora-strength-wrap') || event.target.closest('.lora-select') || event.target.closest('.lora-tw-row')) {
    event.preventDefault();
    return;
  }
  _dragFromIdx = idx;
  _dragNodeId = nodeId;
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('text/plain', idx);
  event.target.classList.add('dragging');
}

function onLoraDragOver(event) {
  event.preventDefault();
  event.dataTransfer.dropEffect = 'move';
  // 移除其他项的 drag-over 类
  document.querySelectorAll('.lora-item.drag-over').forEach(el => el.classList.remove('drag-over'));
  event.currentTarget.classList.add('drag-over');
}

function onLoraDrop(event, nodeId, toIdx) {
  event.preventDefault();
  const loras = loraNodesConfig[nodeId];
  if (!loras || _dragFromIdx < 0 || _dragFromIdx === toIdx) return;
  // 交换位置
  const item = loras.splice(_dragFromIdx, 1)[0];
  loras.splice(toIdx, 0, item);
  _dragFromIdx = -1;
  showNodeDetail(nodeId);
}

function onLoraDragEnd(event) {
  event.target.classList.remove('dragging');
  document.querySelectorAll('.lora-item.drag-over').forEach(el => el.classList.remove('drag-over'));
  _dragFromIdx = -1;
  _dragNodeId = '';
}

function updateLoraForNode(nodeId, idx, key, val) {
  if (loraNodesConfig[nodeId] && loraNodesConfig[nodeId][idx]) {
    loraNodesConfig[nodeId][idx][key] = val;
  }
}

function showLoraContextMenuInModal(nodeId, idx, event) {
  const old = document.getElementById('lora_context_menu');
  if (old) old.remove();
  const loras = loraNodesConfig[nodeId];
  if (!loras || !loras[idx]) return;
  const l = loras[idx];
  const canMoveUp = idx > 0;
  const canMoveDown = idx < loras.length - 1;
  const menu = document.createElement('div');
  menu.id = 'lora_context_menu';
  menu.className = 'lora-context-menu';
  menu.innerHTML = `
    <div class="lora-context-item" data-action="toggle">
      <span class="ctx-icon">${l.on ? '○' : '●'}</span>
      ${l.on ? '禁用 Lora' : '启用 Lora'}
    </div>
    <div class="lora-context-sep"></div>
    <div class="lora-context-item${canMoveUp ? '' : ' disabled'}" data-action="moveUp">
      <span class="ctx-icon">↑</span>
      上移
    </div>
    <div class="lora-context-item${canMoveDown ? '' : ' disabled'}" data-action="moveDown">
      <span class="ctx-icon">↓</span>
      下移
    </div>
    <div class="lora-context-sep"></div>
    <div class="lora-context-item danger" data-action="remove">
      <span class="ctx-icon">✕</span>
      移除 Lora
    </div>`;
  const x = event.clientX || (event.touches && event.touches[0].clientX) || 0;
  const y = event.clientY || (event.touches && event.touches[0].clientY) || 0;
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  menu.querySelectorAll('.lora-context-item').forEach(item => {
    item.addEventListener('click', () => {
      const action = item.dataset.action;
      if (action === 'toggle') {
        loras[idx].on = !loras[idx].on;
        showNodeDetail(nodeId);
      } else if (action === 'moveUp' && idx > 0) {
        [loras[idx - 1], loras[idx]] = [loras[idx], loras[idx - 1]];
        showNodeDetail(nodeId);
      } else if (action === 'moveDown' && idx < loras.length - 1) {
        [loras[idx], loras[idx + 1]] = [loras[idx + 1], loras[idx]];
        showNodeDetail(nodeId);
      } else if (action === 'remove') {
        loras.splice(idx, 1);
        showNodeDetail(nodeId);
      }
      menu.remove();
    });
  });
  const closeMenu = (e) => {
    if (!menu.contains(e.target)) {
      menu.remove();
      document.removeEventListener('click', closeMenu);
    }
  };
  setTimeout(() => document.addEventListener('click', closeMenu), 10);
  document.body.appendChild(menu);
}

function addLora() {
  if (!availableLoras.length) {
    toast('⚠️ 正在加载 Lora 列表，请稍后重试', 'warning');
    return;
  }
  savedLoras.push({ on: true, lora_name: availableLoras[0] || '', strength_model: 1.0, strength_clip: null });
  renderLoraPanel();
  saveParams();
}

function removeLora(idx) {
  savedLoras.splice(idx, 1);
  renderLoraPanel();
  saveParams();
}

function toggleLora(idx) {
  if (savedLoras[idx]) {
    savedLoras[idx].on = !savedLoras[idx].on;
    renderLoraPanel();
    saveParams();
  }
}

function updateLora(idx, key, val) {
  if (savedLoras[idx]) {
    savedLoras[idx][key] = val;
    saveParams();
  }
}

function showNodeDetail(nid) {
  if (!allParamsData || !allParamsData.nodes) return;
  let node = null;
  for (let i = 0; i < allParamsData.nodes.length; i++) {
    if (allParamsData.nodes[i].id === nid) { node = allParamsData.nodes[i]; break; }
  }
  if (!node) return;
  document.getElementById('node_modal_title').textContent = `#${  node.id  } ${  node.title}`;
  const body = document.getElementById('node_modal_body');
  let html = `<p style="font-size:11px;color:var(--text-sub);margin-bottom:8px">类型: ${  node.class_type  }</p>`;

  // 判断是否为多 Lora 节点（如 Power Lora Loader）
  const isLoraNode = /power\s*lora/i.test(node.class_type) || /power\s*lora/i.test(node.title);

  // 如果是 Lora 节点，先渲染 Lora 管理面板
  if (isLoraNode) {
    const loras = loraNodesConfig[node.id] || [];
    html += `<div class="lora-section-in-modal">
      <div class="section-title" style="margin-bottom:6px"><svg class="icon-sm" aria-hidden="true"><use href="#icon-lora"/></svg> Lora 列表</div>
      <div class="lora-list" id="lora_list_${node.id}">`;
    if (loras.length === 0) {
      html += '<div class="empty-state" style="padding:8px;text-align:center;color:var(--text-sub2);font-size:11px">暂无 Lora，点击下方「添加」按钮</div>';
    } else {
      for (let li = 0; li < loras.length; li++) {
        const l = loras[li];
        const disabledCls = l.on ? '' : ' lora-disabled';
        const loraMeta = matchLoraMeta(l.lora_name || '');
        // 自动修复 lora_name：如果 basename 匹配到但路径不对，用正确路径替换
        if (loraMeta && l.lora_name) {
          const correctPath = loraMeta.file_path ? extractComfyUIPath(loraMeta.file_path) : null;
          if (correctPath && correctPath !== l.lora_name && correctPath !== l.lora_name.replace(/\\/g, '/')) {
            console.log(`[Lora] 自动修复 lora_name: ${l.lora_name} -> ${correctPath}`);
            l.lora_name = correctPath;
          }
        }
        const previewUrl = loraMeta && loraMeta.preview_url
          ? `/api/lora-preview?fn=${encodeURIComponent(l.lora_name || '')}`
          : '';
        const twAll = loraMeta ? (loraMeta.trigger_words || []) : [];
        const twSelected = l.trigger_words_selected || [];
        const modelLabel = loraMeta ? (loraMeta.model_name || loraMeta.file_name || '') : '';
        const hasPreview = !!previewUrl;
        html += `<div class="lora-item${disabledCls}" data-idx="${li}"
          draggable="true"
          ondragstart="onLoraDragStart(event, '${node.id}', ${li})"
          ondragover="onLoraDragOver(event)"
          ondrop="onLoraDrop(event, '${node.id}', ${li})"
          ondragend="onLoraDragEnd(event)"
          oncontextmenu="showLoraContextMenuInModal('${node.id}', ${li}, event);return false"
          ontouchstart="onLoraTouchStart(event, '${node.id}', ${li})"
          ontouchend="onLoraTouchEnd(event, '${node.id}', ${li})"
          ontouchmove="onLoraTouchMove()">
          <div class="lora-top-row">
            <span class="lora-status" title="${l.on ? '已启用' : '已禁用'}"></span>
            ${hasPreview ? loraPreviewTag(previewUrl, loraMeta && loraMeta.preview_url, 'lora-thumb') : `<span class="lora-thumb-placeholder"></span>`}
            <div class="lora-select" onclick="toggleLoraDropdown(event, this, '${node.id}', ${li})" title="${modelLabel.replace(/"/g,'&quot;')}">
              ${l.lora_name ? l.lora_name.replace(/^.*[\\\/]/, '') : '— 选择 Lora —'}
            </div>
            <span class="lora-strength-wrap lora-strength-locked" data-lock="1" data-v="${l.strength_model ?? 1.0}">
              <input class="lora-strength" type="range" min="-15" max="15" step="0.05" value="${l.strength_model ?? 1.0}"
                oninput="var w=this.closest('.lora-strength-wrap');if(w.classList.contains('lora-strength-locked')){this.value=w.dataset.v||this.value;return;} w.dataset.v=this.value; updateLoraForNode('${node.id}', ${li}, 'strength_model', parseFloat(this.value) || 0); this.nextElementSibling.value = parseFloat(this.value).toFixed(2)"
                onclick="activateLoraSlider(event, this)"
                ontouchstart="touchStartLoraSlider(event, this)" ontouchend="touchEndLoraSlider(event, this)"
                title="点击激活后拖动调整" aria-label="Lora 权重">
              <input class="lora-strength-val" type="number" min="-15" max="15" step="0.05" value="${(l.strength_model ?? 1.0).toFixed(2)}"
                onchange="updateLoraForNode('${node.id}', ${li}, 'strength_model', parseFloat(this.value) || 0); this.previousElementSibling.value = Math.max(-15, Math.min(15, parseFloat(this.value) || 0))"
                title="直接输入数值" aria-label="Lora 权重数值">
            </span>
          </div>`;
        // 触发词行
        if (twAll.length > 0) {
          html += `<div class="lora-tw-row">`;
          for (let ti = 0; ti < twAll.length; ti++) {
            const tw = twAll[ti];
            const checked = twSelected.includes(tw) ? ' checked' : '';
            html += `<label class="lora-tw-tag${checked ? ' tw-active' : ''}" title="${tw.replace(/"/g,'&quot;')}">
              <input type="checkbox"${checked} onchange="toggleLoraTriggerWord('${node.id}', ${li}, '${escJsStr(tw)}', this.checked)" style="display:none">
              ${tw}
            </label>`;
          }
          html += `</div>`;
        }
        html += `</div>`;
      }
    }
    html += `</div>
      <div style="display:flex;gap:6px;margin-top:6px">
        <button class="btn btn-xs btn-primary" onclick="addLoraForNode('${node.id}')"><svg class="icon-sm" aria-hidden="true"><use href="#icon-plus"/></svg> 添加 Lora</button>
        <button class="btn btn-xs btn-secondary" onclick="toggleAllLoras('${node.id}')" title="全部启用/禁用">⏻ 全部开关</button>
        <button class="btn btn-xs btn-secondary" onclick="refreshLoraMeta()" title="刷新 Lora 元数据">🔄 刷新元数据</button>
      </div>
      <hr style="margin:12px 0;border-color:var(--card-border)">
    </div>`;
  }

  // 渲染常规参数（对 Lora 节点过滤掉已由新 UI 管理的参数）
  const loraFilterKeys = isLoraNode ? ['add lora', 'lora_', 'strength_model', 'strength_clip'] : [];
  node.params.forEach((p) => {
    // 跳过 Lora 相关参数
    if (isLoraNode && loraFilterKeys.some(k => p.key.toLowerCase().includes(k))) return;
    // 跳过对象类型的值（由新 UI 管理）
    if (typeof p.value === 'object') return;
    const modelKey = `${node.class_type  }_${  p.key}`;
    const elId = `mparam_${  node.id  }_${  p.key}`; // mparam_ 前缀：避免与主面板重命名的 param_* id 撞车（曾导致弹窗改动被面板旧值覆盖）
    // 实时同步：改动即回写内存缓存 allParamsData，避免"关掉卡片再打开还是旧值"
    const syncAttr = ` onchange="syncParamValue('${node.id}','${String(p.key).replace(/'/g, "\\'")}',this.value)"`;
    html += `<div class="param-row"><label for="${  elId  }">${  p.key  }</label>`;
    if (Array.isArray(p.options) && p.options.length) {
      // object_info 提供的 COMBO 选项 → 下拉选择
      html += `<select id="${  elId  }"${syncAttr}>${  p.options.map((o) => { return `<option${  String(o) === String(p.value) ? ' selected' : ''  }>${  o  }</option>`; }).join('')  }</select>`;
    } else if (modelLists[modelKey]) {
      html += `<select id="${  elId  }"${syncAttr}>${  modelLists[modelKey].map((o) => { return `<option${  o === p.value ? ' selected' : ''  }>${  o  }</option>`; }).join('')  }</select>`;
    } else if (p.type === 'text') {
      html += `<textarea id="${  elId  }" rows="2"${syncAttr}>${  p.value === 0 ? '0' : (p.value || '')  }</textarea>`;
    } else if (p.type === 'bool') {
      html += `<select id="${  elId  }"${syncAttr}><option value="true"${  p.value ? ' selected' : ''  }>true</option><option value="false"${  !p.value ? ' selected' : ''  }>false</option></select>`;
    } else if (p.type === 'number' && (p.min !== undefined || p.max !== undefined)) {
      // INT/FLOAT 带范围 → 数字输入限幅
      const stp = (p.step !== undefined && p.step > 0) ? p.step : 1;
      html += `<input type="number" id="${  elId  }" value="${  p.value  }"${  p.min !== undefined ? ` min="${  p.min  }"` : ''  }${  p.max !== undefined ? ` max="${  p.max  }"` : ''  } step="${  stp  }"${syncAttr}>`;
    } else if (p.type === 'select') {
      if (Array.isArray(p.value)) {
        html += `<select id="${  elId  }"${syncAttr}>${  p.value.map((o) => { return `<option>${  o  }</option>`; }).join('')  }</select>`;
      } else {
        // 值被覆盖为字符串（如从 workflow_config 读取的保存值）
        html += `<input type="text" id="${  elId  }" value="${  p.value === 0 ? '0' : (p.value || '')  }"${syncAttr}>`;
      }
    } else {
      html += `<input type="text" id="${  elId  }" value="${  p.value === 0 ? '0' : (p.value || '')  }"${syncAttr}>`;
    }
    html += '</div>';
  });
  body.innerHTML = html;
  body.dataset.nodeId = nid;
  document.getElementById('node_modal').classList.add('active');
}

/** 参数控件变更时，实时回写内存缓存 allParamsData
 *  否则关闭卡片再打开会用旧值渲染（用户看到"改了不刷新，要刷新页面才对"） */
function syncParamValue(nodeId, key, value) {
  try {
    if (!allParamsData || !allParamsData.nodes) return;
    const n = allParamsData.nodes.find(x => String(x.id) === String(nodeId));
    if (!n || !n.params) return;
    const p = n.params.find(x => x.key === key);
    if (p) p.value = value;
  } catch (e) { console.warn('syncParamValue error:', e); }
}

function closeNodeModal() {
  // 关闭弹窗前自动保存当前节点的参数
  const body = document.getElementById('node_modal_body');
  const nid = body && body.dataset.nodeId;
  if (nid && allParamsData && allParamsData.nodes) {
    const saveData = {};
    // 只收集弹窗内部的 mparam_* 控件。param_* 是主面板（调度器/UNet 等）重命名后的 id，
    // 与弹窗 id 曾撞车：getElementById 永远命中面板元素 → 弹窗里改参数存回去的是面板旧值
    //（表现为"步数怎么调都锁死"）。解析 id 的 mparam_{nid}_{key}，key 可含点/下划线。
    document.querySelectorAll('#node_modal_body [id^="mparam_"]').forEach((e) => {
      const rest = e.id.slice('mparam_'.length);
      const dot = rest.indexOf('_');
      if (dot < 1) return;
      const nid2 = rest.slice(0, dot), key2 = rest.slice(dot + 1);
      saveData[`${nid2}_${key2}`] = e.value;
      const n2 = allParamsData.nodes.find(x => String(x.id) === String(nid2));
      const p2 = n2 && n2.params && n2.params.find(x => x.key === key2);
      if (p2) p2.value = e.value; // ★ 回写内存缓存，重开弹窗不回退
    });
    // 如果有 Lora 配置，也保存
    if (loraNodesConfig[nid]) {
      saveData.__lora_nodes__ = { ...loraNodesConfig };
    }
    // 如果有变化的值，立即保存到后端
    const keys = Object.keys(saveData);
    if (keys.length > 0) {
      saveData.__prompt_node__ = promptNodeId;
      saveData.__negative_node__ = negativeNodeId;
      saveData.__resolution_node__ = resNodeId;
      saveData.__expanded_text_node__ = expandedTextNodeId;
      saveData.__load_image_nodes__ = loadImageNodeIds.filter(id => id).join(',');
      // 异步保存，不阻塞关闭
      apiFetch('/api/workflow-params', { method:'POST', body:JSON.stringify(saveData) }, true)
        .then((r) => {
          if (r && r.ok) {
            console.log(`[ComfyUI] 节点 #${nid} 参数已自动保存`);
          }
        })
        .catch((e) => console.warn(`[ComfyUI] 节点 #${nid} 自动保存失败:`, e));
    }
  }
  document.getElementById('node_modal').classList.remove('active');
}

// ========================================================================
// 保存参数
// ========================================================================
let _saving = false;
let _savePending = false; // saveParams 进行中再次触发 → 排队（原先直接丢弃，编辑值静默丢失）

async function saveParams() {
  if (!allParamsData) { await loadParams(); if (!allParamsData) return; }
  if (_saving) { _savePending = true; console.warn('saveParams: busy, queued'); return; }
  _saving = true;
  try {
    const c = await apiFetch('/api/workflow-params-config');
    if (!c) { toast('获取配置失败', 'error'); return; }
    const cW = getCurrentWF();
    if (!cW) { toast('未选中工作流', 'error'); return; }

    const d = {
      __disabled_groups__: disabledGroups,
      __groups_source__: groupsSource,
      __bind_target__: bindTarget,
      // v4.9.4: 组数据仅在有内容时上报——空数组曾触发后端"解绑删桶"，
      // 把用户组绑定静默端掉（自毁循环：面板空→保存→桶删→面板永远空）
      __groups_data__: c.__groups_data__ || [],
      __hidden_workflows__: c.__hidden_workflows__ || [],
      __workflow_aliases__: c.__workflow_aliases__ || {},
      // 节点角色只在非空时附带：避免 loadImageNodeIds 等为空（如切换工作流后未初始化）
      // 触发保存时用空值覆盖后端已保存的节点配置，导致刷新后图片/角色丢失
      __empty_load_nodes__: emptyLoadNodes.join(',')
    };
    // v4.9.4: 空 groups_data 不上报（后端已不再删桶，双保险）
    if (!(d.__groups_data__ || []).length) delete d.__groups_data__;
    // 节点角色：默认只在非空时附带（避免切换工作流后未初始化时用空值覆盖后端已存配置）；
    // 但用户「主动取消指定」时必须发送空值，否则后端保留旧值 → 表现为「取消不掉」。
    // _explicitClear 里的角色名 = 本次是用户显式取消，允许发送空串覆盖。
    const _ec = _explicitClear || {};
    if (promptNodeId || _ec.prompt) d.__prompt_node__ = promptNodeId || '';
    if (resNodeId || _ec.resolution) d.__resolution_node__ = resNodeId || '';
    if (negativeNodeId || _ec.negative) d.__negative_node__ = negativeNodeId || '';
    if (expandedTextNodeId || _ec.expanded_text) d.__expanded_text_node__ = expandedTextNodeId || '';
    const imgIds = loadImageNodeIds.filter(id => id);
    if (imgIds.length || _ec.load_image) d.__load_image_nodes__ = imgIds.join(',');
    const audioIds = loadAudioNodeIds.filter(id => id);
    if (audioIds.length || _ec.load_audio) d.__load_audio_nodes__ = audioIds.join(',');
    const videoIds = loadVideoNodeIds.filter(id => id);
    if (videoIds.length || _ec.load_video) d.__load_video_nodes__ = videoIds.join(',');
    d.__disabled_nodes__ = disabledNodes;
    d.__lora_nodes__ = loraNodesConfig;
    console.log('[saveParams] sending:', cW, JSON.stringify({...d, __prompt_node__: d.__prompt_node__, __resolution_node__: d.__resolution_node__, __load_image_nodes__: d.__load_image_nodes__, __negative_node__: d.__negative_node__, __expanded_text_node__: d.__expanded_text_node__}));

    // 收集所有已知节点的参数值（不仅是当前弹窗的）
    if (allParamsData && allParamsData.nodes) {
      allParamsData.nodes.forEach((n) => {
        n.params.forEach((p) => {
          const el = document.getElementById(`param_${n.id}_${p.key}`);
          if (el) d[`${n.id}_${p.key}`] = el.value;
        });
      });
    }

    const promptText = document.getElementById('prompt_textarea').value;
    if (promptNodeId) {
      d[`${promptNodeId}_${promptInputKey}`] = promptText;
    }
    const negText = document.getElementById('neg_textarea').value;
    if (negativeNodeId) {
      d[`${negativeNodeId}_${negativeInputKey}`] = negText;
    }

    const r = await apiFetch('/api/workflow-params', { method:'POST', body:JSON.stringify(d) }, true);
    if (!r || r.ok === false) { toast('保存失败: ' + (r?.error || '后端无响应'), 'error'); return; }
    toast('✅ 已保存');
    loadParams();
  } catch (e) {
    toast('保存失败: ' + (e.message || e), 'error');
  } finally {
    _saving = false;
    if (_savePending) { _savePending = false; setTimeout(() => { saveParams().catch(() => {}); }, 50); }
  }
}

let _promptSaveTimer = null;
let _negSaveTimer = null;

function autoSavePrompt() {
  // 保存到 localStorage 防丢失（含空值：清空也要持久化）
  const text = document.getElementById('prompt_textarea').value;
  localStorage.setItem('draft_prompt', text);
  // 防抖自动保存到后端（停止输入 1.5 秒后触发）
  clearTimeout(_promptSaveTimer);
  _promptSaveTimer = setTimeout(() => {
    const curWf = getCurrentWF();
    if (!curWf || !promptNodeId) return;
    // 直接调后端保存，不走完整的 saveParams（避免 _saving 锁冲突）
    apiFetch('/api/workflow-params', {
      method: 'POST',
      body: JSON.stringify({
        __prompt_node__: promptNodeId,
        __negative_node__: negativeNodeId,
        __resolution_node__: resNodeId,
        __load_image_nodes__: loadImageNodeIds.filter(id => id).join(','),
        __load_audio_nodes__: loadAudioNodeIds.filter(id => id).join(','),
        __load_video_nodes__: loadVideoNodeIds.filter(id => id).join(','),
      __empty_load_nodes__: emptyLoadNodes.join(','),
        [`${promptNodeId}_${promptInputKey}`]: text
      })
    }, true);
  }, 1500);
}
function autoSaveNeg() {
  // 保存到 localStorage 防丢失（含空值：清空也要持久化）
  const text = document.getElementById('neg_textarea').value;
  localStorage.setItem('draft_neg', text);
  // 防抖自动保存到后端（停止输入 1.5 秒后触发）
  clearTimeout(_negSaveTimer);
  _negSaveTimer = setTimeout(() => {
    const curWf = getCurrentWF();
    if (!curWf || !negativeNodeId) return;
    apiFetch('/api/workflow-params', {
      method: 'POST',
      body: JSON.stringify({
        __prompt_node__: promptNodeId,
        __negative_node__: negativeNodeId,
        __resolution_node__: resNodeId,
        __load_image_nodes__: loadImageNodeIds.filter(id => id).join(','),
        __load_audio_nodes__: loadAudioNodeIds.filter(id => id).join(','),
        __load_video_nodes__: loadVideoNodeIds.filter(id => id).join(','),
      __empty_load_nodes__: emptyLoadNodes.join(','),
        [`${negativeNodeId}_${negativeInputKey}`]: text
      })
    }, true);
  }, 1500);
}

// ========================================================================
// 搜索节点
// ========================================================================
function filterNodes() {
  const q = document.getElementById('search_nodes').value.toLowerCase();
  document.querySelectorAll('.node-card').forEach((c) => {
    const t = c.getAttribute('data-search') || '';
    if (!q) { c.classList.remove('hidden'); return; }
    const words = q.split(/\s+/).filter(Boolean); let match = true;
    for (let i = 0; i < words.length; i++) { if (t.indexOf(words[i]) === -1) { match = false; break; } }
    c.classList.toggle('hidden', !match);
  });
}

// ========================================================================
// 组控制
// ========================================================================
async function refreshGroupsUI() {
  const bar = document.getElementById('groups_bar');
  if (!bar) return;
  const cur = getCurrentWF();
  if (!cur) { bar.style.display = 'none'; return; }
  try {
    const d = await apiFetch('/api/groups');
    const groups = (d && d.groups) || [];
    const extractBtn = document.getElementById('btn_extract');
    const clearBtn = document.getElementById('btn_clear_extract');
    if (!groups.length) {
      bar.style.display = 'block';
      document.getElementById('groups_source').textContent = '';
      if (extractBtn) extractBtn.style.display = 'inline-flex';
      if (clearBtn) clearBtn.style.display = 'none';
      $id('groups_list').innerHTML = '<span style="font-size:11px;color:var(--text-sub)">点击「📦 提取」获取工作流组件（自动应用到同结构工作流）</span>';
      return;
    }
    bar.style.display = 'block';
    document.getElementById('groups_source').textContent = `${groups.length} 组`;
    if (extractBtn) extractBtn.style.display = 'none';
    if (clearBtn) clearBtn.style.display = 'inline-flex';
    $id('groups_list').innerHTML = groups.map((x) => {
      const o = disabledGroups[x.id] ? ' off' : '';
      return `<label class="group-tag${o}" style="border-color:${x.color}" data-action="toggleGroupTag" data-groupid="${x.id}">` +
        `<input type="checkbox" ${disabledGroups[x.id] ? '' : 'checked'} data-gid="${x.id}"> ${x.title}</label>`;
    }).join('');
  } catch (e) { console.warn('refreshGroupsUI failed', e); }
}

async function extractGroups() {
  const cur = getCurrentWF();
  if (!cur) { toast('请先选择工作流', 'error'); return; }
  const d = await apiFetch('/api/groups');
  if (!d?.groups?.length) { toast('没有组信息', 'error'); return; }
  // v4.5.0: 自动应用——组数据写入所有节点结构兼容的工作流（节点 id 全覆盖），免手动绑定
  const r = await apiFetch('/api/groups/auto-apply', { method: 'POST', body: JSON.stringify({ source: cur, groups: d.groups }) }, true);
  if (!r || r.ok === false) { toast((r && r.error) || '应用失败', 'error'); return; }
  const skipNote = (r.skipped && r.skipped.length) ? `，跳过 ${r.skipped.length} 个（节点结构不匹配）` : '';
  toast(`✅ 已应用到 ${r.applied.length} 个工作流${skipNote}`);
  await refreshGroupsUI();
}

function toggleGroupTag(g, el) {
  const c = el.querySelector('input');
  c.checked = !c.checked;
  if (c.checked) { delete disabledGroups[g]; el.classList.remove('off'); }
  else { disabledGroups[g] = true; el.classList.add('off'); }
  // 保存禁用状态：不保存则后端提交时读不到最新的 __disabled_groups__，禁用不生效
  saveParams();
}





function clearExtractGroups() {
  const cur = getCurrentWF();
  if (!cur || !confirm('确定清除当前工作流的组数据？（不影响其他已应用的工作流）')) return;
  apiFetch('/api/groups/auto-apply', { method: 'POST', body: JSON.stringify({ source: cur, groups: [] }) }, true)
    .then(() => { toast('🗑 已清除'); refreshGroupsUI(); })
    .catch(() => toast('清除失败', 'error'));
}

// ========================================================================
// 独立工作流管理
// ========================================================================
// 独立工作流 DOM 缓存
let _bindDomCache = null;
function _getBindDoms() {
  if (!_bindDomCache) {
    _bindDomCache = {
      modal: $id('wf_mgr_modal'),
      groupList: $id('bindmgr_grouplist'),
      empty: $id('bindmgr_empty'),
      selected: $id('bindmgr_selected'),
      selType: $id('bindmgr_sel_type'),
      selId: $id('bindmgr_sel_id'),
      boundWfs: $id('bindmgr_bound_wfs'),
      addWf: $id('bindmgr_add_wf'),
      newId: $id('bindmgr_newid'),
      tabGroup: $id('bindmgr_tab_group'),
      tabUser: $id('bindmgr_tab_user'),
    };
  }
  return _bindDomCache;
}

// 独立工作流状态
let _bmState = { type: 'group', data: null, workflows: [], ctxData: null, selId: null };

async function showBindMgrModal() {
  const dom = _getBindDoms();
  try {
    const [bindData, ctxData, workflows] = await Promise.all([
      apiFetch('/api/workflow-bind', {}, true),
      apiFetch('/api/context-workflows', {}, true),
      apiFetch('/api/workflows/all', {}, true),
    ]);
    if (!bindData || !ctxData || !workflows) return;

    // 填充工作流下拉
    dom.addWf.innerHTML = workflows.map(w => `<option>${esc(w.name)}</option>`).join('');

    // 保存全局状态
    _bmState.data = bindData;
    _bmState.workflows = workflows;
    _bmState.ctxData = ctxData;
    _bmState.selId = null;

    // 默认显示群列表
    _bmRenderGroupList();
  } catch (e) {
    toast('加载独立工作流数据失败', 'error');
    console.error('showBindMgrModal error:', e);
  }
}

// 渲染左侧群/用户列表
function _bmRenderGroupList() {
  const dom = _getBindDoms();
  const key = _bmState.type === 'group' ? 'group_bindings' : 'user_bindings';
  const items = (_bmState.data && _bmState.data[key]) || {};
  const emoji = _bmState.type === 'group' ? '💬' : '👤';

  let html = '';
  Object.entries(items).forEach(([id, wfs]) => {
    const isActive = id === String(_bmState.selId);
    html += `<div class="bm-group-item ${isActive ? 'active' : ''}"
                  data-bmid="${id}" style="padding:8px 10px;cursor:pointer;font-size:12px;border-radius:var(--radius-xs);margin-bottom:2px;display:flex;justify-content:space-between;align-items:center;${isActive ? 'background:var(--primary);color:#fff' : ''}"
                  role="button" tabindex="0">
              <span>${emoji} ${id}</span>
              <span style="font-size:10px;opacity:0.7">${Array.isArray(wfs)?wfs.length:1}个</span>
            </div>`;
  });

  if (!html) {
    html = '<div style="padding:16px;text-align:center;color:var(--text-sub);font-size:11px">暂无独立工作流</div>';
  }
  dom.groupList.innerHTML = html;

  // 右侧空状态
  if (!_bmState.selId) {
    dom.empty.style.display = 'flex';
    dom.selected.style.display = 'none';
  }
}

// 选中一个群/用户
function _bmSelectItem(id) {
  _bmState.selId = id;
  const dom = _getBindDoms();
  const key = _bmState.type === 'group' ? 'group_bindings' : 'user_bindings';
  const items = (_bmState.data && _bmState.data[key]) || {};
  const wfs = items[id];
  const wfList = Array.isArray(wfs) ? wfs : (wfs ? [wfs] : []);

  // 更新左侧高亮
  _bmRenderGroupList();

  // 更新右侧详情
  dom.empty.style.display = 'none';
  dom.selected.style.display = 'flex';
  dom.selType.textContent = _bmState.type === 'group' ? '💬 群' : '👤 用户';
  dom.selId.textContent = id;

  // 删除按钮属性
  const delBtn = dom.selected.querySelector('.js-del-group-all');
  if (delBtn) { delBtn.dataset.type = _bmState.type; delBtn.dataset.id = id; }

  // 渲染已设置的独立工作流标签
  let wfHtml = '';
  if (wfList.length === 0) {
    wfHtml = '<span style="color:var(--text-sub);font-size:11px">（无独立工作流）</span>';
  } else {
    wfList.forEach(wf => {
      const isCurrent = wf === (_bmState.ctxData[(_bmState.type === 'group' ? 'group_' : 'private_') + id] || '');
      wfHtml += `<span class="bm-wf-tag" style="display:inline-flex;align-items:center;gap:4px;padding:5px 10px;background:${isCurrent ? 'var(--primary)' : 'var(--bg-tertiary)'};color:${isCurrent ? '#fff':'var(--text)'};border-radius:var(--radius-xs);font-size:11px;cursor:default">
        ${wf}${isCurrent ? ' <span style="opacity:0.7">当前</span>' : ''}
        <button class="js-del-binding"
                data-type="${_bmState.type}" data-id="${id}" data-wf="${wf}"
                style="background:none;border:none;color:inherit;cursor:pointer;padding:0 0 0 4px;vertical-align:middle"><svg class="icon-xs" aria-hidden="true"><use href="#icon-x"/></svg></button>
      </span>`;
    });
  }
  dom.boundWfs.innerHTML = wfHtml;
}

function closeBindMgrModal() {
  closeWfMgrModal();
}

// 添加独立工作流（从右侧下拉框添加工作流到选中的群）
async function addBinding() {
  const dom = _getBindDoms();
  const id = _bmState.selId;
  const type = _bmState.type;
  if (!id) { toast('请先选择一个群或用户', 'error'); return; }
  const wf = dom.addWf.value;
  if (!wf) return;

  const d = await apiFetch('/api/workflow-bind', {
    method: 'POST',
    body: JSON.stringify({ type, id, workflow: wf }),
  });
  if (d?.ok) {
    toast('✅ 已添加');
    // 刷新数据
    showBindMgrModal().then(() => {
      _bmState.selId = id; // 保持选中
      _bmSelectItem(id);
    });
  }
}

// 从左侧输入框快速添加新群/用户
async function bmAddNewItem() {
  const dom = _getBindDoms();
  const id = dom.newId.value.trim();
  if (!id) { toast('请输入群号或QQ号', 'error'); return; }
  const type = _bmState.type;
  const wf = dom.addWf.value || (_bmState.workflows.length > 0 ? _bmState.workflows[0].name : '');

  const d = await apiFetch('/api/workflow-bind', {
    method: 'POST',
    body: JSON.stringify({ type, id, workflow: wf }),
  });
  if (d?.ok) {
    toast('✅ 已添加');
    dom.newId.value = '';
    showBindMgrModal().then(() => {
      _bmState.selId = id;
      _bmSelectItem(id);
    });
  }
}

// 删除单个独立工作流
async function deleteBinding(type, id, wf) {
  const msg = wf ? `确定取消 [${wf}] 吗？` : '确定取消该独立工作流设置？';
  if (!confirm(msg)) return;
  const body = { type, id };
  if (wf) body.workflow = wf;
  const d = await apiFetch('/api/workflow-bind/delete', {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (d?.ok) {
    toast('已删除');
    const prevSel = _bmState.selId;
    showBindMgrModal().then(() => {
      if (prevSel && (_bmState.data[(_bmState.type === 'group' ? 'group_bindings' : 'user_bindings')] || {})[prevSel]) {
        _bmState.selId = prevSel;
        _bmSelectItem(prevSel);
      }
    });
  }
}

// 删除整个群/用户的全部独立工作流
async function deleteGroupAll(type, id) {
  if (!confirm(`确定删除 ${type === 'group' ? '群' : '用户'} ${id} 的所有独立工作流吗？`)) return;
  const d = await apiFetch('/api/workflow-bind/delete', {
    method: 'POST',
    body: JSON.stringify({ type, id }),
  });
  if (d?.ok) {
    toast('已删除');
    _bmState.selId = null;
    showBindMgrModal();
  }
}

/**
 * 独立工作流事件委托
 * 处理：群列表点击、Tab切换、删除工作流、删除群、添加工作流
 */
(function initBindEvents() {
  document.addEventListener('click', async (e) => {
    // 删除单个工作流
    const delBtn = e.target.closest('.js-del-binding');
    if (delBtn) {
      deleteBinding(delBtn.dataset.type, delBtn.dataset.id, delBtn.dataset.wf);
      return;
    }

    // 删除整个群
    const delAllBtn = e.target.closest('.js-del-group-all');
    if (delAllBtn) {
      deleteGroupAll(delAllBtn.dataset.type, delAllBtn.dataset.id);
      return;
    }

    // 点击左侧群列表项
    const groupItem = e.target.closest('.bm-group-item');
    if (groupItem) {
      _bmSelectItem(groupItem.dataset.bmid);
      return;
    }

    // Tab 切换
    const tab = e.target.closest('.bindmgr-tab');
    if (tab) {
      _bmState.type = tab.dataset.bmtab;
      _bmState.selId = null;
      // 更新 tab 样式
      document.querySelectorAll('.bindmgr-tab').forEach(t => {
        t.style.background = t === tab ? 'var(--primary)' : 'transparent';
        t.style.color = t === tab ? '#fff' : 'var(--text-sub)';
        t.style.borderColor = 'transparent';
      });
      _bmRenderGroupList();
      return;
    }

    // 右侧添加按钮
    const addToSel = e.target.closest('.js-add-wf-to-selected');
    if (addToSel) {
      addBinding();
      return;
    }

    // 左侧添加按钮
    if (e.target.id === 'bindmgr_add_btn') {
      bmAddNewItem();
      return;
    }
  });

  // 左侧输入框回车添加
  // v4.13.5 后期调整：导航改为常驻全高竖栏（ComfyUI 官方风格），旧的左缘 mousemove 唤回/收回逻辑已整体移除

document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.id === 'bindmgr_newid') {
      e.preventDefault();
      bmAddNewItem();
    }
  });
})();

// ========================================================================
// 独立工作流概览
// ========================================================================
async function loadBindOverview() {
  try {
    const [bindData, ctxData] = await Promise.all([
      apiFetch('/api/workflow-bind', {}, true),
      apiFetch('/api/context-workflows', {}, true),
    ]);
    if (!bindData || !ctxData) return;

    const items = [];

    const addSection = (bindings, prefix, emoji, label) => {
      if (!bindings) return;
      Object.entries(bindings).forEach(([id, wfs]) => {
        const list = Array.isArray(wfs) ? wfs : [wfs];
        items.push(`<div class="bind-item">
          <span class="bind-label">${emoji} ${label} ${id}</span><br>
          ${list.map(w => {
            const active = w === (ctxData[prefix + id] || '');
            return `${w}${active ? ' <span class="text-success">✅</span>' : ''}`;
          }).join('<br>')}
        </div>`);
      });
    };

    addSection(bindData.group_bindings, 'group_', '💬', '群');
    addSection(bindData.user_bindings, 'private_', '👤', '用户');

    const el = $id('bind_list');
    if (el) {
      el.innerHTML = items.length
        ? items.join('')
        : '<span class="text-muted">暂无独立工作流</span>';
    }
  } catch (e) {
    const el = $id('bind_list');
    if (el) el.innerHTML = '<span class="text-muted">加载失败</span>';
    console.error('loadBindOverview error:', e);
  }
}

// ========================================================================
// 进度条轮询（自适应间隔 + 自动图片预览）
// ========================================================================
let _pollTimer = null;
let _wasRunning = false;
let _pollInterval = 5000;  // 当前轮询间隔

function schedulePoll(interval) {
  clearTimeout(_pollTimer);
  _pollInterval = interval;
  _pollTimer = setTimeout(pollProgress, interval);
}

async function pollProgress() {
  try {
    const d = await apiFetch('/api/progress', {}, true);
    if (!d) { schedulePoll(5000); return; }
    // 更新顶部队列计数
    const total = (d.queue_running || 0) + (d.queue_pending || 0);
    document.getElementById('queue_count').textContent = total;
    const section = document.getElementById('progress_section');
    const bar = document.getElementById('progress_bar');
    const pctLabel = document.getElementById('progress_pct');
    const detail = document.getElementById('progress_detail');
    const stepDetail = document.getElementById('progress_step_detail');
    const nodeName = document.getElementById('progress_node_name');
    const label = document.getElementById('progress_label');

    if (d.running && d.state !== 'idle') {
      section.style.display = 'flex';

      // 计算运行时间显示
      const elapsed = d.elapsed || 0;
      const timeStr = elapsed > 0
        ? (elapsed < 60 ? elapsed + '秒' : Math.floor(elapsed/60) + '分' + (elapsed%60) + '秒')
        : '';

      if (d.state === 'generating') {
        // v4.5.6: 总进度百分比（后端合成：节点级 + 当前节点采样分数），旧字段兜底
        var pct = (typeof d.percent === 'number')
          ? d.percent
          : (d.progress_max > 0 ? Math.min(100, Math.round(d.progress_value / d.progress_max * 100)) : 0);
        // 当前节点：类名 + 节点计数（模型加载等无事件阶段也有实时反馈）
        var nodeParts = [];
        if (d.node_label) nodeParts.push(d.node_label);
        if (d.nodes_total > 0) nodeParts.push('节点 ' + (d.nodes_done || 0) + '/' + d.nodes_total);
        if (nodeParts.length) {
          nodeName.textContent = nodeParts.join(' ');
          nodeName.style.display = '';
          document.querySelectorAll('.progress-sep--node').forEach(el => el.style.display = '');
        } else {
          nodeName.style.display = 'none';
          document.querySelectorAll('.progress-sep--node').forEach(el => el.style.display = 'none');
        }
        if (pct > 0) {
          section.classList.remove('generating');
          bar.classList.add('active');
          bar.classList.remove('indeterminate');
          bar.style.width = pct + '%';
          pctLabel.textContent = pct + '%';
        } else {
          // 尚无百分比（模型加载/排队起始）：扫光动画 + 显示已运行时间
          section.classList.add('generating');
          bar.classList.remove('active', 'indeterminate');
          bar.style.width = '0%';
          pctLabel.textContent = timeStr || '...';
        }
        label.textContent = '生成中';
        // 当前节点采样步数（仅采样节点有）
        if (d.progress_max > 0 && d.progress_value > 0) {
          stepDetail.textContent = '采样 ' + d.progress_value + '/' + d.progress_max;
        } else {
          stepDetail.textContent = '';
        }
        const parts = timeStr ? ['已运行 ' + timeStr] : [];
        if (d.queue_pending > 0) parts.push('前面还有 ' + d.queue_pending + ' 个');
        detail.textContent = parts.join(' · ');
        schedulePoll(1000);
      } else if (d.state === 'queued') {
        section.classList.remove('generating');
        bar.classList.remove('active', 'indeterminate');
        bar.style.width = '10%';
        bar.classList.add('active');
        pctLabel.textContent = '排队';
        label.textContent = '队列中';
        nodeName.style.display = 'none';
        detail.textContent = d.queue_pending > 0 ? '前面还有 ' + d.queue_pending + ' 个' : '等待执行...';
        stepDetail.textContent = '';
        schedulePoll(2000);
      } else {
        section.classList.remove('generating');
        bar.classList.remove('active', 'indeterminate');
        bar.style.width = '30%';
        bar.classList.add('active');
        pctLabel.textContent = '...';
        label.textContent = '生成中';
        detail.textContent = '';
        stepDetail.textContent = '';
        nodeName.style.display = 'none';
        schedulePoll(2000);
      }
    } else {
      // 空闲
      section.style.display = 'none';
      bar.style.width = '0%';
      bar.classList.remove('active', 'indeterminate');
      section.classList.remove('generating');
      if (_wasRunning) {
        loadGalleryImages(true); // v4.12.0: force 立即重扫，新图马上出现在页首
        // 媒体资产面板：生成完成自动弹出（仅 PC），强制拉取最新列表（异步时序：直接渲染会拿到旧列表）
        const mp = window.innerWidth >= 900 ? document.getElementById('media_panel') : null;
        if (mp) {
          if (!mp.classList.contains('show')) mp.classList.add('show');
          setTimeout(() => loadMediaPanel(true), 800); // 缓冲等图片文件落盘
        }
      }
      schedulePoll(5000);
    }
    _wasRunning = d.running;
  } catch(e) { schedulePoll(5000); }
}

// checkNewOutputImage 已移除：用户不需要生成后自动大图预览

// 页面可见性 → 切后台暂停，回来立刻刷新
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    clearTimeout(_pollTimer);
  } else {
    schedulePoll(100);
  }
});

// ========================================================================
// 点击/触摸模态框外部关闭
// ========================================================================
document.addEventListener('click', (e) => {
  // 页面模式下点击背景不关闭（避免露出老布局），仅弹窗模式生效
  if (e.target.id === 'wf_mgr_modal' && !document.body.classList.contains('page-wfmgr')) { closeWfMgrModal(); return; }
  if (e.target.classList.contains('modal') && !e.target.closest('.modal-content')) {
    e.target.classList.remove('active');
  }
  // 画廊外部点击关闭（桌面端点击遮罩或外部，移动端同上）
  const dd = document.getElementById('gallery_dropdown');
  if (dd && dd.classList.contains('show')) {
    if (!dd.contains(e.target) && !e.target.closest('[data-action="toggleGalleryDropdown"]')) {
      closeGalleryDropdown();
    }
  }
  // 画廊关闭按钮（PC 页面模式返回工作流页，移动端关闭浮层）
  if (e.target.id === 'gallery_dropdown_close' || e.target.closest('#gallery_dropdown_close')) {
    if (document.body.classList.contains('page-gallery')) { switchPage('wf'); }
    else { closeGalleryDropdown(); }
  }
  // 画廊类型标签切换
  const tab = e.target.closest('.gallery-tab');
  if (tab && tab.closest('#gallery_type_tabs')) {
    switchGalleryType(tab.dataset.type);
  }
  // 画廊时间筛选切换
  const filter = e.target.closest('.gallery-filter');
  if (filter && filter.closest('#gallery_time_filters')) {
    switchGalleryFilter(filter.dataset.filter);
  }
  // IP分类下拉框外部点击关闭
  const catPanel = document.getElementById('grm_cat_panel');
  const catTrigger = document.getElementById('grm_cat_trigger');
  if (catPanel && catPanel.style.display !== 'none') {
    if (!catPanel.contains(e.target) && !catTrigger?.contains(e.target)) {
      closeGrmCatDropdown();
    }
  }
  // 编辑弹窗内分类下拉框外部点击关闭
  const modalCatPanel = document.getElementById('grm_category_panel');
  const modalCatTrigger = document.getElementById('grm_category_trigger');
  if (modalCatPanel && modalCatPanel.style.display !== 'none') {
    if (!modalCatPanel.contains(e.target) && !modalCatTrigger?.contains(e.target)) {
      closeGrmCategoryDropdown();
    }
  }
});
// 移动端触摸关闭模态框（touchend 比 click 更快响应）
document.addEventListener('touchend', (e) => {
  if (e.target.id === 'sidebar_backdrop' && e.target.classList.contains('active')) {
    e.preventDefault();
    closeSidebar();
  }
  if (e.target.id === 'wf_mgr_modal' && !document.body.classList.contains('page-wfmgr')) {
    e.preventDefault();
    closeWfMgrModal();
  }
  if (e.target.classList.contains('modal') && !e.target.closest('.modal-content')) {
    e.preventDefault();
    e.target.classList.remove('active');
  }
});

// ========================================================================
// 工作流编辑模态框（含可见性 / 别名 / 分类 / 预览 / 保存）
// ========================================================================
// 统一弹窗：工作流独立/编辑
function showWfMgrModal(tab) {
  const modal = document.getElementById('wf_mgr_modal');
  if (!modal) { toast('功能开发中', 'error'); return; }
  modal.classList.add('active');
  // 切换到指定选项卡
  if (tab) switchWfMgrTab(tab);
  // 加载编辑数据
  _editListCache = { all: null, cfg: null };
  loadWfEditList();
}
function closeWfMgrModal() {
  const modal = document.getElementById('wf_mgr_modal');
  if (modal) { modal.classList.remove('active'); const rl = document.getElementById('wf_edit_result'); if (rl) rl.style.display = 'none'; }
  loadWorkflows();
}
// 保留旧函数名作为别名（兼容其他引用）
function showWfEditModal() { showWfMgrModal('edit'); }
function closeWfEditModal() { closeWfMgrModal(); }

// 选项卡切换
function switchWfMgrTab(tab) {
  document.querySelectorAll('.wf-mgr-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tab);
  });
  document.querySelectorAll('.wf-mgr-panel').forEach(p => {
    p.classList.toggle('active', p.id === 'wf_mgr_' + tab + '_panel');
  });
  // 切换到独立工作流时刷新数据
  if (tab === 'bind') {
    showBindMgrModal();
  }
}

// 点击选项卡切换
document.addEventListener('click', (e) => {
  const tabBtn = e.target.closest('.wf-mgr-tab');
  if (tabBtn) switchWfMgrTab(tabBtn.dataset.tab);
});

let _currentWfEditCat = 'all';
let _editAliasCache = {};
let _editCatCache = {};
/** 编辑列表缓存：筛选/切分类直接渲染，不反复请求 API */
let _editListCache = { all: null, cfg: null };

function filterWfByCat(cat, btn) {
  _currentWfEditCat = cat;
  document.querySelectorAll('#wf_cat_tags .wf-cat-tag').forEach((b) =>{ b.classList.toggle('active', b === btn); });
  _renderWfEditList(); // 仅 DOM 重建，无 API
}
async function loadWfEditList() {
  try {
    const [all, cfg] = await Promise.all([
      apiFetch('/api/workflows/all', {}, true),
      apiFetch('/api/workflow-params-config', {}, true)
    ]);
    if (!all || !cfg) return;
    _editListCache.all = all;
    _editListCache.cfg = cfg;
    const aliases = cfg.__workflow_aliases__ || {};
    _editAliasCache = {};
    Object.keys(aliases).forEach((k) => { _editAliasCache[k] = aliases[k]; });
    const serverCats = Object.assign({}, cfg.__wf_categories__ || {}, cfg.__workflow_categories__ || {});
    _editCatCache = {};
    Object.keys(serverCats).forEach((k) => { _editCatCache[k] = serverCats[k]; });
    _renderWfEditList();
  } catch(e) {
    toast('加载工作流列表失败', 'error');
  }
}
function _renderWfEditList() {
  const all = _editListCache.all;
  if (!all) return;
  const curWF = getCurrentWF();
  const grouped = {};
  all.forEach((w) => {
    const cat = _editCatCache[w.name] || '未分类';
    if (_currentWfEditCat !== 'all' && cat !== _currentWfEditCat) return;
    if (!grouped[cat]) grouped[cat] = [];
    grouped[cat].push(w);
  });
  const countEl = document.getElementById('wf_edit_count');
  const listEl = document.getElementById('wf_edit_list');
  let total = 0;
  let html = '';
  Object.keys(grouped).sort().forEach((cat) => {
    const groupWfs = grouped[cat];
    total += groupWfs.length;
    html += `<div class="wf-cat-group-header"><span class="wf-cat-dot"></span>${esc(cat)}<span class="wf-cat-group-count">(${groupWfs.length})</span></div>`;
    groupWfs.forEach((w) => {
      const alias = _editAliasCache[w.name] || '';
      const isCur = w.name === curWF;
      const pvUrl = w.preview ? '/api/workflow-preview?name=' + encodeURIComponent(w.name) : '';
      const pvHtml = pvUrl
        ? `<img src="${pvUrl}" alt="" loading="lazy" onerror="this.parentElement.classList.add('no-img')">`
        : '';
      html += `<div class="wf-edit-item">` +
        `<span class="wf-edit-thumb">${pvHtml}</span>` +
        `<span class="wf-edit-name"><span class="orig-text">${esc(w.name)}</span>${isCur ? '<span class="wf-edit-current">当前</span>' : ''}</span>` +
        `<input class="wf-edit-alias-input" type="text" placeholder="别名" value="${esc(alias)}" oninput="_editAliasCache['${w.name.replace(/'/g, "\\'")}']=this.value" autocomplete="off">` +
        `<select class="wf-edit-cat-select" data-wfname="${w.name.replace(/"/g, '&quot;')}">${getCatOptions(_editCatCache[w.name] || '')}</select>` +
        `<div class="wf-edit-item-actions">` +
        `<button class="wf-edit-preview-btn" title="上传预览图" onclick="event.stopPropagation();uploadWfPreview('${w.name.replace(/'/g, "\\'")}')"><svg class="icon-sm" aria-hidden="true"><use href="#icon-image"/></svg></button>` +
        `<button class="wf-del-btn" data-action="deleteWf" data-wfname="'${w.name.replace(/'/g, "\\'")}'" title="删除工作流" onclick="event.stopPropagation();confirmDeleteWf('${w.name.replace(/'/g, "\\'")}')"><svg class="icon-sm" aria-hidden="true"><use href="#icon-ban"/></svg></button>` +
        `</div>` +
        `</div>`;
    });
  });
  if (countEl) countEl.textContent = `共 ${  total  } 个工作流`;
  if (listEl) listEl.innerHTML = html || '<div style="padding:16px;text-align:center;color:var(--text-sub)">无匹配工作流</div>';
}
// 事件委托：分类下拉框变化 → 即时保存（不触发侧栏重建，关闭弹窗时统一刷新）
document.getElementById('wf_edit_list').addEventListener('change', async (e) => {
  const sel = e.target.closest('.wf-edit-cat-select');
  if (!sel) return;
  const name = sel.dataset.wfname;
  const category = sel.value;
  if (!name) return;
  _editCatCache[name] = category;
  const d = await apiFetch('/api/wf-category', { method:'POST', body:JSON.stringify({name, category}) });
  if (d?.ok) {
    _renderWfEditList(); // 仅编辑列表局部重渲染
    loadWorkflows();     // 同步刷新侧栏工作流列表
  } else {
    toast(`分类保存失败: ${category}`, 'error');
  }
});
async function saveWfEditAll() {
  let cfg = {};
  cfg = await apiFetch('/api/workflow-params-config', {}, true) || {};
  // 过滤空别名
  const cleanAliases = {};
  Object.keys(_editAliasCache).forEach((k) => {
    const v = _editAliasCache[k];
    if (v && v.trim && v.trim() !== '') cleanAliases[k] = v.trim();
  });
  cfg.__workflow_aliases__ = cleanAliases;
  // 过滤"未分类"
  const cleanCats = {};
  Object.keys(_editCatCache).forEach((k) => {
    const v = _editCatCache[k];
    if (v && v !== '未分类') cleanCats[k] = v;
  });
  cfg.__wf_categories__ = cleanCats;
  cfg.__workflow_categories__ = cleanCats; // 兼容旧数据
  cfg.__disabled_groups__ = cfg.__disabled_groups__ || {};
  cfg.__groups_data__ = cfg.__groups_data__ || [];
  cfg.__groups_source__ = cfg.__groups_source__ || '';
  cfg.__bind_target__ = cfg.__bind_target__ || '';
  cfg.__workflow_node_configs__ = cfg.__workflow_node_configs__ || {};
  const d = await apiFetch('/api/workflow-params', { method:'POST', body:JSON.stringify(cfg) });
  if (d?.ok) {
      const resEl = document.getElementById('wf_edit_result');
      const aliasCnt = Object.keys(cleanAliases).length;
      const catCnt = Object.keys(cleanCats).length;
      resEl.textContent = `✅ 保存成功（${  aliasCnt  } 别名 / ${  catCnt  } 分类）`;
      resEl.style.display = 'block';
      // 直接用保存的 cleanCats 更新本地缓存，不依赖服务器重载
      Object.keys(cleanCats).forEach((k) => { _editCatCache[k] = cleanCats[k]; });
      Object.keys(_editCatCache).forEach((k) => { if (!cleanCats[k]) delete _editCatCache[k]; });
      loadWorkflows();
      loadWfEditList();
      setTimeout(() => { resEl.style.display = 'none'; }, 4000);
    } else { toast('保存失败', 'error'); }
}
/** 缓存分类名列表，避免重复 querySelectorAll */
let _cachedCatNames = null;
function _getAvailableCats(forceRefresh) {
  if (_cachedCatNames && !forceRefresh) return _cachedCatNames;
  const tags = document.querySelectorAll('#wf_cat_tags .wf-cat-tag');
  const cats = [];
  tags.forEach((t) => {
    const text = t.textContent.trim();
    if (text && text !== '全部') cats.push(text);
  });
  _cachedCatNames = cats;
  return cats;
}
function getCatOptions(currentCat) {
  const allCats = _getAvailableCats();
  // 如果没有匹配的分类且 currentCat 为空，默认选中"未分类"
  const cat = currentCat || '未分类';
  return allCats.map((c) => { return `<option value="${esc(c)}"${c === cat ? ' selected' : ''}>${esc(c)}</option>`; }).join('');
}
function setWfCat(name, cat) {
  _editCatCache[name] = cat;
}
async function deleteWf(name) {
  // 已通过自定义确认弹窗确认，不再弹原生 confirm（残留的原生 confirm 会导致第二次确认框卡住网页）
  const d = await apiFetch('/api/wf-delete', { method:'POST', body:JSON.stringify({name:name}) });
  if (d?.ok) { toast('已删除'); _editListCache = { all: null, cfg: null }; loadWfEditList(); loadWorkflows(); }
}

/** 删除工作流确认弹窗（替代原生 confirm，参考 web-design-engineer 视觉规范） */
function confirmDeleteWf(name) {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay active wf-confirm-overlay';
  overlay.style.cssText = 'display:flex;align-items:center;justify-content:center;z-index:9999;pointer-events:auto;background:color-mix(in oklch, oklch(0% 0 0 / 0.45), transparent);backdrop-filter:blur(2px);';
  const safeName = String(name).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  const jsName = String(name).replace(/'/g, "\\'");
  overlay.innerHTML = `<div class="modal-panel wf-confirm-panel" style="max-width:360px;padding:22px 24px;text-align:center;border-radius:14px">
    <svg class="icon" style="width:34px;height:34px;color:var(--danger);margin:0 auto 12px;display:block" aria-hidden="true"><use href="#icon-alert-triangle"/></svg>
    <div style="font-size:15px;font-weight:700;margin-bottom:6px">删除工作流</div>
    <div style="font-size:12px;color:var(--text-sub);margin-bottom:18px;word-break:break-all;line-height:1.6">「${safeName}」<br>删除后不可恢复，确认删除？</div>
    <div style="display:flex;gap:10px;justify-content:center">
      <button class="btn btn-sm btn-secondary" onclick="this.closest('.wf-confirm-overlay').remove()">取消</button>
      <button class="btn btn-sm btn-danger" onclick="var o=this.closest('.wf-confirm-overlay');o.remove();deleteWf('${jsName}')">确认删除</button>
    </div>
  </div>`;
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  document.body.appendChild(overlay);
}
async function addWorkflows(input) {
  const files = input.files;
  if (!files || !files.length) return;
  const formData = new FormData();
  for (let i = 0; i < files.length; i++) formData.append('files', files[i]);
  const d = await apiFetch('/api/wf-add', { method:'POST', body:formData });
  if (d?.ok) { toast('✅ 已添加'); _editListCache = { all: null, cfg: null }; loadWfEditList(); loadWorkflows(); }
  input.value = '';
}

// ========================================================================
// 生图面板操作
// ========================================================================
function setResPreset(preset, btn) {
  document.querySelectorAll('#res_presets .res-preset-btn').forEach((b) =>{ b.classList.remove('active'); });
  btn.classList.add('active');
  document.getElementById('vram_warn_2k').style.display = (preset === '2K' || preset === '4K') ? 'block' : 'none';
  document.getElementById('vram_warn_4k').style.display = preset === '4K' ? 'block' : 'none';
  // 通知后端更新质量
  apiFetch('/api/set-quality', { method:'POST', body:JSON.stringify({quality: preset}) }, true).then((d) => {
    if (d && d.ok) {
      document.getElementById('res_info').textContent = `质量: ${preset} · 比例: ${d.ratio} · ${d.width}x${d.height}`;
    }
  }).catch(e => console.error('setResPreset error:', e));
}
function setRatio(ratio, btn) {
  document.querySelectorAll('#ratio_group .ratio-btn').forEach((b) =>{ b.classList.remove('active'); });
  btn.classList.add('active');
  // 通知后端更新比例
  apiFetch('/api/set-ratio', { method:'POST', body:JSON.stringify({ratio: ratio}) }, true).then((d) => {
    if (d && d.ok) {
      document.getElementById('res_info').textContent = `质量: ${d.quality} · 比例: ${d.ratio} · ${d.width}x${d.height}`;
    }
  }).catch(e => console.error('setRatio error:', e));
}

// ========================================================================
// 官方原生节点（ResolutionSelector / PrimitiveFloat）设置
// ========================================================================
const OFFICIAL_ASPECT_OPTIONS = [
  '1:1 (Square)', '2:3 (Portrait Photo)', '3:2 (Photo)',
  '3:4 (Portrait Standard)', '4:3 (Standard)',
  '9:16 (Portrait Widescreen)', '16:9 (Widescreen)', '21:9 (Ultrawide)'
];

/** 视频专属：BasicScheduler 基本调度器面板（scheduler/steps/denoise，写回节点） */
function renderBasicScheduler() {
  const panel = document.getElementById('basic_scheduler_panel');
  if (!panel) return;
  const nodes = (allParamsData && allParamsData.nodes) ? allParamsData.nodes : [];
  const bsNodes = nodes.filter(n => /BasicScheduler/i.test(n.class_type || ''));
  if (!bsNodes.length) { panel.style.display = 'none'; return; }
  panel.style.display = 'block';
  const n = bsNodes[0];
  const val = (key) => { const p = n.params && n.params.find(x => x.key === key); return p ? p.value : undefined; };
  const curS = val('scheduler');
  const curSteps = val('steps');
  const curDenoise = val('denoise');
  // scheduler 下拉（常见调度器 + 当前值兜底）
  const sel = document.getElementById('bs_scheduler_select');
  if (sel && !sel.dataset.inited) {
    const opts = ['normal', 'karras', 'exponential', 'simple', 'ddim_uniform', 'beta', 'linear_quadratic'];
    sel.innerHTML = opts.map(o => `<option${String(o) === String(curS) ? ' selected' : ''}>${o}</option>`).join('') +
      (curS && !opts.includes(String(curS)) ? `<option selected>${esc(String(curS))}</option>` : '');
    sel.dataset.inited = '1';
  }
  const stepsInput = document.getElementById('bs_steps_input');
  if (stepsInput && curSteps !== undefined) stepsInput.value = curSteps;
  const denoiseInput = document.getElementById('bs_denoise_input');
  if (denoiseInput && curDenoise !== undefined && curDenoise !== null && curDenoise !== '') {
    // 先转数值再取精度：saved_texts 回填的是字符串，"1".toFixed 会抛 TypeError
    // 炸掉整个 renderBasicScheduler → id 不重命名 → 面板改动永远收集不到（步数锁死根因）
    const dn = Number(curDenoise);
    if (!isNaN(dn)) denoiseInput.value = dn;
  }
  // 控件 id 用 param_{nid}_{key}，复用 saveParams() 自动收集写回
  if (sel) sel.id = `param_${n.id}_scheduler`;
  if (stepsInput) stepsInput.id = `param_${n.id}_steps`;
  if (denoiseInput) denoiseInput.id = `param_${n.id}_denoise`;
}

/**
 * 分辨率面板统一显隐（重构版：单一入口，纯 JS 内联 style，不依赖 :has / ~ 选择器）：
 * - 官方 ResolutionSelector 节点存在 → 只显示官方方案
 * - 无官方节点 → 只显示旧版预设（480p~4K + 比例）
 * - 官方时长节点(PrimitiveFloat/Float)存在 → 额外显示时长面板
 * 面板默认全部隐藏，此函数每次 updateNodeDisplay 时强制刷新，任何端/任何路径都不并存。
 */
function updateResPanels() {
  const hasOff = (officialResNodes || []).length > 0;
  const hasDur = (officialDurationNodes || []).length > 0;
  // 旧版分辨率节点是否存在（resNodeId 由后端识别；部分工作流无分辨率节点，如纯图生图流）
  const hasLegacy = !!resNodeId;
  const oPanel = document.getElementById('official_res_panel');
  const lPanel = document.getElementById('legacy_res_panel');
  const dPanel = document.getElementById('official_duration_panel');
  if (oPanel) oPanel.style.display = hasOff ? 'block' : 'none';
  // 只有存在旧版分辨率节点时才显示旧版预设；两者都无 → 分辨率区块整体隐藏
  if (lPanel) lPanel.style.display = (hasOff || !hasLegacy) ? 'none' : 'block';
  if (dPanel) dPanel.style.display = hasDur ? 'block' : 'none';
  const prepRes = document.getElementById('prep_resolution');
  if (prepRes) prepRes.style.display = (hasOff || hasLegacy || hasDur) ? 'block' : 'none';
}

function _initOfficialResPanel() {
  const panel = document.getElementById('official_res_panel');
  if (!panel) return;
  const hasOfficial = officialResNodes.length > 0;
  if (!hasOfficial) { _officialResSig = ''; return; }
  // 工作流可能有多个官方分辨率节点（如 H3 的 一采/二采）——每个渲染一组，标题作组名。
  // 写回统一走 param_{nid}_{key} + saveParams()（与调度器面板同通道）。
  const sig = officialResNodes.join(',') + '@' + (getCurrentWF() || '');
  if (sig === _officialResSig) return; // 已构建：保留当前 DOM（不打断正在编辑的输入框）
  _officialResSig = sig;
  const nodes = (allParamsData && allParamsData.nodes) || [];
  const num = (v, fb) => { const x = Number(v); return (v !== undefined && v !== null && v !== '' && !isNaN(x)) ? x : fb; };
  const build = (nid) => {
    const node = nodes.find(n => String(n.id) === String(nid));
    if (!node || !node.params) return '';
    const getP = (k) => { const p = node.params.find(x => x.key === k); return p ? p.value : undefined; };
    const curAspect = getP('aspect_ratio');
    let aspectOpts = '';
    let hasExact = false;
    aspectOpts = OFFICIAL_ASPECT_OPTIONS.map((o) => {
      const sel = String(o) === String(curAspect);
      if (sel) hasExact = true;
      return `<option${sel ? ' selected' : ''}>${o}</option>`;
    }).join('');
    if (curAspect !== undefined && !hasExact) {
      // 兼容简写比例（"9:16" → "9:16 (Portrait Widescreen)"）
      const shortA = String(curAspect).split(' ')[0];
      const match = OFFICIAL_ASPECT_OPTIONS.find(o => o.split(' ')[0] === shortA);
      aspectOpts = OFFICIAL_ASPECT_OPTIONS.map((o) => `<option${o === match ? ' selected' : ''}>${o}</option>`).join('');
      if (!match) aspectOpts += `<option selected>${esc(String(curAspect))}</option>`;
    }
    const title = esc(node.title || '');
    const header = officialResNodes.length > 1
      ? `<div class="official-res-label" style="margin:8px 0 2px;font-weight:700">${title ? title + ' ' : ''}#${nid}</div>`
      : '';
    return header + `
      <div class="official-res-row">
        <label class="official-res-label">宽高比</label>
        <select id="param_${nid}_aspect_ratio" class="official-res-select" onchange="saveParams()">${aspectOpts}</select>
      </div>
      <div class="official-res-row">
        <label class="official-res-label">百万像素</label>
        <input type="number" id="param_${nid}_megapixels" class="official-res-input" min="0.1" max="16" step="0.1" value="${num(getP('megapixels'), 1)}" onchange="saveParams()">
        <span class="official-res-unit">MP</span>
      </div>
      <div class="official-res-row">
        <label class="official-res-label">倍数</label>
        <input type="number" id="param_${nid}_multiple" class="official-res-input" min="8" max="128" step="4" value="${num(getP('multiple'), 32)}" onchange="saveParams()">
      </div>`;
  };
  panel.innerHTML = officialResNodes.map(build).join('');
}

function _initOfficialDurationPanel() {
  const panel = document.getElementById('official_duration_panel');
  if (!panel) return;
  // 面板显隐统一由 updateResPanels() 控制；此函数只负责时长回填
  // 回填当前时长：从工作流节点读取官方时长节点 value，避免刷新后显示默认 5
  if (officialDurationNodes.length > 0 && allParamsData && allParamsData.nodes) {
    const durInput = document.getElementById('official_duration');
    if (durInput) {
      let curDur = null;
      for (const nid of officialDurationNodes) {
        const node = allParamsData.nodes.find(n => String(n.id) === String(nid));
        if (!node || !node.params) continue;
        const pVal = node.params.find(p => p.key === 'value' && typeof p.value === 'number');
        if (pVal) { curDur = pVal.value; break; }
      }
      if (curDur !== null && !isNaN(curDur)) durInput.value = Number(Number(curDur).toFixed(6));
    }
  }
}

function setOfficialRes() {
  const aspect = document.getElementById('official_aspect_ratio')?.value || '';
  const mp = document.getElementById('official_megapixels')?.value;
  const mult = document.getElementById('official_multiple')?.value;
  if (!aspect) return;
  apiFetch('/api/set-official-res', { method:'POST', body:JSON.stringify({ aspect_ratio: aspect, megapixels: mp ? parseFloat(mp) : undefined, multiple: mult ? parseInt(mult) : undefined }) }, true)
    .then((d) => {
      // 成功提示去重：oninput(防抖)+onchange 双触发会连续弹多个 toast
      if (d && d.ok) {
        const sig = `${aspect}|${d.megapixels}|${d.multiple}`;
        if (window._lastResToast !== sig) {
          toast(`✅ 官方分辨率已设置: ${aspect} ${d.megapixels}MP ×${d.multiple}`);
          window._lastResToast = sig;
        }
      }
      else toast(d?.error || '官方分辨率设置失败', 'error');
    }).catch(e => console.error('setOfficialRes error:', e));
}
let _officialResTimer = null;
function setOfficialResDebounced() {
  clearTimeout(_officialResTimer);
  _officialResTimer = setTimeout(setOfficialRes, 600);
}

function setOfficialDuration() {
  const v = document.getElementById('official_duration')?.value;
  if (v === undefined || v === '') return;
  apiFetch('/api/set-duration', { method:'POST', body:JSON.stringify({ duration: parseFloat(v) }) }, true)
    .then((d) => {
      // 成功提示去重：oninput(防抖)+onchange 双触发会连续弹多个 toast,相同值只提示一次
      if (d && d.ok) {
        if (window._lastDurToast !== String(d.duration)) {
          toast(`✅ 视频时长已设置: ${d.duration} 秒`);
          window._lastDurToast = String(d.duration);
        }
      }
      else toast(d?.error || '时长设置失败', 'error');
      // 实时刷新：重新拉取参数并重渲染（预设视频时长 / 指令推理最大帧数联动显示）
      refreshParamsAfterDuration();
    }).catch(e => console.error('setOfficialDuration error:', e));
}
let _officialDurationTimer = null;
function setOfficialDurationDebounced() {
  clearTimeout(_officialDurationTimer);
  _officialDurationTimer = setTimeout(setOfficialDuration, 600);
}

/** 时长设置成功后实时刷新：更新 allParamsData 并重渲染（无需强制刷新页面） */
async function refreshParamsAfterDuration() {
  try {
    const d = await apiFetch('/api/workflow-params', {}, true);
    if (d && !d.error) {
      allParamsData = d;
      updateNodeDisplay();
    }
  } catch (e) { console.warn('refreshParamsAfterDuration error:', e); }
}
function adjustUploadRatio(img) {
  // 图片预览加载完成后，按实际宽高比调整卡片比例（ua-ratio-auto 场景）
  const card = img.closest('.upload-media-card');
  if (!card || !card.classList.contains('ua-ratio-auto')) return;
  if (img.naturalWidth > 0 && img.naturalHeight > 0) {
    card.style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`;
  }
}
function handleImageUpload(input, nodeId, idx) {
  const file = input.files[0];
  // 上传卡片：本地即时预览 + 文件名
  if (file && idx !== undefined) {
    const preview = document.getElementById(`image_preview_${idx}`);
    const empty = document.getElementById(`image_empty_${idx}`);
    const fname = document.getElementById(`image_fname_${idx}`);
    if (preview) {
      preview.src = URL.createObjectURL(file);
      preview.style.display = 'block';
    }
    if (empty) empty.style.display = 'none';
    if (fname) { fname.textContent = file.name; fname.style.display = 'block'; }
  }
  const nameSpan = input.closest('.upload-media-card') ? input.closest('.upload-media-card').querySelector('.ua169-fname') : null;
  if (file && nameSpan) {
    nameSpan.textContent = file.name;
    nameSpan.style.display = 'block';
    const formData = new FormData();
    formData.append('image', file);
    // 附带该行对应的 LoadImage 节点 id，上传成功后把文件名写回工作流节点
    formData.append('node_id', nodeId || '');
    fetch('/api/upload-image', { method:'POST', body:formData }).then((r) =>{ return r.json(); }).then((d) => {
      if (d.ok) { toast(`✅ 已上传到节点 #${nodeId}`); }
      else { toast(d.error || '上传失败', 'error'); }
    }).catch(() => { toast('上传失败', 'error'); });
  } else if (file) {
    // 卡片框上传：即使没有 nameSpan 也走上传
    const formData = new FormData();
    formData.append('image', file);
    formData.append('node_id', nodeId || '');
    fetch('/api/upload-image', { method:'POST', body:formData }).then((r) =>{ return r.json(); }).then((d) => {
      if (d.ok) { toast(`✅ 已上传到节点 #${nodeId}`); }
      else { toast(d.error || '上传失败', 'error'); }
    }).catch(() => { toast('上传失败', 'error'); });
  }
}

/** 音频上传：附带 LoadAudio 节点 id，上传成功后由后端写回工作流节点 audio 输入 */
function handleAudioUpload(input, nodeId, idx) {
  const file = input.files[0];
  if (file && idx !== undefined) {
    const fname = document.getElementById(`audio_fname_${idx}`);
    const empty = document.getElementById(`audio_empty_${idx}`);
    if (fname) { fname.textContent = file.name; fname.style.display = 'block'; }
    if (empty) empty.style.display = 'none';
  }
  const nameSpan = input.closest('.upload-media-card') ? input.closest('.upload-media-card').querySelector('.ua169-fname') : null;
  if (file && nameSpan) {
    nameSpan.textContent = file.name;
    nameSpan.style.display = 'block';
    const formData = new FormData();
    formData.append('media', file);
    formData.append('kind', 'audio');
    formData.append('node_id', nodeId || '');
    fetch('/api/upload-media', { method:'POST', body:formData }).then((r) =>{ return r.json(); }).then((d) => {
      if (d.ok) { toast(`✅ 音频已上传到节点 #${nodeId}`); }
      else { toast(d.error || '上传失败', 'error'); }
    }).catch(() => { toast('上传失败', 'error'); });
  }
}

/** 视频上传：附带视频加载节点 id，上传成功后由后端写回工作流节点 video 输入 */
function handleVideoUpload(input, nodeId, idx) {
  const file = input.files[0];
  if (file && idx !== undefined) {
    const fname = document.getElementById(`video_fname_${idx}`);
    const empty = document.getElementById(`video_empty_${idx}`);
    if (fname) { fname.textContent = file.name; fname.style.display = 'block'; }
    if (empty) empty.style.display = 'none';
  }
  const nameSpan = input.closest('.upload-media-card') ? input.closest('.upload-media-card').querySelector('.ua169-fname') : null;
  if (file && nameSpan) {
    nameSpan.textContent = file.name;
    nameSpan.style.display = 'block';
    const formData = new FormData();
    formData.append('media', file);
    formData.append('kind', 'video');
    formData.append('node_id', nodeId || '');
    fetch('/api/upload-media', { method:'POST', body:formData }).then((r) =>{ return r.json(); }).then((d) => {
      if (d.ok) { toast(`✅ 视频已上传到节点 #${nodeId}`); }
      else { toast(d.error || '上传失败', 'error'); }
    }).catch(() => { toast('上传失败', 'error'); });
  }
}

// ========================================================================
// ✨ 新增: 底部导航滚动高亮 (Scroll Spy)
// ========================================================================
(function initScrollSpy() {
  const navItems = document.querySelectorAll('.bn-item');
  if (!navItems.length) return;
  const sectionMap = [
    { el: document.getElementById('sidebar'), action: 'toggleSidebar' },
    { el: document.getElementById('settings_area'), action: 'toggleSettings' },
  ];

  let scrollTimeout;
  function updateActiveNav() {
    if (scrollTimeout) cancelAnimationFrame(scrollTimeout);
    scrollTimeout = requestAnimationFrame(() => {
      const scrollY = window.scrollY + 120;
      let activeAction = '';
      sectionMap.forEach(s => {
        if (s.el && s.el.offsetTop <= scrollY) activeAction = s.action;
      });
      navItems.forEach(item => {
        const isActive = item.dataset.action === activeAction;
        item.classList.toggle('active', isActive);
        item.classList.toggle('active-dot', isActive);
      });
    });
  }

  // Debounced scroll listener
  let ticking = false;
  window.addEventListener('scroll', () => {
    if (!ticking) {
      window.requestAnimationFrame(() => { updateActiveNav(); ticking = false; });
      ticking = true;
    }
  }, { passive: true });
  // Initial update
  setTimeout(updateActiveNav, 1000);
})();

// ========================================================================
// ✨ 新增: 移动端可折叠参数面板
// ========================================================================
(function initCollapsibleSections() {
  // 仅在移动端启用
  if (window.innerWidth > 768) return;
  const sections = document.querySelectorAll('.prep-section');
  sections.forEach((section) => {
    // 分辨率区块不参与折叠：避免被 Storage 折叠状态隐藏导致"分辨率不显示"（手机端常见）
    if (section.id === 'prep_resolution') return;
    const title = section.querySelector('.section-title');
    const body = section.querySelector('textarea, .res-presets, .upload-area');
    if (!title || !body) return;
    const bodyParent = body.closest('.prep-section > div') || body.parentElement;
    if (!bodyParent) return;

    // 包装内容为可折叠体
    let collapseBody = bodyParent.querySelector('.collapsible-body');
    if (!collapseBody) {
      collapseBody = document.createElement('div');
      collapseBody.className = 'collapsible-body';
      // 将 body 之后的所有兄弟节点移到 collapsible-body 中
      let sibling = body;
      const nodes = [];
      while (sibling && sibling !== section.lastElementChild) {
        const next = sibling.nextElementSibling;
        if (next) nodes.push(next);
        sibling = next;
      }
      // 重新组织: 用 collapsible-body 包裹 body 自身及之后的所有内容
      const wrapStart = body;
      const wrapEnd = nodes.length > 0 ? nodes[nodes.length - 1] : body;
      const fragment = document.createDocumentFragment();
      let current = wrapStart;
      while (current && current !== section.lastElementChild) {
        const next = current.nextElementSibling;
        fragment.appendChild(current);
        if (current === wrapEnd) break;
        current = next;
      }
      collapseBody.appendChild(fragment);
      section.appendChild(collapseBody);
    }

    // 将标题变为可点击
    title.classList.add('collapsible-header');
    const icon = document.createElement('span');
    icon.className = 'collapse-icon';
    icon.textContent = '▼';
    title.appendChild(icon);

    // 恢复展开状态
    const sectionId = section.id || '';
    const stored = sectionId ? Storage.get(`collapse_${sectionId}`) : null;
    if (stored === 'collapsed') {
      title.classList.add('collapsed');
      collapseBody.classList.add('collapsed');
    }

    title.addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      const isCollapsed = title.classList.toggle('collapsed');
      collapseBody.classList.toggle('collapsed', isCollapsed);
      if (sectionId) Storage.set(`collapse_${sectionId}`, isCollapsed ? 'collapsed' : '');
    });
  });
})();

// ========================================================================
// 按钮点击反馈 — 使用 CSS :active 代替 JS Ripple 避免布局抖动
// ========================================================================

// ========================================================================
// ✨ 新增: 进度条展开动画
// ========================================================================
(function patchProgressSection() {
  const origDisplay = Object.getOwnPropertyDescriptor(Element.prototype, 'style');
  // 监听 display 变化
  const observer = new MutationObserver((mutations) => {
    mutations.forEach((m) => {
      if (m.type === 'attributes' && m.attributeName === 'style') {
        const el = m.target;
        if (el.id === 'progress_section' && (el.style.display === 'flex' || el.style.display === 'block')) {
          el.classList.remove('section-appear');
          // 强制回流
          void el.offsetWidth;
          el.classList.add('section-appear');
        }
      }
    });
  });
  const ps = document.getElementById('progress_section');
  if (ps) {
    observer.observe(ps, { attributes: true, attributeFilter: ['style'] });
  }
})();

// ========================================================================
// ✨ 新增: 窗口大小变化时重新初始化可折叠面板
// ========================================================================
// resize 监听（与上行合并，防止重复注册）

// ========================================================================
// ✨ 新增: 触摸设备优化 - 长按提示
// ========================================================================
// ========================================================================
// 画廊 Dropdown
// ========================================================================
let _galleryData = [];
let _galleryFilter = 'all';
let _galleryType = 'image';
let _batchMode = false;
let _acceptData = true;
let _selectedBatch = new Set();
let _galleryPage = 1;
let _galleryPageSize = 20;
let _galleryFiltered = [];
let _galleryHasMore = false;   // v4.12.0: 分页——还有下一页
let _galleryTotal = 0;         // v4.12.0: 服务端过滤后的总数（FAB 徽标用）
let _galleryLoadingMore = false;

function toggleGalleryDropdown() {
  const dd = document.getElementById('gallery_dropdown');
  const backdrop = document.getElementById('gallery_backdrop');
  const isOpen = dd.classList.contains('show');
  dd.classList.toggle('show');
  backdrop.classList.toggle('show');
  if (!isOpen) loadGalleryImages();
  document.body.classList.toggle('gallery-open', !isOpen);
}

/** 画廊视图切换：单图（逐张大图）/ 多图（瀑布流） */
function toggleGalleryView() {
  const dd = document.getElementById('gallery_dropdown');
  if (!dd) return;
  const single = dd.classList.toggle('view-single');
  const btn = document.getElementById('gallery_view_toggle');
  if (btn) {
    btn.classList.toggle('active', single);
    btn.innerHTML = single
      ? '<svg class="icon-sm" aria-hidden="true"><use href="#icon-image"/></svg> 单图'
      : '<svg class="icon-sm" aria-hidden="true"><use href="#icon-grid"/></svg> 多图';
  }
  try { localStorage.setItem('gallery_view', single ? 'single' : 'multi'); } catch (_) { /* noop */ }
  // 切换后重置到第一张，单图模式滚动定位生效
  if (_galleryFiltered && _galleryFiltered.length) {
    _galleryCurrentIdx = 0;
    setTimeout(() => scrollToGalleryIndex(0), 50);
  }
}

/** 应用上次保存的画廊视图模式 */
function initGalleryView() {
  const dd = document.getElementById('gallery_dropdown');
  if (!dd) return;
  let single = false;
  try { single = localStorage.getItem('gallery_view') === 'single'; } catch (_) { /* noop */ }
  if (single) dd.classList.add('view-single');
  const btn = document.getElementById('gallery_view_toggle');
  if (btn) {
    btn.classList.toggle('active', single);
    btn.innerHTML = single
      ? '<svg class="icon-sm" aria-hidden="true"><use href="#icon-image"/></svg> 单图'
      : '<svg class="icon-sm" aria-hidden="true"><use href="#icon-grid"/></svg> 多图';
  }
}
// 页面加载时应用画廊视图模式（切换按钮状态 + 布局）
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initGalleryView);
} else {
  initGalleryView();
}

function closeGalleryDropdown() {
  document.getElementById('gallery_dropdown').classList.remove('show');
  document.getElementById('gallery_backdrop').classList.remove('show');
  document.body.classList.remove('gallery-open');
  if (_batchMode) exitBatchMode();
}

// PC 端初始默认进入「工作流」页（与默认 active tab 一致）；移动端保持生成台+底部导航
// ⚠️ 修复：旧版无条件 switchPage('wf')，导致在生成台刷新后强制退回工作流列表，
//    且该调用会覆盖 ui_last_page 记录，使恢复逻辑失效。现在按上次所在页决定初始页。
function _initInitialPage() {
  if (window.innerWidth > 768 && typeof switchPage === 'function') {
    let target = 'wf';
    try {
      if (localStorage.getItem('ui_last_page') === 'gen') target = 'gen';
    } catch (_) { /* noop */ }
    switchPage(target);
  }
}
document.addEventListener('DOMContentLoaded', () => { _initInitialPage(); });
if (document.readyState !== 'loading' && window.innerWidth > 768 && typeof switchPage === 'function') {
  setTimeout(() => _initInitialPage(), 50);
}

function toggleAcceptData() {
  _acceptData = !_acceptData;
  const btn = document.getElementById('gallery_accept_btn');
  if (btn) {
    btn.classList.toggle('active', _acceptData);
    btn.title = _acceptData ? '数据接收中' : '数据已暂停';
    const label = document.getElementById('gallery_accept_label');
    if (label) label.textContent = _acceptData ? '接收' : '暂停';
  }
  const scrollView = document.getElementById('gallery_scroll_view');
  const empty = document.getElementById('gallery_default_empty');
  if (!_acceptData) {
    _galleryData = [];
    if (scrollView) scrollView.style.display = 'none';
    if (empty) { empty.style.display = 'flex'; empty.innerHTML = emptyGallery('pause', '📡 数据接收已暂停', '点击恢复接收'); empty.onclick = () => toggleAcceptData(); }
  } else {
    loadGalleryImages();
    if (empty) empty.onclick = null;
  }
}

const EMPTY_ILLUS = {
  image: `<svg class="gallery-empty-svg" viewBox="0 0 80 80" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="10" y="16" width="60" height="48" rx="4"/><circle cx="30" cy="34" r="6"/><path d="M12 60l18-22 14 16 10-8 16 14"/></svg>`,
  video: `<svg class="gallery-empty-svg" viewBox="0 0 80 80" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="12" y="18" width="56" height="44" rx="6"/><circle cx="40" cy="40" r="8"/><path d="M36 36l8 4-8 4z"/></svg>`,
  pause: `<svg class="gallery-empty-svg" viewBox="0 0 80 80" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="40" cy="40" r="24"/><path d="M32 30v20M48 30v20"/></svg>`,
};

function emptyGallery(type, msg, sub) {
  const key = type || 'image';
  return `<div class="gallery-empty">${EMPTY_ILLUS[key] || EMPTY_ILLUS.image}<span class="gallery-empty-text">${msg}</span>${sub ? `<span class="gallery-empty-sub">${sub}</span>` : ''}</div>`;
}

function formatRelativeTime(timestamp) {
  const now = Date.now() / 1000;
  const diff = now - timestamp;
  const mins = Math.floor(diff / 60);
  const hours = Math.floor(diff / 3600);
  const days = Math.floor(diff / 86400);
  if (mins < 1) return '刚刚';
  if (mins < 60) return `${mins}分钟前`;
  if (hours < 24) return `${hours}小时前`;
  if (days < 3) return `${days}天前`;
  const d = new Date(timestamp * 1000);
  return `${d.getMonth()+1}/${d.getDate()}`;
}

function escGallery(str) {
  if (str == null) return '';
  const d = document.createElement('div');
  d.textContent = String(str);
  return d.innerHTML;
}

async function loadGalleryImages(force = false, append = false) {
  // v4.12.0: 分页加载——服务端扫描缓存(30s/force 刷新) + type/filter 过滤 + offset/limit 分页，
  // 旧版每次全量扫描+全量返回+一次性渲染全部节点，列表越大越慢
  if (!append && !force && !_acceptData) return; // 数据接收已暂停，不加载（删除后强制刷新不受此限制）
  if (_galleryLoadingMore) return;
  const offset = append ? (_galleryData || []).length : 0;
  const q = new URLSearchParams({ offset: String(offset), limit: '60', type: _galleryType, filter: _galleryFilter });
  if (force) q.set('force', '1');
  const d = await apiFetch('/api/gallery?' + q.toString(), {}, true);
  const page = (d && d.images) ? d.images : [];
  _galleryHasMore = !!(d && d.hasMore);
  _galleryTotal = (d && d.total) || 0;
  if (append) {
    const known = new Set((_galleryData || []).map(x => x.path));
    _galleryData = (_galleryData || []).concat(page.filter(x => !known.has(x.path)));
    _galleryFiltered = _galleryData.slice();
    appendGallerySlides(page.filter(x => !known.has(x.path)));
  } else {
    _galleryData = page.slice();
    _galleryFiltered = _galleryData.slice();
    _galleryCurrentIdx = 0;
    renderGallerySlides();
    initGalleryScroll();
    // v4.12.0: 显示切换补回（旧 applyGalleryFilters 的职责，重构时遗漏）
    const emptyEl = document.getElementById('gallery_default_empty');
    const svEl = document.getElementById('gallery_scroll_view');
    if (emptyEl) emptyEl.style.display = _galleryFiltered.length ? 'none' : 'flex';
    if (svEl) svEl.style.display = _galleryFiltered.length ? 'block' : 'none';
  }
  // 更新FAB徽标（用服务端 total，不再依赖已加载页大小）
  const badge = document.getElementById('gallery_fab_badge');
  if (badge) {
    if (_galleryTotal > 0) { badge.textContent = _galleryTotal > 99 ? '99+' : _galleryTotal; badge.style.display = 'flex'; }
    else { badge.style.display = 'none'; }
  }
}

// 追加渲染新一页的 slide（不重建已有 DOM）
function appendGallerySlides(items) {
  const track = document.getElementById('gallery_scroll_track');
  if (!track || !items.length) return;
  const startIdx = (_galleryFiltered || []).length - items.length;
  items.forEach((img, i) => {
    const idx = startIdx + i;
    const slide = document.createElement('div');
    slide.className = 'gallery-slide';
    slide.dataset.index = idx;
    slide.dataset.name = img.name || '';
    slide.dataset.rel = _relFromItem(img) || img.name || '';
    const wrap = document.createElement('div');
    wrap.className = 'gallery-media-wrap';
    wrap.onclick = function(e) {
      if (_batchMode) {
        toggleCurrentBatchSelect(_relFromItem(img) || img.name);
      } else if (img.type !== 'video') {
        const dd = document.getElementById('gallery_dropdown');
        if (!dd.classList.contains('view-single')) toggleGalleryView();
        _galleryCurrentIdx = idx;
        updateGalleryToolbar();
        scrollToGalleryIndex(idx);
      }
    };
    if (img.type === 'video') {
      const video = document.createElement('video');
      video.src = img.url || ''; video.muted = true; video.preload = 'metadata'; video.controls = true;
      video.setAttribute('playsinline', 'true');
      video.onerror = function() { this.style.display = 'none'; };
      wrap.appendChild(video);
    } else {
      const image = document.createElement('img');
      image.dataset.src = img.url || ''; image.alt = img.name || '';
      image.onerror = function() { this.alt = '加载失败'; };
      wrap.appendChild(image);
    }
    slide.appendChild(wrap);
    track.appendChild(slide);
    if (_galleryLazyObserver) _galleryLazyObserver.observe(slide);
    if (_galleryObserveFn) { /* 滚动观察由 initGalleryScroll 的 observer 统一处理 */ }
  });
  // 加载占位哨兵挪到末尾
  const sentinel = document.getElementById('gallery_more_sentinel');
  if (sentinel) track.appendChild(sentinel);
  initGalleryLazyLoad();
}

// 无限滚动：接近列表末尾时拉下一页
let _galleryMoreLoading = false;
async function loadGalleryMore() {
  if (_galleryMoreLoading || !_galleryHasMore || !_acceptData) return;
  _galleryMoreLoading = true;
  try { await loadGalleryImages(false, true); } finally { _galleryMoreLoading = false; }
}

function applyGalleryFilters() {
  // v4.12.0: type/filter 过滤已下沉到服务端，切换时重取第一页
  if ((_galleryData || []).length || _acceptData) loadGalleryImages(false);
}
function renderGallerySlides() {
  const track = document.getElementById('gallery_scroll_track');
  if (!track) return;
  const filtered = _galleryFiltered || [];
  
  track.innerHTML = '';
  filtered.forEach((img, idx) => {
    const slide = document.createElement('div');
    slide.className = 'gallery-slide';
    slide.dataset.index = idx;
    slide.dataset.name = img.name || '';
    slide.dataset.rel = _relFromItem(img) || img.name || ''; // v4.10.1: 批量删除键改用相对路径
    
    const wrap = document.createElement('div');
    wrap.className = 'gallery-media-wrap';
    wrap.onclick = function(e) {
      if (_batchMode) {
        toggleCurrentBatchSelect(_relFromItem(img) || img.name);
      } else if (img.type !== 'video') {
        // 多图模式：点击进入单图查看器该张（v4.11.0 去掉独立灯箱双系统）
        const dd = document.getElementById('gallery_dropdown');
        if (!dd.classList.contains('view-single')) toggleGalleryView();
        _galleryCurrentIdx = idx;
        updateGalleryToolbar();
        scrollToGalleryIndex(idx);
      }
    };
    
    if (img.type === 'video') {
      const video = document.createElement('video');
      video.src = img.url || '';
      video.muted = true;
      video.preload = 'metadata';
      video.controls = true;
      video.setAttribute('playsinline', 'true');
      video.onerror = function() { this.style.display = 'none'; };
      wrap.appendChild(video);
    } else {
      const image = document.createElement('img');
      // 懒加载：先存 data-src，滚动到可见区域才真正设置 src（避免一次性拉取所有图片）
      image.dataset.src = img.url || '';
      image.alt = img.name || '';
      image.onerror = function() { this.alt = '加载失败'; };
      wrap.appendChild(image);
    }
    
    // Batch overlay per slide
    const overlay = document.createElement('div');
    overlay.className = 'gallery-batch-overlay';
    overlay.style.display = _batchMode ? 'block' : 'none';
    overlay.onclick = function(e) {
      e.stopPropagation();
      toggleCurrentBatchSelect(img.name);
    };
    
    const check = document.createElement('div');
    check.className = 'gallery-batch-check';
    check.style.display = _batchMode ? 'flex' : 'none';
    check.textContent = _selectedBatch.has(img.name) ? '✓' : '○';
    if (_selectedBatch.has(img.name)) {
      overlay.classList.add('selected');
      check.classList.add('selected');
    }
    
    wrap.appendChild(overlay);
    wrap.appendChild(check);
    slide.appendChild(wrap);
    track.appendChild(slide);
  });

  // v4.12.0: 分页哨兵挪到列表末尾
  let sentinel = document.getElementById('gallery_more_sentinel');
  if (!sentinel) {
    sentinel = document.createElement('div');
    sentinel.id = 'gallery_more_sentinel';
    sentinel.style.cssText = 'height:48px;flex-shrink:0;width:100%;display:flex;align-items:center;justify-content:center;color:var(--text-sub);font-size:12px;';
  }
  sentinel.textContent = _galleryHasMore ? '上滑加载更多…' : '';
  track.appendChild(sentinel);

  updateGalleryToolbar();
  // 新渲染 slide 后重连 IntersectionObserver
  if (_galleryObserveFn) _galleryObserveFn();
  // 图片懒加载：滚动到可见区域才设置 src
  initGalleryLazyLoad();
  renderGalleryThumbstrip();
}

// v4.11.0: 单图查看器底部缩略图条（快速跳转 + 当前位置高亮）
function renderGalleryThumbstrip() {
  const ts = document.getElementById('gv_thumbstrip');
  if (!ts) return;
  const filtered = _galleryFiltered || [];
  ts.innerHTML = '';
  filtered.slice(0, 120).forEach((img, idx) => {
    const t = document.createElement('div');
    t.className = 'gv-thumb' + (idx === (_galleryCurrentIdx || 0) ? ' active' : '');
    t.onclick = () => scrollToGalleryIndex(idx);
    if (img.type === 'video') {
      const v = document.createElement('video');
      v.src = img.url || ''; v.muted = true; v.preload = 'metadata';
      t.appendChild(v);
    } else {
      const im = document.createElement('img');
      im.src = img.url || ''; im.loading = 'lazy'; im.alt = '';
      t.appendChild(im);
    }
    ts.appendChild(t);
  });
}

function updateGalleryToolbar() {
  const filtered = _galleryFiltered || [];
  const idx = Math.max(0, Math.min(_galleryCurrentIdx || 0, filtered.length - 1));
  _galleryCurrentIdx = idx;
  const img = filtered[idx];
  const nameEl = document.getElementById('gallery_info_name');
  const timeEl = document.getElementById('gallery_info_time');
  const counterEl = document.getElementById('gallery_counter');
  if (nameEl) nameEl.textContent = img ? (img.name || `文件 ${idx + 1}`) : '--';
  if (timeEl) timeEl.textContent = img ? formatRelativeTime(img.mtime) : '--';
  if (counterEl) counterEl.textContent = img ? `${idx + 1} / ${filtered.length}` : '0 / 0';
  document.querySelectorAll('#gv_thumbstrip .gv-thumb').forEach((t, i) => t.classList.toggle('active', i === (_galleryCurrentIdx || 0)));
  updateGalleryPromptUI();
}

// ===== v4.10.0: 画廊提示词面板 / 手机复制 =====
let _gppOpen = window.innerWidth >= 768, _gppDebounce = null; // v4.11.0: PC 左图右信息栏默认展开

function _curGalleryItem() {
  const f = _galleryFiltered || [];
  const i = Math.max(0, Math.min(_galleryCurrentIdx || 0, f.length - 1));
  return f[i] || null;
}
function _relFromItem(img) {
  try {
    const m = (img.url || '').match(/[?&]path=([^&]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  } catch (e) { return ''; }
}
function toggleGalleryPrompt() {
  // v4.11.1: 手机端该按钮=直接复制提示词（面板在手机隐藏，此前点了无反应）；PC=开合右侧栏
  if (window.innerWidth < 768) { copyGalleryPrompt(); return; }
  _gppOpen = !_gppOpen;
  const panel = document.getElementById('gallery_prompt_panel');
  if (panel) panel.style.display = _gppOpen ? 'flex' : 'none';
  if (_gppOpen) refreshGalleryPrompt();
}
async function refreshGalleryPrompt() {
  const img = _curGalleryItem();
  const body = document.getElementById('gpp_body');
  if (!body) return;
  if (!img) { body.textContent = '--'; return; }
  body.textContent = '加载中…';
  const rel = _relFromItem(img);
  if (!rel) { body.textContent = '未找到生成记录'; return; }
  const d = await apiFetch('/api/gallery/prompt?path=' + encodeURIComponent(rel), {}, true);
  if (!d || !d.ok) { body.textContent = '查询失败'; return; }
  body.textContent = d.matched === 'none' ? '未找到生成记录' : (d.prompt || '(空)');
}
async function copyGalleryPrompt() {
  const img = _curGalleryItem();
  if (!img) return;
  const rel = _relFromItem(img);
  const d = await apiFetch('/api/gallery/prompt?path=' + encodeURIComponent(rel), {}, true);
  const txt = (d && d.ok && d.prompt) || '';
  if (!txt) { toast('未找到生成记录', 'error'); return; }
  try {
    await navigator.clipboard.writeText(txt);
    toast('✅ 提示词已复制');
  } catch (e) {
    // 内置浏览器（X5 等）clipboard API 不可用时兜底
    const ta = document.createElement('textarea');
    ta.value = txt; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.focus(); ta.select();
    try { document.execCommand('copy'); toast('✅ 提示词已复制'); }
    catch (e2) { toast('复制失败，请长按手动复制', 'error'); }
    ta.remove();
  }
}
function updateGalleryPromptUI() {
  const panel = document.getElementById('gallery_prompt_panel');
  if (!panel) return;
  if (window.innerWidth < 768) { panel.style.display = 'none'; return; } // 手机无面板
  panel.style.display = _gppOpen ? 'flex' : 'none';
  if (_gppOpen) {
    clearTimeout(_gppDebounce);
    _gppDebounce = setTimeout(refreshGalleryPrompt, 250);
  }
}
// ===== v4.10.0 end =====

document.addEventListener('keydown', (e) => {
  const dd = document.getElementById('gallery_dropdown');
  if (!dd || !dd.classList.contains('show') || !dd.classList.contains('view-single')) return;
  const tag = (e.target && e.target.tagName) || '';
  if (tag === 'INPUT' || tag === 'TEXTAREA') return;
  if (e.key === 'ArrowLeft') { galleryPrev(); e.preventDefault(); }
  else if (e.key === 'ArrowRight') { galleryNext(); e.preventDefault(); }
  else if (e.key === 'Escape') { closeGalleryDropdown(); }
});

function sv_scrollNearEnd(el, margin) {
  const horiz = el.scrollWidth > el.clientWidth + 4; // 单图=横向轮播，多图=纵向瀑布流
  if (horiz) return el.scrollLeft + el.clientWidth >= el.scrollWidth - margin;
  return el.scrollTop + el.clientHeight >= el.scrollHeight - margin;
}

let _galleryScrollInit = false;
let _galleryScrollSilent = false;
let _galleryObserver = null;
let _galleryObserveFn = null;

// 图片懒加载：滚动到可见区域才设置 src（避免一次性拉取所有图片）
let _galleryLazyObserver = null;

function initGalleryLazyLoad() {
  const scrollView = document.getElementById('gallery_scroll_view');
  if (!scrollView) return;
  if (_galleryLazyObserver) _galleryLazyObserver.disconnect();
  _galleryLazyObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      const slide = entry.target;
      slide.querySelectorAll('img[data-src]').forEach((img) => {
        const src = img.getAttribute('data-src');
        if (src) {
          // 走缓存：命中 Cache API 直接显示，未命中 fetch 后写缓存
          setImgWithCache(img, src);
          img.removeAttribute('data-src');
        }
      });
      _galleryLazyObserver.unobserve(slide);
    });
  }, { root: scrollView, rootMargin: '300px 0px' }); // 提前 300px 预加载
  document.querySelectorAll('.gallery-slide').forEach((s) => _galleryLazyObserver.observe(s));
}

function initGalleryScroll() {
  if (_galleryScrollInit) return;
  _galleryScrollInit = true;
  const scrollView = document.getElementById('gallery_scroll_view');
  if (!scrollView) return;
  // 使用 IntersectionObserver 替代 getBoundingClientRect 循环，
  // 避免布局抖动 (layout thrashing) 导致的滚动卡顿
  const observer = new IntersectionObserver((entries) => {
    if (_galleryScrollSilent) return;
    // 找到可见比例最大的 slide
    let bestEntry = null;
    for (const entry of entries) {
      if (!bestEntry || entry.intersectionRatio > bestEntry.intersectionRatio) {
        bestEntry = entry;
      }
    }
    if (!bestEntry) return;
    const slides = document.querySelectorAll('.gallery-slide');
    const idx = Array.prototype.indexOf.call(slides, bestEntry.target);
    if (idx >= 0 && _galleryCurrentIdx !== idx) {
      _galleryCurrentIdx = idx;
      updateGalleryToolbar();
    }
  }, {
    root: scrollView,
    threshold: [0, 0.25, 0.5, 0.75, 1]
  });
  // 观察所有现有 slide，并在新 slide 渲染时重新连接
  const observeSlides = () => {
    document.querySelectorAll('.gallery-slide').forEach(slide => observer.observe(slide));
  };
  observeSlides();
  // 新 slide 渲染后自动连接（通过 scroll 事件轻触触发检查）
  scrollView.addEventListener('scroll', observeSlides, { passive: true, once: true });
  // v4.12.0: 无限滚动——距离最后一张真实 slide 不足 400px 拉下一页
  //（不能用 scrollWidth 判端：分页哨兵自身的占位会把端点顶远，永远差一页）
  scrollView.addEventListener('scroll', () => {
    const sent = document.getElementById('gallery_more_sentinel');
    if (!sent || _galleryMoreLoading || !_galleryHasMore) return;
    const sr = scrollView.getBoundingClientRect();
    const tr = sent.getBoundingClientRect();
    const horiz = sv_scrollNearEnd(sv, 0) ? false : (sv.scrollWidth > sv.clientWidth + 4);
    if (horiz) { if (tr.left - sr.right < 400) loadGalleryMore(); }
    else { if (tr.top - sr.bottom < 400) loadGalleryMore(); }
  }, { passive: true });
  _galleryObserveFn = observeSlides;
  _galleryObserver = observer;
}

function scrollToGalleryIndex(idx) {
  const scrollView = document.getElementById('gallery_scroll_view');
  const slides = document.querySelectorAll('.gallery-slide');
  if (!scrollView || !slides[idx]) return;
  _galleryScrollSilent = true;
  slides[idx].scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
  setTimeout(() => { _galleryScrollSilent = false; }, 350);
  _galleryCurrentIdx = idx;
  updateGalleryToolbar();
}

function galleryPrev() {
  if (!_galleryFiltered || _galleryCurrentIdx <= 0) return;
  scrollToGalleryIndex(_galleryCurrentIdx - 1);
}

function galleryNext() {
  if (!_galleryFiltered || _galleryCurrentIdx >= _galleryFiltered.length - 1) return;
  scrollToGalleryIndex(_galleryCurrentIdx + 1);
}

function galleryCurrentDelete() {
  const img = _galleryFiltered && _galleryFiltered[_galleryCurrentIdx];
  if (!img) return;
  if (!confirm('确定删除这张图片？')) return;
  gallerySingleDelete(_relFromItem(img) || img.name);
}

function openCurrentGalleryLightbox() {
  const img = _galleryFiltered && _galleryFiltered[_galleryCurrentIdx];
  if (!img) return;
  if (img.type === 'video') return;
  openLightbox(img.url, img.name || '');
}

function switchGalleryType(type) {
  _galleryType = type;
  document.querySelectorAll('.gallery-tab').forEach(t => t.classList.toggle('active', t.dataset.type === type));
  applyGalleryFilters();
}

function switchGalleryFilter(filter) {
  _galleryFilter = filter;
  document.querySelectorAll('.gallery-filter').forEach(f => f.classList.toggle('active', f.dataset.filter === filter));
  applyGalleryFilters();
}

// 批量删除模式 — 单图查看器适配版
function toggleGalleryBatchDel() {
  const btn = document.getElementById('gallery_batch_toggle');
  const bar = document.getElementById('gallery_batch_bar');
  const delBtn = document.getElementById('gallery_delete_btn');
  if (_batchMode) {
    exitBatchMode();
    return;
  }
  _batchMode = true;
  _selectedBatch.clear();
  if (btn) { btn.classList.add('active'); btn.innerHTML = '✓ 完成'; } // v4.11.2: 再点=退出，文字跟随
  if (delBtn) delBtn.style.display = 'none'; // v4.11.2: 批量模式隐藏单删按钮，防止误删当前张
  if (bar) bar.style.display = 'flex';
  updateBatchBar();
  updateBatchSelectUI();
  toast('浏览时点击图片选择要删除的项，然后点击「删除选中」', 'info');
}

function exitBatchMode() {
  if (!_batchMode && _selectedBatch.size === 0) return;
  _batchMode = false;
  const toggleBtn = document.getElementById('gallery_batch_toggle');
  if (toggleBtn) { toggleBtn.classList.remove('active'); toggleBtn.innerHTML = '批量'; }
  const delBtn = document.getElementById('gallery_delete_btn');
  if (delBtn) delBtn.style.display = '';
  _selectedBatch.clear();
  const btn = document.getElementById('gallery_batch_toggle');
  const bar = document.getElementById('gallery_batch_bar');
  if (btn) btn.classList.remove('active');
  if (bar) bar.style.display = 'none';
  updateBatchSelectUI();
}

function toggleCurrentBatchSelect(name) {
  if (!_batchMode) return;
  const _cur = _galleryFiltered && _galleryFiltered[_galleryCurrentIdx];
  const imgName = name || (_cur && (_relFromItem(_cur) || _cur.name));
  if (!imgName) return;
  if (_selectedBatch.has(imgName)) {
    _selectedBatch.delete(imgName);
  } else {
    _selectedBatch.add(imgName);
  }
  updateBatchBar();
  updateBatchSelectUI();
}

function updateBatchBar() {
  const el = document.getElementById('gallery_batch_count');
  const delBtn = document.getElementById('gallery_batch_del_btn');
  if (el) el.textContent = `已选 ${_selectedBatch.size} 项`;
  if (delBtn) delBtn.disabled = _selectedBatch.size === 0;
}

function updateBatchSelectUI() {
  const slides = document.querySelectorAll('.gallery-slide');
  slides.forEach(slide => {
    const name = slide.dataset.rel || slide.dataset.name; // v4.10.1: 键与 _selectedBatch 一致（rel）
    const overlay = slide.querySelector('.gallery-batch-overlay');
    const check = slide.querySelector('.gallery-batch-check');
    if (!overlay || !check) return;
    if (!_batchMode) {
      overlay.style.display = 'none';
      check.style.display = 'none';
      return;
    }
    overlay.style.display = 'block';
    check.style.display = 'flex';
    const selected = _selectedBatch.has(name);
    overlay.classList.toggle('selected', selected);
    check.classList.toggle('selected', selected);
    check.textContent = selected ? '✓' : '○';
  });
}

function toggleSelectAllGallery() {
  if (!_batchMode) return;
  const slides = document.querySelectorAll('.gallery-slide');
  const allSelected = Array.from(slides).every(slide => _selectedBatch.has(slide.dataset.name));
  if (allSelected) {
    _selectedBatch.clear();
  } else {
    slides.forEach(slide => {
      const name = slide.dataset.name;
      if (name) _selectedBatch.add(name);
    });
  }
  updateBatchBar();
  updateBatchSelectUI();
}

function executeBatchDelete() {
  if (!_batchMode || _selectedBatch.size === 0) {
    toast('请先选择要删除的图片', 'error');
    return;
  }
  if (!confirm(`确定要删除选中的 ${_selectedBatch.size} 项吗？`)) return;
  const pending = Array.from(_selectedBatch);
  let success = 0;
  let fail = 0;
  function deleteNext() {
    if (pending.length === 0) {
      if (success > 0) toast(`✅ 已删除 ${success} 项`);
      if (fail > 0) toast(`❌ ${fail} 项删除失败`, 'error');
      exitBatchMode();
      loadGalleryImages(true); // 删除后强制刷新，立即从画廊消失
      return;
    }
    const key = pending.shift();
    const body = key.includes('/') ? { path: key, name: key.split('/').pop() } : { name: key };
    apiFetch('/api/gallery/delete', { method: 'POST', body: JSON.stringify(body) }).then(r => {
      if (r && r.ok) { success++; _selectedBatch.delete(name); }
      else { fail++; }
      deleteNext();
    }).catch(() => { fail++; deleteNext(); });
  }
  deleteNext();
}
// End of Gallery functions

function gallerySingleDelete(key) {
  const isRel = key && key.includes('/');
  if (!confirm(`确定要删除此文件吗？\n${key}`)) return;
  const body = isRel ? { path: key, name: key.split('/').pop() } : { name: key };
  apiFetch('/api/gallery/delete', { method: 'POST', body: JSON.stringify(body) }).then(r => {
    if (r && r.ok) { toast('✅ 已删除'); loadGalleryImages(true); } // 删除后强制刷新，立即从画廊消失
    else { toast('❌ ' + (r?.error || '删除失败'), 'error'); }
  });
}

// 触摸设备隐藏画廊模式
document.addEventListener('touchstart', () => {
  document.body.classList.add('touch-device');
}, { once: true, passive: true });

// 为表单输入添加移动端适配
(function initMobileFormFix() {
  // iOS 上 textarea 的字体缩放修复
  const textareas = document.querySelectorAll('textarea');
  textareas.forEach((ta) => {
    ta.addEventListener('focus', () => {
      ta.style.fontSize = '16px';
    });
  });
})();

// ========================================================================
// 初始化
// ========================================================================
setTimeout(() => { testComfyUI(); loadWorkflows(); }, 500);
setTimeout(() => { loadParams(); }, 800);
// 页面加载即加载魔导书固定标签/随机池并渲染输入框下方标签栏
// （修复：旧版只在打开魔导书(showGrimoire→loadGrimoireData→updatePinnedBar)时才渲染，
//   导致刚进页面时输入框下方不显示标签）
setTimeout(() => { loadPromptPinsStandalone(); }, 1500);
// 刷新后恢复上次所在页面（生成台/工作流列表）：
// 在生成台刷新界面时，回到当前工作流的生成台而不是工作流列表
setTimeout(() => {
  try {
    const lastPage = localStorage.getItem('ui_last_page');
    if (lastPage === 'gen' && document.querySelector('.wf-item.active')) {
      switchPage('gen');
    }
  } catch (_) { /* noop */ }
}, 2200);
schedulePoll(2000);

// ========================================================================
// 魔导书功能 (Grimoire)
// ========================================================================
let _grimoireSources = [];
let _grimoireCurrentSource = '';
let _grimoireCurrentPage = 1;
let _grimoirePageSize = 50;
let _grimoireCurrentData = [];
let _grimoireCurrentTotal = 0;
let _grimoireSourceFilter = '';
let _grimoireDataFilter = '';
let _grimoireCategoryFilter = '';
let _grimoireAllCategories = [];
let _grimoireEditingIndex = -1;
let _grimoireEditMode = false;
let _grimoireSelectedIndices = new Set();
let _grimoirePins = {};
let _grimoireStars = {};
let _grimoireRandPool = [];
let _grimoireCollapsedDirs = {};

/** 数据源中文名映射 */
const GRIMOIRE_CN_NAMES = {
  'anima': 'Anima',
  'anima/artists': '画师',
  'anima/characters': '角色',
  'anima/clothing': '服装',
  'scene': '场景/环境',
  'scene/environment': '环境',
  'lighting': '光影/色调',
  'lighting/lighting': '光影',
  'shot': '镜头/构图',
  'shot/framing': '构图',
  'pose_action': '姿势/动作',
  'pose_action/Normal posture': '常规姿势',
  'pose_action/Sex positions': '特殊姿势',
  'custom': '自定义',
  'k2': 'K2 词库',
};

function toggleGrimoire() {
  const sheet = document.getElementById('grimoire_sheet');
  if (sheet && sheet.classList.contains('show')) {
    hideGrimoire();
  } else {
    showGrimoire();
  }
}

function showGrimoire() {
  const sheet = document.getElementById('grimoire_sheet');
  const backdrop = document.getElementById('grimoire_backdrop');
  if (!sheet || !backdrop) return;
  sheet.classList.add('show');
  backdrop.classList.add('show');
  document.body.style.overflow = 'hidden';
  loadGrimoireData();
  syncGrimoireK2UI(); // 按当前模式切换：K2 显示字段锁定面板，anima 显示标签浏览
  updateActiveNav('bn_grimoire');
  loadGrimoirePresets();
}

function hideGrimoire() {
  const sheet = document.getElementById('grimoire_sheet');
  const backdrop = document.getElementById('grimoire_backdrop');
  if (!sheet || !backdrop) return;
  sheet.classList.remove('show');
  sheet.classList.remove('drill-down');
  backdrop.classList.remove('show');
  document.body.style.overflow = '';
  // 关闭时退出编辑模式
  _grimoireEditMode = false;
}

/** 手机端钻取返回分类列表 */
function backToGrimoireSources() {
  const sheet = document.getElementById('grimoire_sheet');
  if (!sheet) return;
  sheet.classList.remove('drill-down');
  document.getElementById('grm_back_btn').style.display = 'none';
}

/** 独立加载固定标签/随机池并渲染输入框下方标签栏（页面加载时调用） */
async function loadPromptPinsStandalone() {
  try {
    const pinsData = await apiFetch('/api/grimoire/pins', {}, true);
    if (pinsData && pinsData.ok) {
      _grimoirePins = {};
      for (const [k, v] of Object.entries(pinsData.pins || {})) {
        _grimoirePins[k.replace(/\\/g, '/')] = v;
      }
    }
    const poolData = await apiFetch('/api/grimoire/rand-pool', {}, true);
    if (poolData && poolData.ok) {
      _grimoireRandPool = (poolData.pool || []).map(p => p.replace(/\\/g, '/'));
    } else {
      _grimoireRandPool = [];
    }
    renderPromptPins();
  } catch (e) {
    // 静默失败，不影响页面其它功能
  }
}

async function loadGrimoireData() {
  // Load status
  try {
    const status = await apiFetch('/api/grimoire/status', {}, true);
    if (status) updateGrimoireToggle(status.enabled);
  } catch (e) {}
  
  // Load models
  try {
    const modelData = await apiFetch('/api/grimoire/models', {}, true);
    if (modelData && modelData.models) {
      const sel = document.getElementById('grm_model_selector');
      const current = await apiFetch('/api/grimoire/model', {}, true);
      sel.innerHTML = '';
      for (const m of modelData.models) {
        const opt = document.createElement('option');
        opt.value = m.id;
        // 模型显示名：k2 → K2（其余保持原名）
        opt.textContent = (m.id === 'k2') ? 'K2' : (m.name || m.id);
        if (current && current.model === m.id) opt.selected = true;
        sel.appendChild(opt);
      }
    }
  } catch (e) {}
  
  // Load sources
  const srcData = await apiFetch('/api/grimoire/sources', {}, true);
  if (!srcData || !srcData.sources) {
    document.getElementById('grimoire_source_list').innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-sub2);font-size:12px">暂无数据源</div>';
    return;
  }
  _grimoireSources = srcData.sources;
  renderGrimoireSources();
  
  // 加载固定标签（统一路径格式）
  const pinsData = await apiFetch('/api/grimoire/pins', {}, true);
  if (pinsData && pinsData.ok) {
    _grimoirePins = {};
    for (const [k, v] of Object.entries(pinsData.pins || {})) {
      _grimoirePins[k.replace(/\\/g, '/')] = v;
    }
  }
  updatePinnedBar();
  
  // 加载收藏标签（从后端持久化）
  const starsData = await apiFetch('/api/grimoire/stars', {}, true);
  if (starsData && starsData.ok) {
    _grimoireStars = {};
    for (const [k, v] of Object.entries(starsData.stars || {})) {
      _grimoireStars[k] = new Set(v);
    }
  } else {
    _grimoireStars = {};
  }
  
  // 加载随机池（统一路径格式）
  const poolData = await apiFetch('/api/grimoire/rand-pool', {}, true);
  if (poolData && poolData.ok) {
    _grimoireRandPool = (poolData.pool || []).map(p => p.replace(/\\/g, '/'));
  } else {
    _grimoireRandPool = [];
  }
  // 重新渲染源列表以显示 🎲 状态
  renderGrimoireSources();
  updatePinnedBar();
  
  // 手机端不自动钻取：每次打开优先显示分类/子分类选择列表；PC 保持自动选第一个源
  if (window.innerWidth > 768) {
    if (!_grimoireCurrentSource && _grimoireSources.length > 0) {
      selectGrimoireSource(_grimoireSources[0].path);
    } else if (_grimoireCurrentSource) {
      selectGrimoireSource(_grimoireCurrentSource);
    }
  }
}

function updateGrimoireToggle(enabled) {
  const toggle = document.getElementById('grimoire_toggle');
  if (!toggle) return;
  toggle.classList.toggle('on', enabled);
  toggle.dataset.enabled = enabled ? '1' : '0';
}

async function toggleGrimoireSwitch() {
  const toggle = document.getElementById('grimoire_toggle');
  if (!toggle) return;
  const current = toggle.dataset.enabled === '1';
  const next = !current;
  const result = await apiFetch('/api/grimoire/status', {
    method: 'POST',
    body: JSON.stringify({ enabled: next })
  }, true);
  if (result && result.ok) {
    updateGrimoireToggle(result.enabled);
    toast(`✅ 魔导书已${result.enabled ? '启用' : '禁用'}`);
  }
}

async function changeGrimoireModel(model) {
  try {
    const res = await apiFetch('/api/grimoire/model', {
      method: 'POST',
      body: JSON.stringify({model: model})
    }, true);
    if (res && res.ok) {
      // 刷新源列表（顺序可能因模型不同而变化）
      const srcData = await apiFetch('/api/grimoire/sources', {}, true);
      if (srcData && srcData.sources) {
        _grimoireSources = srcData.sources;
        _k2ComposeMode = (model === 'k2') ? 'k2' : 'anima'; // 同步组句模式，NSFW 开关随模型切换显示
        // 双向同步：魔导书切模型也回写设置面板下拉框，保证两处视觉一致
        const k2Sel = document.getElementById('k2_compose_mode');
        if (k2Sel) k2Sel.value = _k2ComposeMode;
        renderGrimoireSources();
        syncGrimoireK2UI(); // 切换模型后立即同步魔导书 UI（K2 面板 / anima 浏览）
      }
    }
  } catch (e) {}
}

// =========================================================================
// 【K2 控制 · 前端】魔导书内 K2 字段锁定面板 + NSFW 开关
//   功能区边界：本注释 ↓ 到 applyK2LocksToEngine()（k2Compose 里调用）为止。
//   涉及 UI：魔导书 Bottom Sheet 内 #grimoire_k2_panel（搜 <!--【K2】-->
//   涉及函数：syncGrimoireK2UI / toggleK2Nsfw / 字段锁定全家（见下）
//   后端接口：GET/POST /api/k2-locks 、 POST /api/k2-nsfw（存 __k2_locks__/__k2_nsfw__）
//   跨模式切换统一入口 = syncGrimoireK2UI()：K2 显字段锁定+隐藏 anima 浏览，anima 反之。
// =========================================================================
// K2 模式下的魔导书 UI 同步：K2 模式显示专属控制面板（字段锁定+NSFW），隐藏 anima 标签浏览；
// anima 模式反之。统一入口，避免多处重复控制。
function syncGrimoireK2UI() {
  const isK2 = (_k2ComposeMode === 'k2');
  const k2Panel = document.getElementById('grimoire_k2_panel');
  if (k2Panel) k2Panel.style.display = isK2 ? 'block' : 'none';
  // anima 标签浏览区：K2 模式隐藏（K2 自带组句，无需标签源/随机池）
  const srcArea = document.querySelector('.grimoire-sources');
  const detailArea = document.querySelector('.grimoire-detail');
  if (srcArea) srcArea.style.display = isK2 ? 'none' : '';
  if (detailArea) detailArea.style.display = isK2 ? 'none' : '';
  // NSFW 按钮迁入 K2 面板：初始/切换时同步其 class 与文本（避免静态 HTML 残留未插值占位）
  const nsfwBtn = document.getElementById('grm_k2_nsfw_btn');
  const nsfwText = document.getElementById('grm_k2_nsfw_text');
  if (nsfwBtn) nsfwBtn.className = 'btn btn-xs ' + (window._k2NsfwOn ? 'btn-primary' : 'btn-secondary');
  if (nsfwText) nsfwText.textContent = window._k2NsfwOn ? 'NSFW开' : 'NSFW关';
  if (isK2) renderK2LocksPanel();
  // 同步模型选择器视觉（若魔导书开着）
  const sel = document.getElementById('grm_model_selector');
  if (sel) sel.value = isK2 ? 'k2' : 'anima';
}

// K2 NSFW 开关状态（window 全局，跨脚本共享：工具栏切换 + k2Compose 读取）
if (typeof window._k2NsfwOn === 'undefined') {
  try { window._k2NsfwOn = localStorage.getItem('k2_nsfw_on') === '1'; } catch (e) { window._k2NsfwOn = false; }
}
function toggleK2Nsfw() {
  window._k2NsfwOn = !window._k2NsfwOn;
  try { localStorage.setItem('k2_nsfw_on', window._k2NsfwOn ? '1' : '0'); } catch (e) {}
  const bt = document.getElementById('grm_k2_nsfw_text');
  if (bt) bt.textContent = window._k2NsfwOn ? 'NSFW开' : 'NSFW关';
  const bc = document.getElementById('grm_k2_nsfw_btn');
  if (bc) bc.className = 'btn btn-xs ' + (window._k2NsfwOn ? 'btn-primary' : 'btn-secondary');
  toast(window._k2NsfwOn ? '🔞 K2 随机已开启 NSFW（交合/裸露强化）' : 'K2 随机已关闭 NSFW，仅出 SFW 人像描述');
  // 同步到后端：QQ /随机图 组句遵循同一 NSFW 开关
  apiFetch('/api/k2-nsfw', { method: 'POST', body: JSON.stringify({ nsfw: window._k2NsfwOn }) }, true).catch(() => {});
}

// ★ Anima NSFW 开关（魔导书工具栏；存后端配置 anima_nsfw，供随机抽取过滤）
//   关闭（默认）= 过滤裸体/性行为/性器官/BDSM（内衣/泳装/透视保留，即"宽松档"）
//   开启 = 完全放开，不做任何过滤
if (typeof window._animaNsfwOn === 'undefined') {
  try { window._animaNsfwOn = localStorage.getItem('anima_nsfw_on') === '1'; } catch (e) { window._animaNsfwOn = false; }
}
function _syncAnimaNsfwUI() {
  const bt = document.getElementById('grm_anima_nsfw_text');
  if (bt) bt.textContent = window._animaNsfwOn ? 'NSFW开' : 'SFW';
  const bc = document.getElementById('grm_anima_nsfw_btn');
  if (bc) bc.className = 'btn btn-xs ' + (window._animaNsfwOn ? 'btn-primary' : 'btn-secondary');
}
function toggleAnimaNsfw() {
  window._animaNsfwOn = !window._animaNsfwOn;
  try { localStorage.setItem('anima_nsfw_on', window._animaNsfwOn ? '1' : '0'); } catch (e) {}
  _syncAnimaNsfwUI();
  toast(window._animaNsfwOn
    ? '🔞 Anima 随机已放开（不做过滤）'
    : 'Anima 随机已开启宽松过滤（跳过裸体/性行为，保留内衣泳装）');
  // 同步到后端配置
  apiFetch('/api/config', { method: 'POST', body: JSON.stringify({ anima_nsfw: window._animaNsfwOn }) }, true).catch(() => {});
}

// ===================== K2 字段锁定面板 =====================
// 状态：{ fieldId: { mode:'random'|'fixed'|'off', value:'' } }
let _k2Locks = {};
let _k2LocksLoaded = false;
let _k2SecCollapsed = {}; // 各 section 折叠态（序号→bool），render 重建后恢复

// 从引擎数据构建「分类 → 字段」结构；缓存供渲染使用
let _k2FieldMeta = null; // { sections:[{title, fields:[{id,label,opts:[{v,t}],text}]}] }
function _getK2FieldMeta() {
  if (_k2FieldMeta) return _k2FieldMeta;
  try {
    const mod = window.__k2Mod;
    if (!mod || !mod.D) return null;
    const D = mod.D;
    const sections = [];
    (D.SECTIONS || []).forEach(sec => {
      const fields = (sec.fields || []).map(f => {
        let opts = [], rawOpts = [];
        if (f.list && D.OPT && D.OPT[f.list]) rawOpts = (D.OPT[f.list] || []).map(o => (typeof o === 'string' ? { v: o, t: o } : { v: o.v, t: o.t }));
        // 必选判定：选项池若含「不启用」→ 该字段可排除(可 off)；不含 → 必选，前端不提供「不选」。
        const canOff = rawOpts.some(o => o.v === '不启用');
        // 过滤掉「不启用」占位项（不选模式用单独按钮表达）
        opts = rawOpts.filter(o => o.v !== '不启用');
        return { id: f.id, label: f.label || f.id, opts, text: !!f.text, canOff };
      }).filter(f => f.opts.length || f.text);
      if (fields.length) sections.push({ title: sec.title || sec.name || '其他', fields });
    });
    _k2FieldMeta = { sections };
    return _k2FieldMeta;
  } catch (e) { console.warn('K2 field meta build failed', e); return null; }
}

async function renderK2LocksPanel() {
  if (_k2ComposeMode !== 'k2') return;
  const panel = document.getElementById('k2_locks_panel');
  if (!panel) return;
  // 确保引擎模块已加载（buildK2Engine 全局），以便读取 D.SECTIONS/OPT
  if (!window.__k2Mod) {
    try {
      const res = await fetch('/api/k2gen/data').then(r => r.json());
      if (res && res.ok) window.__k2Mod = buildK2Engine(res.source);
    } catch (e) {}
  }
  const meta = _getK2FieldMeta();
  if (!meta) { panel.innerHTML = '<div class="style-empty">K2 引擎未加载，无法列出字段</div>'; return; }
  // 初始化本地存储
  if (!_k2LocksLoaded) {
    try { _k2Locks = JSON.parse(localStorage.getItem('k2_locks') || '{}') || {}; } catch (e) { _k2Locks = {}; }
    _k2LocksLoaded = true;
  }
  let html = '';
  meta.sections.forEach((sec, si) => {
    const flds = sec.fields.map(f => {
      // 必选字段(canOff=false)不可排除：若历史状态残留 off 则强制回 random
      let st = _k2Locks[f.id] || { mode: 'random', value: '' };
      if (!f.canOff && st.mode === 'off') st = { mode: 'random', value: '' };
      const canOff = f.canOff;
      const modes = canOff ? ['random', 'fixed', 'off'] : ['random', 'fixed'];
      const modeLabels = { random: '随机', fixed: '固定', off: '不选' };
      const fidJs = escJsStr(f.id); // onclick JS 字符串参数：escJsStr 转义单引号/反斜杠，防破坏 JS 字符串
      const modeBtns = modes.map(m => `<button class="${st.mode === m ? 'active' : ''}" data-f="${esc(f.id)}" data-m="${m}" onclick="setK2LockMode('${fidJs}','${m}')">${modeLabels[m]}</button>`).join('');
      // 固定值控件：有 opts 用下拉，text 字段允许手输
      let valCtrl = '';
      if (st.mode === 'fixed') {
        if (f.opts.length) {
          const optsHtml = ['<option value="">— 选值 —</option>'].concat(
            // 宽松比较：st.value 来自 select 必为 string，option 值可能为 number，用 String() 归一避免 reload 后选中态丢失
            f.opts.map(o => `<option value="${esc(o.v)}"${String(o.v) === String(st.value) ? ' selected' : ''}>${esc(o.t || o.v)}</option>`)
          ).join('');
          // 移动端用原生 <select>（系统 picker），避免 <input list> 在 WebView 里被手势/搜索吞掉
          valCtrl = `<select class="k2fld-val show" onchange="setK2LockValue('${fidJs}', this.value)" onclick="event.stopPropagation()">${optsHtml}</select>`;
        } else if (f.text) {
          valCtrl = `<input class="k2fld-val show" value="${esc(st.value)}" onchange="setK2LockValue('${fidJs}', this.value)" onclick="event.stopPropagation()" placeholder="手输值（如 18岁）">`;
        } else {
          valCtrl = `<input class="k2fld-val show" value="${esc(st.value)}" onchange="setK2LockValue('${fidJs}', this.value)" onclick="event.stopPropagation()" placeholder="固定值">`;
        }
      }
      return `<div class="k2fld">
        <span class="k2fld-label" title="${esc(f.id)}">${esc(f.label)}</span>
        <span class="k2fld-modes">${modeBtns}</span>
        ${valCtrl}
      </div>`;
    }).join('');
    const secCollapsed = _k2SecCollapsed[si] === true ? ' collapsed' : '';
    html += `<div class="k2sec">
      <div class="k2sec-head" onclick="toggleK2Sec(${si})"><svg class="icon-xs" aria-hidden="true"><use href="#icon-folder-closed"/></svg> ${esc(sec.title)}<span class="k2sec-arrow${secCollapsed}" id="k2sec_arr_${si}">▼</span></div>
      <div class="k2sec-body${secCollapsed}" id="k2sec_body_${si}">${flds}</div>
    </div>`;
  });
  html += `<div class="k2-locks-tip">提示：固定「年龄」选 18岁少女（或手输 18岁），其余保持「随机」即可每次换其他特征。设置已自动保存，QQ /随机图 同样遵守。</div>`;
  panel.innerHTML = html;
  updateK2LocksBadge();
  // 同步「收起/展开全部」按钮文字：任一 section 展开则显示「收起全部」，全收起则显示「展开全部」
  const ctxt = document.getElementById('grm_k2_collapse_text');
  if (ctxt) {
    let allCollapsed = true;
    for (let si = 0; si < meta.sections.length; si++) { if (!_k2SecCollapsed[si]) { allCollapsed = false; break; } }
    ctxt.textContent = allCollapsed ? '展开全部' : '收起全部';
  }
  if (typeof updateK2AllToggleBtn === 'function') updateK2AllToggleBtn();
}

function _k2FieldCanOff(fid) {
  const m = _getK2FieldMeta();
  if (!m) return true;
  for (const s of m.sections) { const f = s.fields.find(x => x.id === fid); if (f) return f.canOff; }
  return true;
}
function setK2LockMode(fid, mode) {
  // 必选字段不允许「不选(off)」→ 强制回到 random
  if (mode === 'off' && !_k2FieldCanOff(fid)) mode = 'random';
  if (!_k2Locks[fid]) _k2Locks[fid] = { mode: 'random', value: '' };
  _k2Locks[fid].mode = mode;
  // 非 fixed 时清空 value，避免 off→fixed 或 random→fixed 残留旧固定值
  if (mode !== 'fixed') _k2Locks[fid].value = '';
  persistK2Locks();
  renderK2LocksPanel();
}
function setK2LockValue(fid, val) {
  if (!_k2Locks[fid]) _k2Locks[fid] = { mode: 'fixed', value: '' };
  _k2Locks[fid].mode = 'fixed';
  _k2Locks[fid].value = val;
  persistK2Locks();
  renderK2LocksPanel();
}
function toggleK2Sec(si) {
  const body = document.getElementById('k2sec_body_' + si);
  const arr = document.getElementById('k2sec_arr_' + si);
  if (!body) return;
  body.classList.toggle('collapsed');
  if (arr) arr.classList.toggle('collapsed');
  // 记录折叠态，供 renderK2LocksPanel 重建后恢复（避免每次切 mode/选值丢折叠）
  _k2SecCollapsed[si] = body.classList.contains('collapsed');
}
function persistK2Locks() {
  try { localStorage.setItem('k2_locks', JSON.stringify(_k2Locks)); } catch (e) {}
  // 同步到后端（QQ /随机图 遵守）
  apiFetch('/api/k2-locks', { method: 'POST', body: JSON.stringify({ locks: _k2Locks }) }, true).catch(() => {});
}
function updateK2LocksBadge() {
  const badge = document.getElementById('grimoire_k2_badge');
  if (!badge) return;
  const nLock = Object.values(_k2Locks).filter(s => s.mode === 'fixed').length;
  const nOff = Object.values(_k2Locks).filter(s => s.mode === 'off').length;
  badge.textContent = (nLock || nOff) ? `固定${nLock}/不选${nOff}` : '随机';
}

// 在 k2Compose 生成前按锁定设定 configure 引擎：fixed→setVal+lockField；off→setVal('不启用')
function applyK2LocksToEngine(eng) {
  if (!eng) return;
  const locks = _k2Locks || {};
  Object.keys(locks).forEach(fid => {
    const st = locks[fid];
    if (!st) return;
    try {
      if (st.mode === 'fixed') {
        const v = (st.value || '').trim();
        if (v) { eng.set(fid, v); eng.lockField(fid, true); }
      } else if (st.mode === 'off') {
        // off=排除：设「不启用」后必须 lockField，否则 generate({randomize:true}) 会把它当
        // 未锁字段重新随机覆盖（引擎端实测：无 lock 时排除失效）。与 fixed 及后端 --set 语义一致。
        eng.set(fid, '不启用');
        eng.lockField(fid, true);
      }
    } catch (e) {}
  });
}

// ===================== 【K2】批量操作 + 预设 =====================
function _k2AllFieldIds(){
  const m = _getK2FieldMeta();
  if (!m) return [];
  return m.sections.reduce((acc, s) => acc.concat(s.fields.map(f => f.id)), []);
}
// 双功能单按钮（同 anima）：当前若所有「可选(可排除)」字段都在随机(无锁定/排除) → 显示并执行「全部排除」；
// 否则 → 显示并执行「全选随机」。必选字段(canOff=false)不参与「排除」，始终走随机。
function _k2CanOffMap() {
  const m = _getK2FieldMeta();
  const map = {};
  if (m) m.sections.forEach(s => s.fields.forEach(f => { if (f.canOff) map[f.id] = true; }));
  return map;
}
function _k2AllRandomNow() {
  if (!_k2Locks) return true;
  return !Object.values(_k2Locks).some(s => s && (s.mode === 'fixed' || s.mode === 'off'));
}
function toggleAllK2Locks() {
  if (!_k2LocksLoaded) {
    try { _k2Locks = JSON.parse(localStorage.getItem('k2_locks') || '{}') || {}; } catch (e) { _k2Locks = {}; }
    _k2LocksLoaded = true;
  }
  const ids = _k2AllFieldIds();
  if (!ids.length) { toast('K2 字段未加载', 'error'); return; }
  const goRandom = !_k2AllRandomNow(); // 当前非全随机 → 这次全选随机；当前已全随机 → 反选排除
  const canOffMap = _k2CanOffMap();
  ids.forEach(id => {
    if (goRandom) delete _k2Locks[id];
    else if (canOffMap[id]) _k2Locks[id] = { mode: 'off', value: '' };
    // 必选字段：不排除，保持随机
  });
  persistK2Locks();
  renderK2LocksPanel();
  updateK2AllToggleBtn();
  toast(goRandom ? '🎲 已全部设为随机' : '🚫 已反选：全部设为排除（必选分类除外）');
}
function updateK2AllToggleBtn() {
  const txt = document.getElementById('grm_k2_all_text');
  const btn = document.getElementById('grm_k2_all_toggle');
  if (!txt && !btn) return;
  const allRandom = _k2AllRandomNow();
  if (txt) txt.textContent = allRandom ? '全部排除' : '全选随机';
  if (btn) { btn.classList.toggle('btn-primary', !allRandom); btn.classList.toggle('btn-secondary', allRandom); }
}
// 展开/收起全部 section
function toggleAllK2SecCollapse() {
  const m = _getK2FieldMeta();
  if (!m) return;
  const n = m.sections.length;
  // 判断当前是否"全收起" → 切换
  let allCollapsed = true;
  for (let si = 0; si < n; si++) { if (!_k2SecCollapsed[si]) { allCollapsed = false; break; } }
  const collapse = !allCollapsed; // 当前非全收起则去收起；已全收起则全展开
  for (let si = 0; si < n; si++) {
    const body = document.getElementById('k2sec_body_' + si);
    const arr = document.getElementById('k2sec_arr_' + si);
    if (body) body.classList.toggle('collapsed', collapse);
    if (arr) arr.classList.toggle('collapsed', collapse);
    _k2SecCollapsed[si] = collapse;
  }
  const txt = document.getElementById('grm_k2_collapse_text');
  if (txt) txt.textContent = collapse ? '展开全部' : '收起全部';
}
// ---- K2 预设：存 localStorage {name: {locks, nsfw}} ----
function _k2Presets(){ try { return JSON.parse(localStorage.getItem('k2_locks_presets') || '{}') || {}; } catch(e){ return {}; } }
function _saveK2Presets(obj){ try { localStorage.setItem('k2_locks_presets', JSON.stringify(obj)); } catch(e){} }
function showK2PresetDropdown() {
  const menu = document.getElementById('grm_k2_preset_menu');
  if (!menu) return;
  const presets = _k2Presets();
  const names = Object.keys(presets);
  let items = names.length
    ? names.map(n => `<div onclick="applyK2Preset('${escJsStr(n)}')" style="padding:5px 10px;font-size:11px;cursor:pointer;display:flex;align-items:center;gap:6px" onmouseover="this.style.background='var(--card-hover)'" onmouseout="this.style.background=''">📌 ${esc(n)}</div>`).join('')
    : '<div style="padding:6px 10px;font-size:11px;color:var(--text-sub2)">暂无预设</div>';
  items += '<div style="border-top:1px solid var(--card-border);margin:3px 0"></div>';
  items += `<div onclick="saveK2Preset()" style="padding:5px 10px;font-size:11px;cursor:pointer" onmouseover="this.style.background='var(--card-hover)'" onmouseout="this.style.background=''">＋ 保存当前为预设</div>`;
  menu.innerHTML = items;
  menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
}
function saveK2Preset() {
  const name = (prompt('预设名称：') || '').trim();
  if (!name) return;
  const presets = _k2Presets();
  presets[name] = { locks: JSON.parse(JSON.stringify(_k2Locks || {})), nsfw: !!window._k2NsfwOn };
  _saveK2Presets(presets);
  setK2PresetLabel(name);
  const menu = document.getElementById('grm_k2_preset_menu');
  if (menu) menu.style.display = 'none';
  toast(`✅ K2 预设「${name}」已保存`);
}
function applyK2Preset(name) {
  const presets = _k2Presets();
  const p = presets[name];
  if (!p) return;
  _k2Locks = JSON.parse(JSON.stringify(p.locks || {}));
  _k2LocksLoaded = true;
  try { localStorage.setItem('k2_locks', JSON.stringify(_k2Locks)); } catch (e) {}
  // NSFW 一并应用
  if (p.nsfw !== undefined && !!p.nsfw !== !!window._k2NsfwOn) {
    window._k2NsfwOn = !!p.nsfw;
    try { localStorage.setItem('k2_nsfw_on', window._k2NsfwOn ? '1' : '0'); } catch (e) {}
    const bt = document.getElementById('grm_k2_nsfw_text');
    if (bt) bt.textContent = window._k2NsfwOn ? 'NSFW开' : 'NSFW关';
    const bc = document.getElementById('grm_k2_nsfw_btn');
    if (bc) bc.className = 'btn btn-xs ' + (window._k2NsfwOn ? 'btn-primary' : 'btn-secondary');
    apiFetch('/api/k2-nsfw', { method: 'POST', body: JSON.stringify({ nsfw: window._k2NsfwOn }) }, true).catch(() => {});
  }
  persistK2Locks();
  renderK2LocksPanel();
  setK2PresetLabel(name);
  const menu = document.getElementById('grm_k2_preset_menu');
  if (menu) menu.style.display = 'none';
  toast(`📌 已应用 K2 预设「${name}」`);
}
function deleteK2Preset() {
  const label = document.getElementById('grm_k2_preset_label');
  const name = label ? label.textContent : '';
  if (!name || name === '预设') { toast('请先选择一个预设', 'error'); return; }
  if (!confirm(`确定删除预设「${name}」？`)) return;
  const presets = _k2Presets();
  delete presets[name];
  _saveK2Presets(presets);
  setK2PresetLabel('');
  const menu = document.getElementById('grm_k2_preset_menu');
  if (menu) menu.style.display = 'none';
  toast(`已删除 K2 预设「${name}」`);
}
function setK2PresetLabel(name) {
  const lb = document.getElementById('grm_k2_preset_label');
  if (lb) lb.textContent = name || '预设';
}


function renderGrimoireSources() {
  const list = document.getElementById('grimoire_source_list');
  if (!list) return;
  
  let sources = _grimoireSources;
  if (_grimoireSourceFilter) {
    const q = _grimoireSourceFilter.toLowerCase();
    sources = sources.filter(s => (s.name || '').toLowerCase().includes(q) || (s.dir || '').toLowerCase().includes(q));
  }
  
  if (!sources.length) {
    list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-sub2);font-size:12px">无匹配数据源</div>';
    return;
  }
  
  // 按目录分组
  const groups = {};
  sources.forEach(s => {
    const dir = s.dir || '';
    if (!groups[dir]) groups[dir] = { dir, items: [], total: 0 };
    groups[dir].items.push(s);
    groups[dir].total += s.count || 0;
  });
  
  // 目录排序：按后端返回的顺序（Anima section 顺序），不重新按拼音排
  const dirOrder = Object.keys(groups).sort((a, b) => {
    // 找到每个目录在 sources 数组中最先出现的位置
    const idxA = sources.findIndex(s => (s.dir || '') === a);
    const idxB = sources.findIndex(s => (s.dir || '') === b);
    return (idxA >= 0 ? idxA : 999) - (idxB >= 0 ? idxB : 999);
  });
  
  function cnDir(d) {
    return GRIMOIRE_CN_NAMES[d] || d;
  }
  function cnItem(s) {
    const key = s.dir ? `${s.dir}/${s.name}` : s.name;
    return GRIMOIRE_CN_NAMES[key] || s.name;
  }
  
  let html = '<div class="grm-toolbar">' +
    `<button class="btn btn-xs btn-secondary" id="grm_toggle_all_btn" onclick="toggleAllGrimoireGroups()" title="展开/收起全部"><svg class="icon-xs" aria-hidden="true"><use href="#icon-folder-open"/></svg> <span id="grm_toggle_all_text">展开全部</span></button>` +
    `<button class="btn btn-xs btn-secondary" id="grm_toggle_rand_btn" onclick="toggleAllRandom()" title="全选/取消随机池" style="margin-left:4px"><svg class="icon-xs" aria-hidden="true"><use href="#icon-shuffle"/></svg> <span id="grm_toggle_rand_text">全选随机</span></button>` +
    `<span style="margin-left:8px;display:inline-flex;gap:4px;align-items:center;position:relative">` +
      `<button id="grm_preset_btn" onclick="showPresetDropdown()" style="font-size:10px;padding:3px 8px;border-radius:var(--radius-xs);border:1px solid var(--card-border);background:var(--input-bg);color:var(--text);cursor:pointer;display:inline-flex;align-items:center;gap:4px;white-space:nowrap;font-family:inherit"><svg class="icon-xs" aria-hidden="true"><use href="#icon-bookmark"/></svg> <span id="grm_preset_label">预设</span></button>` +
      `<button class="btn btn-xs btn-secondary" onclick="deleteGrimoirePreset()" title="删除当前选中的预设" style="font-size:9px;padding:0 4px;min-height:18px;border-radius:var(--radius-xs);background:transparent;border:1px solid var(--card-border);color:var(--text-sub);cursor:pointer">✕</button>` +
      `<div id="grm_preset_menu" style="display:none;position:absolute;top:100%;left:0;min-width:140px;background:var(--card-bg);border:1px solid var(--card-border);border-radius:var(--radius-xs);box-shadow:0 4px 12px rgba(0,0,0,0.15);z-index:999;padding:4px 0;margin-top:4px"></div>` +
    `</span>` +
    '</div>';
  html += '<div class="source-group-list" ondragover="event.preventDefault()">';
  dirOrder.forEach(dir => {
    const g = groups[dir];
    const displayDir = cnDir(dir || '其他');
    const isEditable = dir && dir !== 'anima';
    const editBtns = _grimoireEditMode && isEditable
      ? `<button class="group-action-btn" onclick="event.stopPropagation();renameGrimoireCategory('${esc(dir)}','${esc(displayDir)}')" title="重命名分类"><svg class="icon-sm" aria-hidden="true"><use href="#icon-edit"/></svg></button>` +
        `<button class="group-action-btn del" onclick="event.stopPropagation();deleteGrimoireCategory('${esc(dir)}','${esc(displayDir)}')" title="删除分类">🗑️</button>`
      : '';
    const addSubBtn = '';
    const isCollapsed = _grimoireCollapsedDirs ? _grimoireCollapsedDirs[dir] === true : false;
    const arrowClass = isCollapsed ? 'collapsed' : '';
    
    html += `<div class="source-group" draggable="true" data-dir="${esc(dir)}" ondragstart="onDirDragStart(event)" ondragover="onDirDragOver(event)" ondrop="onDirDrop(event)" ondragend="onDirDragEnd(event)">`;
    html += `<div class="source-group-header" onclick="toggleGrimoireGroup('${esc(dir)}')">` +
      `<span class="group-arrow ${arrowClass}">▼</span>` +
      `<span class="group-name">${esc(displayDir)}</span>` +
      `<span class="group-count">${g.total}</span>` +
      `<span class="group-actions">${addSubBtn}${editBtns}</span>` +
      `</div>`;
    
    if (!isCollapsed) {
      html += `<div class="source-group-children">`;
      g.items.forEach(s => {
        const active = s.path === _grimoireCurrentSource ? ' active' : '';
        const displayName = cnItem(s);
        const isDeletable = !(s.path || '').startsWith('anima');
        // 路径统一用正斜杠，避免反斜杠被 JS 字符串吃掉
        const safePath = (s.path || '').replace(/\\/g, '/');
        // 池子比较也用正斜杠，与后端存储格式一致
        const poolPath = safePath;
        const inPool = _grimoireRandPool && _grimoireRandPool.includes(poolPath);
        const hasPin = _grimoirePins && _grimoirePins[safePath] && (
          Array.isArray(_grimoirePins[safePath]) ? _grimoirePins[safePath].length > 0 : true
        );
        const pinClass = hasPin ? ' has-pin' : '';
        const poolBtn = !_grimoireEditMode
          ? `<button class="source-pool-btn${inPool ? ' active' : ''}" onclick="event.stopPropagation();toggleRandPool('${esc(safePath)}')" title="${inPool ? '移出随机池' : '加入随机池'}"><svg class="icon-sm" aria-hidden="true"><use href="#icon-shuffle"/></svg></button>`
          : '';
        const delBtn = _grimoireEditMode && isDeletable
          ? `<button class="source-del-btn" onclick="event.stopPropagation();renameGrimoireSource('${esc(safePath)}','${esc(displayName)}')" title="重命名"><svg class="icon-sm" aria-hidden="true"><use href="#icon-edit"/></svg></button>` +
            `<button class="source-del-btn" onclick="event.stopPropagation();deleteGrimoireSource('${esc(safePath)}','${esc(displayName)}')" title="删除">🗑️</button>`
          : '';
        html += `<div class="source-item${active}${pinClass}" draggable="true"
              ondragstart="onSourceDragStart(event)"
              ondragover="onSourceDragOver(event)"
              ondrop="onSourceDrop(event)"
              ondragend="onSourceDragEnd(event)"
              data-path="${esc(s.path)}" onclick="selectGrimoireSource(this.dataset.path)">` +
          `<span class="drag-handle">⠿</span>` +
          `<span class="source-name">${esc(displayName)}</span>` +
          `<span class="source-count">${s.count || 0}</span>${poolBtn}${delBtn}` +
          `</div>`;
      });
      html += `</div>`;
    }
    html += `</div>`;
  });
  html += '</div>';
  list.innerHTML = html;
  updateRandomToggleBtn();
  loadGrimoirePresets();
  // 点击外部关闭预设菜单
  setTimeout(() => {
    document.addEventListener('click', function _closePreset(e) {
      if (!e.target.closest('#grm_preset_btn') && !e.target.closest('#grm_preset_menu')) {
        const m = document.getElementById('grm_preset_menu');
        if (m) m.style.display = 'none';
        document.removeEventListener('click', _closePreset);
      }
    });
  }, 0);
}

// ── 魔导书子分类拖拽排序 ──
let _srcDragEl = null;

function onSourceDragStart(e) {
  _srcDragEl = e.target.closest('.source-item');
  if (!_srcDragEl) return;
  _srcDragEl.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', _srcDragEl.dataset.path || '');
}

function onSourceDragOver(e) {
  e.preventDefault();
  const target = e.target.closest('.source-item');
  if (!target || target === _srcDragEl) return;
  document.querySelectorAll('.source-item').forEach((el) => el.classList.remove('drag-over'));
  target.classList.add('drag-over');
}

async function onSourceDrop(e) {
  e.preventDefault();
  const target = e.target.closest('.source-item');
  if (!target || target === _srcDragEl) return;
  // 获取当前同一总分类下的所有 source-item
  const parent = _srcDragEl.closest('.source-group-children');
  if (!parent) return;
  const items = Array.from(parent.querySelectorAll('.source-item'));
  const fromIdx = items.indexOf(_srcDragEl);
  const toIdx = items.indexOf(target);
  if (fromIdx < 0 || toIdx < 0) return;
  // 收集被拖拽的 path 和目标 path
  const fromPath = _srcDragEl.dataset.path;
  const toPath = target.dataset.path;
  // 重排数组
  items.splice(fromIdx, 1);
  items.splice(toIdx < fromIdx ? toIdx : toIdx, 0, items.splice(fromIdx > toIdx ? fromIdx - 1 : fromIdx, 1)[0]);
  // 获取目录名
  const groupNameEl = _srcDragEl.closest('.source-group')?.querySelector('.source-group-header .group-name');
  const dir = groupNameEl?.textContent?.trim() || '';
  // 收集新顺序
  const newOrder = items.map((el) => el.dataset.path).filter(Boolean);
  // 保存到后端
  const result = await apiFetch('/api/grimoire/source-order', {
    method: 'POST',
    body: JSON.stringify({ order: { [dir]: newOrder } })
  }, true);
  if (result?.ok) {
    // 重新加载以应用后端排序
    loadGrimoireData();
  }
}

function onSourceDragEnd(e) {
  document.querySelectorAll('.source-item').forEach((el) => el.classList.remove('dragging', 'drag-over'));
  _srcDragEl = null;
}

/* ─── 目录组拖拽排序 ─── */
let _dirDragEl = null;

function onDirDragStart(e) {
  _dirDragEl = e.target.closest('.source-group');
  if (!_dirDragEl) return;
  _dirDragEl.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  const nameEl = _dirDragEl.querySelector('.group-name');
  e.dataTransfer.setData('text/plain', nameEl?.textContent?.trim() || '');
}

function onDirDragOver(e) {
  e.preventDefault();
  const target = e.target.closest('.source-group');
  if (!target || target === _dirDragEl) return;
  document.querySelectorAll('.source-group').forEach(el => el.classList.remove('drag-over'));
  target.classList.add('drag-over');
}

async function onDirDrop(e) {
  e.preventDefault();
  const target = e.target.closest('.source-group');
  if (!target || target === _dirDragEl) return;
  const parent = _dirDragEl.closest('.source-group-list');
  if (!parent) return;
  
  // 先把 DOM 元素移到目标位置（用户立刻看到变化）
  if (target.parentNode === parent) {
    parent.insertBefore(_dirDragEl, target.nextSibling);
  }
  
  // 收集新顺序（读 data-dir）
  const items = Array.from(parent.querySelectorAll('.source-group'));
  const newDirOrder = items.map(el => el.dataset.dir || '').filter(Boolean);
  
  // 更新 _grimoireSources 数组，让下次 renderGrimoireSources() 使用正确顺序
  if (_grimoireSources) {
    const dirRank = {};
    newDirOrder.forEach((d, i) => { dirRank[d] = i; });
    _grimoireSources.sort((a, b) => {
      const ra = dirRank[a.dir] ?? 999;
      const rb = dirRank[b.dir] ?? 999;
      return ra - rb || (a.name || '').localeCompare(b.name || '');
    });
  }
  
  // 后台保存，不 reload
  const result = await apiFetch('/api/grimoire/dir-order', {
    method: 'POST',
    body: JSON.stringify({ order: newDirOrder })
  }, true);
  if (!result?.ok) {
    toast('❌ 保存失败', 'error');
    loadGrimoireData(); // 失败时回滚
  }
}

function onDirDragEnd(e) {
  document.querySelectorAll('.source-group').forEach(el => el.classList.remove('dragging', 'drag-over'));
  _dirDragEl = null;
}

function filterGrimoireSources() {
  const input = document.getElementById('grimoire_source_search');
  _grimoireSourceFilter = input ? input.value : '';
  renderGrimoireSources();
}

async function selectGrimoireSource(path) {
  if (!path) return;
  // 退出编辑模式（但保留按钮可见）
  _grimoireEditMode = false;
  
  _grimoireCurrentSource = (path || '').replace(/\\/g, '/');
  _grimoireCurrentPage = 1;
  _grimoireDataFilter = '';
  const dataInput = document.getElementById('grimoire_data_search');
  if (dataInput) dataInput.value = '';
  // 重置分类筛选
  const catLabel = document.getElementById('grm_cat_label');
  if (catLabel) catLabel.textContent = '全部IP分类';
  const catSearch = document.getElementById('grm_cat_search_input');
  if (catSearch) catSearch.value = '';
  _grimoireCategoryFilter = '';
  _grimoireAllCategories = [];
  
  // Update UI selection
  document.querySelectorAll('.source-item').forEach(el => {
    el.classList.toggle('active', el.dataset.path === path);
  });
  
  const source = _grimoireSources.find(s => s.path === path);
  const title = document.getElementById('grimoire_detail_title');
  if (title) title.textContent = source ? (source.name || path) : path;
  
  await Promise.all([fetchGrimoireData(), loadGrimoireCategories()]);
  // 手机端钻取：选择分类后隐藏左侧列表，显示详情
  if (window.innerWidth <= 768) {
    const sheet = document.getElementById('grimoire_sheet');
    if (sheet) sheet.classList.add('drill-down');
    const backBtn = document.getElementById('grm_back_btn');
    if (backBtn) backBtn.style.display = 'inline-flex';
  }
}

/** 折叠/展开分类组 */
function toggleGrimoireGroup(dir) {
  if (!_grimoireCollapsedDirs) _grimoireCollapsedDirs = {};
  _grimoireCollapsedDirs[dir] = !_grimoireCollapsedDirs[dir];
  renderGrimoireSources();
}

/** 一键展开/收起所有分类（单击切换） */
function toggleAllGrimoireGroups() {
  if (!_grimoireCollapsedDirs) _grimoireCollapsedDirs = {};
  // 判断当前状态：如果任意目录是展开的，就全部收起；否则全部展开
  const list = document.querySelector('.source-group-list');
  let anyExpanded = false;
  if (list) {
    list.querySelectorAll('.source-group').forEach(el => {
      const dirName = el.dataset.dir || '';
      if (dirName && _grimoireCollapsedDirs[dirName] === false) anyExpanded = true;
      if (dirName && _grimoireCollapsedDirs[dirName] === undefined) anyExpanded = true;
    });
  }
  const collapse = anyExpanded; // 有展开的 → 收起；全收起 → 展开
  if (list) {
    list.querySelectorAll('.source-group').forEach(el => {
      const dirName = el.dataset.dir || '';
      if (dirName) _grimoireCollapsedDirs[dirName] = collapse;
    });
  }
  for (const key of Object.keys(_grimoireCollapsedDirs)) {
    _grimoireCollapsedDirs[key] = collapse;
  }
  renderGrimoireSources();
  // 更新按钮文字和图标
  const btn = document.getElementById('grm_toggle_all_btn');
  const txt = document.getElementById('grm_toggle_all_text');
  if (btn && txt) {
    const icon = btn.querySelector('use');
    if (collapse) {
      txt.textContent = '展开全部';
      if (icon) icon.setAttribute('href', '#icon-folder-open');
    } else {
      txt.textContent = '收起全部';
      if (icon) icon.setAttribute('href', '#icon-folder-closed');
    }
  }
}

/** ===== 魔导书图片缓存 ===== */

/** 查询当前数据源的缓存状态并显示 */
async function updateGrimoireCacheStatus(sourcePath) {
  const el = document.getElementById('grm_cache_status');
  const dot = document.getElementById('grm_cache_dot');
  const text = document.getElementById('grm_cache_text');
  if (!el || !dot || !text) return;
  
  // 统一正斜杠，避免 Windows 反斜杠问题
  const normPath = (sourcePath || '').replace(/\\/g, '/');
  // 只对 anima 数据源显示缓存状态
  if (!normPath || !normPath.startsWith('anima/')) {
    el.style.display = 'none';
    return;
  }
  
  el.style.display = 'inline-flex';
  dot.className = 'dot loading';
  text.textContent = '检查缓存...';
  
  try {
    const result = await apiFetch(`/api/grimoire/cache-status?source=${encodeURIComponent(sourcePath)}`, {}, true);
    if (result && result.ok) {
      const pct = result.total > 0 ? Math.round(result.cached / result.total * 100) : 0;
      // 显示大小信息
      let sizeInfo = '';
      if (result.cache_size_gb >= 1) {
        sizeInfo = ` ${result.cache_size_gb}GB`;
      } else if (result.cache_size_mb >= 0.1) {
        sizeInfo = ` ${result.cache_size_mb}MB`;
      }
      dot.className = pct >= 100 ? 'dot done' : result.cached > 0 ? 'dot' : 'dot pending';
      dot.style.background = pct >= 100 ? '' : result.cached > 0 ? 'var(--accent)' : 'var(--text-sub2)';
      text.textContent = pct >= 100
        ? `✅ ${result.total}张${sizeInfo}`
        : `📸 ${result.cached}/${result.total}${sizeInfo} (${pct}%)`;
      el.title = result.pending > 0 ? `点击下载剩余 ${result.pending} 张图片到本地` : '全部图片已缓存';
    } else {
      dot.className = 'dot pending';
      text.textContent = '📸 缓存';
    }
  } catch (e) {
    dot.className = 'dot pending';
    text.textContent = '📸 缓存';
  }
}

/** 批量缓存当前数据源的全部图片 — 后台运行 + 轮询进度 */
async function cacheAllGrimoire() {
  const source = (_grimoireCurrentSource || '').replace(/\\/g, '/');
  if (!source || !source.startsWith('anima/')) return;
  
  const el = document.getElementById('grm_cache_status');
  const dot = document.getElementById('grm_cache_dot');
  const text = document.getElementById('grm_cache_text');
  if (!el || !dot || !text) return;
  
  // 先查状态，看是否还需要缓存
  const status = await apiFetch(`/api/grimoire/cache-status?source=${encodeURIComponent(source)}`, {}, true);
  if (!status || !status.ok) return;
  
  if (status.cached >= status.total) {
    toast('✅ 全部图片已缓存');
    return;
  }
  
  if (!confirm(`即将下载 ${status.pending} 张图片到本地缓存，方便离线查看。继续吗？`)) return;
  
  dot.className = 'dot loading';
  text.textContent = '启动中...';
  el.style.pointerEvents = 'none';
  
  // 启动后台任务
  try {
    const resp = await fetch('/api/grimoire/cache-all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source })
    });
    const result = await resp.json();
    if (!result || !result.ok) {
      toast('❌ 启动缓存失败: ' + (result?.error || ''), 'error');
      el.style.pointerEvents = '';
      dot.className = 'dot pending';
      text.textContent = status.cached > 0 ? `📸 ${status.cached}/${status.total}` : '📸 缓存';
      return;
    }
  } catch (e) {
    toast('❌ 启动缓存失败: ' + e.message, 'error');
    el.style.pointerEvents = '';
    return;
  }
  
  // 轮询进度
  let pollTimer = setInterval(async () => {
    try {
      const prog = await apiFetch(`/api/grimoire/cache-progress?source=${encodeURIComponent(source)}`, {}, true);
      if (!prog || !prog.ok) return;
      
      if (prog.total > 0) {
        const pct = Math.round(prog.done / prog.total * 100);
        text.textContent = `📸 ${prog.done}/${prog.total} (${pct}%)`;
        dot.className = 'dot loading';
      } else {
        text.textContent = `📸 ${prog.done || 0}/?`;
      }
      
      if (!prog.running) {
        clearInterval(pollTimer);
        el.style.pointerEvents = '';
        if (prog.done >= prog.total && prog.total > 0) {
          dot.className = 'dot done';
          text.textContent = `✅ 已全部缓存 ${prog.total}`;
          toast(`✅ 缓存完成: ${prog.success} 成功, ${prog.failed} 失败`);
          // 刷新当前列表（更新 cached 状态）
          await fetchGrimoireData();
        } else {
          dot.className = 'dot pending';
          text.textContent = prog.message || '📸 缓存';
          if (prog.message) toast('⚠️ ' + prog.message);
          // 不管怎样也刷新一下
          await fetchGrimoireData();
          updateGrimoireCacheStatus(source);
        }
      }
    } catch (e) {
      // 轮询出错不打断
    }
  }, 2000);
}

/** 刷新所有子分类的缓存状态到设置面板 */
async function refreshCacheStatus() {
  const list = document.getElementById('cache_status_list');
  if (!list || !_grimoireSources) return;
  list.innerHTML = '<span style="color:var(--text-sub2)">正在查询...</span>';
  let html = '';
  let totalPending = 0;
  for (const src of _grimoireSources) {
    const path = src.path.replace(/\\/g, '/');
    const status = await apiFetch(`/api/grimoire/cache-status?source=${encodeURIComponent(path)}`, {}, true);
    if (status && status.ok) {
      const name = src.name || path.split('/').pop();
      const pct = status.total > 0 ? Math.round(status.cached / status.total * 100) : 0;
      const pending = status.pending || 0;
      totalPending += pending;
      if (status.total > 0) {
        html += `<div style="display:flex;justify-content:space-between;padding:2px 4px;border-bottom:1px solid var(--card-border);line-height:1.8">
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">${esc(name)}</span>
          <span style="flex-shrink:0;margin-left:8px;${pending > 0 ? 'color:var(--warning)' : 'color:var(--success)'}">${status.cached}/${status.total} (${pct}%)</span>
        </div>`;
      }
    }
  }
  if (!html) {
    list.innerHTML = '<span style="color:var(--text-sub2)">没有找到有图片的子分类</span>';
    return;
  }
  html = `<div style="display:flex;justify-content:space-between;padding:2px 4px;font-weight:600;border-bottom:1px solid var(--card-border)">
    <span>总计</span>
    <span style="${totalPending > 0 ? 'color:var(--warning)' : 'color:var(--success)'}">待下载 ${totalPending} 张</span>
  </div>` + html;
  list.innerHTML = html;
  if (totalPending > 0) {
    toast(`📸 共 ${totalPending} 张图片待下载`);
  } else {
    toast('✅ 所有图片已缓存');
  }
}

/** 缓存所有缺失的图片 */
async function cacheAllMissing() {
  if (!_grimoireSources) return;
  const toCache = [];
  for (const src of _grimoireSources) {
    const path = src.path.replace(/\\/g, '/');
    const status = await apiFetch(`/api/grimoire/cache-status?source=${encodeURIComponent(path)}`, {}, true);
    if (status && status.ok && status.pending > 0) {
      toCache.push({ path, name: src.name, pending: status.pending });
    }
  }
  if (toCache.length === 0) {
    toast('✅ 所有图片已缓存');
    return;
  }
  const totalPending = toCache.reduce((s, c) => s + c.pending, 0);
  if (!confirm(`即将下载 ${toCache.length} 个子分类共 ${totalPending} 张图片，继续吗？`)) return;
  for (const item of toCache) {
    await apiFetch('/api/grimoire/cache-all', {
      method: 'POST',
      body: JSON.stringify({ source: item.path })
    }, true);
  }
  toast(`✅ 已启动 ${toCache.length} 个分类的缓存任务`);
}

/** 魔导书图片灯箱：点击缩略图放大查看 */
function showGrimoireLightbox(src, name) {
  const lb = document.getElementById('grm_lightbox');
  const img = document.getElementById('grm_lightbox_img');
  if (!lb || !img) return;
  img.src = src;
  img.alt = name || '';
  lb.classList.add('show');
  document.body.style.overflow = 'hidden';
}
function closeGrimoireLightbox(e) {
  if (e) e.stopPropagation();
  const lb = document.getElementById('grm_lightbox');
  if (!lb) return;
  lb.classList.remove('show');
  document.body.style.overflow = '';
}

/** 重命名总分类（目录） */
async function renameGrimoireCategory(dir, displayName) {
  const newName = prompt(`重命名分类「${displayName}」：`, dir);
  if (!newName || newName === dir) return;
  if (!/^[a-zA-Z0-9_\-\u4e00-\u9fff]+$/.test(newName)) {
    toast('❌ 分类名仅支持字母、数字、下划线、中文', 'error');
    return;
  }
  const result = await apiFetch('/api/grimoire/category', {
    method: 'PUT',
    body: JSON.stringify({ category: dir, newName })
  });
  if (result && result.ok) {
    toast('✅ 分类已重命名');
    await loadGrimoireData();
  } else {
    toast('❌ ' + (result?.error || '重命名失败'), 'error');
  }
}

/** 删除总分类（目录） */
async function deleteGrimoireCategory(dir, displayName) {
  if (!confirm(`确定删除分类「${displayName}」吗？\n其下所有子分类和数据都将被删除！`)) return;
  const result = await apiFetch('/api/grimoire/category', {
    method: 'DELETE',
    body: JSON.stringify({ category: dir })
  });
  if (result && result.ok) {
    toast('✅ 分类已删除');
    _grimoireCurrentSource = '';
    await loadGrimoireData();
  } else {
    toast('❌ ' + (result?.error || '删除失败'), 'error');
  }
}

/** 添加子分类到指定总分类 */
async function addGrimoireSubCategory(dir, displayDir) {
  const name = prompt(`在「${displayDir}」下添加子分类：\n输入文件名（不需要.json）`);
  if (!name) return;
  const result = await apiFetch('/api/grimoire/source', {
    method: 'POST',
    body: JSON.stringify({ filename: name, category: dir })
  });
  if (result && result.ok) {
    toast('✅ 子分类已创建');
    await loadGrimoireData();
  } else {
    toast('❌ ' + (result?.error || '创建失败'), 'error');
  }
}

/** 重命名子分类（数据源文件） */
async function renameGrimoireSource(path, displayName) {
  // 从 path 中提取纯文件名（不含扩展名）仅用于后端，提示框显示中文名
  const newName = prompt(`重命名「${displayName}」\n输入新名称（不需要 .json）：`, displayName);
  if (!newName || newName === displayName) return;
  const result = await apiFetch('/api/grimoire/source', {
    method: 'PUT',
    body: JSON.stringify({ source: path, newName })
  });
  if (result && result.ok) {
    toast('✅ 子分类已重命名');
    // 如果当前选中的就是重命名的源，更新选中路径
    if (_grimoireCurrentSource === path && result.path) {
      _grimoireCurrentSource = result.path;
    }
    await loadGrimoireData();
  } else {
    toast('❌ ' + (result?.error || '重命名失败'), 'error');
  }
}

async function fetchGrimoireData() {
  if (!_grimoireCurrentSource) return;
  const params = new URLSearchParams({
    source: _grimoireCurrentSource,
    page: String(_grimoireCurrentPage),
    pageSize: String(_grimoirePageSize)
  });
  if (_grimoireDataFilter) params.append('q', _grimoireDataFilter);
  if (_grimoireCategoryFilter) params.append('category', _grimoireCategoryFilter);
  // v4.13.2: preferred = 固定在前 + 收藏次之（后端在分页前排序，固定条目天然进首页第一位）
  const pinData = _grimoirePins && _grimoirePins[_grimoireCurrentSource];
  const pinNames = [];
  if (pinData) (Array.isArray(pinData) ? pinData : [pinData]).forEach(p => { if (p && p.name) pinNames.push(p.name); });
  const starredNames = _grimoireStars && _grimoireStars[_grimoireCurrentSource];
  const starList = starredNames ? Array.from(starredNames).filter(n => !pinNames.includes(n)) : [];
  const pref = pinNames.concat(starList);
  if (pref.length) params.append('preferred', pref.join(','));
  
  const data = await apiFetch(`/api/grimoire/data?${params.toString()}`, {}, true);
  if (!data) return;
  if (!data.ok) {
    document.getElementById('grimoire_data_table').innerHTML = `<div style="padding:40px;text-align:center;color:var(--danger);font-size:13px">${esc(data.error || '加载失败')}</div>`;
    return;
  }
  _grimoireCurrentData = data.items || [];
  _grimoireCurrentTotal = data.total || 0;
  renderGrimoireData(data.total || 0, data.page || 1);
}

/** 加载当前数据源的分类列表 */
async function loadGrimoireCategories() {
  const catFilter = document.getElementById('grimoire_cat_filter');
  const catDropdown = document.getElementById('grm_cat_dropdown');
  if (!catFilter || !catDropdown || !_grimoireCurrentSource) {
    if (catFilter) catFilter.style.display = 'none';
    return;
  }
  const result = await apiFetch(`/api/grimoire/categories?source=${encodeURIComponent(_grimoireCurrentSource)}`, {}, true);
  if (result && result.ok && result.categories_cn && result.categories_cn.length > 0) {
    _grimoireAllCategories = result.categories_cn;
    renderGrmCatOptions();
    catFilter.style.display = '';
  } else {
    _grimoireAllCategories = [];
    catFilter.style.display = 'none';
  }
}

/** ===== IP分类自定义下拉框 ===== */

/** 切换下拉面板 */
function toggleGrmCatDropdown() {
  const panel = document.getElementById('grm_cat_panel');
  const trigger = document.getElementById('grm_cat_trigger');
  if (!panel || !trigger) return;
  const isOpen = panel.style.display !== 'none';
  panel.style.display = isOpen ? 'none' : 'block';
  trigger.setAttribute('aria-expanded', !isOpen);
  if (!isOpen) {
    const input = document.getElementById('grm_cat_search_input');
    if (input) { input.value = ''; input.focus(); }
    renderGrmCatOptions();
  }
}

/** 关闭下拉面板 */
function closeGrmCatDropdown() {
  const panel = document.getElementById('grm_cat_panel');
  const trigger = document.getElementById('grm_cat_trigger');
  if (panel) panel.style.display = 'none';
  if (trigger) trigger.setAttribute('aria-expanded', 'false');
}

/** 渲染分类选项（按搜索过滤） */
function renderGrmCatOptions() {
  const container = document.getElementById('grm_cat_options');
  if (!container) return;
  const q = (document.getElementById('grm_cat_search_input')?.value || '').toLowerCase().trim();
  container.innerHTML = '';
  // 全部
  const allDiv = document.createElement('div');
  allDiv.className = 'grm-cat-option' + (!_grimoireCategoryFilter ? ' active' : '');
  allDiv.dataset.value = '';
  allDiv.role = 'option';
  allDiv.textContent = '全部';
  allDiv.onclick = () => selectGrmCatOption('');
  container.appendChild(allDiv);
  let hasMatch = false;
  _grimoireAllCategories.forEach(cat => {
    const displayName = cat.name_cn || cat.category;
    if (!q || displayName.toLowerCase().includes(q) || cat.category.toLowerCase().includes(q)) {
      hasMatch = true;
      const div = document.createElement('div');
      div.className = 'grm-cat-option' + (cat.category === _grimoireCategoryFilter ? ' active' : '');
      div.dataset.value = cat.category;
      div.role = 'option';
      div.textContent = displayName;
      div.onclick = () => selectGrmCatOption(cat.category);
      container.appendChild(div);
    }
  });
  if (!hasMatch && q) {
    container.innerHTML = '<div class="grm-cat-empty">无匹配分类</div>';
  }
}

/** 搜索过滤选项（输入时实时过滤） */
function filterGrmCatOptions() {
  renderGrmCatOptions();
}

/** 选择分类 */
function selectGrmCatOption(value) {
  const label = document.getElementById('grm_cat_label');
  if (label) {
    if (!value) {
      label.textContent = '全部IP分类';
    } else {
      const cat = _grimoireAllCategories.find(c => c.category === value);
      label.textContent = cat ? (cat.name_cn || cat.category) : value;
    }
  }
  _grimoireCategoryFilter = value;
  _grimoireCurrentPage = 1;
  closeGrmCatDropdown();
  fetchGrimoireData();
}

/** 渲染分类筛选（被loadGrimoireCategories调用 — 兼容外部队列） */
function renderGrimoireCatFilter() {
  renderGrmCatOptions();
  const input = document.getElementById('grm_cat_search_input');
  if (input) input.value = '';
  const label = document.getElementById('grm_cat_label');
  if (label && !_grimoireCategoryFilter) label.textContent = '全部IP分类';
}

/** 切换魔导书编辑模式 */
function toggleGrimoireEditMode() {
  _grimoireEditMode = !_grimoireEditMode;
  _grimoireSelectedIndices.clear();
  renderGrimoireSources();
  renderGrimoireData(_grimoireCurrentTotal || 0, _grimoireCurrentPage || 1);
}

function renderGrimoireData(total, page) {
  // 画廊模式：带图片的数据源以网格展示
  if (_grimoireCurrentData.some(item => item.image_url)) {
    renderGrimoireGallery(total, page);
    return;
  }
  const table = document.getElementById('grimoire_data_table');
  const count = document.getElementById('grimoire_detail_count');
  const pagination = document.getElementById('grimoire_pagination');
  const batchBtn = document.getElementById('btn_grimoire_batch');
  const addBtn = document.getElementById('btn_grimoire_add');
  
  if (count) count.textContent = total ? `共 ${total} 条` : '';
  // 所有数据源都显示新增和批量导入按钮（不限 custom）
  const hasData = _grimoireCurrentData && _grimoireCurrentData.length > 0;
  if (batchBtn) batchBtn.style.display = _grimoireCurrentSource ? '' : 'none';
  if (addBtn) addBtn.style.display = _grimoireCurrentSource ? '' : 'none';
  // 显示编辑模式切换按钮
  const editToggle = document.getElementById('btn_grimoire_edit_toggle');
  if (editToggle) {
    editToggle.style.display = '';
    const txt = document.getElementById('grm_edit_toggle_text');
    if (txt) txt.textContent = _grimoireEditMode ? '退出编辑' : '编辑';
    editToggle.classList.toggle('btn-danger', _grimoireEditMode);
    editToggle.classList.toggle('btn-secondary', !_grimoireEditMode);
  }
  
  // 编辑模式时显示全选 + 批量删除栏
  if (_grimoireEditMode && _grimoireCurrentData.length > 0) {
    const selCount = _grimoireSelectedIndices.size;
    let batchHtml = `<div class="grm-batch-bar" id="grm_batch_bar">` +
      `<label class="grm-select-all" onclick="event.stopPropagation()">` +
        `<input type="checkbox" id="grm_select_all" onchange="toggleSelectAllGrimoire(this.checked)" ${selCount === _grimoireCurrentData.length ? 'checked' : ''}>` +
        `<span> ${selCount > 0 ? '已选 ' + selCount + ' 条' : '全选'}</span>` +
      `</label>` +
      (selCount > 0 ? `<button class="btn btn-xs btn-danger" onclick="batchDeleteGrimoire()">🗑️ 删除 ${selCount} 条</button>` : '') +
      `</div>`;
    // 追加到表格或单独容器
    let batchContainer = document.getElementById('grm_batch_container');
    if (!batchContainer) {
      batchContainer = document.createElement('div');
      batchContainer.id = 'grm_batch_container';
      table.parentNode.insertBefore(batchContainer, table.nextSibling);
    }
    batchContainer.innerHTML = batchHtml;
  } else {
    const bc = document.getElementById('grm_batch_container');
    if (bc) bc.innerHTML = '';
    _grimoireSelectedIndices.clear();
  }
  
  if (!_grimoireCurrentData.length) {
    table.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-sub2);font-size:13px">暂无数据</div>';
    if (pagination) pagination.style.display = 'none';
    return;
  }
  
  // 收藏+固定排序：收藏排最前，已固定排第二，其余在后
  const stars = _grimoireStars && _grimoireStars[_grimoireCurrentSource];
  const pinData = _grimoirePins && _grimoirePins[_grimoireCurrentSource];
  const pinNames = new Set();
  if (pinData) {
    (Array.isArray(pinData) ? pinData : [pinData]).forEach(p => { if (p.name) pinNames.add(p.name); });
  }
  const sorted = [..._grimoireCurrentData].sort((a, b) => {
    // v4.12.5: 固定标签绝对优先（哪怕没收藏也排最前），收藏次之，其余按原序
    const aPin = pinNames.has(a.name) ? 1 : 0;
    const bPin = pinNames.has(b.name) ? 1 : 0;
    const aStar = stars && stars.has(a.name) ? 1 : 0;
    const bStar = stars && stars.has(b.name) ? 1 : 0;
    return (bPin * 10 + bStar) - (aPin * 10 + aStar);
  });

  let html = '';
  let hadStar = false;
  sorted.forEach((item, sortIdx) => {
    const isStarred = stars && stars.has(item.name);
    // 首次遇到收藏项时插入"我的咒语"头部
    if (isStarred && !hadStar) {
      html += `<div class="grm-spells-header"><svg class="icon-sm" aria-hidden="true" style="color:var(--primary)"><use href="#icon-star"/></svg> <span>我的咒语</span></div>`;
      hadStar = true;
    }
    // 从收藏区切到普通区时插入分隔线
    if (!isStarred && hadStar) {
      html += `<div class="grm-star-divider"></div>`;
      hadStar = false;
    }
    // 查找原索引用于按钮回调
    const origIdx = _grimoireCurrentData.indexOf(item);
    // 编辑模式：编辑/删除；浏览模式：固定按钮常显 + 复制按钮悬停
    const actions = _grimoireEditMode
      ? `<button class="grm-item-btn edit" onclick="showGrimoireEditModal(${origIdx})" title="编辑"><svg class="icon-sm" aria-hidden="true"><use href="#icon-edit"/></svg></button>` +
        `<button class="grm-item-btn del" onclick="deleteGrimoireItem(${origIdx})" title="删除"><svg class="icon-sm" aria-hidden="true"><use href="#icon-trash"/></svg></button>`
      : '';
    const isPinned = _grimoirePins && _grimoirePins[_grimoireCurrentSource] && 
    (Array.isArray(_grimoirePins[_grimoireCurrentSource]) 
      ? _grimoirePins[_grimoireCurrentSource].some(x => x.name === item.name)
      : _grimoirePins[_grimoireCurrentSource].name === item.name);
    const pinBtn = !_grimoireEditMode
      ? `<button class="grm-item-btn pin${isPinned ? ' active' : ''}" onclick="toggleGrimoirePin(${origIdx})" title="${isPinned ? '取消固定' : '固定标签'}"><svg class="icon-sm" aria-hidden="true"><use href="#icon-pin"/></svg></button>`
      : '';
    const copyBtn = !_grimoireEditMode
      ? `<button class="grm-item-btn copy-btn" onclick="copyGrimoireTags(${origIdx})" title="复制标签"><svg class="icon-sm" aria-hidden="true"><use href="#icon-clipboard"/></svg></button>`
      : '';
    const starBtn = !_grimoireEditMode
      ? `<button class="grm-item-btn star${isStarred ? ' active' : ''}" onclick="toggleGrimoireStar(${origIdx})" title="${isStarred ? '取消收藏' : '收藏标签'}"><svg class="icon-sm" aria-hidden="true"><use href="#icon-star"/></svg></button>`
      : '';
    const checkbox = _grimoireEditMode
      ? `<input type="checkbox" class="grm-item-checkbox" data-idx="${item.index}" onchange="toggleGrimoireItem(${item.index}, this.checked)" ${_grimoireSelectedIndices.has(item.index) ? 'checked' : ''}>`
      : '';
    // 图片缩略图（如果有 image_url）
    let imgHtml = '';
    let hasImgClass = '';
    let cachedBadge = '';
    if (item.image_url) {
      const fullSrc = item.image_url;
      imgHtml = `<img class="grm-item-img" data-src="${fullSrc}" alt="${esc(item.name || '')}" loading="lazy" onclick="showGrimoireLightbox('${esc(fullSrc)}', '${esc(item.name || '')}')" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" onload="this.style.opacity='1'">` +
        `<div class="grm-item-img-placeholder" style="display:none">🎨</div>`;
      hasImgClass = ' has-img';
    }
    html += `<div class="grm-item-card${_grimoireSelectedIndices.has(item.index) ? ' grm-selected' : ''}${isStarred ? ' starred' : ''}${isPinned ? ' pinned' : ''}${hasImgClass}">` +
      checkbox +
      imgHtml +
      cachedBadge +
      `<div class="grm-item-info" style="display:block;min-width:0;overflow:hidden;visibility:visible;opacity:1">` +
        `<div class="grm-item-name" style="display:block;visibility:visible;opacity:1;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px;line-height:1.3">${esc(item.name_cn || item.name || '')}` +
          (item.name_cn && item.name_cn !== item.name ? `<span class="grm-item-name-en">${esc(item.name)}</span>` : '') +
        `</div>` +
        (item.category_cn || item.category ? `<div class="grm-item-category">${esc(item.category_cn || item.category)}</div>` : '') +
        `<div class="grm-item-tags" style="display:block;visibility:visible;opacity:1;color:var(--text-sub2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10px;margin-top:2px;line-height:1.3">${esc(item.tags || '')}</div>` +
      `</div>` +
      `<div class="grm-item-actions" style="display:flex;gap:4px;align-items:center;white-space:nowrap;justify-content:flex-end;visibility:visible">${pinBtn}${copyBtn}${starBtn}${actions}</div>` +
      `</div>`;
  });
  table.innerHTML = html;
  // 魔导书标签图走缓存加载（data-src → Cache API）
  table.querySelectorAll('img[data-src]').forEach((img) => {
    const src = img.getAttribute('data-src');
    if (src) {
      setImgWithCache(img, src);
      img.removeAttribute('data-src');
    }
  });

  
  // Pagination
  const totalPages = Math.ceil(total / _grimoirePageSize) || 1;
  if (pagination) {
    pagination.style.display = totalPages > 1 ? 'flex' : 'none';
    const pageInfo = document.getElementById('grimoire_page_info');
    if (pageInfo) pageInfo.textContent = `${page}/${totalPages}`;
    const prevBtn = document.getElementById('btn_grimoire_prev');
    const nextBtn = document.getElementById('btn_grimoire_next');
    if (prevBtn) prevBtn.disabled = page <= 1;
    if (nextBtn) nextBtn.disabled = page >= totalPages;
    // 同步跳页输入框
    const jumpInput = document.getElementById('grimoire_page_jump');
    if (jumpInput) {
      jumpInput.max = totalPages;
      jumpInput.value = page;
    }
  }
}

/** 画廊模式：带图片的数据源以网格展示 */
function renderGrimoireGallery(total, page) {
  const table = document.getElementById('grimoire_data_table');
  const count = document.getElementById('grimoire_detail_count');
  const pagination = document.getElementById('grimoire_pagination');
  if (count) count.textContent = total ? `共 ${total} 条` : '';
  if (!_grimoireCurrentData.length) {
    table.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-sub2);font-size:13px">暂无数据</div>';
    if (pagination) pagination.style.display = 'none';
    return;
  }
  // 收藏+固定排序
  const stars = _grimoireStars && _grimoireStars[_grimoireCurrentSource];
  const pinData = _grimoirePins && _grimoirePins[_grimoireCurrentSource];
  const pinNames = new Set();
  if (pinData) {
    (Array.isArray(pinData) ? pinData : [pinData]).forEach(p => { if (p.name) pinNames.add(p.name); });
  }
  const sorted = [..._grimoireCurrentData].sort((a, b) => {
    // v4.12.5: 固定标签绝对优先（哪怕没收藏也排最前），收藏次之，其余按原序
    const aPin = pinNames.has(a.name) ? 1 : 0;
    const bPin = pinNames.has(b.name) ? 1 : 0;
    const aStar = stars && stars.has(a.name) ? 1 : 0;
    const bStar = stars && stars.has(b.name) ? 1 : 0;
    return (bPin * 10 + bStar) - (aPin * 10 + aStar);
  });
  let html = '<div class="grm-gallery-grid">';
  sorted.forEach((item) => {
    const origIdx = _grimoireCurrentData.indexOf(item);
    const isPinned = pinNames.has(item.name);
    const isStarred = stars && stars.has(item.name);
    const pinBtn = `<button class="grm-item-btn pin${isPinned ? ' active' : ''}" onclick="toggleGrimoirePin(${origIdx})" title="${isPinned ? '取消固定' : '固定标签'}"><svg class="icon-sm" aria-hidden="true"><use href="#icon-pin"/></svg></button>`;
    const copyBtn = `<button class="grm-item-btn copy-btn" onclick="copyGrimoireTags(${origIdx})" title="复制标签"><svg class="icon-sm" aria-hidden="true"><use href="#icon-clipboard"/></svg></button>`;
    const starBtn = `<button class="grm-item-btn star${isStarred ? ' active' : ''}" onclick="toggleGrimoireStar(${origIdx})" title="${isStarred ? '取消收藏' : '收藏标签'}"><svg class="icon-sm" aria-hidden="true"><use href="#icon-star"/></svg></button>`;
    const imgSrc = item.image_url || '';
    html += `<div class="grm-gallery-card${isPinned ? ' pinned' : ''}${isStarred ? ' starred' : ''}">` +
      `<div class="grm-gallery-img-wrap">` +
      (imgSrc ? `<img class="grm-gallery-img" data-src="${imgSrc}" alt="${esc(item.name || '')}" loading="lazy" onclick="showGrimoireLightbox('${esc(imgSrc)}', '${esc(item.name||'')}')" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">` : '') +
      `<div class="grm-gallery-img-placeholder" style="${imgSrc ? 'display:none' : 'display:flex'}">🎨</div>` +
      `</div>` +
      `<div class="grm-gallery-name">${esc(item.name_cn || item.name || '')}</div>` +
      `<div class="grm-gallery-actions">${pinBtn}${copyBtn}${starBtn}</div>` +
      `</div>`;
  });
  html += '</div>';
  table.innerHTML = html;
  // 魔导书画廊图走缓存加载（data-src → Cache API）
  table.querySelectorAll('img[data-src]').forEach((img) => {
    const src = img.getAttribute('data-src');
    if (src) {
      setImgWithCache(img, src);
      img.removeAttribute('data-src');
    }
  });
  // 分页
  const totalPages = Math.ceil(total / _grimoirePageSize) || 1;
  if (pagination) {
    pagination.style.display = totalPages > 1 ? 'flex' : 'none';
    document.getElementById('grimoire_page_info').textContent = `第 ${page}/${totalPages} 页`;
    document.getElementById('grimoire_page_jump').value = page;
    document.getElementById('grimoire_page_jump').max = totalPages;
    document.getElementById('btn_grimoire_prev').disabled = page <= 1;
    document.getElementById('btn_grimoire_next').disabled = page >= totalPages;
  }
}

function filterGrimoireData() {
  const input = document.getElementById('grimoire_data_search');
  _grimoireDataFilter = input ? input.value : '';
  _grimoireCurrentPage = 1;
  fetchGrimoireData();
}

function grimoirePage(delta) {
  _grimoireCurrentPage = Math.max(1, _grimoireCurrentPage + delta);
  fetchGrimoireData();
}
function jumpGrimoirePage() {
  const input = document.getElementById('grimoire_page_jump');
  if (!input) return;
  const val = parseInt(input.value);
  if (!val || val < 1) return;
  _grimoireCurrentPage = val;
  fetchGrimoireData();
}

function renderGrmCategoryOptions(selected) {
  const optionsContainer = document.getElementById('grm_category_options');
  const hiddenInput = document.getElementById('grm_category');
  const triggerText = document.getElementById('grm_category_trigger_text');
  if (!optionsContainer || !hiddenInput) return;
  optionsContainer.innerHTML = '<div class="grm-cat-option' + (!selected ? ' active' : '') + '" data-value="" role="option" onclick="selectGrmCategoryOption(\'\')">未分类</div>';
  const cats = _grimoireAllCategories || [];
  let selectedLabel = '未分类';
  cats.forEach(cat => {
    const value = cat.category || '';
    const label = cat.name_cn || value || '';
    if (!value) return;
    const active = value === selected ? ' active' : '';
    optionsContainer.innerHTML += `<div class="grm-cat-option${active}" data-value="${esc(value)}" role="option" onclick="selectGrmCategoryOption('${esc(value)}')">${esc(label)}</div>`;
    if (value === selected) selectedLabel = label;
  });
  hiddenInput.value = selected || '';
  if (triggerText) triggerText.textContent = selectedLabel;
}

function toggleGrmCategoryDropdown() {
  const panel = document.getElementById('grm_category_panel');
  const trigger = document.getElementById('grm_category_trigger');
  if (!panel || !trigger) return;
  const isOpen = panel.style.display === 'block';
  panel.style.display = isOpen ? 'none' : 'block';
  trigger.setAttribute('aria-expanded', String(!isOpen));
  if (!isOpen) {
    setTimeout(() => {
      const input = document.getElementById('grm_category_search_input');
      if (input) input.focus();
    }, 10);
  }
}

function closeGrmCategoryDropdown() {
  const panel = document.getElementById('grm_category_panel');
  const trigger = document.getElementById('grm_category_trigger');
  if (panel) panel.style.display = 'none';
  if (trigger) trigger.setAttribute('aria-expanded', 'false');
}

function filterGrmCategoryDropdown() {
  const input = document.getElementById('grm_category_search_input');
  const container = document.getElementById('grm_category_options');
  if (!input || !container) return;
  const kw = input.value.trim().toLowerCase();
  const options = container.querySelectorAll('.grm-cat-option');
  options.forEach(opt => {
    const text = opt.textContent.toLowerCase();
    opt.style.display = !kw || text.includes(kw) ? '' : 'none';
  });
}

function selectGrmCategoryOption(value) {
  const hiddenInput = document.getElementById('grm_category');
  const triggerText = document.getElementById('grm_category_trigger_text');
  const catLabel = document.getElementById('grm_current_category_label');
  if (hiddenInput) hiddenInput.value = value;
  const cats = _grimoireAllCategories || [];
  const selectedCat = cats.find(c => c.category === value);
  const label = selectedCat ? (selectedCat.name_cn || selectedCat.category) : (value || '未分类');
  if (triggerText) triggerText.textContent = label;
  if (catLabel) catLabel.textContent = label;
  // 更新 active 样式
  const container = document.getElementById('grm_category_options');
  if (container) {
    container.querySelectorAll('.grm-cat-option').forEach(opt => {
      opt.classList.toggle('active', opt.dataset.value === value);
    });
  }
  closeGrmCategoryDropdown();
}

function showGrimoireAddModal() {
  _grimoireEditingIndex = -1;
  document.getElementById('grimoire_item_modal_title').textContent = '新增条目';
  document.getElementById('grm_name').value = '';
  document.getElementById('grm_name_cn').value = '';
  document.getElementById('grm_tags').value = '';
  document.getElementById('grm_style').value = 'general';
  document.getElementById('grm_note').value = '';
  // 显示当前数据源路径
  const label = document.getElementById('grm_current_source_label');
  if (label) {
    const src = _grimoireCurrentSource || '';
    const srcName = src.replace(/\.json$/i, '').replace(/[/\\]/g, ' → ');
    label.textContent = srcName || '请先选择数据源';
  }
  // 新增模式隐藏分类行
  const catRow = document.getElementById('grm_current_category_row');
  if (catRow) catRow.style.display = 'none';
  // 分类下拉默认使用当前筛选的分类
  renderGrmCategoryOptions(_grimoireCategoryFilter || '');
  showModal('grimoire_item_modal');
}

function showGrimoireEditModal(idx) {
  const item = _grimoireCurrentData[idx];
  if (!item) return;
  _grimoireEditingIndex = item.index;
  document.getElementById('grimoire_item_modal_title').textContent = '编辑条目';
  document.getElementById('grm_name').value = item.name || '';
  document.getElementById('grm_name_cn').value = item.name_cn || '';
  document.getElementById('grm_tags').value = item.tags || '';
  document.getElementById('grm_style').value = item.style || 'general';
  document.getElementById('grm_note').value = item.note || '';
  // 显示当前数据源路径
  const label = document.getElementById('grm_current_source_label');
  if (label) {
    const src = _grimoireCurrentSource || '';
    const srcName = src.replace(/\.json$/i, '').replace(/[/\\]/g, ' → ');
    label.textContent = srcName || '请先选择数据源';
  }
  // 显示当前分类
  const catRow = document.getElementById('grm_current_category_row');
  const catLabel = document.getElementById('grm_current_category_label');
  if (catRow) catRow.style.display = '';
  if (catLabel) {
    const cat = item.category_cn || item.category || '';
    catLabel.textContent = cat || '未分类';
  }
  // 分类下拉回显
  renderGrmCategoryOptions(item.category || '');
  showModal('grimoire_item_modal');
}

async function saveGrimoireItem() {
  const name = document.getElementById('grm_name').value.trim();
  const name_cn = document.getElementById('grm_name_cn').value.trim();
  const tags = document.getElementById('grm_tags').value.trim();
  const style = document.getElementById('grm_style').value;
  const note = document.getElementById('grm_note').value.trim();
  const category = document.getElementById('grm_category').value.trim();
  const categoryObj = category ? (_grimoireAllCategories || []).find(c => c.category === category) : null;
  const category_cn = categoryObj ? (categoryObj.name_cn || category) : (category || '');
  if (!name) { toast('英文名称不能为空', 'error'); return; }
  if (!tags) { toast('标签不能为空', 'error'); return; }

  const item = { name, name_cn, tags, style, note, category, category_cn };
  let url = '/api/grimoire/data';
  let method = 'POST';
  let body = { source: _grimoireCurrentSource, item };

  if (_grimoireEditingIndex >= 0) {
    method = 'PUT';
    body = { source: _grimoireCurrentSource, index: _grimoireEditingIndex, item };
  }

  const result = await apiFetch(url, { method, body: JSON.stringify(body) }, true);
  if (result && result.ok) {
    hideModal('grimoire_item_modal');
    toast('✅ 保存成功');
    await fetchGrimoireData();
    await loadGrimoireData(); // refresh count
  } else {
    toast('❌ 保存失败: ' + (result?.error || ''), 'error');
  }
}

async function deleteGrimoireItem(idx) {
  if (!confirm('确定删除该条目？')) return;
  const item = _grimoireCurrentData[idx];
  if (!item) return;
  const result = await apiFetch('/api/grimoire/data', {
    method: 'DELETE',
    body: JSON.stringify({ source: _grimoireCurrentSource, index: item.index })
  }, true);
  if (result && result.ok) {
    toast('✅ 已删除');
    await fetchGrimoireData();
    await loadGrimoireData();
  } else {
    toast('❌ 删除失败', 'error');
  }
}

/** 复制魔导书标签到剪贴板 */
function copyGrimoireTags(idx) {
  const item = _grimoireCurrentData[idx];
  if (!item || !item.tags) {
    toast('没有可复制的标签', 'error');
    return;
  }
  const tags = item.tags;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(tags).then(() => {
      toast('✅ 已复制: ' + (item.name || '') + ' → ' + tags.substring(0, 40) + (tags.length > 40 ? '...' : ''));
    }).catch(() => {
      fallbackCopy(tags);
    });
  } else {
    fallbackCopy(tags);
  }
}
function fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); toast('✅ 已复制'); } catch(e) { toast('❌ 复制失败', 'error'); }
  document.body.removeChild(ta);
}

/** 固定/取消固定标签 */
async function toggleGrimoirePin(idx) {
  const item = _grimoireCurrentData[idx];
  if (!item || !_grimoireCurrentSource) return;
  const src = _grimoireCurrentSource;
  const pinData = _grimoirePins[src];
  const isPinned = pinData && (Array.isArray(pinData) ? pinData.some(x => x.name === item.name) : pinData.name === item.name);
  const action = isPinned ? 'unpin' : 'pin';
  
  const result = await apiFetch('/api/grimoire/pin', {
    method: 'POST',
    body: JSON.stringify({ source: src, action, item: { name: item.name, tags: item.tags } })
  }, true);
  if (result && result.ok) {
    _grimoirePins = {};
    for (const [k, v] of Object.entries(result.pins || {})) {
      _grimoirePins[k.replace(/\\/g, '/')] = v;
    }
    // v4.13.2: 固定后跳回首页第一位（后端 preferred 已把固定条目排到全源最前）
    _grimoireCurrentPage = 1;
    await fetchGrimoireData();
    toast(isPinned ? '✅ 已取消固定' : '✅ 已固定: ' + (item.name_cn || item.name));
    updatePinnedBar();
    renderGrimoireSources(); // v4.12.3: 源列表的 has-pin 框框即时更新（此前要手动刷新才变）
  } else {
    toast('❌ 操作失败: ' + (result?.error || ''), 'error');
  }
}

/** 收藏/取消收藏标签（持久化到后端） */
async function toggleGrimoireStar(idx) {
  const item = _grimoireCurrentData[idx];
  if (!item || !item.name || !_grimoireCurrentSource) return;
  const key = _grimoireCurrentSource;
  if (!_grimoireStars[key]) _grimoireStars[key] = new Set();
  const stars = _grimoireStars[key];
  if (stars.has(item.name)) {
    stars.delete(item.name);
    if (stars.size === 0) delete _grimoireStars[key];
  } else {
    stars.add(item.name);
  }
  // 持久化到后端（Set → Object 序列化）
  const serialized = {};
  for (const [k, v] of Object.entries(_grimoireStars)) {
    serialized[k] = Array.from(v);
  }
  await apiFetch('/api/grimoire/stars', {
    method: 'POST',
    body: JSON.stringify({ stars: serialized })
  }, true);
  // 留在当前页重新渲染，收藏的条目会在当前页排最前
  renderGrimoireData(_grimoireCurrentTotal || 0, _grimoireCurrentPage || 1);
  toast(stars.has(item.name) ? '⭐ 已收藏' : '已取消收藏');
}

/** 渲染正面提示词下方的固定标签 / 随机池展示区（与魔导书状态同步） */
function renderPromptPins() {
  const bar = document.getElementById('prompt_pins_bar');
  if (!bar) return;
  const pinEntries = Object.entries(_grimoirePins || {});
  const poolCount = (_grimoireRandPool || []).length;
  if (pinEntries.length === 0 && poolCount === 0) {
    bar.style.display = 'none';
    bar.innerHTML = '';
    return;
  }
  let html = '<span class="prompt-pins-title"><svg class="icon-xs" aria-hidden="true"><use href="#icon-pin"/></svg> 注入标签</span>';
  for (const [src, info] of pinEntries) {
    const items = Array.isArray(info) ? info : [info];
    const label = src.replace('.json', '').split(/[/\\]/).pop();
    for (const item of items) {
      const text = item && item.name ? `${label}: ${item.name}` : label;
      // 标签可点击：取消固定（×）或点击打开魔导书定位该源
      html += `<span class="prompt-pins-tag" title="点击取消固定">` +
              `<span class="prompt-pins-name" onclick="showGrimoire()" style="cursor:pointer">${esc(text)}</span>` +
              `<button class="prompt-pins-x" onclick="event.stopPropagation();unpinSource('${esc(src)}','${esc(item && item.name ? item.name : '')}')" title="取消固定">×</button>` +
              `</span>`;
    }
  }
  if (poolCount > 0) {
    html += `<span class="prompt-pins-tag" title="点击打开魔导书">` +
            `<span class="prompt-pins-name" onclick="showGrimoire()" style="cursor:pointer">🎲 随机池 <span class="prompt-pins-count">${poolCount}</span></span>` +
            `</span>`;
  }
  // 添加按钮：打开魔导书选择/固定其它标签
  html += `<button class="prompt-pins-add" onclick="showGrimoire()" title="添加魔导书标签">` +
          `<svg class="icon-xs" aria-hidden="true"><use href="#icon-plus"/></svg> 添加</button>`;
  bar.innerHTML = html;
  bar.style.display = 'flex';
}

/** 更新已固定标签栏和随机池状态 */
function updatePinnedBar() {
  // 移除旧 bar 重新创建，避免 DOM 残留
  const oldBar = document.getElementById('grm_pinned_bar');
  if (oldBar) oldBar.remove();
  
  const pinEntries = Object.entries(_grimoirePins || {});
  const poolCount = (_grimoireRandPool || []).length;
  // 同步渲染正面提示词下方的固定标签/随机池展示区
  renderPromptPins();
  if (pinEntries.length === 0 && poolCount === 0) return;
  
  const dataTable = document.getElementById('grimoire_data_table');
  if (!dataTable || !dataTable.parentNode) return;
  
  let html = '<div class="grm-pinned-bar" id="grm_pinned_bar">';
  if (pinEntries.length > 0) {
    html += '<span class="grm-pinned-title"><svg class="icon-xs" aria-hidden="true"><use href="#icon-pin"/></svg></span>';
    for (const [src, info] of pinEntries) {
      const items = Array.isArray(info) ? info : [info];
      const label = src.replace('.json','').split(/[/\\]/).pop();
      for (const item of items) {
        const tagName = window.innerWidth <= 768 ? esc(item.name || '') : `${esc(label)}: ${esc(item.name || '')}`;
        html += `<span class="grm-pinned-tag"><span class="grm-pinned-name">${tagName}</span> <button class="grm-pinned-remove" onclick="unpinSource('${esc(src)}','${esc(item.name||'')}')" title="取消固定"><svg class="icon-xs" aria-hidden="true"><use href="#icon-x"/></svg></button></span>`;
      }
    }
  }
  if (poolCount > 0) {
    html += '<span class="grm-pinned-title" style="margin-left:6px"><svg class="icon-xs" aria-hidden="true"><use href="#icon-shuffle"/></svg></span>';
    html += `<span class="grm-pinned-tag">${poolCount} 个随机池 <button class="grm-pinned-remove" onclick="clearAllPins()" title="清空随机池"><svg class="icon-xs" aria-hidden="true"><use href="#icon-x"/></svg></button></span>`;
  }
  if (pinEntries.length > 0 || poolCount > 0) {
    html += `<button class="grm-item-btn btn-secondary" onclick="saveGrimoirePreset()" style="font-size:10px;padding:2px 6px;border-radius:4px;border:1px solid var(--card-border);background:var(--input-bg);color:var(--text-sub);cursor:pointer;flex-shrink:0;margin-right:4px" title="保存当前固定标签和随机池为预设"><svg class="icon-xs" aria-hidden="true"><use href="#icon-save"/></svg></button>`;
    html += `<button class="btn btn-xs btn-danger" onclick="clearAllPins()" style="font-size:10px;flex-shrink:0">清空</button>`;
  }
  html += '</div>';
  
  if (window.innerWidth <= 768) {
    // 手机端：插入到分类列表下方（在 source-list 外面）
    const sourceList = document.querySelector('.grimoire-source-list');
    if (sourceList && sourceList.parentNode) {
      sourceList.parentNode.insertBefore(document.createRange().createContextualFragment(html), sourceList.nextSibling);
    }
  } else {
    dataTable.parentNode.insertBefore(document.createRange().createContextualFragment(html), dataTable);
  }
}

/** 取消指定数据源的固定 */
async function unpinSource(src, name) {
  const body = { source: src, action: 'unpin' };
  if (name) body.item = { name: name };
  const result = await apiFetch('/api/grimoire/pin', {
    method: 'POST',
    body: JSON.stringify(body)
  }, true);
  if (result && result.ok) {
    _grimoirePins = {};
    for (const [k, v] of Object.entries(result.pins || {})) {
      _grimoirePins[k.replace(/\\/g, '/')] = v;
    }
    // v4.13.2: 取消固定后回首页刷新（该条目回到其在源数据中的原位）
    _grimoireCurrentPage = 1;
    await fetchGrimoireData();
    updatePinnedBar();
    renderGrimoireSources(); // v4.12.3: 源列表 has-pin 框框即时消失
    toast('✅ 已取消固定');
  }
}

/** 清空所有固定标签和随机池 */
async function clearAllPins() {
  const pinEntries = Object.entries(_grimoirePins || {});
  const poolEntries = [...(_grimoireRandPool || [])];
  if (pinEntries.length === 0 && poolEntries.length === 0) return;
  if (!confirm(`确定清空全部固定标签 (${pinEntries.length}) 和随机池 (${poolEntries.length}个源)？`)) return;
  // 清空固定标签
  for (const [src] of pinEntries) {
    await apiFetch('/api/grimoire/pin', {
      method: 'POST',
      body: JSON.stringify({ source: src, action: 'unpin' })
    }, true);
  }
  // 清空随机池
  for (const src of poolEntries) {
    await apiFetch('/api/grimoire/rand-pool', {
      method: 'POST',
      body: JSON.stringify({ source: src, action: 'remove' })
    }, true);
  }
  _grimoirePins = {};
  _grimoireRandPool = [];
  renderGrimoireSources();
  renderGrimoireData(_grimoireCurrentTotal || 0, _grimoireCurrentPage || 1);
  updatePinnedBar();
  toast('✅ 已清空全部固定标签和随机池');
}

// ========= 预设相关 =========

/** 加载预设列表到自定义菜单 */
async function loadGrimoirePresets(setValue) {
  const menu = document.getElementById('grm_preset_menu');
  const label = document.getElementById('grm_preset_label');
  if (!menu) return;
  const r = await apiFetch('/api/grimoire/presets', {}, true);
  if (!r || !r.ok || !r.presets) { menu.innerHTML = ''; return; }
  let h = '';
  for (const name of (r.presets || [])) {
    h += `<div class="grm-preset-item" onclick="applyGrimoirePreset('${esc(name)}')"><span>${esc(name)}</span></div>`;
  }
  menu.innerHTML = h;
  if (setValue && label) label.textContent = setValue;
}

/** 显示/隐藏预设菜单 */
function showPresetDropdown() {
  const menu = document.getElementById('grm_preset_menu');
  if (!menu) return;
  menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
}

/** 保存当前状态为预设 */
async function saveGrimoirePreset() {
  const name = prompt('输入预设名称：');
  if (!name) return;
  const r = await apiFetch('/api/grimoire/presets', {
    method: 'POST', body: JSON.stringify({ name })
  }, true);
  if (r && r.ok) {
    toast(`✅ 预设「${name}」已保存`);
    await loadGrimoirePresets(name);
  } else {
    toast('❌ 保存失败: ' + (r?.error || ''), 'error');
  }
}

/** 应用预设 */
async function applyGrimoirePreset(name) {
  if (!name) return;
  const r = await apiFetch('/api/grimoire/presets/apply', {
    method: 'POST', body: JSON.stringify({ name })
  }, true);
  if (r && r.ok) {
    _grimoirePins = r.pins || {};
    _grimoireRandPool = (r.pool || []).map(p => p.replace(/\\/g, '/'));
    renderGrimoireSources();
    updatePinnedBar();
    await loadGrimoirePresets(name);
    toast(`✅ 已加载预设「${name}」`);
  } else {
    toast('❌ 加载失败: ' + (r?.error || ''), 'error');
  }
}

/** 删除当前选中的预设 */
async function deleteGrimoirePreset() {
  const label = document.getElementById('grm_preset_label');
  const name = label ? label.textContent : '';
  if (!name || name === '预设') { toast('请先选择一个预设', 'error'); return; }
  if (!confirm(`确定删除预设「${name}」？`)) return;
  const r = await apiFetch('/api/grimoire/presets/delete', {
    method: 'POST', body: JSON.stringify({ name })
  }, true);
  if (r && r.ok) {
    toast(`已删除预设「${name}」`);
    loadGrimoirePresets();
  } else {
    toast('❌ 删除失败: ' + (r?.error || ''), 'error');
  }
}

/** 切换子分类到随机池 */
async function toggleRandPool(source) {
  const inPool = _grimoireRandPool && _grimoireRandPool.includes(source);
  const action = inPool ? 'remove' : 'add';
  const result = await apiFetch('/api/grimoire/rand-pool', {
    method: 'POST',
    body: JSON.stringify({ source, action })
  }, true);
  if (result && result.ok) {
    _grimoireRandPool = (result.pool || []).map(p => p.replace(/\\/g, '/'));
    renderGrimoireSources();
    updatePinnedBar();
    toast(inPool ? '🎲 已移出随机池' : '🎲 已加入随机池');
  } else {
    toast('❌ 操作失败: ' + (result?.error || ''), 'error');
  }
}

/** 将所有子分类加入随机池 */
async function selectAllRandom() {
  if (!_grimoireSources || _grimoireSources.length === 0) {
    toast('没有可选的子分类', 'error');
    return;
  }
  const allPaths = _grimoireSources.map(s => s.path.replace(/\\/g, '/'));
  const alreadyIn = new Set(_grimoireRandPool || []);
  const toAdd = allPaths.filter(p => !alreadyIn.has(p));
  if (toAdd.length === 0) {
    toast('所有子分类已在随机池中');
    return;
  }
  // v4.5.1: 批量一次往返（原先逐分类串行 N 次请求 + N 次全量配置落盘，非常卡）
  const result = await apiFetch('/api/grimoire/rand-pool', {
    method: 'POST',
    body: JSON.stringify({ action: 'add', sources: toAdd })
  }, true);
  if (result && result.ok) {
    _grimoireRandPool = (result.pool || []).map(p => p.replace(/\\/g, '/'));
    renderGrimoireSources();
    updatePinnedBar();
    updateRandomToggleBtn();
    toast(`✅ 已将 ${toAdd.length} 个子分类加入随机池`);
  } else {
    toast((result && result.error) || '操作失败', 'error');
  }
}

/** 全选/取消随机池切换 */
async function toggleAllRandom() {
  if (!_grimoireSources || _grimoireSources.length === 0) {
    toast('没有可选的子分类', 'error');
    return;
  }
  const allPaths = _grimoireSources.map(s => s.path.replace(/\\/g, '/'));
  const pool = _grimoireRandPool || [];
  const allInPool = allPaths.every(p => pool.includes(p));
  if (allInPool) {
    // v4.5.1: 批量一次往返
    const result = await apiFetch('/api/grimoire/rand-pool', {
      method: 'POST',
      body: JSON.stringify({ action: 'remove', sources: allPaths })
    }, true);
    if (result && result.ok) {
      _grimoireRandPool = (result.pool || []).map(p => p.replace(/\\/g, '/'));
      renderGrimoireSources();
      updatePinnedBar();
      updateRandomToggleBtn();
      toast(`已从随机池移除 ${allPaths.length} 个子分类`);
    } else {
      toast((result && result.error) || '操作失败', 'error');
    }
  } else {
    // 全部添加
    selectAllRandom();
  }
}

/** 更新随机池切换按钮的文字 */
function updateRandomToggleBtn() {
  const btn = document.getElementById('grm_toggle_rand_btn');
  const text = document.getElementById('grm_toggle_rand_text');
  if (!btn || !text || !_grimoireSources) return;
  const allPaths = _grimoireSources.map(s => s.path.replace(/\\/g, '/'));
  const pool = _grimoireRandPool || [];
  const allInPool = allPaths.every(p => pool.includes(p));
  text.textContent = allInPool ? '取消随机' : '全选随机';
  btn.classList.toggle('btn-primary', allInPool);
  btn.classList.toggle('btn-secondary', !allInPool);
}

/** 删除整个数据源 */
async function deleteGrimoireSource(path, displayName) {
  if (!confirm(`确定删除数据源「${displayName}」吗？\n此操作不可恢复！`)) return;
  const result = await apiFetch('/api/grimoire/source', {
    method: 'DELETE',
    body: JSON.stringify({ source: path })
  }, true);
  if (result && result.ok) {
    toast('✅ 数据源已删除');
    _grimoireCurrentSource = '';
    await loadGrimoireData();
  } else {
    toast('❌ 删除失败: ' + (result?.error || ''), 'error');
  }
}

async function showNewSourceModal() {
  const filename = prompt('请输入新数据源文件名（不需要.json）：');
  if (!filename) return;
  const result = await apiFetch('/api/grimoire/source', {
    method: 'POST',
    body: JSON.stringify({ filename })
  }, true);
  if (result && result.ok) {
    toast('✅ 数据源创建成功');
    await loadGrimoireData();
  } else {
    toast('❌ 创建失败: ' + (result?.error || ''), 'error');
  }
}

/** 切换单条选中 */
function toggleGrimoireItem(idx, checked) {
  if (checked) _grimoireSelectedIndices.add(idx);
  else _grimoireSelectedIndices.delete(idx);
  renderGrimoireData(_grimoireCurrentTotal || 0, _grimoireCurrentPage || 1);
}

/** 全选/取消全选 */
function toggleSelectAllGrimoire(checked) {
  _grimoireSelectedIndices.clear();
  if (checked) _grimoireCurrentData.forEach((item) => _grimoireSelectedIndices.add(item.index));
  renderGrimoireData(_grimoireCurrentTotal || 0, _grimoireCurrentPage || 1);
}

/** 批量删除 */
async function batchDeleteGrimoire() {
  if (!_grimoireCurrentSource) { toast('❌ 未选择数据源', 'error'); return; }
  const indices = Array.from(_grimoireSelectedIndices);
  if (indices.length === 0) return;
  if (!confirm(`确定删除选中的 ${indices.length} 条数据？`)) return;
  const result = await apiFetch('/api/grimoire/batch-delete', {
    method: 'POST',
    body: JSON.stringify({ source: _grimoireCurrentSource, indices })
  }, true);
  if (result && result.ok) {
    toast(`✅ 已删除 ${result.deleted || 0} 条`);
    _grimoireSelectedIndices.clear();
    await fetchGrimoireData();
    await loadGrimoireData();
  } else {
    toast('❌ 删除失败' + (result?.error ? ': ' + result.error : '（后端无响应）'), 'error');
  }
}

async function showGrimoireBatchModal() {
  // 显示当前数据源路径
  const label = document.getElementById('grm_batch_source_label');
  if (label) {
    const src = _grimoireCurrentSource || '';
    const srcName = src.replace(/\.json$/i, '').replace(/[/\\]/g, ' → ');
    label.textContent = srcName || '请先选择数据源';
  }
  document.getElementById('grm_batch_input').value = '';
  clearBatchFile();
  document.getElementById('batch_preview_section').style.display = 'none';
  document.getElementById('batch_import_count').textContent = '';
  showModal('grimoire_batch_modal');
}

/** Handle file selection from dropzone or file picker */
function handleBatchFile(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (e) => {
    const text = e.target.result;
    document.getElementById('grm_batch_input').value = text;
    document.getElementById('batch_file_name').textContent = file.name;
    document.getElementById('batch_file_info').style.display = 'flex';
    document.getElementById('batch_dropzone_text').style.display = 'none';
    document.getElementById('batch_dropzone_icon').setAttribute('href', '#icon-document');
    document.getElementById('batch_dropzone').classList.add('has-file');
    updateBatchPreview(text);
  };
  reader.readAsText(file);
  input.value = '';
}

/** Clear the selected file */
function clearBatchFile() {
  document.getElementById('batch_file_info').style.display = 'none';
  document.getElementById('batch_dropzone_text').style.display = '';
  document.getElementById('batch_dropzone_icon').setAttribute('href', '#icon-upload-cloud');
  document.getElementById('batch_dropzone').classList.remove('has-file');
  document.getElementById('batch_preview_section').style.display = 'none';
  document.getElementById('batch_import_count').textContent = '';
}

/** Parse JSON and show preview */
function updateBatchPreview(text) {
  const previewSection = document.getElementById('batch_preview_section');
  const previewList = document.getElementById('batch_preview_list');
  const countEl = document.getElementById('batch_preview_count');
  const trimmed = text.trim();
  if (!trimmed) { previewSection.style.display = 'none'; return; }
  let items = [];
  try {
    items = JSON.parse(trimmed);
    if (!Array.isArray(items)) throw new Error('Not array');
  } catch (_) {
    try {
      items = trimmed.split('\n').map(l => l.trim()).filter(l => l).map(l => JSON.parse(l));
    } catch (_) {
      previewSection.style.display = 'none';
      return;
    }
  }
  if (!items.length) { previewSection.style.display = 'none'; return; }
  countEl.textContent = items.length;
  previewList.innerHTML = items.slice(0, 30).map((item, i) =>
    `<div class="batch-import-preview-item">
      <span class="batch-import-preview-name">${esc(item.name || `#${i}`)}</span>
      <span class="batch-import-preview-tags">${esc((item.tags || '').slice(0, 60))}</span>
    </div>`
  ).join('');
  previewSection.style.display = '';
  document.getElementById('batch_import_count').innerHTML =
    `共 <strong>${items.length}</strong> 条 | 预览前 ${Math.min(30, items.length)} 条`;
}

async function executeGrimoireBatchImport() {
  const raw = document.getElementById('grm_batch_input').value.trim();
  if (!raw) { toast('❌ 请输入数据或上传文件', 'error'); return; }
  let items = [];
  try {
    items = JSON.parse(raw);
    if (!Array.isArray(items)) throw new Error('必须是数组');
  } catch (e) {
    try {
      const lines = raw.trim().split('\n').map(l => l.trim()).filter(l => l);
      items = lines.map(l => JSON.parse(l));
    } catch (e2) {
      toast('❌ JSON 格式错误', 'error');
      return;
    }
  }
  if (items.length === 0) { toast('❌ 没有有效数据', 'error'); return; }
  const btn = document.getElementById('btn_grm_batch_import');
  btn.disabled = true;
  btn.innerHTML = '<svg class="icon-sm" aria-hidden="true"><use href="#icon-refresh"/></svg> 导入中...';
  const result = await apiFetch('/api/grimoire/batch-import', {
    method: 'POST',
    body: JSON.stringify({ source: _grimoireCurrentSource, items })
  }, true);
  btn.disabled = false;
  btn.innerHTML = '<svg class="icon-sm" aria-hidden="true"><use href="#icon-upload"/></svg> 导入';
  if (result && result.ok) {
    toast(`✅ 成功导入 ${result.imported || 0} 条`);
    hideModal('grimoire_batch_modal');
    await fetchGrimoireData();
    await loadGrimoireData();
  } else {
    toast('❌ 导入失败: ' + (result?.error || ''), 'error');
  }
}

// ========================================================================
// 批量导入拖拽上传
// ========================================================================
function initBatchDropzone() {
  const dz = document.getElementById('batch_dropzone');
  if (!dz) { setTimeout(initBatchDropzone, 500); return; }
  dz.addEventListener('click', () => document.getElementById('batch_file_input').click());
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('dragover'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
  dz.addEventListener('drop', (e) => {
    e.preventDefault();
    dz.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file && file.name.endsWith('.json')) {
      const input = document.getElementById('batch_file_input');
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      handleBatchFile(input);
    } else {
      toast('❌ 仅支持 .json 文件', 'error');
    }
  });
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initBatchDropzone);
} else {
  initBatchDropzone();
}

// 底部导航激活状态
function updateActiveNav(id) {
  document.querySelectorAll('.bottom-nav .bn-item').forEach(el => {
    el.classList.toggle('active', el.id === id);
  });
}
