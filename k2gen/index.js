'use strict';
/* k2gen 入口：K2 人像提示词生成器 v12 随机组合引擎
 *
 * 快速上手：
 *   const { generate } = require('./k2gen');
 *   const r = generate({ seed: 42 });          // SFW 全随机
 *   const r2 = generate({ seed: 42, nsfw: true }); // NSFW（姿态+服装+人物强化）
 *   console.log(r2.text, r2.mode, r2.checks);
 *
 * 同 seed + 同参数 → 逐字相同的输出（可复现、可批量、可 A/B）。
 */
const { Engine } = require('./engine.js');

/**
 * 生成一条提示词
 * @param {object} opts
 * @param {number} [opts.seed]        RNG 种子（缺省随机）
 * @param {boolean} [opts.nsfw]       NSFW 总开关（=toggleNsfwMaster：姿态库+服装强化+人物强化）
 * @param {boolean} [opts.lingerie]   情趣内衣衣柜（需 nsfw）
 * @param {boolean} [opts.nude]       全裸（需 nsfw，与 lingerie 互斥）
 * @param {string} [opts.stylePreset] 设计风格预设名（如「法式优雅」「哥特暗黑」，见 D.STYLE_PRESETS）
 * @param {string} [opts.scene]       指定场景（如「温泉」）
 * @param {object} [opts.values]      指定字段值 {fieldId: value}。
 *                    语义 = 页面「手选 + 🔒锁定」：重掷不改动（页面里不锁定的手选值
 *                    会被「生成提示词」整体重掷，要保留必须 🔒，此参数等价该组合）
 * @param {object} [opts.custom]      自定义文本 {personCustom, nsfwCustom1, nsfwCustom2}
 * @param {boolean} [opts.autoTrim]   超 700 字自动精简（默认 true）
 * @param {object} [opts.settings]    完整设定对象（dumpSettingsObj 的产物）。
 *                    传入后与同 seed 一起完整重放（locked 从设定恢复，重掷序列逐位相同
 *                    → 输出与产生该设定的那次调用逐字一致），实现精确复现/微调
 * @param {boolean} [opts.randomize]  是否整体重掷（默认 true；false=只补全+组装，幂等）
 * @returns {{text:string, mode:'SFW'|'NSFW', checks:{F:[],S:[],n:number,fn:string}, trimmed:number, filled:number, seed:number, settings:object}}
 */
function generate(opts = {}){
  const e = new Engine({ seed: opts.seed, autoTrim: opts.autoTrim });
  if (opts.settings) e.loadSettingsObj(opts.settings);
  if (opts.nsfw) e.toggleNsfwMaster();
  if (opts.lingerie) e.setLingerieMode(true);
  if (opts.nude) e.setNudeMode(true);
  const setLocked = (id, val) => { e.set(id, val); e.lockField(id, true); };
  if (opts.stylePreset) setLocked('stylePreset', opts.stylePreset);
  if (opts.scene) setLocked('scene', opts.scene);
  if (opts.values) Object.keys(opts.values).forEach(id => setLocked(id, opts.values[id]));
  if (opts.custom) Object.keys(opts.custom).forEach(id => e.setCustom(id, opts.custom[id]));
  const r = e.generate({ randomize: opts.randomize !== false, autoTrim: opts.autoTrim !== undefined ? opts.autoTrim : true });
  r.settings = e.dumpSettingsObj(); /* 带上完整设定：下次传回 opts.settings 即可精确复现/微调 */
  return r;
}

/** 批量生成（独立种子，便于去重/筛选） */
function generateBatch(n, baseOpts = {}){
  const out = [];
  for (let i = 0; i < n; i++) {
    const seed = baseOpts.seed !== undefined ? (baseOpts.seed + i) >>> 0 : undefined;
    out.push(generate(Object.assign({}, baseOpts, { seed })));
  }
  return out;
}

module.exports = {
  generate,
  generateBatch,
  Engine,
  data: require('./engine.js').D,
  listStyles: () => Object.keys(require('./engine.js').D.STYLE_PRESETS),
  listScenes: () => require('./engine.js').D.OPT.scene.map(o => o.v),
  listFields: () => {
    const e = new Engine();
    return e.allFieldIds.map(id => ({ id, label: e.FIELD_LABEL[id] }));
  }
};
