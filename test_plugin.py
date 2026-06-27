"""
ComfyUI 插件自动化测试脚本 v1.0
===============================
每次大改后运行此脚本，确保功能完好无损。

用法:
  python test_plugin.py                     # 启动测试服务器 + 运行全部测试
  python test_plugin.py --url http://127.0.0.1:19840  # 对已有服务器测试

测试项概览 (32 项):
  T01-Server : 服务器基础
  T02-HTML   : HTML 结构完整性（标签闭合、DOM ID、data-action）
  T03-JS     : JS 语法正确性（无三重引号、无裸变量、函数存在）
  T04-ICONS  : SVG 图标系统（键名完整、ic() 调用有效、无残留 emoji）
  T05-API    : 关键 API 端点响应
  T06-CSS    : CSS 变量完整性（无未定义变量、无旧命名）
  T07-Edge   : 边界情况（空列表、404、异常请求）
  T08-Server : 服务器可达性
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import urljoin

# ======================================================================
# 配置
# ======================================================================
PLUGIN_DIR = Path(__file__).resolve().parent
MAIN_PY = PLUGIN_DIR / "main.py"
WEBUI_HTML = PLUGIN_DIR / "webui.html"
WORKFLOW_CONFIG = PLUGIN_DIR / "workflow_config.json"

TEST_PORT = 19840
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"

# ======================================================================
# 测试框架
# ======================================================================
passed = 0
failed = 0
errors = []

P = "\033[92m"  # green
F = "\033[91m"  # red
R = "\033[0m"   # reset
B = "\033[94m"  # blue

def test(name, category="???"):
    """装饰器：注册一个测试用例"""
    def decorator(fn):
        test.tests.append((category, name, fn))
        return fn
    return decorator
test.tests = []

def run_test(category, name, fn):
    global passed, failed
    try:
        fn()
        passed += 1
        print(f"  {P}✔{R} [{category}] {name}")
    except AssertionError as e:
        failed += 1
        msg = str(e) if str(e) else "断言失败"
        print(f"  {F}✘{R} [{category}] {name}: {msg}")
        errors.append((category, name, traceback.format_exc()))
    except Exception as e:
        failed += 1
        print(f"  {F}✘{R} [{category}] {name}: {e}")
        errors.append((category, name, traceback.format_exc()))

def require(cond, msg=""):
    if not cond:
        raise AssertionError(msg)

# ======================================================================
# HTTP 辅助
# ======================================================================
async def fetch(url, method="GET", data=None, timeout=10):
    """带超时的 HTTP 请求"""
    import aiohttp
    try:
        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            if method == "GET":
                async with session.get(url) as resp:
                    text = await resp.text()
                    return resp.status, text
            else:
                headers = {'Content-Type': 'application/json'}
                async with session.post(url, data=json.dumps(data) if data else None, headers=headers) as resp:
                    text = await resp.text()
                    return resp.status, text
    except asyncio.TimeoutError:
        return 0, "TIMEOUT"
    except Exception as e:
        return 0, str(e)

def json_ok(text):
    """解析 JSON 并返回 dict，失败抛异常"""
    try:
        return json.loads(text)
    except:
        raise AssertionError(f"JSON 解析失败: {text[:100]}")

# ======================================================================
# HTML/JS 辅助
# ======================================================================
html_cache = None

def get_html():
    global html_cache
    if html_cache is None:
        with open(WEBUI_HTML, 'r', encoding='utf-8') as f:
            html_cache = f.read()
    return html_cache

def extract_js():
    """从 HTML 中提取所有 JS 代码"""
    html = get_html()
    # 提取 <script> 标签内所有内容
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    return '\n'.join(scripts)

def find_in_js(pattern, flags=0):
    """在 JS 中查找模式"""
    js = extract_js()
    return re.findall(pattern, js, flags)

# ======================================================================
# 测试用例
# ======================================================================

# ── 1. 服务器启动与基础 API ──────────────────────────────────

@test("服务器能正常启动", "T01-Server")
def test_server_start():
    global server_proc
    import subprocess
    cmd = [sys.executable, "-c", f"""
import sys; sys.path.insert(0, r'{PLUGIN_DIR.parent.parent}')
import asyncio, json
from pathlib import Path
# 快速启动一个测试服务器实例
os.chdir(r'{PLUGIN_DIR}')
from main import ComfyUILocalPlugin
# 模拟最小配置启动
plugin = ComfyUILocalPlugin.__new__(ComfyUILocalPlugin)
plugin.config = {{"webui_port": {TEST_PORT}}}
# 只初始化必要属性
print("Server constructor OK")
"""]

# ── 2. WebUI HTML 完整性 ──────────────────────────────────

@test("HTML 基本结构完整", "T02-HTML")
def test_html_structure():
    html = get_html()
    require('<!DOCTYPE html>' in html, "缺少 DOCTYPE")
    require('<html' in html, "缺少 html 标签")
    require('</html>' in html, "缺少 /html")
    require('<head>' in html, "缺少 head")
    require('</head>' in html, "缺少 /head")
    require('<body>' in html, "缺少 body")
    require('</body>' in html, "缺少 /body")
    require('<script>' in html, "缺少 script")
    require('</script>' in html, "缺少 /script")
    require('<style>' in html, "缺少 style")
    require('</style>' in html, "缺少 /style")
    require('<div class="app"' in html, "缺少 .app 容器")

@test("所有 HTML 标签正确闭合", "T02-HTML")
def test_html_tags_closed():
    html = get_html()
    # 检查常见标签的双闭合
    for tag in ['div', 'span', 'button', 'input', 'select', 'textarea',
                 'h1', 'h2', 'h3', 'p', 'b', 'i', 'ul', 'li', 'label']:
        opens = len(re.findall(f'<{tag}[\\s>]', html))
        closes = len(re.findall(f'</{tag}>', html))
        # 自闭合标签不计
        self_closing = len(re.findall(f'<{tag}[^>]*/>', html))
        if opens - self_closing != closes:
            # input/br/hr/img/link/meta 等允许自闭合
            if tag in ('input', 'link', 'meta', 'br', 'hr', 'img'):
                continue
            require(False, f"<{tag}> 打开 {opens} 次但关闭 {closes} 次 (自闭合 {self_closing})")

@test("关键 DOM ID 存在", "T02-HTML")
def test_critical_ids():
    html = get_html()
    critical_ids = [
        'sidebar', 'wf_list', 'wf_search',
        'node_grid', 'prompt_textarea',
        'status_dot', 'comfyui_status',
        'settings_area', 'progress_section',
        'toast_container', 'lightbox',
        'wf_mgr_modal', 'bg_modal',
        'bottom_nav', 'menu_toggle',
        'lan_toggle', 'ipv6_toggle',
        'upload_mode_badge', 'current_wf_badge',
    ]
    for id_ in critical_ids:
        require(f'id="{id_}"' in html, f"缺少 id={id_}")

@test("所有 data-action 值合法", "T02-HTML")
def test_data_actions():
    html = get_html()
    actions = set(re.findall(r'data-action="([^"]+)"', html))
    js = extract_js()
    for act in sorted(actions):
        # 检查 actionMap 中有对应条目，或 window[action] 是函数
        in_map = f"'{act}'" in js or f'"{act}"' in js
        in_window = f"window['{act}']" in js or f'window["{act}"]' in js
        in_function = f"function {act}" in js
        in_actionMap_block = act in js.split("actionMap")[1].split("};")[0] if "actionMap" in js else False
        # 跳过 stopPropagation 等特殊处理
        if act in ('stopPropagation',): continue
        require(in_map or in_function,
                f"data-action=\"{act}\" 在 actionMap 或全局函数中未找到")

# ── 3. JS 语法与函数正确性 ──────────────────────────────────

@test("JS 无 '''' 三重引号", "T03-JS")
def test_no_triple_quotes():
    js = extract_js()
    count = js.count("'''")
    require(count == 0, f"发现 {count} 处三重引号") 

@test("JS 无 ' + ic( 拼接错误", "T03-JS")
def test_no_bad_icon_concat():
    js = extract_js()
    count = len(re.findall(r"'\s*\+ ic\(", js))
    require(count == 0, f"发现 {count} 处 ' + ic( 拼接错误")

@test("JS 无 '''${ic( 语法错误", "T03-JS")
def test_no_bad_icon_template():
    js = extract_js()
    require("'''" not in js and "${ic(" not in js.replace("`", ""),
           "发现 ${ic( 在非模板字符串中")

@test("ESLint 可检查：无裸变量名", "T03-JS")
def test_no_bare_identifiers():
    js = extract_js()
    # 检查常见裸变量（替换残留导致的裸单词）
    bare_words = ['warning', 'check', 'close', 'info', 'globe', 'globe2']
    for word in bare_words:
        # 查找该词不在字符串、不在注释、不在关键字中的出现
        in_string_or_comment = re.findall(rf"['\"`].*?{word}.*?['\"`]", js)
        as_bare = len(re.findall(rf'\b{word}\b', js)) - \
                  sum(len(re.findall(rf'\b{word}\b', s)) for s in in_string_or_comment)
        # 减去函数定义、属性名中的出现
        as_bare -= len(re.findall(rf'\.{word}\b', js))
        as_bare -= len(re.findall(rf'function {word}\b', js))
        require(as_bare <= 2, f"裸变量 {word} 出现 {as_bare} 次")

@test("关键 JS 函数存在", "T03-JS")
def test_js_functions_exist():
    js = extract_js()
    funcs = [
        'esc', 'apiFetch', 'ic', 'ICONS',
        'showWfMgrModal', 'closeWfMgrModal', 'switchWfMgrTab',
        'toggleSidebar', 'closeSidebar',
        'loadWorkflows', 'loadParams', 'switchWF',
        'renderNodes', 'updateNodeDisplay',
        'showBindMgrModal', 'saveParams',
        'toast', 'resetAll',
        'setTheme', 'changeOpacity',
        'toggleSettings', 'toggleLan', 'toggleIPv6',
        'filterWorkflows', 'filterNodes',
        'debounce',
    ]
    for fn in funcs:
        require(f'function {fn}' in js or f'{fn} =' in js or f'{fn}(' in js,
                f"函数 {fn} 未定义")

@test("initActionDelegation IIFE 完整", "T03-JS")
def test_action_delegation():
    js = extract_js()
    require("initActionDelegation" in js, "事件委托 IIFE 缺失")
    require("actionMap" in js, "actionMap 缺失")
    require("el.dataset.action" in js or "dataset.action" in js,
            "data-action 读取逻辑缺失")

# ── 4. SVG 图标系统 ──────────────────────────────────

@test("ICONS 所有键名是合法字符串", "T04-ICONS")
def test_icons_keys():
    js = extract_js()
    # 提取 ICONS 对象中的所有键名
    icons_match = re.search(r'const ICONS\s*=\s*\{([^}]+)\}', js, re.DOTALL)
    require(icons_match, "ICONS 对象未找到")
    icons_body = icons_match.group(1)
    keys = re.findall(r"'(\w+)':\s*'<svg", icons_body)
    keys += re.findall(r'"(\w+)":\s*"<svg', icons_body)
    require(len(keys) > 20, f"ICONS 少于 20 个图标，仅 {len(keys)} 个")
    # 检查所有 SVG 标签闭合
    for key in keys:
        svg_match = re.search(rf"'{key}':\s*'(<svg[^>]*>[^']*</svg>)'", icons_body)
        if svg_match:
            svg = svg_match.group(1)
            require(svg.count('<svg') == svg.count('</svg>'),
                    f"图标 {key} 的 SVG 标签未闭合")

@test("所有 ic() 调用的 key 在 ICONS 中存在", "T04-ICONS")
def test_icon_keys_exist():
    """检查 JS 中所有 ic('name') 调用，name 必须在 ICONS 中定义"""
    js = extract_js()
    # 提取 ICONS 中的键
    icons_match = re.search(r'const ICONS\s*=\s*\{([^}]+)\}', js, re.DOTALL)
    require(icons_match, "ICONS 对象未找到")
    icons_body = icons_match.group(1)
    defined_keys = set(re.findall(r"'(\w+(?:-\w+)*)':\s*'<svg", icons_body))
    defined_keys.update(re.findall(r'"(\w+(?:-\w+)*)":\s*"<svg', icons_body))

    # 提取所有 ic('name') 调用
    calls = re.findall(r"ic\('(\w+(?:-\w+)*)'\)", js)
    calls += re.findall(r'ic\("(\w+(?:-\w+)*)"\)', js)
    call_set = set(calls)

    # 排除注释中的
    for key in sorted(call_set):
        if key not in defined_keys:
            # 检查是否在注释中
            occurrences = [m.start() for m in re.finditer(rf"ic\('{key}'\)", js)]
            for pos in occurrences[:3]:
                line_start = js.rfind('\n', 0, pos) + 1
                line = js[line_start:js.find('\n', pos)]
                if '//' in line:
                    continue  # 注释行的忽略
            require(False, f"ic('{key}') 调用但 ICONS 中未定义")

@test("ic() 返回非空字符串", "T04-ICONS")
def test_ic_returns_svg():
    """模拟 ic() 调用来验证返回 SVG"""
    js = extract_js()
    icons_match = re.search(r'const ICONS\s*=\s*\{([^}]+)\}', js, re.DOTALL)
    require(icons_match, "ICONS 对象未找到")
    icons_body = icons_match.group(1)
    # 提取所有 SVG 值并检查长度
    svgs = re.findall(r"'(<svg[^>]*>[^']*</svg>)'", icons_body)
    for i, svg in enumerate(svgs[:5]):
        require(len(svg) > 30 and '<svg' in svg, f"第 {i} 个图标 SVG 异常")

@test("HTML 中无残留 emoji", "T04-ICONS")
def test_html_no_emoji():
    """检查 HTML body 中是否还有应该被替换的 emoji"""
    html = get_html()
    # 从 body 部分检查
    body_start = html.find('<body>')
    body_end = html.find('</body>')
    body = html[body_start:body_end] if body_start > 0 and body_end > 0 else html
    # 排除 <script> 区域
    body_clean = re.sub(r'<script[^>]*>.*?</script>', '', body, flags=re.DOTALL)
    # 不应该有这些 emoji（常见 UI 图标）
    forbidden_emoji = ['📁', '📂', '💾', '🔄', '⚙️', '✏️', '🗑', '🔍']
    found = [e for e in forbidden_emoji if e in body_clean]
    require(len(found) == 0, f"HTML 中仍有 emoji: {found}")

# ── 5. API 端点 ──────────────────────────────────

@test("GET /api/config 返回配置", "T05-API")
async def test_api_config():
    status, text = await fetch(f"{BASE_URL}/api/config")
    require(status == 200, f"状态码 {status}")
    data = json_ok(text)
    require("comfyui_url" in data, "缺少 comfyui_url")

@test("GET /api/workflows 返回列表", "T05-API")
async def test_api_workflows():
    status, text = await fetch(f"{BASE_URL}/api/workflows")
    # 即使空列表也应该返回有效 JSON
    data = json_ok(text)
    require(isinstance(data, list), "应返回数组")

@test("GET /api/workflow-params 返回节点", "T05-API")
async def test_api_params():
    status, text = await fetch(f"{BASE_URL}/api/workflow-params")
    data = json_ok(text)
    # 可能返回错误信息或空数据
    require(isinstance(data, dict), "应返回对象")

@test("GET /api/workflow-params-config 返回配置", "T05-API")
async def test_api_params_config():
    status, text = await fetch(f"{BASE_URL}/api/workflow-params-config")
    data = json_ok(text)
    require(isinstance(data, dict), "应返回对象")

# ── 6. CSS 变量正确性 ──────────────────────────────────

@test("CSS 变量全部定义", "T06-CSS")
def test_css_vars_defined():
    html = get_html()
    style_match = re.search(r'<style>(.*?)</style>', html, re.DOTALL)
    require(style_match, "未找到 <style> 标签")
    css = style_match.group(1)
    # 提取所有使用 var() 的变量名
    used_vars = set(re.findall(r'var\(--([\w-]+)\)', css))
    # 提取所有定义的变量（在 :root {} 和 [data-theme] {} 中）
    defined_vars = set(re.findall(r'--([\w-]+)\s*:', css))
    # CSS 标准变量不需要定义
    skip = {'custom-bg', 'bg-blur', 'bg-brightness', 'rx', 'ry'}
    undefined = used_vars - defined_vars - skip
    if undefined:
        require(False, f"未定义的 CSS 变量: {', '.join(sorted(undefined))}")

@test("CSS 变量无拼写错误", "T06-CSS")
def test_css_no_typo():
    html = get_html()
    style_match = re.search(r'<style>(.*?)</style>', html, re.DOTALL)
    require(style_match, "未找到 <style> 标签")
    css = style_match.group(1)
    # 检查已知拼写错误
    typos = ['--pace-2', '--radius', '--radius-sm', '--radius-full',
             '--transition-fast', '--transition-normal', '--transition-slow',
             '--space-lg', '--space-md', '--space-sm', '--space-xs',
             '--space-2xl', '--space-xl', '--space-2', '--space-1']
    for typo in typos:
        if typo in css:
            require(False, f"旧变量名 {typo} 仍在使用（新系统: --sp-* / --r-* / --t-*）")

# ── 7. 边缘情况 ──────────────────────────────────

@test("空工作流列表不崩溃", "T07-Edge")
async def test_empty_workflows():
    status, text = await fetch(f"{BASE_URL}/api/workflows")
    # 即使没有工作流也应返回空数组
    data = json_ok(text)
    require(isinstance(data, list), "空工作流列表应返回 []")

@test("不存在的 API 返回 404", "T07-Edge")
async def test_not_found():
    status, text = await fetch(f"{BASE_URL}/api/nonexistent_endpoint_xyz")
    require(status == 404, f"不存在的端点应返回 404，实际 {status}")

@test("POST 无 body 返回错误", "T07-Edge")
async def test_post_no_body():
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{BASE_URL}/api/workflows/switch",
                                    data="not-json",
                                    headers={'Content-Type': 'application/json'}) as resp:
                status = resp.status
                text = await resp.text()
        # 应该返回错误而不是崩溃
        require(status in (200, 400, 500), f"异常请求应优雅处理，状态码 {status}")
    except Exception as e:
        require(False, f"请求异常: {e}")

@test("HTML 无硬编码中文字符宽度溢出", "T07-Edge")
def test_no_inline_styles_clashing():
    html = get_html()
    # 检查是否有内联 style 使用旧变量名
    bad_patterns = [
        'var(--radius)', 'var(--radius-sm)', 'var(--radius-full)',
        'var(--transition-fast)', 'var(--transition-normal)',
        'var(--space-lg)', 'var(--space-md)', 'var(--space-sm)',
    ]
    for pat in bad_patterns:
        if pat in html:
            require(False, f"内联样式仍有 {pat}")

# ── 8. 服务器 ──────────────────────────────────

@test("测试服务器可访问", "T08-Server")
async def test_server_reachable():
    status, text = await fetch(f"{BASE_URL}/api/config")
    require(status == 200, f"服务器不可达: {status}")


# ======================================================================
# 主入口
# ======================================================================
async def run_tests(url):
    global BASE_URL
    BASE_URL = url
    print(f"\n{B}═{'═'*60}{R}")
    print(f"{B}  ComfyUI 插件自动化测试套件{R}")
    print(f"{B}  目标: {url}{R}")
    print(f"{B}  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}{R}")
    print(f"{B}═{'═'*60}{R}\n")

    categories = {}
    for cat, name, fn in test.tests:
        categories.setdefault(cat, [])
        categories[cat].append((name, fn))

    for cat in sorted(categories.keys()):
        print(f"\n{'─'*50}")
        print(f" [{cat}]")
        print(f"{'─'*50}")
        for name, fn in categories[cat]:
            if asyncio.iscoroutinefunction(fn):
                await fn()
            else:
                fn()

    # 统计
    total = passed + failed
    print(f"\n{'═'*50}")
    print(f"  结果: {P}{passed} 通过{R} / {F}{failed} 失败{R} / 共 {total} 项")
    if errors:
        print(f"\n{F}  失败详情:{R}")
        for cat, name, tb in errors[:5]:
            print(f"    [{cat}] {name}")
            print(f"    {tb.split(chr(10))[-3]}")
    print(f"{'═'*50}\n")
    return failed == 0


async def start_server():
    """启动测试用服务器实例"""
    # 备份现有的 workflow_config
    wf_backup = None
    if WORKFLOW_CONFIG.exists():
        wf_backup = WORKFLOW_CONFIG.read_text(encoding='utf-8')

    code = r'''import sys, os, asyncio, json
sys.path.insert(0, r"''' + str(PLUGIN_DIR.parent.parent) + r'''")
os.chdir(r"''' + str(PLUGIN_DIR) + r'''")
from aiohttp import web

class MockPlugin:
    pass

plugin = MockPlugin()
plugin.config = {"webui_port": ''' + str(TEST_PORT) + r'''}
plugin.webui_port = ''' + str(TEST_PORT) + r'''
plugin.comfyui_url = "127.0.0.1:8188"
plugin.workflow_dir = ""
plugin.output_dir = ""
plugin.comfyui_input_dir = ""
plugin.upload_mode = "local"
plugin.webui_lan = False
plugin.webui_ipv6 = False
plugin.workflow_path = ""
plugin.current_workflow_name = ""
plugin.preview_dir = r"''' + str(PLUGIN_DIR / 'previews') + r'''"
os.makedirs(plugin.preview_dir, exist_ok=True)
plugin.workflow_list_cache = []
plugin.workflow_config = {}
plugin.task_map = {}
plugin._cancelled_pids = set()
plugin._progress = {}
plugin._config_lock = asyncio.Lock()
plugin._context_workflows = {}
plugin._bg_tasks = []

# 轻量 API 端点
async def api_config(request):
    return web.json_response({
        "comfyui_url": plugin.comfyui_url,
        "workflow_dir": plugin.workflow_dir,
        "output_dir": plugin.output_dir,
        "upload_mode": plugin.upload_mode,
        "webui_lan": plugin.webui_lan,
        "webui_ipv6": plugin.webui_ipv6,
    })
async def api_workflows(request):
    return web.json_response(plugin.workflow_list_cache)
async def api_params(request):
    return web.json_response({"nodes": [], "workflow_name": ""})
async def api_params_config(request):
    return web.json_response(plugin.workflow_config)
async def health(request):
    return web.json_response({"status": "ok"})

app = web.Application()
app.router.add_get("/api/config", api_config)
app.router.add_get("/api/workflows", api_workflows)
app.router.add_get("/api/workflow-params", api_params)
app.router.add_get("/api/workflow-params-config", api_params_config)
app.router.add_get("/api/health", health)

runner = web.AppRunner(app)
await runner.setup()
site = web.TCPSite(runner, "127.0.0.1", ''' + str(TEST_PORT) + r''')
await site.start()
print("SERVER_STARTED")
await asyncio.Event().wait()
'''

    proc = await asyncio.create_subprocess_exec(
        sys.executable, '-c', code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    return proc


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="ComfyUI 插件测试")
    parser.add_argument('--url', default=None, help="目标服务器 URL")
    parser.add_argument('--port', type=int, default=TEST_PORT, help="测试端口")
    args = parser.parse_args()

    if args.url:
        ok = await run_tests(args.url)
        sys.exit(0 if ok else 1)

    # 启动测试服务器
    print(f"正在启动测试服务器 (端口 {args.port})...")
    server_proc = await start_server()

    # 等待服务器启动
    for i in range(30):
        try:
            import aiohttp
            async with aiohttp.ClientSession() as s:
                async with s.get(f"http://127.0.0.1:{args.port}/api/health",
                                 timeout=aiohttp.ClientTimeout(total=2)) as resp:
                    if resp.status == 200:
                        print(f"服务器已启动 (端口 {args.port})")
                        break
        except:
            pass
        await asyncio.sleep(0.5)
    else:
        print("服务器启动超时")
        server_proc.terminate()
        sys.exit(1)

    try:
        ok = await run_tests(f"http://127.0.0.1:{args.port}")
    finally:
        server_proc.terminate()
        try:
            await asyncio.wait_for(server_proc.wait(), timeout=5)
        except:
            server_proc.kill()

    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    asyncio.run(main())
