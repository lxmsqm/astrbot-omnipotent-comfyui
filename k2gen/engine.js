'use strict';
/* =====================================================================
 * k2gen · K2 人像提示词生成器 v12 —— 随机组合引擎（无 DOM 纯逻辑版）
 *
 * 来源：E:/AIwork/插件/K2/K2人像提示词生成器v12.html
 *   data.js   词库数据块（HTML 行 790–4344 原样复制，零改写）
 *   engine.js 本文件：状态模型 + 可播种 RNG + 随机/补全/锁定/风格预设/
 *             智能匹配/提示词组装(buildPrompt)/自检(S1–S17, F1–F10)
 *
 * 与 HTML 的行为差异：
 *   1. 无界面：字段值存于 engine.val（'' = 未选·随机补全；自定义文本在 engine.custom）
 *   2. Math.random → mulberry32(seed)：同一 seed 的完整调用序列可逐字复现
 *   3. generate({randomize:true}) 等价 HTML「生成提示词」按钮
 *      （先解锁未锁字段整体重掷 → 补全 → 组装 → 自检 → 超 700 字按 TRIM_ORDER 精简）
 *   4. 调用顺序与 HTML 一致（buildPrompt 内部智能匹配、S2 自检的随机抽条
 *      都会消耗 RNG），因此复现粒度 = 整个 generate() 序列
 * ===================================================================== */
const fs = require('fs');
const path = require('path');

/* ---- 载入原始数据块（data.js 为 HTML 词库区原样文本） ---- */
function loadData(){
  const src = fs.readFileSync(path.join(__dirname, 'data.js'), 'utf8');
  const factory = new Function(src +
    '\n;return {norm,OPT,CLOTH,SHOE_POOL,LINGERIE_ITEMS,POSES,POSE_HAND,HAND_POOLS,EMOTION_EYE,' +
    'SIM_POSES,SIM_CATS,SIM_COUNT,SFW_POSES,SFW_CATS,SFW_COUNT,SCENE_ENV,SECTIONS,CLOTH_FIELDS,POSE_FIELDS,' +
    'STYLE_PRESETS,STYLE_POOLS,STYLE_COLOR_POOLS,SFW_CLOTH_IDS,LINGERIE_IDS,PANTY_IDS};');
  return factory();
}
const D = loadData();

/* ---- 可播种 RNG（mulberry32，确定性） ---- */
function mulberry32(a){
  return function(){
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

/* ---- 字段常量（位于 HTML 数据块之外：行 4369/5313/4214/4970/5450/5718/6152） ---- */
const CUSTOM_FIELD_IDS = ['personCustom', 'nsfwCustom1', 'nsfwCustom2'];
const NEG_EFFECT_IDS = ['smudge', 'styleTag', 'film', 'cine', 'imperf1', 'imperf2', 'tattoo', 'tattooPos'];
const SPLIT_FIELDS = ['outerwear', 'collarStyle', 'topLength', 'bottomStyle', 'splitColor'];
const STYLE_CTRL_IDS = ['clothMat', 'clothPattern', 'clothDeco', 'bottomLength', 'outerwear', 'collarStyle', 'topLength', 'bottomStyle', 'splitColor'];
const TRIM_ORDER = ['styleTag', 'cine', 'film', 'imperf2', 'imperf1', 'weather', 'prop',
  'nsfwChain',
  'hairAccSide', 'hairAccBack', 'hairAccFront', 'poseExtra', 'makeupDetail', 'nails', 'smudge',
  'clothDeco', 'collarStyle', 'topLength', 'bottomStyle', 'bottomLength', 'outerwear', 'clothPattern', 'ambient', 'device', 'tattoo',
  'clothLayer', 'clothMat', 'accessory', 'sockType', 'shoes'];
const RANGE_FIELDS = {};
D.SECTIONS.forEach(sec => (sec.fields || []).forEach(f => { if (f.range) RANGE_FIELDS[f.id] = f; }));

/* 半裸态上身裸露部位池（NSFW 强化且非全裸时随机取一组置于最开头） */
const UPPER_NUDE_PARTS = [
  '双乳与乳头大片裸露，胸口肌肤完全袒露',
  '乳房与乳头完全裸露，胸口肌肤无遮',
  '双乳与乳头裸露，胸口肌肤完全裸露',
  '双乳与乳头大片裸露，胸口肌肤一览无遗',
  '双乳与乳头裸露，胸口肌肤尽露'
];
/* 禁令总表（SFW 全扫；NSFW 按授权完全放行） */
const BAN_A = ['汗湿', '湿润', '油光', '水雾', '水珠', '湿身', '油亮肌肤', '陶瓷水嫩', '细腻如瓷', '通透光泽', '婴儿肌'];
const BAN_B = ['倾泻', '流淌', '如流水般', '如水流般', '如霜雾般', '如雾', '如露珠', '水波般', '涟漪般', '浸润', '泼洒', '飞溅', '水汽', '雾化', '弥漫如雾'];
const BAN_D = ['nipple', 'pussy', 'vagina', 'vulva', 'penis', 'cock', 'dick', 'anus', 'asshole', 'clitoris', 'labia', 'scrotum', 'testicles', 'areola'];

class Engine {
  constructor(opts = {}){
    /* ---- 字段注册（等价 HTML collectFieldIds） ---- */
    this.allFieldIds = [];
    this.FIELD_LABEL = {};
    const reg = f => { if (f && f.id) { this.allFieldIds.push(f.id); if (f.label) this.FIELD_LABEL[f.id] = f.label; } };
    D.SECTIONS.forEach(sec => (sec.fields || []).forEach(reg));
    D.CLOTH_FIELDS.sfw.concat(D.CLOTH_FIELDS.nsfw).forEach(reg);
    D.POSE_FIELDS.sfw.concat(D.POSE_FIELDS.nsfw).forEach(reg);
    this.FIELD_SEC = {};
    D.SECTIONS.forEach(sec => this.sectionFieldIds(sec).forEach(fid => { this.FIELD_SEC[fid] = sec.id; }));

    /* ---- 状态模型（'' = 未选·随机补全；滑条默认 1） ---- */
    this.val = {};
    this.allFieldIds.forEach(id => {
      this.val[id] = RANGE_FIELDS[id] ? String(RANGE_FIELDS[id].def !== undefined ? RANGE_FIELDS[id].def : '1') : '';
    });
    /* 负面效果字段初始默认「不启用」（等价 HTML makeSelect：NEG 字段建表即置不启用） */
    NEG_EFFECT_IDS.forEach(id => { this.val[id] = '不启用'; });
    this.custom = { personCustom: '', nsfwCustom1: '', nsfwCustom2: '' };
    this.customEnabled = { personCustom: true, nsfwCustom1: true, nsfwCustom2: true };
    this.userPicked = {};
    this.locked = {};
    this.secOff = {};
    this.secOffBackup = {};
    this.poseMode = 'SFW';
    this.nsfwClothOn = false;
    this.lingerieOn = false;
    this.nudeOn = false;
    this.sfwExposureOn = false;
    this.personNsfwOn = false;
    this.sockUnlock = false;
    this.lastMode = 'SFW';
    this.pantyBackup = {};
    this.autoTrim = opts.autoTrim !== false; /* HTML 复选框默认勾选 */

    /* ---- 可播种 RNG ---- */
    this.seed = (opts.seed !== undefined && opts.seed !== null) ? (opts.seed >>> 0) : null;
    if (this.seed === null) this.seed = (Date.now() ^ ((Math.random() * 0x7fffffff) | 0)) >>> 0;
    this.rng = mulberry32(this.seed);
  }

  /* ================= 随机基础 ================= */
  rand(){ return this.rng(); }
  randOf(arr){ return arr[Math.floor(this.rand() * arr.length)]; }
  v(id){ return this.val[id] || ''; }

  /* 用户设值（等价 UI 下拉 change，含 onFieldChange 联动） */
  set(id, val){
    if (CUSTOM_FIELD_IDS.indexOf(id) >= 0) { this.custom[id] = val == null ? '' : String(val); return; }
    if (val == null) val = '';
    if ((this.val[id] || '') === val) return;
    this.val[id] = val;
    this.userPicked[id] = true;
    this.onFieldChange(id);
  }
  setCustom(id, text){ this.custom[id] = text == null ? '' : String(text); }
  customEnable(id, on){ this.customEnabled[id] = !!on; }

  /* ================= 锁定机制 ================= */
  isLocked(id){ return !!this.locked[id]; }
  lockField(id, on){ this.locked[id] = !!on; }
  lockSection(secId, on){
    const sec = D.SECTIONS.find(s => s.id === secId);
    if (!sec) return;
    this.sectionFieldIds(sec).forEach(id => { this.locked[id] = !!on; });
  }
  allLockedNow(){
    return this.allFieldIds.every(id => CUSTOM_FIELD_IDS.indexOf(id) >= 0 || !!this.locked[id]);
  }

  /* ================= 段落 ================= */
  secActive(id){ return !this.secOff[id]; }
  fieldSection(id){ return this.FIELD_SEC[id]; }
  sectionFieldIds(sec){
    const ids = [];
    (sec.fields || []).forEach(f => { ids.push(f.id); });
    if (sec.id === 'cloth') D.CLOTH_FIELDS.sfw.concat(D.CLOTH_FIELDS.nsfw).forEach(f => { ids.push(f.id); });
    if (sec.id === 'pose') D.POSE_FIELDS.sfw.concat(D.POSE_FIELDS.nsfw).forEach(f => { ids.push(f.id); });
    return ids.filter(id => CUSTOM_FIELD_IDS.indexOf(id) < 0);
  }
  /* 本段不启用：整段排除于生成（服装⑦/姿态⑧ 不允许整段停用，与 HTML 一致） */
  sectionOff(secId, on){
    const sec = D.SECTIONS.find(s => s.id === secId);
    if (!sec || sec.id === 'cloth' || sec.id === 'pose') return;
    if (on === undefined) on = !this.secOff[secId];
    this.secOff[secId] = !!on;
    if (on) {
      const bak = {};
      (sec.fields || []).forEach(f => {
        if (f.custom || CUSTOM_FIELD_IDS.indexOf(f.id) >= 0) return;
        if (f.range) return;
        bak[f.id] = { v: this.val[f.id] || '', p: !!this.userPicked[f.id] };
        const list = f.list ? (D.OPT[f.list] || []) : [];
        const hasNone = list.some(x => (x && x.v) === '不启用');
        if (hasNone) this.val[f.id] = '不启用';
        else if (this.val[f.id] !== '') this.val[f.id] = '';
        this.userPicked[f.id] = false;
      });
      this.secOffBackup[secId] = bak;
    } else {
      const bak = this.secOffBackup[secId] || {};
      (sec.fields || []).forEach(f => {
        if (f.custom || CUSTOM_FIELD_IDS.indexOf(f.id) >= 0) return;
        if (f.range) return;
        if (f.id in bak) { this.val[f.id] = bak[f.id].v; this.userPicked[f.id] = bak[f.id].p; }
      });
      delete this.secOffBackup[secId];
      if (secId === 'expression') this.syncEye();
    }
  }

  /* ================= NSFW 开关体系（等价 HTML 各 toggle） ================= */
  nsfwClothEnabled(){ return this.nsfwClothOn; }
  personNsfwEnabled(){ return this.personNsfwOn; }
  nsfwAnyActive(){ return this.poseMode === 'NSFW' || this.nsfwClothOn || this.personNsfwOn; }
  setPoseMode(m){
    this.poseMode = (m === 'NSFW') ? 'NSFW' : 'SFW';
    this.lastMode = '';
  }
  /* 服装 NSFW 强化开关（含安全内裤备份/还原，等价 syncClothModeUI 的取值侧） */
  setNsfwCloth(on){
    const was = this.nsfwClothOn;
    this.nsfwClothOn = !!on;
    if (!this.nsfwClothOn) { this.lingerieOn = false; this.nudeOn = false; }
    const PANTY = D.PANTY_IDS;
    if (this.nsfwClothOn && !was) {
      PANTY.forEach(id => {
        if (this.val[id] && this.val[id] !== '不启用') {
          if (this.pantyBackup[id] === undefined) this.pantyBackup[id] = this.val[id];
          this.val[id] = '不启用'; this.userPicked[id] = false;
        }
      });
    } else if (!this.nsfwClothOn && was) {
      PANTY.forEach(id => {
        if (this.pantyBackup[id] !== undefined) {
          this.val[id] = this.pantyBackup[id];
          if (this.val[id]) this.userPicked[id] = true;
          this.pantyBackup[id] = undefined;
        }
      });
    }
    this.lastMode = '';
  }
  setLingerieMode(on){
    if (!this.nsfwClothOn) return;
    this.lingerieOn = !!on;
    if (this.lingerieOn) this.nudeOn = false;
    this.lastMode = '';
  }
  setNudeMode(on){
    if (!this.nsfwClothOn) return;
    this.nudeOn = !!on;
    if (this.nudeOn) this.lingerieOn = false;
    this.lastMode = '';
  }
  setSfwExposure(on){
    this.sfwExposureOn = !!on;
    if (!on) ['sfwExposure', 'clothTransparency'].forEach(id => { this.val[id] = ''; this.userPicked[id] = false; });
    this.lastMode = '';
  }
  setPersonNsfw(on){ this.personNsfwOn = !!on; this.lastMode = ''; }
  setSockUnlock(on){ this.sockUnlock = !!on; }
  /* 总开关：一键联动 姿态二选一 + 服装强化 + 人物强化 */
  toggleNsfwMaster(){
    if (this.nsfwAnyActive()) {
      this.setPoseMode('SFW');
      this.setNsfwCloth(false);
      this.personNsfwOn = false;
    } else {
      this.setNsfwCloth(true);
      this.setPoseMode('NSFW');
      this.personNsfwOn = true;
    }
  }

  /* ================= 模式判定 ================= */
  detectMode(){
    if (this.poseMode === 'NSFW') return 'NSFW';
    if (this.nsfwClothEnabled()) {
      if (this.lingerieOn || this.nudeOn) return 'NSFW';
      const s1 = this.v('nsfwState');
      if (s1 && s1 !== '不启用') return 'NSFW';
      const c1 = this.custom.nsfwCustom1, c2 = this.custom.nsfwCustom2;
      if ((c1 && this.customEnabled.nsfwCustom1 !== false) || (c2 && this.customEnabled.nsfwCustom2 !== false)) return 'NSFW';
    }
    return 'SFW';
  }
  isNSFW(){ return this.detectMode() === 'NSFW'; }
  /* 模式切换后重校验 NSFW 标记字段（等价 refreshModeUI 的取值侧） */
  syncModeDeps(){
    const m = this.detectMode();
    if (m !== this.lastMode) {
      this.lastMode = m;
      ['mouth', 'prop', 'styleTag', 'imperf1', 'imperf2'].forEach(id => {
        const list = D.OPT[id.replace(/\d+$/, '')] || D.OPT[id] || [];
        const cur = this.val[id] || '';
        if (cur && !list.some(o => o.v === cur && (m === 'NSFW' || !o.nsfw))) {
          this.val[id] = ''; this.userPicked[id] = false;
        }
      });
    }
  }

  /* ================= 字段参与判定 ================= */
  fieldActive(id){
    if (id === 'sfwSimMode') return this.poseMode === 'SFW';
    if (id === 'sfwSimCoreCat') return this.poseMode === 'SFW' && this.v('sfwSimMode') === '智能匹配';
    if (id === 'sfwSimCat' || id === 'sfwSimPick') return this.poseMode === 'SFW' && this.v('sfwSimMode') === '手动选类';
    if (id === 'simMode') return this.poseMode === 'NSFW';
    if (id === 'simCoreCat') return this.poseMode === 'NSFW' && this.v('simMode') === '智能匹配';
    if (id === 'simCat' || id === 'simPick') return this.poseMode === 'NSFW' && this.v('simMode') === '手动选类';
    if (id === 'nsfwChain') return this.poseMode === 'NSFW';
    if (id === 'nsfwState') return this.nsfwClothEnabled() && !this.nudeOn;
    if (id === 'nsfwLowerBody') return this.personNsfwEnabled();
    if (id === 'nsfwBreastDetail') return this.personNsfwEnabled();
    if (id === 'nsfwCustom1' || id === 'nsfwCustom2') return this.nsfwClothEnabled();
    if (D.LINGERIE_IDS.indexOf(id) >= 0) return this.nsfwClothEnabled() && this.lingerieOn;
    if (id === 'nsfwExposure') return this.nsfwClothEnabled() && !(this.lingerieOn || this.nudeOn);
    if (id === 'sfwExposure') return this.sfwExposureOn && !this.nsfwClothEnabled();
    if (id === 'clothTransparency') return this.sfwExposureOn && !this.nsfwClothEnabled();
    if (D.SFW_CLOTH_IDS.indexOf(id) >= 0) return !(this.nsfwClothEnabled() && (this.lingerieOn || this.nudeOn));
    return true;
  }

  /* ================= 词库反查 ================= */
  optT(list, val){
    if (!list) return val;
    for (let i = 0; i < list.length; i++) if (list[i].v === val) return list[i].t || list[i].v;
    return val;
  }
  flatClothItems(cat){ return D.CLOTH[cat].map(x => ({ v: cat + '｜' + x[0], t: x[1] })); }
  clothItemT(val){
    if (!val || val.indexOf('｜') < 0) return val;
    const cut = val.split('｜'); const cat = cut[0]; const name = cut.slice(1).join('｜');
    const arr = D.CLOTH[cat]; if (!arr) return val;
    for (let i = 0; i < arr.length; i++) if (arr[i][0] === name) return arr[i][1];
    return val;
  }
  findPose(name){
    for (const c in D.POSES) {
      for (let i = 0; i < D.POSES[c].length; i++)
        if (D.POSES[c][i][0] === name) return { cat: c, t: D.POSES[c][i][1], risk: !!D.POSES[c][i][2] };
    }
    return { cat: '', t: name, risk: false };
  }
}

/* =====================================================================
 * 联动池 / 过滤器 / 智能匹配 / 随机 / 补全
 * ===================================================================== */
Object.assign(Engine.prototype, {

  /* ---- 鞋履适配池（随主件类别，恒含「不启用」「赤脚」逃生口） ---- */
  shoePoolFor(cat){
    const names = D.SHOE_POOL[cat];
    if (!names) return D.OPT.shoes.slice();
    const pool = [{ v: '不启用', t: '' }];
    names.forEach(n => {
      const o = D.OPT.shoes.filter(x => x.v === n)[0];
      if (o) pool.push({ v: o.v, t: o.t });
    });
    if (!names.some(n => n === '赤脚')) {
      const bf = D.OPT.shoes.filter(x => x.v === '赤脚')[0];
      if (bf) pool.push({ v: bf.v, t: bf.t });
    }
    return pool;
  },
  isSplitCat(){ return this.v('clothCat') === '上下装'; },

  /* ---- 手部细节适配池（旧姿态体系残留接口；擦边动作库下恒全池） ---- */
  handPoolFor(pose){
    const tag = pose ? D.POSE_HAND[pose] : null;
    if (!tag || !D.HAND_POOLS[tag]) return D.OPT.poseExtra.slice();
    const pool = [{ v: '不启用', t: '' }];
    D.HAND_POOLS[tag].forEach(n => {
      const o = D.OPT.poseExtra.filter(x => x.v === n)[0];
      if (o) pool.push({ v: o.v, t: o.t });
    });
    return pool;
  },

  /* ---- 情绪×眼神 串联池 ---- */
  eyePoolFor(emotion){
    const names = emotion ? D.EMOTION_EYE[emotion] : null;
    if (!names || !names.length) return D.OPT.eye.slice();
    const pool = [];
    names.forEach(n => {
      const o = D.OPT.eye.filter(x => x.v === n)[0];
      if (o) pool.push({ v: o.v, t: o.t });
    });
    return pool;
  },
  /* 选情绪后：当前眼神不适配则回落池内第一项（眼神必选不留空） */
  syncEye(){
    const pool = this.eyePoolFor(this.v('emotion'));
    const cur = this.v('eye');
    if (cur && pool.some(o => o.v === cur)) return;
    this.val['eye'] = pool.length ? pool[0].v : '';
    this.userPicked['eye'] = true;
  },

  /* ---- 风格驱动池过滤（§7.5-F） ---- */
  currentStyle(){
    const s = this.v('stylePreset');
    return (s && s !== '不启用' && D.STYLE_POOLS[s]) ? s : null;
  },
  styleActive(){ return this.currentStyle() !== null; },
  randomClothCatPool(){
    const st = this.currentStyle();
    if (st) { const c = D.STYLE_POOLS[st].cat || []; if (c.length) return c; }
    return D.OPT.clothCat.map(o => o.v);
  },
  stylePoolFor(id){
    const st = this.currentStyle();
    if (!st) return null;
    if (id === 'splitColor') return D.STYLE_COLOR_POOLS[st] || null;
    if (id === 'sockType' || id === 'sockLen' || id === 'sockColor' || id === 'sockOpacity') {
      if (this.sockUnlock) return null;
      const k = (id === 'sockType') ? 'sock' : id;
      return D.STYLE_POOLS[st][k] || null;
    }
    const key = D_STYLE_FIELD_KEY[id]; if (!key) return null;
    return D.STYLE_POOLS[st][key] || null;
  },
  filterStylePool(list, id){
    const allow = this.stylePoolFor(id);
    if (!allow) return list;
    return list.filter(o => allow.indexOf(o.v) >= 0);
  },

  /* ---- 情趣内衣 ---- */
  lingerieCatKey(val){
    for (let i = 0; i < D.OPT.lingerieCat.length; i++) if (D.OPT.lingerieCat[i].v === val) return D.OPT.lingerieCat[i].t || val;
    return val;
  },

  /* ---- 智能匹配（动作库）：剔除核心同类 → 各类抽 1 → 场景环境关键词打分 → 取最高（平局随机）；全 0 分兜底含「地」 ---- */
  simModeActive(){ const m = this.v('simMode'); return m && m !== '' && m !== '不启用'; },
  coreSimCat(){
    const sc = this.v('simCoreCat'); if (!sc || sc === '不启用') return null;
    return sc.charAt(0);
  },
  simPickText(){
    const p = this.v('simPick'); if (!p) return '';
    const item = (D.SIM_POSES[p.charAt(0)] || []).filter(x => x.id === p)[0];
    return item ? item.t : '';
  },
  smartSimPick(){
    const env = D.SCENE_ENV[this.v('scene')] || D.SCENE_ENV['__default'];
    const core = this.coreSimCat();
    const cats = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J'].filter(c => c !== core);
    const cands = cats.map(c => {
      const arr = D.SIM_POSES[c] || [];
      return arr[Math.floor(this.rand() * arr.length)];
    }).filter(Boolean);
    cands.forEach(it => { it._score = env.filter(kw => it.t.indexOf(kw) >= 0).length; });
    cands.sort((a, b) => b._score - a._score);
    if (cands[0]._score > 0) {
      const best = cands.filter(it => it._score === cands[0]._score);
      return best[Math.floor(this.rand() * best.length)].t;
    }
    const ground = cands.filter(it => it.t.indexOf('地') >= 0);
    if (ground.length) return ground[Math.floor(this.rand() * ground.length)].t;
    return cands[Math.floor(this.rand() * cands.length)].t;
  },

  /* ---- SFW 擦边动作库（同构） ---- */
  sfwSimModeActive(){ const m = this.v('sfwSimMode'); return m && m !== '' && m !== '不启用'; },
  sfwCoreSimCat(){
    const sc = this.v('sfwSimCoreCat'); if (!sc || sc === '不启用') return null;
    return sc.charAt(0);
  },
  sfwSimPickText(){
    const p = this.v('sfwSimPick'); if (!p) return '';
    const item = (D.SFW_POSES[p.charAt(0)] || []).filter(x => x.id === p)[0];
    return item ? item.t : '';
  },
  smartSfwPick(){
    const env = D.SCENE_ENV[this.v('scene')] || D.SCENE_ENV['__default'];
    const core = this.sfwCoreSimCat();
    const cats = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J'].filter(c => c !== core);
    const cands = cats.map(c => {
      const arr = D.SFW_POSES[c] || [];
      return arr[Math.floor(this.rand() * arr.length)];
    }).filter(Boolean);
    cands.forEach(it => { it._score = env.filter(kw => it.t.indexOf(kw) >= 0).length; });
    cands.sort((a, b) => b._score - a._score);
    if (cands[0]._score > 0) {
      const best = cands.filter(it => it._score === cands[0]._score);
      return best[Math.floor(this.rand() * best.length)].t;
    }
    const ground = cands.filter(it => it.t.indexOf('地') >= 0);
    if (ground.length) return ground[Math.floor(this.rand() * ground.length)].t;
    return cands[Math.floor(this.rand() * cands.length)].t;
  },

  /* ---- S12 服装细节连贯（颜色/材质族归一；随机规避与自检共用） ---- */
  itemColorFam12(it){ for (const k in D_ITEM_C12) { if (it.indexOf(k) >= 0) return D_ITEM_C12[k]; } return null; },
  itemMatFam12(it){ for (const k in D_MAT_FAM12) { if (it.indexOf(k) >= 0) return D_MAT_FAM12[k]; } return null; },
  matFam12(mt){ for (const k in D_MAT_FAM12) { if (mt.indexOf(k) >= 0) return D_MAT_FAM12[k]; } return null; },
  itemEmbedsMainMat(it){ return !this.itemMatFam12(it) && EMBED_MAIN_MAT_RE.test(this.clothItemT(it) || ''); },
  splitTopColor12(sc){ const seg = (sc || '').split('·').pop() || sc; return seg.split('下')[0].replace('上', ''); },
  s12FilterList(fid, arr){
    if (fid !== 'clothMat' && fid !== 'splitColor') return arr;
    const it = this.v('clothItem') || '';
    if (fid === 'clothMat' && this.itemEmbedsMainMat(it)) return [{ v: '不启用', t: '不启用' }];
    if (!this.isSplitCat()) return arr;
    let out;
    if (fid === 'clothMat') {
      const mFam = this.itemMatFam12(it);
      if (!mFam) return arr;
      out = arr.filter(o => { const mf = this.matFam12(o.v); return !mf || mf === mFam; });
    } else {
      const cFam = this.itemColorFam12(it);
      if (!cFam) return arr;
      out = arr.filter(o => { const tc = this.splitTopColor12(o.v); return !tc || tc.indexOf(cFam) >= 0; });
    }
    return out.length ? out : [{ v: '不启用', t: '不启用' }];
  },

  /* ---- 色调×天气 / 场景×色调 / 妆容×糊妆 / 袜子 / 情绪×口型 兼容过滤 ---- */
  weatherToneFilter(list){
    const bad = D_WEATHER_TONE_FILTER[this.v('colorTone')];
    if (!bad) return list;
    return list.filter(o => !bad[o.v]);
  },
  weatherToneConflict(){
    const tn = this.v('colorTone'), we = this.v('weather');
    return (tn && we && we !== '不启用' && D_WEATHER_TONE_FILTER[tn] && D_WEATHER_TONE_FILTER[tn][we]) ? we : null;
  },
  sceneToneFilter(list){
    const bad = D_SCENE_TONE_FILTER[this.v('colorTone')];
    if (!bad) return list;
    return list.filter(o => !bad[o.v]);
  },
  sceneToneConflict(){
    const tn = this.v('colorTone'), sc = this.v('scene');
    return (tn && sc && sc !== '不启用' && D_SCENE_TONE_FILTER[tn] && D_SCENE_TONE_FILTER[tn][sc]) ? sc : null;
  },
  smudgeFilter(list){
    if (this.v('makeup') === '素颜') return [{ v: '不启用', t: '不启用' }];
    return list;
  },
  sockLenFilter(list){
    if (D_SOCK_LEN_FIX[this.v('sockType')]) return [{ v: '不启用', t: '不启用' }];
    return list;
  },
  sockTypeFilter(list){
    if (this.v('sockLen') !== '连裤') return list;
    const out = list.filter(o => !D_SOCK_LONG_FOOT_TYPES[o.v]);
    return out.length ? out : [{ v: '不启用', t: '不启用' }];
  },
  sockLenTypeConflict(){
    const st = this.v('sockType'), sl = this.v('sockLen');
    if (!st || st === '不启用' || !sl || sl === '不启用') return null;
    if (D_SOCK_LEN_FIX[st]) return '类型「' + st + '」已自带长度语义，长度字段应置「不启用」';
    if (sl === '连裤' && D_SOCK_LONG_FOOT_TYPES[st]) return '长度「连裤」为整腿袜，与分段袜「' + st + '」不同类';
    return null;
  },
  mouthFilter(list){
    const ban = D_EMOTION_MOUTH_BAN[this.v('emotion')];
    if (!ban) return list;
    const out = list.filter(o => !ban[o.v]);
    return out.length ? out : [{ v: '不启用', t: '不启用' }];
  },
  mouthEmotionConflict(){
    const ban = D_EMOTION_MOUTH_BAN[this.v('emotion')], mt = this.v('mouth');
    if (!ban || !mt || mt === '不启用') return null;
    return ban[mt] ? '情绪「' + this.v('emotion') + '」与口型「' + mt + '」矛盾' : null;
  },

  /* ---- 字段联动（等价 onFieldChange 取值侧） ---- */
  onFieldChange(id){
    if (id === 'clothCat') this.userPicked['clothItem'] = false;
    if (id === 'sfwSimMode') { /* 隐藏侧字段值保留，fieldActive 判定即可 */ }
    if (id === 'sfwSimCat') this.userPicked['sfwSimPick'] = false;
    if (id === 'emotion') this.syncEye();
    if (id === 'lingerieCat') this.userPicked['lingerieItem'] = false;
    if (id === 'simMode') { /* 同上 */ }
    if (id === 'simCat') this.userPicked['simPick'] = false;
    if (id === 'stylePreset') {
      const p = D.STYLE_PRESETS[this.v('stylePreset')];
      if (p) {
        /* 上下连贯：先清空预设未覆盖的服装字段旧值（保留锁定与自定义），再整体填入预设 */
        D.SFW_CLOTH_IDS.forEach(fid => {
          if (fid === 'stylePreset' || (fid in p)) return;
          if (!this.locked[fid]) { this.val[fid] = ''; this.userPicked[fid] = false; }
        });
        Object.keys(p).forEach(k => {
          if (this.allFieldIds.indexOf(k) >= 0) { this.val[k] = p[k]; this.userPicked[k] = true; }
        });
      }
    }
  },

  /* ---- 单字段随机（等价 randomizeField；''=未选 由 fillBlanks 补全，这里直接掷值） ---- */
  randomizeField(f){
    if (CUSTOM_FIELD_IDS.indexOf(f.id) >= 0) return;
    if (this.locked[f.id]) return;
    if (this.secOff[this.fieldSection(f.id)]) return;
    if (!this.fieldActive(f.id)) return;
    if (SPLIT_FIELDS.indexOf(f.id) >= 0 && !this.isSplitCat()) return;
    if (f.id === 'bottomLength' && !this.isSplitCat()) return;
    if (NEG_EFFECT_IDS.indexOf(f.id) >= 0) {
      this.val[f.id] = '不启用'; this.userPicked[f.id] = false;
      return;
    }
    if (f.id === 'simCoreCat') {
      this.val[f.id] = '不启用'; this.userPicked[f.id] = false;
      return;
    }
    if (this.nsfwClothEnabled() && D.PANTY_IDS.indexOf(f.id) >= 0) {
      this.val[f.id] = '不启用'; this.userPicked[f.id] = false;
      return;
    }
    if (f.range) {
      const rmin = parseFloat(f.min || 1), rmax = parseFloat(f.max || 2), rstep = parseFloat(f.step || 0.05);
      const rk = Math.floor(this.rand() * (Math.round((rmax - rmin) / rstep) + 1));
      this.val[f.id] = (rmin + rk * rstep).toFixed(2);
      this.userPicked[f.id] = true;
      return;
    }
    let list = D.OPT[f.list] || [];
    if (f.list === 'clothItem') { const cat = this.v('clothCat') || '上下装'; list = this.flatClothItems(cat); }
    if (f.list === 'shoes') { list = this.shoePoolFor(this.v('clothCat') || '上下装'); }
    if (f.list === 'poseExtra') { list = this.handPoolFor(this.v('pose')); }
    if (f.list === 'eye') { list = this.eyePoolFor(this.v('emotion')); }
    if (f.list === 'pose') { const pc = this.v('poseCat') || '静态姿势'; list = D.POSES[pc].map(x => ({ v: x[0], t: x[1] })); }
    if (f.list === 'lingerieItem') { const lck = this.lingerieCatKey(this.v('lingerieCat')); list = (D.LINGERIE_ITEMS[lck] || []).map(x => ({ v: x[1], t: x[1] })); }
    if (f.list === 'simPick') { const sc = this.v('simCat') || ''; const key = sc ? sc.charAt(0) : ''; list = (D.SIM_POSES[key] || []).map(x => ({ v: x.id, t: x.t })); }
    if (f.list === 'sfwSimPick') { const ssc = this.v('sfwSimCat') || ''; const skey = ssc ? ssc.charAt(0) : ''; list = (D.SFW_POSES[skey] || []).map(x => ({ v: x.id, t: x.t })); }
    if (f.id === 'sfwSimCoreCat') { this.val[f.id] = '不启用'; this.userPicked[f.id] = false; return; }
    if (f.list === 'clothCat' || f.list === 'poseCat') return; /* 主类别由主项随机带动 */
    if (f.list === 'weather') list = this.weatherToneFilter(list);
    if (f.list === 'scene') list = this.sceneToneFilter(list);
    list = this.filterStylePool(list, f.id);
    let arr = list.filter(o => (this.isNSFW() || !o.nsfw) && o.v !== '不启用');
    if (f.id === 'smudge') arr = this.smudgeFilter(arr);
    arr = this.s12FilterList(f.id, arr);
    if (f.id === 'sockLen') arr = this.sockLenFilter(arr);
    if (f.id === 'sockType') arr = this.sockTypeFilter(arr);
    if (f.id === 'mouth') arr = this.mouthFilter(arr);
    if (!arr.length) return;
    this.val[f.id] = arr[Math.floor(this.rand() * arr.length)].v;
    this.userPicked[f.id] = true;
  },

  /* ---- 整段随机 / 全部随机 ---- */
  randomizeSection(sec){
    if (sec.id === 'cloth') {
      const clothHidden = this.nsfwClothEnabled() && (this.lingerieOn || this.nudeOn);
      if (!clothHidden && !this.locked['clothCat']) {
        const cats = this.randomClothCatPool();
        this.val['clothCat'] = cats[Math.floor(this.rand() * cats.length)];
        this.userPicked['clothCat'] = true;
      }
      D.CLOTH_FIELDS.sfw.concat(D.CLOTH_FIELDS.nsfw).forEach(f => this.randomizeField(f));
    } else if (sec.id === 'pose') {
      if (this.poseMode === 'SFW' && !this.locked['sfwSimMode']) {
        const pcs = D.OPT.sfwSimMode;
        this.val['sfwSimMode'] = pcs[Math.floor(this.rand() * pcs.length)].v;
        this.userPicked['sfwSimMode'] = true;
      }
      D.POSE_FIELDS.sfw.concat(D.POSE_FIELDS.nsfw).forEach(f => this.randomizeField(f));
    } else {
      sec.fields.forEach(f => this.randomizeField(f));
    }
  },
  randomizeAll(){
    D.SECTIONS.forEach(sec => {
      if (sec.id === 'cloth') {
        const clothHidden = this.nsfwClothEnabled() && (this.lingerieOn || this.nudeOn);
        if (!clothHidden && !this.locked['clothCat']) {
          const cats = this.randomClothCatPool();
          this.val['clothCat'] = cats[Math.floor(this.rand() * cats.length)];
          this.userPicked['clothCat'] = true;
        }
        D.CLOTH_FIELDS.sfw.concat(D.CLOTH_FIELDS.nsfw).forEach(f => this.randomizeField(f));
      } else if (sec.id === 'pose') {
        if (this.poseMode === 'SFW' && !this.locked['sfwSimMode']) {
          const pcs = D.OPT.sfwSimMode;
          this.val['sfwSimMode'] = pcs[Math.floor(this.rand() * pcs.length)].v;
          this.userPicked['sfwSimMode'] = true;
        }
        D.POSE_FIELDS.sfw.concat(D.POSE_FIELDS.nsfw).forEach(f => this.randomizeField(f));
      } else {
        sec.fields.forEach(f => this.randomizeField(f));
      }
    });
  },
  /* 解锁并全部随机（页头按钮） */
  unlockAndRandomizeAll(){
    this.allFieldIds.forEach(id => { if (CUSTOM_FIELD_IDS.indexOf(id) < 0) this.locked[id] = false; });
    this.randomizeAll();
  },

  /* ---- 补全（fillBlanks）：只补空未选项，已选值即事实来源 ---- */
  randomValueFor(id){
    if (NEG_EFFECT_IDS.indexOf(id) >= 0) return '不启用';
    if (id === 'simCoreCat') return '不启用';
    if (this.nsfwClothEnabled() && D.PANTY_IDS.indexOf(id) >= 0) return '不启用';
    if (id === 'clothItem') {
      const cat = this.v('clothCat') || '上下装';
      return this.randOf(this.flatClothItems(cat)).v;
    }
    if (id === 'pose') {
      const pc = this.v('poseCat') || '静态姿势';
      return this.randOf(D.POSES[pc].map(x => ({ v: x[0], t: x[1] }))).v;
    }
    if (id === 'sfwSimPick') {
      const ssck = this.v('sfwSimCat') || ''; const sskey = ssck ? ssck.charAt(0) : '';
      const ssl = (D.SFW_POSES[sskey] || []).map(x => ({ v: x.id, t: x.t }));
      if (!ssl.length) return '';
      return this.randOf(ssl).v;
    }
    if (id === 'sfwSimCoreCat') return '不启用';
    if (id === 'shoes') {
      const sp = this.filterStylePool(this.shoePoolFor(this.v('clothCat') || '上下装'), 'shoes').filter(o => o.v !== '');
      if (!sp.length) return '';
      return this.randOf(sp).v;
    }
    if (id === 'poseExtra') {
      const hp = this.handPoolFor(this.v('pose')).filter(o => o.v !== '');
      if (!hp.length) return '';
      return this.randOf(hp).v;
    }
    if (id === 'eye') {
      const ep = this.eyePoolFor(this.v('emotion'));
      if (!ep.length) return '';
      return this.randOf(ep).v;
    }
    if (id === 'lingerieItem') {
      const llck = this.lingerieCatKey(this.v('lingerieCat'));
      const ll = (D.LINGERIE_ITEMS[llck] || []).map(x => ({ v: x[1], t: x[1] }));
      if (!ll.length) return '';
      return this.randOf(ll).v;
    }
    if (id === 'simPick') {
      const sck = this.v('simCat') || ''; const skey = sck ? sck.charAt(0) : '';
      const sl = (D.SIM_POSES[skey] || []).map(x => ({ v: x.id, t: x.t }));
      if (!sl.length) return '';
      return this.randOf(sl).v;
    }
    let list = (D.OPT[id] || []);
    if (id === 'smudge') list = this.smudgeFilter(list);
    if (id === 'weather') list = this.weatherToneFilter(list);
    if (id === 'scene') list = this.sceneToneFilter(list);
    list = this.filterStylePool(list, id);
    list = list.filter(o => (this.isNSFW() || !o.nsfw) && o.v !== '' && o.v !== '不启用');
    list = this.s12FilterList(id, list);
    if (id === 'sockLen') list = this.sockLenFilter(list);
    if (id === 'sockType') list = this.sockTypeFilter(list);
    if (id === 'mouth') list = this.mouthFilter(list);
    if (!list.length) return '';
    return this.randOf(list).v;
  },
  fillBlanks(){
    let filled = 0;
    const inferCat = id => {
      if ((this.val[id] || '') === '' && !this.locked[id]) {
        const itemSel = (id === 'clothCat') ? this.v('clothItem') : this.v('pose');
        if (itemSel && itemSel !== '不启用') {
          if (id === 'clothCat') this.val[id] = itemSel.split('｜')[0];
          else { const po = this.findPose(itemSel); if (po.cat) this.val[id] = po.cat; }
        }
        if (this.val[id] === '') this.val[id] = this.randOf(D.OPT[id]).v;
        this.userPicked[id] = true;
        filled++;
      }
    };
    const clothHidden = this.nsfwClothEnabled() && (this.lingerieOn || this.nudeOn);
    if (!clothHidden) inferCat('clothCat');
    this.allFieldIds.forEach(id => {
      if (id === 'clothCat' || id === 'poseCat') return;
      if (CUSTOM_FIELD_IDS.indexOf(id) >= 0) return;
      if (id === 'stylePreset') return;
      if (this.secOff[this.fieldSection(id)]) return;
      if (this.locked[id]) return;
      if (SPLIT_FIELDS.indexOf(id) >= 0 && !this.isSplitCat()) return;
      if (id === 'bottomLength' && !this.isSplitCat()) return;
      if (!this.fieldActive(id)) return;
      if ((this.val[id] || '') !== '') return;
      const rv = this.randomValueFor(id);
      if (rv !== '' && rv !== undefined) { this.val[id] = rv; filled++; }
    });
    return filled;
  },

  /* ---- 清空（等价 clearAll 取值侧） ---- */
  clearAll(){
    this.allFieldIds.forEach(id => {
      this.val[id] = RANGE_FIELDS[id] ? String(RANGE_FIELDS[id].def !== undefined ? RANGE_FIELDS[id].def : '1') : '';
    });
    Object.keys(this.userPicked).forEach(k => { this.userPicked[k] = false; });
    this.nsfwClothOn = false; this.lingerieOn = false; this.nudeOn = false;
    this.pantyBackup = {};
    this.personNsfwOn = false;
    this.sfwExposureOn = false;
    this.poseMode = 'SFW';
    this.allFieldIds.forEach(id => { if (CUSTOM_FIELD_IDS.indexOf(id) < 0) this.locked[id] = false; });
    CUSTOM_FIELD_IDS.forEach(id => { this.custom[id] = ''; this.customEnabled[id] = true; });
    this.secOff = {}; this.secOffBackup = {};
    this.lastMode = 'SFW';
    ['mouth', 'prop'].concat(NEG_EFFECT_IDS).forEach(id => { this.val[id] = ''; this.userPicked[id] = false; });
    NEG_EFFECT_IDS.forEach(id => { this.val[id] = '不启用'; this.userPicked[id] = false; });
    this.syncEye();
  },

  /* ---- 设定导入/导出（JSON 序列化，跨会话复用同一份「配方」） ---- */
  dumpSettingsObj(){
    return {
      val: Object.assign({}, this.val),
      custom: Object.assign({}, this.custom),
      customEnabled: Object.assign({}, this.customEnabled),
      locked: Object.assign({}, this.locked),
      secOff: Object.assign({}, this.secOff),
      poseMode: this.poseMode,
      nsfwClothOn: this.nsfwClothOn,
      lingerieOn: this.lingerieOn,
      nudeOn: this.nudeOn,
      sfwExposureOn: this.sfwExposureOn,
      personNsfwOn: this.personNsfwOn,
      sockUnlock: this.sockUnlock,
      autoTrim: this.autoTrim,
      seed: this.seed
    };
  },
  loadSettingsObj(o){
    if (!o) return;
    if (o.val) Object.keys(o.val).forEach(id => { if (id in this.val) this.val[id] = o.val[id]; });
    if (o.custom) Object.keys(o.custom).forEach(id => { if (id in this.custom) this.custom[id] = o.custom[id]; });
    if (o.customEnabled) Object.keys(o.customEnabled).forEach(id => { if (id in this.customEnabled) this.customEnabled[id] = o.customEnabled[id]; });
    if (o.locked) this.locked = Object.assign({}, o.locked);
    if (o.secOff) this.secOff = Object.assign({}, o.secOff);
    if (o.poseMode) this.poseMode = o.poseMode;
    this.nsfwClothOn = !!o.nsfwClothOn;
    this.lingerieOn = !!o.lingerieOn;
    this.nudeOn = !!o.nudeOn;
    this.sfwExposureOn = !!o.sfwExposureOn;
    this.personNsfwOn = !!o.personNsfwOn;
    this.sockUnlock = !!o.sockUnlock;
    if (o.autoTrim !== undefined) this.autoTrim = o.autoTrim;
    if (o.seed !== undefined && o.seed !== null) this.seed = o.seed >>> 0;
    this.rng = mulberry32(this.seed);
    /* lastMode 取当前模式：设定是「求解完的状态」，再跑时不应触发模式切换回落
       （HTML 里 lastMode 是常驻内存状态，等价于已稳定） */
    this.lastMode = this.detectMode();
    /* 联动一致性：换风格/类别后回落不适配值由下次 generate 的 fillBlanks 兜底 */
  },
  dumpSettingsLines(){
    const lines = [];
    D.SECTIONS.forEach(sec => {
      if (this.secOff[sec.id]) return;
      const sub = [];
      this.sectionFieldIds(sec).concat(
        sec.id === 'cloth' ? [] : sec.id === 'pose' ? [] : []
      ).forEach(id => {
        const val = (this.val[id] || '').trim();
        if (!val || val === '不启用') return;
        if (!this.fieldActive(id)) return;
        if (SPLIT_FIELDS.indexOf(id) >= 0 && !this.isSplitCat()) return;
        sub.push((this.FIELD_LABEL[id] || id) + '：' + val);
      });
      if (sub.length) lines.push('· ' + (sec.title || sec.id) + '：' + sub.join('；'));
    });
    CUSTOM_FIELD_IDS.forEach(id => {
      if (this.customEnabled[id] === false) return;
      const val = (this.custom[id] || '').trim(); if (!val) return;
      lines.push('· 自定义补充（' + (this.FIELD_LABEL[id] || id) + '）：' + val);
    });
    return lines;
  }
});

/* ---- 过滤器常量（HTML 行 5190–5298，与 S12/S13/S15/S16 自检共用同一口径） ---- */
const D_ITEM_C12 = { '象牙白': '白', '白色': '白', '奶白': '白', '奶色': '白', '雪白': '白', '黑色': '黑', '墨黑': '黑', '深蓝': '蓝', '驼色': '驼', '淡粉': '粉', '浅卡其': '卡其', '复古波点': '白', '灰色': '灰', '深灰': '灰',
  '米白': '白', '米色': '白', '奶油白': '白', '素白': '白', '月白': '白', '月白色': '白',
  '浅粉': '粉', '粉色': '粉',
  '浅蓝': '蓝', '蓝色': '蓝', '藏青': '蓝', '藏青色': '蓝',
  '浅绿': '绿', '淡绿': '绿', '墨绿': '绿',
  '红色': '红', '酒红': '红', '正红': '红', '绯红': '红', '大红': '红',
  '浅紫': '紫', '紫色': '紫', '绛紫': '紫',
  '香槟': '香槟', '银灰': '灰', '银色': '银' };
const D_MAT_FAM12 = { '棉质': '棉', '罗纹': '针织', '针织': '针织', '丝绒': '丝绒', '缎面': '丝', '丝质': '丝', '真丝': '丝', '雪纺': '雪纺', '羊绒': '羊绒', '蕾丝': '蕾丝', '镂空针织': '针织', '牛仔': '牛仔', '皮革': '皮革', '乳胶': '乳胶', '织锦缎': '织锦', '织锦': '织锦', '提花': '提花', '厚实': '棉', '薄纱': '纱', '欧根纱': '纱', '香云纱': '香云纱' };
const EMBED_MAIN_MAT_RE = /棉质|针织|罗纹|牛仔|雪纺|真丝|丝质|缎面|蕾丝|薄纱|丝绒|皮革|羊绒|乳胶|织锦|织金|提花|欧根纱|香云纱|厚实|纱/;
const D_WEATHER_TONE_FILTER = { '日光清新': { '夜色深沉': 1, '黄昏暮色': 1 }, '阴天柔灰': { '夜色深沉': 1, '黄昏暮色': 1 }, '中性白': { '夜色深沉': 1 } };
const D_NIGHT_SCENES = { '情人旅馆': 1, '酒店套房': 1, '电车': 1, '车内': 1, '便利店': 1, '点歌厅': 1, '夜店': 1, '天台': 1, '夜晚公园': 1, '后巷': 1, '监禁密室': 1 };
const D_SCENE_TONE_FILTER = { '日光清新': D_NIGHT_SCENES, '阴天柔灰': D_NIGHT_SCENES, '中性白': D_NIGHT_SCENES };
const D_SOCK_LEN_FIX = { '过膝长筒袜': ['过膝', '大腿'], '吊带袜': ['大腿', '过膝'], '毛绒长袜': ['过膝', '大腿', '小腿'], '泡泡袜': ['及踝', '短袜'], '足袋': ['及踝'] };
const D_SOCK_LONG_FOOT_TYPES = { '吊带袜': 1, '过膝长筒袜': 1, '毛绒长袜': 1, '泡泡袜': 1, '足袋': 1 };
const D_EMOTION_MOUTH_BAN = {
  '隐忍': { '大笑': 1, '微张': 1, '吐舌': 1, '舔唇': 1, '唾液': 1 },
  '痛苦': { '大笑': 1, '微张': 1, '吐舌': 1, '舔唇': 1, '唾液': 1 },
  '愉悦': { '闭合': 1, '大笑': 1 },
  '惊恐': { '闭合': 1, '大笑': 1 },
  '害羞': { '大笑': 1 }
};
const D_STYLE_FIELD_KEY = { clothMat: 'mat', clothPattern: 'pattern', clothDeco: 'deco',
  bottomLength: 'bottomLen', outerwear: 'outerwear', collarStyle: 'collar', topLength: 'topLen',
  bottomStyle: 'bottom', sockType: 'sock', shoes: 'shoe' };

/* =====================================================================
 * 提示词组装（buildPrompt）—— 与 HTML 行 5458–5715 逐段一致
 * ===================================================================== */
Engine.prototype.countChars = function(s){
  const cjk = (s.match(/[\u4e00-\u9fff]/g) || []).length;
  const lat = (s.match(/[a-zA-Z]+/g) || []).length;
  return cjk + lat;
};

Engine.prototype.buildPrompt = function(skip){
  skip = skip || {};
  const e = this;
  const parts = [];
  const v = e.v.bind(e);
  const optT = e.optT.bind(e);
  const OPT = D.OPT;
  const poseNSFW = (e.poseMode === 'NSFW') && e.simModeActive();
  const clothNSFW = e.nsfwClothEnabled() && (function(){
    let s = v('nsfwState'); if (e.nudeOn) s = '';
    if (s && s !== '不启用') return true;
    return !!((e.custom.nsfwCustom1 && e.customEnabled.nsfwCustom1 !== false) || (e.custom.nsfwCustom2 && e.customEnabled.nsfwCustom2 !== false));
  })();

  /* 0. 衣着状态前置句（NSFW 强化：全裸=赤裸描述；否则半裸+随机上身部位） */
  if (e.nsfwClothEnabled()) {
    parts.push(e.nudeOn ? '全身赤裸，肌肤完全裸露。' : '人物半裸，' + e.randOf(UPPER_NUDE_PARTS) + '。');
  }

  /* ① 拍摄角度 */
  if (!e.secOff.camera) {
    let s1 = optT(OPT.lens, v('lens')) + '，' + optT(OPT.viewpoint, v('viewpoint'));
    const ss = v('shotSize'); if (ss && ss !== '不启用') s1 += '，取' + ss + '景别';
    const df = v('dof'); if (df && df !== '不启用') s1 += '，' + optT(OPT.dof, df);
    const dev = v('device'); if (dev && dev !== '不启用' && !skip.device) s1 += '，画面以' + optT(OPT.device, dev) + '呈现';
    parts.push(s1 + '。');
  }

  /* ② 光影 · 色调 · 氛围 */
  let s2 = '';
  if (!e.secOff.light) {
    const ml = v('mainLight'); if (ml && ml !== '不启用' && !skip.mainLight) s2 += optT(OPT.mainLight, ml) + '。';
    const amb = v('ambient'); if (amb && amb !== '不启用' && !skip.ambient) s2 += optT(OPT.ambient, amb) + '。';
    s2 += optT(OPT.colorTone, v('colorTone')) + '。';
  }
  if (!e.secOff.extra) {
    const film = v('film'); if (film && film !== '不启用' && !skip.film) s2 += optT(OPT.film, film) + '。';
    const cine = v('cine'); if (cine && cine !== '不启用' && !skip.cine) s2 += optT(OPT.cine, cine) + '。';
  }
  if (s2) parts.push(s2);

  /* ③ 人物维度 */
  function pval(id){ const x = v(id); return (x && x !== '不启用') ? x : ''; }
  let s3 = '';
  if (!e.secOff.person) {
    const pGroups = [];
    const ptm = pval('temperament'), pag = pval('age');
    if (ptm && pag) pGroups.push(ptm + '的' + pag); else if (ptm || pag) pGroups.push(ptm || pag);
    if (pval('race')) pGroups.push(pval('race'));
    const psk = pval('skin'), ptx = pval('texture');
    if (psk && ptx) pGroups.push(psk + ptx); else if (psk || ptx) pGroups.push(psk || ptx);
    if (pval('face')) pGroups.push(pval('face'));
    const pbl = [pval('body'), pval('leg') ? optT(OPT.leg, pval('leg')) : ''].filter(Boolean).join('、');
    if (pbl) pGroups.push(pbl);
    const lrv = parseFloat(v('legRatio')) || 1;
    if (lrv > 1) {
      let lrd;
      if (lrv >= 1.9) lrd = '双腿极其修长，腿长接近上半身两倍，九头身超模比例，满屏长腿';
      else if (lrv >= 1.7) lrd = '双腿极为修长，腿长约为上半身的' + lrv.toFixed(1) + '倍，逆天长腿，九头身比例';
      else if (lrv >= 1.45) lrd = '双腿修长挺拔，腿长约为上半身的' + lrv.toFixed(1) + '倍，高挑显腿长';
      else if (lrv >= 1.2) lrd = '双腿修长匀称，腿长约为上半身的' + lrv.toFixed(1) + '倍，身形比例极佳';
      else lrd = '双腿比例自然修长，腿长略长于上半身';
      pGroups.push(lrd);
    }
    const bsxv = pval('breastSize'), bshv = pval('breastShape');
    const pbx = [bsxv ? optT(OPT.breastSize, bsxv) : '', bshv ? optT(OPT.breastShape, bshv) : ''].filter(Boolean).join('，');
    if (pbx) pGroups.push(pbx);
    const bodyPart = [pval('shoulder') ? optT(OPT.shoulder, pval('shoulder')) : '',
      pval('chestPos') ? optT(OPT.chestPos, pval('chestPos')) : '',
      pval('waist') ? optT(OPT.waist, pval('waist')) : '',
      pval('hip') ? optT(OPT.hip, pval('hip')) : '',
      pval('arm') ? optT(OPT.arm, pval('arm')) : ''].filter(Boolean).join('，');
    if (bodyPart) pGroups.push(bodyPart);
    const nlb = pval('nsfwLowerBody');
    if (nlb && e.personNsfwEnabled() && !skip.nsfwLowerBody) pGroups.push(optT(OPT.nsfwLowerBody, nlb));
    const nbd = pval('nsfwBreastDetail');
    if (nbd && e.personNsfwEnabled() && !skip.nsfwBreastDetail) pGroups.push(optT(OPT.nsfwBreastDetail, nbd));
    if (pval('firstImp')) pGroups.push(pval('firstImp'));
    let pcx = (e.custom.personCustom || '').replace(/^[，。、\s]+|[，。、\s]+$/g, '');
    if (pcx && e.customEnabled.personCustom !== false && !skip.personCustom) pGroups.push(pcx);
    s3 = pGroups.length ? pGroups.join('，') + '。' : '';
  }
  if (!e.secOff.extra) {
    const tatt = v('tattoo'), tpos = v('tattooPos');
    if (tatt && tatt !== '不启用' && tpos && tpos !== '不启用' && !skip.tattoo) s3 += tpos + '有一枚' + tatt + '纹身，墨色贴合皮肤轮廓自然晕染，边缘微微褪色，如同渗入皮肤下层的真墨。';
  }
  if (s3) parts.push(s3);

  /* ④ 发型 */
  if (!e.secOff.hair) {
    const hairCore = [v('hairLen'), v('hairColor'), v('hairCurl')].filter(x => x && x !== '不启用').join('');
    const hairParts = [];
    if (hairCore) hairParts.push(hairCore);
    const hte = v('hairTie'); if (hte && hte !== '不启用') hairParts.push(hte);
    const hbn = v('hairBangs'); if (hbn && hbn !== '不启用') hairParts.push(hbn);
    const hst = v('hairState'); if (hst && hst !== '不启用') hairParts.push(hst);
    let s4 = hairParts.join('，');
    const haf = v('hairAccFront'); if (haf && haf !== '不启用' && !skip.hairAccFront) s4 += (s4 ? '，' : '') + optT(OPT.hairAccFront, haf);
    const hab = v('hairAccBack'); if (hab && hab !== '不启用' && !skip.hairAccBack) s4 += (s4 ? '，' : '') + optT(OPT.hairAccBack, hab);
    const has = v('hairAccSide'); if (has && has !== '不启用' && !skip.hairAccSide) s4 += (s4 ? '，' : '') + optT(OPT.hairAccSide, has);
    if (s4) parts.push(s4 + '。');
  }

  /* ⑤ 妆容 */
  let s5 = '';
  if (!e.secOff.makeup) {
    s5 = '妆容为' + optT(OPT.makeup, v('makeup'));
    const md = v('makeupDetail'); if (md && md !== '不启用' && !skip.makeupDetail) s5 += '，' + md;
    const sm = v('smudge'); if (sm && sm !== '不启用' && !skip.smudge) s5 += '，' + optT(OPT.smudge, sm);
    const na = v('nails'); if (na && na !== '不启用' && !skip.nails) s5 += '，' + optT(OPT.nails, na);
  }
  if (s5) parts.push(s5 + '。');

  /* ⑥ 表情 */
  let s6 = '';
  if (!e.secOff.expression) {
    s6 = optT(OPT.emotion, v('emotion')) + '，' + optT(OPT.eye, v('eye')) + '，' + optT(OPT.mouth, v('mouth')) + '。';
  }
  if (!e.secOff.extra) {
    const ims = [v('imperf1'), v('imperf2')].filter((x, idx) => x && x !== '不启用' && !skip['imperf' + (idx + 1)]);
    if (ims.length) { s6 += ims.map(x => optT(OPT.imperf, x)).join('，') + '。'; }
  }
  if (s6) parts.push(s6);

  /* ⑧ 姿态（先算，供服装段判断安全内裤） */
  let s8 = '';
  if (poseNSFW) {
    const simT = (v('simMode') === '手动选类') ? e.simPickText() : e.smartSimPick();
    if (simT) s8 = simT;
    const ch = v('nsfwChain');
    if (ch && ch !== '不启用' && !skip.nsfwChain) {
      const chTxt = optT(OPT.nsfwChain, ch).replace(/^,/, '');
      s8 = s8.replace(/[。，\s]+$/, '') + '，' + chTxt + '。';
    }
  } else {
    let simT2 = '';
    if (e.sfwSimModeActive()) simT2 = (v('sfwSimMode') === '手动选类') ? e.sfwSimPickText() : e.smartSfwPick();
    if (simT2) s8 = simT2;
  }

  /* ⑦ 服装（SFW 主体；NSFW 强化叠加；全裸/情趣衣柜替换） */
  let s7 = '';
  const clothModeHide = e.nsfwClothEnabled() && (e.lingerieOn || e.nudeOn);
  let nstHead = '';
  if (clothNSFW) { let nst = v('nsfwState'); if (e.nudeOn) nst = ''; if (nst && nst !== '不启用') nstHead = optT(OPT.nsfwState, nst); }
  const dims = [];
  if (!clothModeHide) {
    const it = v('clothItem'); if (it && it !== '不启用') dims.push((e.isSplitCat() ? '上身穿着' : '') + e.clothItemT(it));
    if (nstHead) dims.push(nstHead);
    const mt = v('clothMat'); if (mt && mt !== '不启用' && !skip.clothMat) dims.push(optT(OPT.clothMat, mt));
    const pa = v('clothPattern'); if (pa && pa !== '不启用' && !skip.clothPattern) dims.push(optT(OPT.clothPattern, pa));
    const dc = v('clothDeco'); if (dc && dc !== '不启用' && !skip.clothDeco) dims.push(dc + '装饰细节');
    if (e.isSplitCat()) {
      const ow = v('outerwear'); if (ow && ow !== '不启用' && !skip.outerwear) dims.push('外面' + optT(OPT.outerwear, ow));
      const cs = v('collarStyle'); if (cs && cs !== '不启用' && !skip.collarStyle) dims.push(optT(OPT.collarStyle, cs));
      const tl = v('topLength'); if (tl && tl !== '不启用' && !skip.topLength) dims.push(optT(OPT.topLength, tl));
      const ly = v('clothLayer'); if (ly && ly !== '不启用' && !skip.clothLayer) dims.push(optT(OPT.clothLayer, ly));
      const bs = v('bottomStyle'); if (bs && bs !== '不启用' && !skip.bottomStyle) dims.push('下身穿着' + optT(OPT.bottomStyle, bs));
      const bl = v('bottomLength'); if (bl && bl !== '不启用' && !skip.bottomLength) dims.push(optT(OPT.bottomLength, bl));
      const sc2 = v('splitColor'); if (sc2 && sc2 !== '不启用' && !skip.splitColor) dims.push(optT(OPT.splitColor, sc2));
    } else {
      const ly2 = v('clothLayer'); if (ly2 && ly2 !== '不启用' && !skip.clothLayer) dims.push(optT(OPT.clothLayer, ly2));
      const bl2 = v('bottomLength'); if (bl2 && bl2 !== '不启用' && !skip.bottomLength) dims.push(optT(OPT.bottomLength, bl2));
    }
    const sh = v('shoes'); if (sh && sh !== '不启用' && !skip.shoes) dims.push('脚踩' + optT(OPT.shoes, sh));
    const st = v('sockType');
    if (st && st !== '不启用' && !skip.sockType) {
      const sl = v('sockLen'), sc = v('sockColor'), so = v('sockOpacity');
      const slTxt = (sl && sl !== '不启用' && !D_SOCK_LEN_FIX[st]) ? sl : '';
      const sn = (sc && sc !== '不启用' ? sc : '') + slTxt + st;
      const sd = [optT(OPT.sockType, st)];
      if (so && so !== '不启用' && !skip.sockOpacity) { const sod = optT(OPT.sockOpacity, so); if (sod) sd.push(sod); }
      dims.push('双腿穿着' + sn + (sd.length ? ('，' + sd.join('，')) : ''));
    }
    const ac = v('accessory'); if (ac && ac !== '不启用' && !skip.accessory) dims.push(optT(OPT.accessory, ac));
    const pc = v('pantyColor'), ps = v('pantyStyle');
    if ((pc && pc !== '不启用') || (ps && ps !== '不启用')) {
      dims.push('下身' + (pc && pc !== '不启用' ? pc : '') + (ps && ps !== '不启用' ? ps : '') + '内裤完整覆盖私处，面料厚实不透明');
    }
    const exv = v('sfwExposure'); if (e.sfwExposureOn && !e.nsfwClothEnabled() && exv && exv !== '不启用' && !skip.sfwExposure) dims.push(optT(OPT.exposure, exv));
    const ctt = v('clothTransparency'); if (e.sfwExposureOn && !e.nsfwClothEnabled() && ctt && ctt !== '不启用' && !skip.clothTransparency) dims.push(optT(OPT.clothTransparency, ctt));
  }
  s7 = dims.length ? dims.join('，') + '。' : '';
  /* 中文接中文不加空格，跨语种以空格分隔 */
  const cjkJoin = function(base, add){
    if (!add) return base;
    if (!base) return add;
    if (/[\u4e00-\u9fff。！？，、；：]/.test(base.charAt(base.length - 1)) && /^[\u4e00-\u9fff]/.test(add)) return base + add;
    return base + ' ' + add;
  };
  if (clothNSFW) {
    let nc1 = (e.custom.nsfwCustom1 || '').replace(/^[，。、\s]+|[，。、\s]+$/g, '');
    if (e.customEnabled.nsfwCustom1 === false) nc1 = '';
    let nc2 = (e.custom.nsfwCustom2 || '').replace(/^[，。、\s]+|[，。、\s]+$/g, '');
    if (e.customEnabled.nsfwCustom2 === false) nc2 = '';
    const nstTail = cjkJoin(cjkJoin('', nc1), nc2);
    if (nstHead && !dims.length) s7 = nstHead + '，' + s7;
    s7 = cjkJoin(s7, nstTail);
  }
  if (e.nsfwClothEnabled() && e.lingerieOn) {
    const lgi = [];
    const lck = e.lingerieCatKey(v('lingerieCat'));
    if (lck && lck !== '不启用') lgi.push('身着' + lck + '情趣内衣');
    const li = v('lingerieItem');
    if (li && li !== '不启用') lgi.push(li);
    const lc1 = v('lingerieColor1'); if (lc1 && lc1 !== '不启用') lgi.push('主色调为' + lc1);
    const lc2 = v('lingerieColor2'); if (lc2 && lc2 !== '不启用') lgi.push('辅色调为' + lc2);
    if (lgi.length) s7 = cjkJoin(s7, lgi.join('，') + '。');
  }
  if (e.nsfwClothEnabled() && !(e.lingerieOn || e.nudeOn)) {
    const nx = v('nsfwExposure');
    if (nx && nx !== '不启用' && !skip.nsfwExposure) { s7 = cjkJoin(s7, optT(OPT.exposureNsfw, nx) + '。'); }
  }

  /* 装配姿态 + 服装 */
  if (s8) parts.push(s8 + (poseNSFW ? '' : '。'));
  if (s7) parts.push(s7);

  /* ⑨ 背景 · 道具 · 天气 */
  if (!e.secOff.bg) {
    let s9 = optT(OPT.scene, v('scene')) + '。';
    const pr = v('prop'); if (pr && pr !== '不启用' && !skip.prop) s9 += optT(OPT.prop, pr) + '。';
    const we = v('weather'); if (we && we !== '不启用' && !skip.weather) s9 += optT(OPT.weather, we) + '。';
    if (s9) parts.push(s9);
  }

  /* ⑩ 构图 */
  let s10 = '';
  if (!e.secOff.comp) {
    const cpv = v('compPos');
    s10 = optT(OPT.comp, v('comp')) + (cpv && cpv !== '不启用' ? '，人物' + optT(OPT.compPos, cpv) : '') + '。';
  }
  if (!e.secOff.extra) {
    const stt = v('styleTag'); if (stt && stt !== '不启用' && !skip.styleTag) s10 += '整体呈现' + stt + '风格。';
  }
  if (s10) parts.push(s10);

  /* 段间拼接：跨语种补空格 */
  function joinParts(arr){
    let out = '';
    arr.forEach(p => {
      if (!p) return;
      if (out) {
        const a = out.charAt(out.length - 1), b = p.charAt(0);
        if (!/[\u4e00-\u9fff\u3000。！？，、；：]/.test(a) && /[\u4e00-\u9fff]/.test(b)) out += ' ';
      }
      out += p;
    });
    return out;
  }
  return { text: joinParts(parts), risk: { risky: false, poseName: '', poseCat: '' } };
};

/* =====================================================================
 * 自检（S1–S17 / F1–F10）+ 生成入口（generate）
 * —— 与 HTML 行 5724–5939 / 6158–6174 一致
 * ===================================================================== */
Engine.prototype.sceneObj = function(){
  const s = this.v('scene');
  return D.OPT.scene.filter(o => o.v === s)[0] || {};
};

Engine.prototype.runSelfCheck = function(prompt, risk, skip){
  skip = skip || {};
  const e = this;
  const v = e.v.bind(e);
  const isNSFW = (v('nsfwState') !== '') || e.simModeActive();
  const n = e.countChars(prompt);
  const scene = e.sceneObj();
  const water = !!scene.water;
  const F = [], S = [];
  const fn = isNSFW ? 'NSFW' : 'SFW';

  /* F1 物理自洽 */
  let f1ok = true, f1note = '光源方向与场景时间匹配';
  if (e.secOff.bg || e.secOff.light) { f1note = '涉及段落已「本段不启用」，跳过自检'; }
  else {
    const NIGHT = { '情人旅馆': 1, '夜店': 1, '天台': 1, '夜晚公园': 1, '后巷': 1, '监禁密室': 1, '酒店套房': 1 };
    if (NIGHT[scene.v] && (v('colorTone') === '日光清新')) { f1ok = false; f1note = '夜晚场景搭配了日光色调，光源矛盾'; }
  }
  F.push({ id: 'F1', name: '物理自洽', ok: f1ok, note: f1note });

  /* F2 语言模式 */
  let f2ok, f2note;
  if (isNSFW) {
    f2ok = true;
    f2note = 'NSFW 姿态为中文模拟动作库描述（+可选英文反应链）';
  } else {
    f2ok = !/[a-zA-Z]{2,}/.test(prompt.replace(/JK|KC|Lolita|Blazer|JSK|OP|POLO|Polo|polo|babydoll|BabyDoll/g, ''));
    f2note = f2ok ? '全文为中文叙事（V领/A字/JK 等形状与缩写字母不计）' : '发现成段英文，SFW 要求中文叙事';
  }
  F.push({ id: 'F2', name: '语言模式', ok: f2ok, note: f2note });

  /* F3 禁令总表：SFW 全扫；NSFW 完全放行 */
  const hit = [];
  if (!isNSFW) {
    BAN_A.concat(BAN_B).forEach(w => { if (prompt.indexOf(w) >= 0) hit.push(w); });
    BAN_D.forEach(w => { if (prompt.toLowerCase().indexOf(w) >= 0) hit.push(w); });
  }
  const f3ok = hit.length === 0 || water;
  const f3note = hit.length === 0 ? (isNSFW ? 'NSFW 模式完全放行（无限制，唯一底线：不涉未成年/真实人物·词库自律）' : '禁令词扫描通过')
    : (water ? '命中词出现在含水场景，按豁免条款放行：' + hit.join('、')
             : '命中禁令词：' + hit.join('、'));
  F.push({ id: 'F3', name: '禁令总表', ok: f3ok, note: f3note });

  /* F4 内裤锚定（手动可选） */
  F.push({ id: 'F4', name: '内裤锚定', ok: true, note: '内裤颜色/款式为手动可选，不再自动锚定' });

  /* F5 字数 */
  const f5ok = n >= 300 && n <= 700;
  F.push({ id: 'F5', name: '字数 300–700', ok: f5ok, note: '当前约 ' + n + ' 字' + (n < 300 ? '（略少，可补充细节）' : n > 700 ? '（超出上限，建议精简）' : '') });

  /* F6 无违禁格式 */
  const f6ok = !/\([^)]*:\s*[\d.]+\)/.test(prompt) && !/masterpiece|best quality/i.test(prompt);
  F.push({ id: 'F6', name: '无违禁格式', ok: f6ok, note: '无权重语法/质量标签/标签串' });

  /* F7 视角冲击力 */
  F.push({ id: 'F7', name: '视角冲击力', ok: true, note: '已选明确视角 + 明确镜头类型' });

  /* F8 服装维度 */
  if (!isNSFW) {
    let dimCount = 0;
    ['clothLayer', 'clothItem', 'outerwear', 'collarStyle', 'topLength', 'bottomStyle', 'splitColor', 'bottomLength', 'clothMat', 'clothPattern', 'clothDeco', 'shoes', 'sockType', 'accessory'].forEach(id => {
      const val = v(id); if (val && val !== '不启用' && !skip[id]) dimCount++;
    });
    const f8Split = e.isSplitCat();
    const f8ok = f8Split ? (dimCount >= 7 && dimCount <= 14) : (dimCount >= 6 && dimCount <= 10);
    F.push({ id: 'F8', name: '服装维度', ok: f8ok, note: '当前 ' + dimCount + ' 维' + (f8Split ? '（上下装全字段 7–14）' : '（常规类别 6–10）') });
  } else {
    F.push({ id: 'F8', name: '服装维度', ok: true, note: 'NSFW 免检' });
  }

  /* F9 服装来源 */
  const item = v('clothItem') || '';
  const demoHit = item.indexOf('深蓝灰缎面吊带裙') >= 0 || (item.indexOf('白色丝质衬衫') >= 0 && /紧身短裙|包臀/.test(item));
  F.push({ id: 'F9', name: '服装来源', ok: true, note: demoHit ? '主件与文档示范主件接近（用户指定除外）' : '主件来自款式子表' });

  /* F10 NSFW 模拟动作库 */
  if (isNSFW) {
    F.push({ id: 'F10', name: 'NSFW 模拟动作库', ok: true, note: '姿态为交合模拟动作库描述' });
  }

  /* S1 情绪→光影 */
  const emo = v('emotion'), light = v('mainLight');
  if (/慵懒|温柔/.test(emo) && light === '硬光') S.push({ id: 'S1', ok: false, note: '慵懒温柔的情绪配硬光直射，建议换柔光' });
  else S.push({ id: 'S1', ok: true, note: '情绪与光影协调' });

  /* S2 身份→姿态（手动选类取所选；智能匹配现抽一条判定——与 HTML 一致消耗 RNG） */
  if (v('firstImp') === '楚楚可怜' && !isNSFW) {
    const s2pose = (v('sfwSimMode') === '手动选类') ? e.sfwSimPickText() : (v('sfwSimMode') === '智能匹配' ? e.smartSfwPick() : '');
    if (/直立|叉腰|背手/.test(s2pose))
      S.push({ id: 'S2', ok: false, note: '楚楚可怜配霸气站姿，气质撕裂（反差设定除外）' });
    else S.push({ id: 'S2', ok: true, note: '身份与姿态协调' });
  } else S.push({ id: 'S2', ok: true, note: '身份与姿态协调' });

  /* S3 色调→情绪 */
  if (e.secOff.light || e.secOff.expression) { S.push({ id: 'S3', ok: true, note: '色调/情绪段落已「本段不启用」，跳过' }); }
  else {
    const tone = v('colorTone');
    if (/霓虹混合|冷白荧光/.test(tone) && /冷淡|恍惚失神|宁静/.test(emo))
      S.push({ id: 'S3', ok: false, note: '霓虹/冷荧光撞情绪主调，注意色彩情绪一致' });
    else S.push({ id: 'S3', ok: true, note: '色调与情绪一致' });
  }

  /* S4 配饰→世界观 */
  S.push({ id: 'S4', ok: true, note: '无跨世界观配饰冲突' });

  /* S5 设备→画质 */
  if (e.secOff.camera) { S.push({ id: 'S5', ok: true, note: '拍摄角度段已「本段不启用」，跳过' }); }
  else {
    const dev = v('device'), lens = v('lens'), dof = v('dof');
    if ((dev === '手机自拍' && /长焦|中焦/.test(lens)) || (dev === '监控摄像头' && dof === '浅景深'))
      S.push({ id: 'S5', ok: false, note: '设备与画质自洽性存疑（手机不加焦段、监控不配浅景深）' });
    else S.push({ id: 'S5', ok: true, note: '设备与画质自洽' });
  }

  /* S6 裸露→场景 */
  if (e.secOff.bg) { S.push({ id: 'S6', ok: true, note: '背景段已「本段不启用」，跳过' }); }
  else if (isNSFW) { S.push({ id: 'S6', ok: true, note: 'NSFW 场景×裸露禁忌已按授权无限制放行' }); }
  else S.push({ id: 'S6', ok: true, note: 'SFW 免检' });

  /* S7 纹身融合 */
  if (e.secOff.extra) S.push({ id: 'S7', ok: true, note: '附加段已「本段不启用」，跳过' });
  else if (v('tattoo') !== '不启用') S.push({ id: 'S7', ok: true, note: '纹身已带皮肤融合描述，避免贴纸感' });
  else S.push({ id: 'S7', ok: true, note: '未使用纹身' });

  /* S8 道具位置 */
  if (e.secOff.bg) S.push({ id: 'S8', ok: true, note: '背景段已「本段不启用」，跳过' });
  else if (v('prop') !== '不启用') S.push({ id: 'S8', ok: true, note: '道具已交代位置关系' });
  else S.push({ id: 'S8', ok: true, note: '未使用道具' });

  /* S9 眼神→角度 */
  if (e.secOff.camera || e.secOff.expression) { S.push({ id: 'S9', ok: true, note: '机位/表情段已「本段不启用」，跳过' }); }
  else {
    const vp = v('viewpoint'), eye = v('eye');
    if (/俯拍|鸟瞰/.test(vp) && eye === '挑逗')
      S.push({ id: 'S9', ok: false, note: '俯拍机位配「挑逗注视」存在视线方向矛盾' });
    else if (/仰拍|虫视/.test(vp) && eye === '乞求哀怨')
      S.push({ id: 'S9', ok: false, note: '仰拍机位配「向上望的哀求」存在视线方向矛盾' });
    else S.push({ id: 'S9', ok: true, note: '眼神方向与机位匹配' });
  }

  /* S10 鞋履 × 主件类别适配 */
  const shv = v('shoes'), cc = v('clothCat');
  if (shv && shv !== '不启用' && shv !== '赤脚' && D.SHOE_POOL[cc] && D.SHOE_POOL[cc].indexOf(shv) < 0)
    S.push({ id: 'S10', ok: false, note: '「' + shv + '」与主件类别「' + cc + '」适配度低，建议换适配鞋款或赤脚' });
  else S.push({ id: 'S10', ok: true, note: '鞋履与主件类别适配（含赤脚/不启用）' });

  /* S11 姿态 × 手部（擦边动作库后恒通过） */
  S.push({ id: 'S11', ok: true, note: '动作描述已内含手部姿态（手部细节体系废弃）' });

  /* S12 服装细节连贯 */
  let S12ok = true, S12note = '服装细节连贯（颜色/材质与主件一致）';
  const clothHideS12 = e.nsfwClothEnabled() && (e.lingerieOn || e.nudeOn);
  if (e.secOff.cloth || clothHideS12) { S12note = '服装段已隐藏/停用，跳过'; }
  else if (e.isSplitCat()) {
    const it12 = v('clothItem') || '';
    const c12 = e.itemColorFam12(it12), m12 = e.itemMatFam12(it12);
    const sc12 = v('splitColor'), mt12 = v('clothMat');
    if (c12 && sc12 && sc12 !== '不启用') {
      const topC = e.splitTopColor12(sc12);
      if (topC && topC.indexOf(c12) < 0) { S12ok = false; S12note = '主件为' + c12 + '色系，但上下装配色「' + sc12 + '」上衣为' + topC + '色系，颜色冲突，建议换色或「不启用」'; }
    }
    if (mt12 && mt12 !== '不启用') {
      if (e.itemEmbedsMainMat(it12)) { S12ok = false; S12note = '主件描述已自带材质（厚实/纱/缎面等），但「材质·光效」仍选了' + mt12 + '，材质重复或冲突，建议「不启用」'; }
      else if (m12) {
        const mf12 = e.matFam12(mt12);
        if (mf12 && mf12 !== m12) { S12ok = false; S12note = '主件材质为' + m12 + '，但「材质·光效」选了' + mt12 + '，材质冲突，建议一致或「不启用」'; }
      }
    }
  } else {
    const mt12b = v('clothMat');
    S12note = '非上下装类别：主件词条自带颜色/材质，免检';
    if (mt12b && mt12b !== '不启用' && e.itemEmbedsMainMat(v('clothItem') || '')) { S12ok = false; S12note = '主件描述已自带材质（厚实/纱/缎面等），但「材质·光效」仍选了' + mt12b + '，材质重复或冲突，建议「不启用」'; }
  }
  S.push({ id: 'S12', ok: S12ok, note: S12note });

  /* S13 色调×天气 */
  const S13conf = e.weatherToneConflict();
  S.push({ id: 'S13', ok: !S13conf, note: S13conf ? '色调「' + v('colorTone') + '」与天气「' + S13conf + '」冲突（白昼/夜景矛盾），建议换其一' : '色调与天气一致' });

  /* S14 妆容×糊妆 */
  const S14bad = (v('makeup') === '素颜') && v('smudge') !== '' && v('smudge') !== '不启用';
  S.push({ id: 'S14', ok: !S14bad, note: S14bad ? '妆容「素颜·裸妆感无妆」与糊妆「' + v('smudge') + '」矛盾（无妆可糊），建议糊妆置「不启用」' : '妆容与糊妆状态一致' });

  /* S15 袜子长度×类型 */
  const S15conf = e.sockLenTypeConflict();
  S.push({ id: 'S15', ok: !S15conf, note: S15conf ? S15conf + '，建议长度与类型取其一' : '袜子长度与类型一致' });

  /* S16 情绪×口型 */
  const S16conf = e.mouthEmotionConflict();
  S.push({ id: 'S16', ok: !S16conf, note: S16conf ? S16conf + '，建议换其一' : '情绪与口型一致' });

  /* S17 模拟动作×环境适配 */
  let S17ok = true, S17note = '模拟动作与环境适配';
  if (e.simModeActive() && v('simMode') === '手动选类' && v('scene')) {
    const env17 = D.SCENE_ENV[v('scene')] || D.SCENE_ENV['__default'];
    const txt17 = e.simPickText();
    if (txt17) {
      const hits17 = env17.filter(kw => txt17.indexOf(kw) >= 0).length;
      if (hits17 === 0) { S17ok = false; S17note = '模拟动作所需支撑物与场景「' + v('scene') + '」不匹配（该场景可用：' + env17.join('/') + '）'; }
    }
  } else if (e.simModeActive() && v('simMode') === '手动选类') {
    S17note = '未选场景，跳过环境匹配';
  } else if (e.simModeActive() && v('simMode') === '智能匹配') {
    S17note = '智能匹配：已按场景环境自动适配';
  }
  S.push({ id: 'S17', ok: S17ok, note: S17note });

  return { F, S, n, fn };
};

/* ---- 生成入口：等价 HTML「生成提示词」按钮 ----
 * randomize=true（默认）→ 先整体重掷（尊重🔒锁定）再补全
 * randomize=false       → 只补全未选项（幂等，重复调用输出稳定）
 * autoTrim=true（默认）  → 超 700 字按 TRIM_ORDER 顺序精简次要选项（HTML 复选框默认勾选） */
Engine.prototype.generate = function(opts){
  opts = opts || {};
  const randomize = opts.randomize !== false;
  if (randomize) this.randomizeAll();
  this.syncModeDeps();
  /* 注：不做「物化随机」——HTML 的 buildPrompt 在每次 trim 迭代都会重新抽
     （nudePart/smartSim），runSelfCheck 的 S2 也会重掷；引擎保持同序调用，
     同 seed 下 RNG 序列完全确定 → 输出逐字可复现且与 HTML 原版逐字一致 */
  const filled = this.fillBlanks();
  let skip = {};
  let result = this.buildPrompt(skip);
  let checks = this.runSelfCheck(result.text, result.risk, skip);
  if (this.autoTrim !== false && opts.autoTrim !== false && checks.n > 700) {
    for (let i = 0; i < TRIM_ORDER.length && checks.n > 700; i++) {
      skip[TRIM_ORDER[i]] = true;
      result = this.buildPrompt(skip);
      checks = this.runSelfCheck(result.text, result.risk, skip);
    }
  }
  result.trimmed = Object.keys(skip).length;
  result.filled = filled;
  result.mode = this.detectMode();
  result.checks = checks;
  result.seed = this.seed;
  return result;
};

module.exports = { Engine, D, mulberry32, CUSTOM_FIELD_IDS, NEG_EFFECT_IDS, SPLIT_FIELDS, STYLE_CTRL_IDS, TRIM_ORDER, RANGE_FIELDS, UPPER_NUDE_PARTS, BAN_A, BAN_B, BAN_D };
