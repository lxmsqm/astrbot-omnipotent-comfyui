#!/usr/bin/env node
'use strict';
/* k2gen CLI —— 命令行生成 K2 人像提示词
 *
 * 用法：
 *   node cli.js                          # 随机 1 条 SFW
 *   node cli.js -n 5                     # 随机 5 条（种子递增）
 *   node cli.js --seed 42                # 指定种子（可复现）
 *   node cli.js --nsfw                   # NSFW（姿态库+服装强化+人物强化）
 *   node cli.js --nsfw --lingerie        # NSFW + 情趣内衣衣柜
 *   node cli.js --nsfw --nude            # NSFW + 全裸
 *   node cli.js --style 法式优雅          # 指定风格预设
 *   node cli.js --scene 温泉             # 指定场景
 *   node cli.js --set makeup=甜妹妆·蜜桃 --set hairLen=及腰长发
 *   node cli.js --custom personCustom=皮肤上有细微雀斑
 *   node cli.js --json                   # 输出完整 JSON（text+checks+settings）
 *   node cli.js --settings my.json       # 载入已有设定文件（dumpSettingsObj 产物）
 *   node cli.js --save-out out.json      # 把本次设定保存为 .json（复现/微调用）
 *   node cli.js --check                  # 附加打印自检 S/F 明细
 *   node cli.js --styles                 # 列出风格预设
 *   node cli.js --scenes                 # 列出场景
 *   node cli.js --fields                 # 列出字段
 */
const fs = require('fs');
const path = require('path');
const k2 = require('./index.js');

function parseArgs(argv){
  const o = { n: 1, set: {}, custom: {}, flags: {} };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    switch (a) {
      case '-n': case '--count': o.n = parseInt(next(), 10) || 1; break;
      case '--seed': o.seed = parseInt(next(), 10); break;
      case '--nsfw': o.flags.nsfw = true; break;
      case '--lingerie': o.flags.lingerie = true; break;
      case '--nude': o.flags.nude = true; break;
      case '--style': case '--stylePreset': o.stylePreset = next(); break;
      case '--scene': o.scene = next(); break;
      case '--set': {
        const kv = next() || '';
        const idx = kv.indexOf('=');
        if (idx > 0) o.set[kv.slice(0, idx)] = kv.slice(idx + 1);
        break;
      }
      case '--custom': {
        const kv = next() || '';
        const idx = kv.indexOf('=');
        if (idx > 0) o.custom[kv.slice(0, idx)] = kv.slice(idx + 1);
        break;
      }
      case '--json': o.flags.json = true; break;
      case '--check': o.flags.check = true; break;
      case '--styles': o.flags.styles = true; break;
      case '--scenes': o.flags.scenes = true; break;
      case '--fields': o.flags.fields = true; break;
      case '--settings': o.settingsFile = next(); break;
      case '--save-out': o.saveOut = next(); break;
      case '-h': case '--help': o.flags.help = true; break;
      default:
        console.error('未知参数：' + a + '（--help 查看用法）');
        process.exit(1);
    }
  }
  return o;
}

function printChecks(c){
  const lines = [];
  lines.push('── 自检 ────────────────────────────────');
  c.F.forEach(x => lines.push((x.ok ? '✅' : '❌') + ' ' + x.id + ' ' + x.name + '：' + x.note));
  c.S.forEach(x => lines.push((x.ok ? '✅' : '⚠️') + ' ' + x.id + '：' + x.note));
  lines.push('模式 ' + c.fn + ' · 约 ' + c.n + ' 字');
  return lines.join('\n');
}

function main(){
  const o = parseArgs(process.argv.slice(2));
  if (o.flags.help) { console.log(fs.readFileSync(path.join(__dirname, 'README.md'), 'utf8')); return; }
  if (o.flags.styles) { k2.listStyles().forEach(s => console.log(s)); return; }
  if (o.flags.scenes) { k2.listScenes().forEach(s => console.log(s)); return; }
  if (o.flags.fields) { k2.listFields().forEach(f => console.log(f.id + '\t' + f.label)); return; }

  const opts = {
    n: o.n,
    seed: o.seed,
    nsfw: o.flags.nsfw,
    lingerie: o.flags.lingerie,
    nude: o.flags.nude,
    stylePreset: o.stylePreset,
    scene: o.scene,
    values: o.set,
    custom: o.custom
  };
  if (o.settingsFile) {
    opts.settings = JSON.parse(fs.readFileSync(o.settingsFile, 'utf8'));
  }

  const results = o.n > 1 ? k2.generateBatch(o.n, opts) : [k2.generate(opts)];

  if (o.flags.json) {
    const slim = results.map(r => ({
      seed: r.seed, mode: r.mode, trimmed: r.trimmed,
      text: r.text, checks: r.checks, settings: r.settings
    }));
    console.log(JSON.stringify(o.n > 1 ? slim : slim[0], null, 2));
  } else {
    results.forEach((r, i) => {
      if (o.n > 1) console.log('──────────── #' + (i + 1) + ' · ' + r.mode + ' · seed=' + r.seed + (r.trimmed ? ' · 已精简' + r.trimmed + '项' : '') + ' ────────────');
      console.log(r.text);
      if (o.flags.check) console.log(printChecks(r.checks));
      console.log('');
    });
  }

  if (o.saveOut) {
    fs.writeFileSync(o.saveOut, JSON.stringify(results[0].settings, null, 2), 'utf8');
    console.error('设定已保存：' + o.saveOut + '（复现：--settings ' + o.saveOut + '）');
  }
}

main();
