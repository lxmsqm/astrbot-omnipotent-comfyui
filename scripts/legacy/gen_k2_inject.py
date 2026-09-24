# -*- coding: utf-8 -*-
"""把浏览器化的 k2gen 引擎 + k2Compose() 组句函数注入 webui.html，支持 K2 引擎组句输出提示词。

实现：
  1. 读 k2gen/engine.js，浏览器化（去 require / use strict，loadData 用 __K2_SRC__）
  2. 生成一段 <script>，含全局 __K2_ENGINE__（engine 源码字符串）+ buildK2Engine + k2Compose
  3. 读 webui.html，在 </body> 前插入该 script（幂等：若已含 K2_ENGINE 标记则跳过）
  4. 写回 webui.html

运行： python scripts/gen_k2_inject.py
"""
import os
import re
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "k2gen", "engine.js")
WEBUI = os.path.join(ROOT, "webui.html")
MARK = "/* __K2_INJECTED__ */"

with open(ENGINE, "r", encoding="utf-8") as f:
    js = f.read()

# 浏览器化
js = re.sub(r"^\s*'use strict';\s*\n?", "", js)
js = re.sub(r"const fs = require\('fs'\);\s*\n?", "", js)
js = re.sub(r"const path = require\('path'\);\s*\n?", "", js)
js = js.replace(
    "const src = fs.readFileSync(path.join(__dirname, 'data.js'), 'utf8');",
    "const src = __K2_SRC__;",
)
js = js.replace(
    "module.exports = { Engine, D, mulberry32, CUSTOM_FIELD_IDS, NEG_EFFECT_IDS, SPLIT_FIELDS, STYLE_CTRL_IDS, TRIM_ORDER, RANGE_FIELDS, UPPER_NUDE_PARTS, BAN_A, BAN_B, BAN_D };",
    "return { Engine, D, mulberry32, CUSTOM_FIELD_IDS, NEG_EFFECT_IDS, SPLIT_FIELDS, STYLE_CTRL_IDS, TRIM_ORDER, RANGE_FIELDS, UPPER_NUDE_PARTS, BAN_A, BAN_B, BAN_D };",
)

engine_literal = json.dumps(js, ensure_ascii=False)

script = f"""
<script>
{MARK}
/* ================= K2 引擎组句（k2Compose）：K2 模式随机/生成时排成中文成句 ================= */
var __K2_ENGINE__ = {engine_literal};
function buildK2Engine(src){{
  return new Function(
    'var __K2_SRC__ = ' + JSON.stringify(src) + ';\\n' +
    '__K2_SRC__ = __K2_SRC__.replace(/^\\\\uFEFF/, "");' +
    'return new Function("__K2_SRC__", ' + JSON.stringify(__K2_ENGINE__) + ')(__K2_SRC__);'
  )();
}}
/* 用 K2 引擎随机组句，生成完整中文提示词并回填到正面提示词输入框 */
async function k2Compose(){{
  try {{
    if (typeof buildK2Engine !== 'function') {{ toast('❌ K2 引擎未加载', 'error'); return ''; }}
    var res = await fetch('/api/k2gen/data').then(function(r){{ return r.json(); }}).catch(function(){{ return null; }});
    if (!res || !res.ok) {{ toast('❌ K2 词库加载失败', 'error'); return ''; }}
    var mod = buildK2Engine(res.source);
    var eng = new mod.Engine({{ autoTrim: true }});
    var r = eng.generate({{ randomize: true, autoTrim: true }});
    var ta = document.getElementById('prompt_textarea');
    if (ta) ta.value = r.text;
    toast('✅ K2 已组句并填入提示词（字数 ' + r.checks.n + '）');
    return r.text;
  }} catch (e) {{
    toast('❌ K2 组句失败: ' + e.message, 'error');
    return '';
  }}
}}
</script>
"""

with open(WEBUI, "r", encoding="utf-8") as f:
    html = f.read()

if MARK in html:
    print("webui.html 已含 K2 注入标记，跳过（如需重新注入请先移除标记块）")
else:
    html = html.replace("</body>", script + "\n</body>", 1)
    with open(WEBUI, "w", encoding="utf-8") as f:
        f.write(html)
    print("已注入 K2 引擎组句 script 到 webui.html")