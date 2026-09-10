'use strict';
/* k2gen 冒烟测试：1000 SFW + 1000 NSFW
 * 硬错（必须 0，= 页面「致命项」且可规避）：F1/F2/F3/F6/F8/S12/S13/S15/S16
 * F5 字数：SFW 必须 0 超 700（autoTrim 保证）；NSFW 页面同款 trim 存在残留
 *          （反应链等无 skip 钩子的项无法精简）→ NSFW F5 超 700 记参考值不计失败，
 *          与 HTML 原版行为一致（原版 NSFW 同样偶发 702–718 字）
 * 软告警（统计，页面同款提示）：S1/S3/S5/S9/S10/S17 等
 */
const k2 = require('../index.js');
const HARD = new Set(['F1', 'F2', 'F3', 'F6', 'F8', 'S12', 'S13', 'S15', 'S16']);

function run(n, base, label){
  let hard = 0, soft = 0, over = 0, lenSum = 0, trimmedCnt = 0, modeCnt = {};
  const softDetail = {};
  const hardIsSfw = !base.nsfw;
  for (let i = 0; i < n; i++) {
    const seed = (1000 + i) >>> 0;
    const r = k2.generate(Object.assign({ seed }, base));
    modeCnt[r.mode] = (modeCnt[r.mode] || 0) + 1;
    lenSum += r.checks.n;
    if (r.checks.n > 700) over++;
    if (r.trimmed > 0) trimmedCnt++;
    const all = r.checks.F.concat(r.checks.S);
    all.forEach(c => {
      if (!c.ok) {
        if (c.id === 'F5') { if (hardIsSfw) { hard++; if (hard <= 5) console.error('  HARD[' + label + ' seed=' + seed + '] F5: ' + c.note); } return; }
        if (HARD.has(c.id)) { hard++; if (hard <= 5) console.error('  HARD[' + label + ' seed=' + seed + '] ' + c.id + ': ' + c.note); }
        else { soft++; softDetail[c.id] = (softDetail[c.id] || 0) + 1; }
      }
    });
  }
  console.log('[' + label + '] n=' + n + ' 硬错=' + hard + ' 软告警=' + soft +
    ' 平均字数=' + Math.round(lenSum / n) + ' 超700=' + over + (base.nsfw ? '（NSFW参考值，与HTML原版一致）' : '（必须0）') +
    ' 触发精简=' + trimmedCnt +
    ' 模式=' + JSON.stringify(modeCnt) + (Object.keys(softDetail).length ? ' 软明细=' + JSON.stringify(softDetail) : ''));
  return hard;
}

let fail = 0;

/* 1) 冒烟主体 */
fail += run(1000, {}, 'SFW');
fail += run(1000, { nsfw: true }, 'NSFW');

/* 2) 同 seed 复现 */
const a = k2.generate({ seed: 42 });
const b = k2.generate({ seed: 42 });
const c = k2.generate({ seed: 43 });
const reproOk = a.text === b.text && a.text !== c.text;
console.log('[复现] 同seed逐字一致=' + (a.text === b.text) + ' 异seed不同=' + (a.text !== c.text));
if (!reproOk) fail++;

/* 3) NSFW 前置句 / 动作库存在 */
const ns = k2.generate({ seed: 7, nsfw: true });
const nsOk = /^人物半裸，|^全身赤裸/.test(ns.text);
console.log('[NSFW前置句] ' + (nsOk ? 'OK' : 'FAIL') + ' · 开头: ' + ns.text.slice(0, 30));
if (!nsOk) fail++;
const ng = k2.generate({ seed: 7, nsfw: true, lingerie: true });
const ngOk = ng.text.indexOf('情趣内衣') >= 0;
console.log('[情趣衣柜] ' + (ngOk ? 'OK' : 'FAIL'));
if (!ngOk) fail++;
const nn = k2.generate({ seed: 7, nsfw: true, nude: true });
const nnOk = nn.text.indexOf('全身赤裸') === 0;
console.log('[全裸] ' + (nnOk ? 'OK' : 'FAIL'));
if (!nnOk) fail++;

/* 4) 风格预设池生效（法式优雅 → 主件类别应在适配池内） */
const sp = k2.generate({ seed: 1, stylePreset: '法式优雅' });
const spOk = ['上下装'].indexOf(sp.settings.val.clothCat) >= 0;
console.log('[风格预设] 法式优雅→clothCat=' + sp.settings.val.clothCat + ' ' + (spOk ? 'OK' : 'FAIL'));
if (!spOk) fail++;

/* 5) 指定字段保留（锁定语义：手选值不被重掷） */
const pv = k2.generate({ seed: 1, values: { makeup: '御姐妆·玫瑰', scene: '温泉' } });
const pvOk = pv.settings.val.makeup === '御姐妆·玫瑰' && pv.settings.val.scene === '温泉' && pv.text.indexOf('温泉') >= 0;
console.log('[指定字段保留] ' + (pvOk ? 'OK' : 'FAIL'));
if (!pvOk) fail++;

/* 6) 本段不启用 */
const eo = new k2.Engine({ seed: 5 });
eo.sectionOff('camera', true);
const eor = eo.generate();
const eoOk = eor.text.indexOf('镜头') < 0;
console.log('[本段不启用 camera] ' + (eoOk ? 'OK' : 'FAIL'));
if (!eoOk) fail++;

/* 7) settings 往返 */
const rt = k2.generate({ seed: 9, nsfw: true });
const rt2 = k2.generate({ settings: rt.settings });
const rtOk = rt.text === rt2.text;
console.log('[settings往返复现] ' + (rtOk ? 'OK' : 'FAIL'));
if (!rtOk) fail++;

console.log(fail === 0 ? '\n=== SMOKE PASS ===' : '\n=== SMOKE FAIL (' + fail + ') ===');
process.exit(fail === 0 ? 0 : 1);
