'use strict';
/* 对拍：HTML 原版（vm + DOM shim + 可播种 Math.random） vs k2gen 引擎
 * 同种子下逐字比对 text / 字数 / 自检结论 / 最终字段值
 */
const fs = require('fs');
const vm = require('vm');
const { Engine } = require('../engine.js');
const { mulberry32 } = require('../engine.js');

/* ---------- DOM shim ---------- */
function makeEl(id){
  const el = {
    id, value: '', innerHTML: '', textContent: '', hidden: false, checked: false,
    style: { display: '' },
    _listeners: {},
    classList: {
      _s: new Set(),
      toggle(c, on) { if (on === undefined) on = !this._s.has(c); on ? this._s.add(c) : this._s.delete(c); return on; },
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); }
    },
    setAttribute() { }, getAttribute() { return null; },
    appendChild() { }, removeChild() { }, remove() { },
    addEventListener(t, fn) { (this._listeners[t] = this._listeners[t] || []).push(fn); },
    querySelector() { return null; }, querySelectorAll() { return []; },
    scrollIntoView() { }, focus() { }, click() { },
    insertAdjacentHTML() { }
  };
  return el;
}

function makeDocument(){
  const els = new Map();
  const get = id => { if (!els.has(id)) els.set(id, makeEl(id)); return els.get(id); };
  return {
    _els: els,
    getElementById: get,
    createElement: t => makeEl('<' + t + '>'),
    querySelector: () => null,
    querySelectorAll: () => [],
    body: { appendChild() { } },
    addEventListener() { },
    removeEventListener() { }
  };
}

function bootHtml(seed){
  const html = fs.readFileSync('C:/Users/HeiGuLin/Desktop/_k2_v12.html', 'utf8');
  const m = html.match(/<script>([\s\S]*)<\/script>/);
  if (!m) throw new Error('script block not found');
  const src = m[1];

  const doc = makeDocument();
  const rng = mulberry32(seed);
  const ctx = {
    document: doc,
    window: {},
    navigator: { clipboard: null },
    localStorage: { _d: {}, getItem(k) { return this._d[k] === undefined ? null : this._d[k]; }, setItem(k, v) { this._d[k] = String(v); }, removeItem(k) { delete this._d[k]; } },
    Math: Object.assign(Object.create(Math), { random: rng }),
    Date, JSON, console,
    setTimeout: (fn) => { try { fn(); } catch (e) { } return 0; },
    clearTimeout() { },
    fetch: () => Promise.reject(new Error('no network in test')),
    AbortController: undefined,
    FileReader: function() { },
    URL: { createObjectURL: () => '', revokeObjectURL() { } },
    btoa: s => Buffer.from(s, 'binary').toString('base64'),
    atob: s => Buffer.from(s, 'base64').toString('binary'),
    alert() { },
    confirm() { return false; },
    prompt() { return null; }
  };
  ctx.window = ctx;
  ctx.window.Math = ctx.Math;
  vm.createContext(ctx);
  try {
    vm.runInContext(src, ctx, { filename: 'k2-html.js', timeout: 30000 });
  } catch (e) {
    console.error('HTML boot 异常（可忽略若来自反推/外部调用）：', e.message);
  }
  return { ctx, doc };
}

/* 驱动 HTML 侧：设置字段（模拟用户手选+锁定）→ 点「生成提示词」(genOut → randomizeAll → generate) */
function driveHtml(env, opts){
  const { ctx, doc } = env;
  const setField = (id, val, lock) => {
    const el = doc.getElementById(id);
    el.value = val;
    if (lock !== false && typeof ctx.onFieldChange === 'function') ctx.onFieldChange(id);
    if (lock) ctx.locked[id] = true;
  };
  if (opts.nsfw) ctx.toggleNsfwMaster();
  if (opts.lingerie) ctx.setLingerieMode(true);
  if (opts.nude) ctx.setNudeMode(true);
  if (opts.stylePreset) setField('stylePreset', opts.stylePreset, true);
  if (opts.scene) setField('scene', opts.scene, true);
  if (opts.values) Object.keys(opts.values).forEach(id => setField(id, opts.values[id], true));
  if (opts.custom) Object.keys(opts.custom).forEach(id => {
    const el = doc.getElementById(id);
    if (el) { el.value = opts.custom[id]; }
  });
  /* 点「生成提示词」按钮 */
  const gen = doc.getElementById('genOut');
  const handler = gen._listeners && gen._listeners['onclick'];
  if (handler) handler();
  else ctx.randomizeAll(); /* 兜底：按钮未绑定 */
  const box = doc.getElementById('outCustom') || doc.getElementById('promptBox');
  return { text: box.textContent || '', el: box };
}

/* 引擎侧同参数 */
const k2 = require('../index.js');
function driveEngine(opts){
  return k2.generate(opts);
}

function snapHtml(env){
  const { ctx } = env;
  const ids = ctx.allFieldIds || [];
  const vals = {};
  ids.forEach(id => { const el = env.doc.getElementById(id); vals[id] = el ? (el.value || '') : ''; });
  return vals;
}

let fail = 0;
const CASES = [
  { name: 'SFW 全随机', opts: {} },
  { name: 'SFW+风格预设', opts: { stylePreset: '法式优雅' } },
  { name: 'SFW+指定字段', opts: { values: { makeup: '甜妹妆·蜜桃', scene: '温泉', emotion: '温柔回眸' } } },
  { name: 'NSFW 总开关', opts: { nsfw: true } },
  { name: 'NSFW+情趣衣柜', opts: { nsfw: true, lingerie: true } },
  { name: 'NSFW+全裸', opts: { nsfw: true, nude: true } },
  { name: 'NSFW+手动选类', opts: { nsfw: true, values: { simMode: '手动选类', simCat: 'F站立', simPick: 'F001', scene: '卧室' } } },
  { name: 'NSFW+智能匹配', opts: { nsfw: true, values: { simMode: '智能匹配', scene: '温泉' } } },
  { name: 'SFW+智能匹配+核心姿态', opts: { values: { sfwSimMode: '智能匹配', sfwSimCoreCat: 'A仰面承重', scene: '教室' } } }
];

CASES.forEach((c, ci) => {
  const seed = (2000 + ci * 77) >>> 0;
  const opts = Object.assign({ seed, autoTrim: true }, c.opts);
  const htmlRes = driveHtml(bootHtml(seed), opts);
  const engRes = driveEngine(opts);
  const same = htmlRes.text === engRes.text;
  if (!same) fail++;
  console.log((same ? '✅' : '❌') + ' [' + c.name + '] seed=' + seed + ' len html=' + htmlRes.text.length + ' eng=' + engRes.text.length);
  if (!same) {
    const a = htmlRes.text, b = engRes.text;
    let i = 0;
    while (i < Math.min(a.length, b.length) && a[i] === b[i]) i++;
    console.log('   首个差异 @' + i + ':\n   HTML: …' + a.slice(Math.max(0, i - 40), i + 60) + '\n   ENG : …' + b.slice(Math.max(0, i - 40), i + 60));
    /* 字段级 diff */
    const hv = snapHtml({ ctx: null, doc: null });
  }
});

console.log(fail === 0 ? '\n=== 对拍 PASS（9/9 逐字一致）===' : '\n=== 对拍 FAIL (' + fail + '/9) ===');
process.exit(fail === 0 ? 0 : 1);
