# k2gen · K2 人像提示词生成器 v12 —— 随机组合引擎

从 `K2人像提示词生成器v12.html` 拆出的**纯逻辑 Node 模块**（无 DOM、可播种、可复现），
保留原页面的全部随机组合机制：随机/补全/锁定、风格预设联动池、智能匹配（场景环境打分）、
SFW/NSFW 双动作库、服装 SFW/NSFW 并排叠加、提示词组装、自检 S1–S17 / F1–F10、超 700 字自动精简。

## 文件

| 文件 | 说明 |
|---|---|
| `data.js` | 词库数据块（HTML 行 790–4344 **原样**复制，零改写：OPT 全词库 + CLOTH 9 类主件 + LINGERIE_ITEMS + POSES + SIM_POSES 1040 条 + SFW_POSES 360 条 + SCENE_ENV + SECTIONS + 风格预设/适配池） |
| `engine.js` | 引擎：状态模型、mulberry32 可播种 RNG、联动池/过滤器、随机/补全、buildPrompt、自检、generate |
| `index.js` | 入口 API：`generate()` / `generateBatch()` / `listStyles()` / `listScenes()` / `listFields()` |
| `cli.js` | 命令行 |
| `test/smoke.js` | 冒烟测试（1000 SFW + 1000 NSFW） |
| `K2提示词词库分类v12.json`（上级目录） | 全部提示词按 10 大分类打包的词库 JSON（含 NSFW），供查阅/喂模型 |

## 快速上手

```js
const { generate } = require('./k2gen');

// 1) 一条 SFW 全随机
const r = generate();
console.log(r.text);            // 提示词正文
console.log(r.checks.n);        // 字数
console.log(r.mode);            // 'SFW' | 'NSFW'

// 2) 同 seed 逐字复现
const a = generate({ seed: 42 });
const b = generate({ seed: 42 });
console.log(a.text === b.text); // true

// 3) NSFW（姿态交合模拟库 + 服装强化 + 人物细节）
const r3 = generate({ seed: 7, nsfw: true });
const r4 = generate({ seed: 7, nsfw: true, lingerie: true }); // 情趣衣柜
const r5 = generate({ seed: 7, nsfw: true, nude: true });     // 全裸

// 4) 指定部分字段，其余随机
const r6 = generate({
  seed: 1,
  stylePreset: '法式优雅',
  scene: '影棚',
  values: { makeup: '甜妹妆·蜜桃', hairLen: '及腰长发', emotion: '温柔回眸' },
  custom: { personCustom: '皮肤上有细微雀斑' }
});

// 5) 精确复现/微调：generate 返回的 settings 传回即可
const r7 = generate({ settings: r6.settings, values: { makeup: '御姐妆·玫瑰' } });

// 6) 批量
const batch = require('./k2gen').generateBatch(20, { nsfw: true, seed: 100 });
```

### generate(opts) 参数

| 参数 | 说明 |
|---|---|
| `seed` | RNG 种子（缺省随机）。同 seed + 同参数 = 逐字相同输出 |
| `nsfw` | NSFW 总开关（等价页面按钮：姿态切交合模拟库 + 服装强化开 + 人物强化开） |
| `lingerie` / `nude` | 情趣内衣衣柜 / 全裸（均需 `nsfw`，互斥） |
| `stylePreset` | 风格预设名（`--styles` 查看），选中后服装池自动收缩为该风格适配池 |
| `scene` | 指定场景（影响智能匹配的动作环境打分） |
| `values` | 指定字段值 `{fieldId: value}`，字段 id 用 `--fields` 查看 |
| `custom` | 自由文本 `{personCustom, nsfwCustom1, nsfwCustom2}` |
| `autoTrim` | 超 700 字按 TRIM_ORDER 精简次要选项（默认 true，= 页面复选框默认勾选） |
| `settings` | `dumpSettingsObj()` 产物（完整配方：字段值+开关+锁定+seed） |

### 返回

```
{ text, mode: 'SFW'|'NSFW', seed,
  trimmed: 精简跳过项数, filled: 本次补全字段数,
  checks: { F: [F1..F10], S: [S1..S17], n: 字数, fn: 模式 },
  settings: 完整设定（传回 generate({settings}) 可精确复现/微调） }
```

## 命令行

```bash
node cli.js -n 5                     # 随机 5 条 SFW
node cli.js --seed 42 --check        # 可复现 1 条 + 自检明细
node cli.js --nsfw --json            # NSFW 完整 JSON
node cli.js --style 哥特暗黑 --scene 夜店
node cli.js --set makeup=御姐妆·玫瑰 --set clothCat=汉服
node cli.js --json --save-out 配方.json     # 保存本次设定
node cli.js --settings 配方.json    # 载入配方复现
node cli.js --styles | --scenes | --fields  # 列出预设/场景/字段
```

## 与 HTML 行为的关系

1. **数据零改写**：`data.js` 是 HTML 词库区原样切片，`K2提示词词库分类v12.json` 与之同源。
2. **逻辑逐函数对齐**：随机/补全/锁定/联动池/智能匹配/buildPrompt/自检/autoTrim 与 HTML 同名函数
   逐段移植（含调用顺序——智能匹配与 S2 自检内部的随机抽条同样消耗 RNG）。
3. **可复现**：HTML 用 `Math.random` 无法复现；本引擎用 mulberry32(seed)，
   复现粒度 = 一次 `generate()` 的完整序列（同 seed 同参数逐字一致）。
4. **无界面副作用**：下拉池重建、按钮状态等 UI 行为省略，但其"取值结果"（冲突回落、
   眼神必选不留空、安全内裤备份还原、风格预设先清空再填入）全部保留。
5. 未移植：反推/参考图 API（`ref*`/`callLocalModel`/`buildReverseTask` 等）——属外部模型调用，
   非随机组合逻辑；`REVERSE_KERNEL` 等反推词库保留在 HTML 与词库 JSON 的说明范围内。

## 注意

- 词库含 NSFW 内容（原页面即为成人向生图工具，含用户授权记录）；请仅用于合法合规的成年人生成场景。
- `data.js` 为 CommonJS 环境可 require 的纯数据脚本（顶层 `var` 经 `new Function` 包一层导出，避免全局污染）。
- 更新 HTML 词库后：重新切片 `data.js`（行号以 `var OPT = {};` 起、`var sockUnlock` 止），
  重跑 `test/smoke.js` 即可。
