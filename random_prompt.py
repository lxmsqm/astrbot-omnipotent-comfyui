"""
SmartComfy 随机提示词系统 — Python 移植版
从 SmartComfy (shenhuaxiyuan) 的 JS 前端逻辑翻译而来
"""

import json
import random
import copy
from pathlib import Path
from typing import Optional

# ─── 常量 ────────────────────────────────────────────────────────

# 硬编码 NSFW 身体标签组（用于去重检测）
NSFW_BODY_GROUPS = [
    ["small breasts", "medium breasts", "large breasts", "flat chest", "huge breasts", "massive breasts"],
    ["pale skin", "tanned", "dark skin", "fair skin", "dark-skinned female"],
    ["tall", "short", "petite", "gigantic"],
    ["slender", "chubby", "fat", "obese", "muscular", "plump"],
    ["young", "mature", "old", "teen", "adult", "elderly", "middle-aged"],
    ["solo", "1girl", "1boy", "threesome", "foursome", "gangbang", "orgy", "group sex",
     "double penetration", "bukkake", "3girls", "4girls", "5girls",
     "6+girls", "multiple girls", "multiple boys", "2girls", "2boys", "hetero"],
    ["yuri", "yaoi", "hetero"],
    ["virgin", "sex", "fuck", "penis", "cock", "dick", "cum", "creampie", "semen", "sperm"],
    # 发色互斥
    ["blonde hair", "silver hair", "black hair", "brown hair", "red hair", "blue hair",
     "pink hair", "purple hair", "green hair", "white hair", "grey hair", "orange hair",
     "aqua hair", "multicolored hair", "two-tone hair"],
    # 瞳色互斥
    ["blue eyes", "red eyes", "green eyes", "brown eyes", "purple eyes", "golden eyes",
     "grey eyes", "pink eyes", "yellow eyes", "heterochromia", "aqua eyes"],
    # 服装互斥 + 裸体（不能同时穿衣服和裸体）
    ["bikini", "swimsuit", "school uniform", "maid", "kimono", "armor", "suit",
     "casual", "sportswear", "lingerie", "evening dress", "wedding dress",
     "hoodie", "robe", "uniform", "cosplay", "military uniform", "nurse",
     "leotard", "bodysuit", "white bodysuit",
     "gothic lolita", "military lolita", "aristocratic clothes", "fur coat", "funeral dress",
     "sleeveless dress", "black dress", "layered dress", "peacoat", "overalls",
     "crop top", "latex top", "miniskirt", "fishnet thighhighs", "dress",
     "santa costume", "jacket", "skirt", "latex bodysuit underwear", "black pantyhose",
     "trench coat", "apron", "shrug", "bowtie", "gloves", "long sleeves",
     "topless", "bottomless", "heart pasties", "maebari", "groin",
     "trousers", "boots", "high heels", "thighhighs", "waist cape", "detached sleeves",
     "completely nude", "nude", "naked", "no clothes"],
    # 动作互斥
    ["standing", "sitting", "lying", "laying", "kneeling", "squatting",
     "crouching", "prone", "all fours", "punching", "holding hands",
     "archery", "diving", "dynamic combat pose", "posing", "stretching",
     "leaning forward", "playing instrument", "clapping", "dashing",
     "writing", "waving", "jogging", "cooking", "aiming", "reaching out",
     "thumbs up", "climbing", "eating", "peace sign", "skipping",
     "drinking", "crossed legs", "kneeling", "hand on chin", "aiming",
     "reaching out"],
    ["walking", "running", "jumping", "dancing", "falling", "flying", "swimming",
     "typing", "punching", "kicking", "stretching", "bending"],
    ["arms crossed", "arms up", "hands on hips", "hand on own chest",
     "hands behind back", "hand on own face", "arms behind head",
     "hand on face"],
    # 场景互斥
    ["outdoors", "indoors", "beach", "forest", "mountain", "city", "cafe",
     "classroom", "bedroom", "bathroom", "kitchen", "pool", "ocean",
     "garden", "castle", "space", "underwater", "night", "day",
     "lake", "river", "pond", "desert", "snow", "rain",
     "library", "shop", "store", "museum", "temple", "church", "stadium",
     "park", "rooftop", "balcony", "corridor", "basement"],
    # 色调/色彩风格互斥
    ["monochrome", "greyscale", "black and white", "sepia"],
    ["colorful", "vibrant colors", "pastel colors", "dark", "bright"],
    # 渲染风格互斥（2D vs 3D 等矛盾风格）
    ["line art", "sketch", "anime screencap", "cell shade", "anime coloring",
     "watercolor", "ink", "painted", "key visual", "cell shading",
     "ufotable style", "art nouveau", "gothic", "steampunk", "cyberpunk",
     "makoto shinkai style", "anime style", "manga style", "digital art"],
    ["PBR texturing", "subsurface scattering", "ray tracing", "global illumination",
     "physically based rendering", "octane render", "unreal engine", "3d render",
     "redshift render", "vray", "cycles", "arnold"],
]

# 触发词-提升标签 关联
TRIGGER_BOOSTS = [
    {"trigger": "makoto shinkai style", "boost": ["sunlight", "backlighting", "highres",
     "detailed background", "blue sky", "cloud", "lens flare"]},
    {"trigger": "maid", "boost": ["brown hair", "short hair", "soft lighting",
     "bedroom", "indoors", "apron", "frills"]},
    {"trigger": "kitsune", "boost": ["red hair", "kimono", "moonlight",
     "japanese architecture", "fox ears", "fox tail", "multiple tails"]},
    {"trigger": "catgirl", "boost": ["cat ears", "cat tail", "bell", "collar", "nekomimi"]},
    {"trigger": "witch", "boost": ["witch hat", "magic", "staff", "robe",
     "starry sky", "moon", "fireflies"]},
    {"trigger": "samurai", "boost": ["katana", "japanese armor", "cherry blossoms",
     "mountain", "fog", "dramatic lighting"]},
    {"trigger": "elf", "boost": ["long hair", "pointy ears", "forest",
     "nature", "bow", "arrow", "green eyes"]},
    {"trigger": "vampire", "boost": ["red eyes", "pale skin", "black hair",
     "long hair", "castle", "night", "moon", "blood"]},
    {"trigger": "mermaid", "boost": ["ocean", "underwater", "fish tail",
     "shell", "bubbles", "coral", "long hair"]},
    {"trigger": "school uniform", "boost": ["serafuku", "pleated skirt",
     "thighhighs", "classroom", "school", "outdoors"]},
    {"trigger": "kimono", "boost": ["japanese clothes", "obi", "geta",
     "hair ornament", "cherry blossoms", "shrine"]},
    {"trigger": "armor", "boost": ["sword", "shield", "cape", "castle",
     "battle stance", "dramatic lighting", "fantasy"]},
    {"trigger": "unreal engine 5", "boost": ["octane render", "PBR texturing",
     "ray tracing", "global illumination", "subsurface scattering",
     "ultra realistic", "8k uhd"]},
    {"trigger": "3d render", "boost": ["unreal engine 5", "octane render",
     "physically based rendering", "ray tracing"]},
    {"trigger": "anime style", "boost": ["cel shading", "anime screencap",
     "masterpiece", "best quality"]},
    {"trigger": "studio ghibli style", "boost": ["watercolor", "soft lighting",
     "nature", "forest", "sky", "cloud", "warm colors"]},
    {"trigger": "cyberpunk", "boost": ["neon lights", "city", "rain", "night",
     "futuristic", "mechanical parts", "hologram"]},
    {"trigger": "worm's eye view", "boost": ["from below", "macro foot shot",
     "focus on feet", "low angle shot", "strong bokeh"]},
]

# 分类优先级（数值越小越靠前）
CATEGORY_PRIORITY = {
    "艺术风格": 1,
    "画质与渲染": 3,
    "人物主体": 4,
    "动作姿态": 5,
    "发型与发色": 6,
    "五官与表情": 7,
    "服装与配饰": 8,
    "场景与背景": 9,
    "物品与道具": 10,
    "动植物与自然": 11,
    "构图与视角": 12,
    "色彩与氛围": 13,
}

# NSFW 子分类特殊优先级（在 NSFW 启用时替代默认）
NSFW_SUBCAT_PRIORITY = {
    "亲密互动": 5.5,
    "神态暗示": 7.5,
    "暴露程度": 8.5,
    "暧昧氛围": 9.5,
    "氛围暗示": 12.5,
}

# 模型专属品质标签
QUALITY_TAGS = {
    "anima": ["masterpiece level, highest quality, breathtaking",
              "8K resolution, insanely detailed, tack sharp"],
    "flux": ["大师级杰作，最高品质，令人叹为观止",
             "8K分辨率，极度精细，锐利无比"],
}

# 默认品质标签（anima 用）
DEFAULT_QUALITY_TAGS = ["masterpiece", "best quality", "ultra-detailed"]

# 模型配置
MODEL_CONFIGS = {
    "anima": {
        "use_quality": True,
        "use_weights": True,
        "use_natural_order": True,
        "separator": ", ",
    },
    "flux": {
        "use_quality": False,
        "use_weights": False,
        "use_natural_order": True,
        "separator": ". ",
    },
}

# ─── 模型提示词顺序（决定随机池标签的排列顺序） ────────────
# 每个模型的提示词有固定结构，比如 Anima 是：
#   [质量] [人数] [角色名] [作品] [画师] [特征标签]
# 值越小越靠前，同一个 section 内的按原顺序保持
PROMPT_SECTION_ORDER = {
    "anima": {
        # 顶级 section
        "section_order": [
            "质量", "画师", "光影",
            "风格", "人数体型", "角色名", "作品",
            "发型", "发色", "面部", "服饰", "材质",
            "场景", "色彩", "构图", "动作", "其他"
        ],
        # 子分类名 → section 映射
        "subcategory_map": {
            # 质量
            "品质保证": "质量",
            # 风格
            "动漫风格": "风格",
            "画风技法": "风格",
            "渲染技术": "风格",
            # 人数体型
            "体型": "人数体型",
            "肤质": "人数体型",
            "年龄段": "人数体型",
            # 特征——发型发色
            "发型": "发型",
            "发色": "发色",
            # 特征——面部
            "眼色": "面部",
            "表情": "面部",
            # 特征——服饰
            "上衣": "服饰",
            "下装": "服饰",
            "连衣裙": "服饰",
            "内衣泳装": "服饰",
            "鞋袜": "服饰",
            "配饰": "服饰",
            # 特征——材质
            "金属": "材质",
            "布料": "材质",
            "皮革": "材质",
            "表面": "材质",
            # 特征——动作
            "站坐卧": "动作",
            "腿部动作": "动作",
            "手臂动作": "动作",
            "全身动作": "动作",
            # 特征——光影
            "光影效果": "光影",
            # 特征——场景
            "自然环境": "场景",
            "建筑场景": "场景",
            "天气时间": "场景",
            # 特征——色彩
            "色彩氛围": "色彩",
            # 特征——构图
            "构图景别": "构图",
            "镜头效果": "构图",
        },
        # 特殊源（非子分类文件）的映射
        "special_source_map": {
            "anima/lora角色": "角色名",
        },
        # fallback
        "default_section": "其他",
    },
}


def get_prompt_section(source_name: str, subcategory: str = "",
                       model: str = "anima") -> tuple[int, str]:
    """
    根据源名和子分类名，返回 (section_index, section_name)
    用于排序：index 越小越靠前
    """
    cfg = PROMPT_SECTION_ORDER.get(model, PROMPT_SECTION_ORDER["anima"])
    order = cfg["section_order"]
    sub_map = cfg["subcategory_map"]
    special_map = cfg.get("special_source_map", {})
    default = cfg.get("default_section", "其他")

    # 1. 先查特殊源（anim/lora角色等）
    for key, section in special_map.items():
        if key in source_name:
            idx = order.index(section) if section in order else len(order)
            return (idx, section)

    # 2. 按子分类名查
    section = sub_map.get(subcategory, default)
    idx = order.index(section) if section in order else len(order)
    return (idx, section)

# ─── 服装-动作互斥组（防矛盾提示词） ──────────────────────────
# 同组内两个不同项不能同时出现 —— 比如"thighhighs"和"barefoot"矛盾
# 但"thighhighs"和"high heels"不矛盾（可以穿长袜+高跟鞋）
# 覆盖：鞋袜、头饰、眼镜、首饰、上衣、下装、内衣、全身、配饰
CLOTHING_CONFLICT_GROUPS = [
    # ── 1. 袜类/腿饰 vs 光腿/脱袜 ──
    # 动作方: 在脱/没穿；状态方: 穿着中
    ["thighhighs", "kneehighs", "stockings", "pantyhose", "tights",
     "socks", "legwear", "hose", "fishnets", "net tights",
     "taking off socks", "removing socks", "removing stockings",
     "socks removed", "stockings removed",
     "barefoot", "bare foot", "bare legs", "no socks", "no stockings",
     "no tights", "no legwear", "bare thighs", "exposed legs",
     "partially undressed bottom", "half undressed bottom"],

    # ── 2. 鞋类 vs 光脚/脱鞋 ──
    ["shoes", "boots", "sandals", "pumps", "loafers", "sneakers",
     "flats", "mules", "clogs", "platforms", "wedges",
     "taking off shoes", "removing shoes", "shoes removed",
     "barefoot", "bare foot", "bare feet", "no shoes", "without shoes",
     "shoeless", "unshod", "bare soles"],

    # ── 3. 高跟鞋 vs 平底/光脚 ──
    ["high heels", "stiletto", "heeled boots", "heeled sandals",
     "platform heels", "kitten heels", "wedge heels",
     "no heels", "flat shoes", "without heels", "flat sandals",
     "barefoot", "bare foot"],

    # ── 4. 手套 vs 不戴手套/露手 ──
    ["gloves", "mittens", "handwear", "wrist gloves", "arm gloves",
     "opera gloves", "fingerless gloves",
     "taking off gloves", "removing gloves",
     "bare hands", "no gloves", "bare fingers"],

    # ── 5. 帽子/头饰 vs 不戴/脱帽 ──
    ["hat", "cap", "hood", "hoodie up", "headgear", "beret",
     "cowboy hat", "top hat", "beanie", "sun hat", "baseball cap",
     "taking off hat", "removing hat", "hat removed",
     "bare head", "no hat", "no cap", "uncovered head",
     "bald", "head uncovered"],

    # ── 6. 眼镜 vs 不戴眼镜 ──
    ["glasses", "sunglasses", "eyewear", "goggles", "monocle",
     "safety glasses", "reading glasses", "shades",
     "taking off glasses", "removing glasses",
     "no glasses", "without glasses", "no eyewear"],

    # ── 7. 颈饰 vs 不戴颈饰 ──
    ["necklace", "choker", "neck ribbon", "collar", "pendant",
     "beads", "necklet", "amulet", "chains",
     "bare neck", "no necklace", "no choker", "no collar",
     "without necklace", "neck exposed"],

    # ── 8. 围巾/披肩 vs 不戴 ──
    ["scarf", "muffler", "stole", "wrap", "shawl",
     "removing scarf", "scarf removed",
     "no scarf", "no muffler", "bare neck", "without scarf"],

    # ── 9. 上身着装 vs 裸露/脱衣/半脱 动作主导 ──
    # 动作: 任何"脱"相关的动作 → 移除上身所有的穿着描述
    # 状态: 穿着上衣 ↔ 裸露 / 正在脱
    ["shirt", "blouse", "t-shirt", "tank top", "crop top",
     "sweater", "hoodie", "sweatshirt", "cardigan",
     "jacket", "coat", "blazer", "vest", "outerwear",
     "uniform top", "jersey", "polo", "turtleneck",
     "chest", "torso", "upper body", "belly", "midriff",
     # 动作方——这些一出现，上身任何穿着都矛盾
     "shirtless", "bare chest", "no shirt", "topless",
     "no top", "bare torso", "nude upper body",
     "no jacket", "no coat", "jacket removed", "coat removed",
     "taking off shirt", "removing shirt", "shirt removed",
     "pulling off shirt", "shirt pulled off",
     "unbuttoning", "unbuttoned", "open shirt",
     "shirt open", "shirt hanging open",
     # 半脱状态
     "partially undressed top", "half undressed top",
     "clothes torn top", "torn shirt", "ripped shirt",
     "shirt slipping off", "shirt falling off",
     "clothes partially removed",
     # 掀衣服动作
     "lift shirt", "lifting shirt", "shirt lifted",
     "pull up shirt", "pulling up shirt"],

    # ── 10. 下身着装 vs 裸露/脱/半脱 动作主导 ──
    ["pants", "jeans", "trousers", "leggings", "shorts",
     "skirt", "miniskirt", "pleated skirt", "skirt",
     "shorts", "hotpants", "sweatpants",
     "no pants", "no skirt", "no shorts",
     "bottomless", "nude bottom", "bare legs",
     "taking off skirt", "removing pants", "pants removed",
     "pulling down pants", "pants pulled down",
     "skirt lifted", "skirt pulled up", "lifting skirt",
     "skirt hitched up", "skirt sliding up",
     "partially undressed bottom", "half undressed bottom",
     "torn pants", "ripped jeans", "pants torn",
     "underwear visible", "waistband visible"],

    # ── 11. 连衣裙/连体 vs 脱/裸露 ──
    ["dress", "gown", "evening dress", "sundress",
     "bodysuit", "catsuit", "leotard", "unitard",
     "no dress", "taking off dress", "dress removed",
     "dress pulled up", "dress hiked up",
     "dress slipping off", "dress falling off",
     "naked", "undressed"],

    # ── 12. 内衣 vs 不穿/外露 ──
    ["bra", "bikini", "swimsuit", "lingerie", "underwear",
     "panties", "briefs", "boxers", "bikini bottom",
     "braless", "no bra", "no underwear", "without panties",
     "underwear visible", "panties visible",
     "removing underwear", "taking off underwear",
     "topless", "naked", "nude"],

    # ── 13. 腰带/腰饰 vs 不戴 ──
    ["belt", "waist belt", "sash", "obi", "waistband",
     "no belt", "without belt", "belt removed",
     "unbuckled", "unbuckling"],

    # ── 14. 耳饰 vs 不戴 ──
    ["earrings", "ear cuffs", "ear studs",
     "no earrings", "without earrings"],

    # ── 15. 腕表/手链 vs 不戴 ──
    ["watch", "wristwatch", "bracelet", "bangle", "wristband",
     "no watch", "no bracelet", "without bracelet"],
]


def check_conflict(tags: list) -> list:
    """
    检查标签列表中的服装/动作冲突
    返回所有冲突对被移除的索引（保留前者移除后者）
    如果没有冲突返回空列表
    """
    low_tags = [t.lower() for t in tags]
    remove_indices = set()

    # 特殊检查：naked/nude + 任何穿着 → 冲突
    nude_keywords = ["nude", "naked", "fully nude", "completely naked",
                     "no clothes", "no clothing", "without clothes"]
    clothing_keywords = ["shirt", "pants", "skirt", "dress", "jacket", "coat",
                         "shoes", "socks", "hat", "gloves", "underwear",
                         "swimsuit", "uniform", "lingerie", "thighhighs"]
    has_nude = any(any(nk == lt for nk in nude_keywords) for lt in low_tags)
    if has_nude:
        for i, lt in enumerate(low_tags):
            if any(nk == lt for nk in nude_keywords):
                continue  # 保留 nude/naked
            for ck in clothing_keywords:
                if ck in lt or lt in ck:
                    remove_indices.add(i)
                    break

    for i in range(len(low_tags)):
        if i in remove_indices:
            continue
        for j in range(i + 1, len(low_tags)):
            if j in remove_indices:
                continue
            # 检查 CLOTHING_CONFLICT_GROUPS
            for group in CLOTHING_CONFLICT_GROUPS:
                i_matches = set()
                j_matches = set()
                for g in group:
                    gl = g.lower()
                    # 精确匹配 或 长关键词作为子串匹配
                    if gl == low_tags[i] or (len(gl) > 4 and gl in low_tags[i]):
                        i_matches.add(g)
                    if gl == low_tags[j] or (len(gl) > 4 and gl in low_tags[j]):
                        j_matches.add(g)
                # 双方都在同一组有匹配，且不是匹配到完全相同的项
                if i_matches and j_matches and i_matches != j_matches:
                    # 避免"heels"在"high heels"中误匹配自身的子串
                    # 只有当两个标签匹配到组内不同的项时才判冲突
                    if i_matches - j_matches or j_matches - i_matches:
                        remove_indices.add(j)
                        break
    
    return sorted(remove_indices)

# ─── 核心工具函数 ──────────────────────────────────────────────

def is_nsfw_category(name: str) -> bool:
    """检查分类名是否为 NSFW"""
    return name == "NSFW" or name == "NSFW标签"


def shuffle(arr: list) -> list:
    """Fisher-Yates 洗牌算法"""
    a = arr[:]
    for i in range(len(a) - 1, 0, -1):
        j = random.randint(0, i)
        a[i], a[j] = a[j], a[i]
    return a


def random_weight(enabled: bool) -> float:
    """生成随机权重 0.5~1.5，步长 0.1"""
    if not enabled:
        return 1.0
    return round(0.5 + random.random() * 1.0, 1)


def matches_body_group(text: str, body_groups: list = None) -> Optional[list]:
    """
    检查文本是否匹配身体标签组中的某一组
    返回匹配的组列表，无匹配返回 None
    """
    groups = body_groups or NSFW_BODY_GROUPS
    text_lower = text.lower()
    for group in groups:
        for tag in group:
            if tag.lower() in text_lower:
                return group
    return None


def has_body_tag_conflict(text: str, selected_tags: list) -> bool:
    """
    检查文本是否有与已选标签冲突的身体标签
    即：文本命中某组，而已选标签也命中同一组的不同项
    """
    group = matches_body_group(text)
    if not group:
        return False
    text_lower = text.lower()
    for tag in group:
        if tag.lower() == text_lower:
            return False  # 自己重复自己不算冲突（调用侧会去重）
    for sel in selected_tags:
        sel_en = sel.get("en", "").lower()
        if sel_en in [t.lower() for t in group]:
            return True  # 已选同组不同项 → 冲突
    return False


def find_tag_in_categories(categories: list, en_tag: str) -> Optional[dict]:
    """在分类数据中查找标签，返回标签信息（含 category/subcategory）"""
    for cat in categories:
        for sub in cat.get("subcategories", []):
            for tag in sub.get("tags", []):
                if tag["en"] == en_tag:
                    return {
                        "en": tag["en"],
                        "zh": tag.get("zh", tag["en"]),
                        "weight": 1.0,
                        "category": cat["name"],
                        "subcategory": sub["name"],
                    }
    return None


# ─── 核心选取算法 ──────────────────────────────────────────────

def smart_pick(categories: list, selected_tags: list = None,
               allow_nsfw: bool = False, rand_weight: bool = False,
               global_max: int = 10,
               cat_limits: dict = None, sc_limits: dict = None,
               hidden_categories: dict = None,
               hidden_subcategories: dict = None) -> list:
    """
    核心随机标签选取算法

    参数:
        categories: 分类数据（tags-flux.json 的 categories 列表）
        selected_tags: 已选中的标签列表（锁定标签）
        allow_nsfw: 是否允许 NSFW
        rand_weight: 是否随机权重
        global_max: 最大标签数
        cat_limits: {分类名: True} - 强制从这些分类中选
        sc_limits: {"分类|子分类": True} - 强制从这些子分类中选
        hidden_categories: {分类名: True} - 隐藏的分类
        hidden_subcategories: {"分类|子分类": True} - 隐藏的子分类

    返回:
        标签列表 [{"en":..., "zh":..., "weight":..., "category":..., "subcategory":...}, ...]
    """
    selected = list(selected_tags) if selected_tags else []
    seen = {t["en"] for t in selected}
    used_pools = {}  # poolGroup -> True

    # 跟踪已选标签的 poolGroup
    for t in selected:
        cat_name = t.get("category", "")
        sub_name = t.get("subcategory", "")
        if not cat_name or not sub_name:
            continue
        cat = next((c for c in categories if c["name"] == cat_name), None)
        if not cat:
            continue
        sub = next((s for s in cat.get("subcategories", []) if s["name"] == sub_name), None)
        if sub and sub.get("poolGroup"):
            used_pools[sub["poolGroup"]] = True

    cat_limits = cat_limits or {}
    sc_limits = sc_limits or {}
    hidden_cats = hidden_categories or {}
    hidden_subs = hidden_subcategories or {}

    has_limits = any(v for v in list(cat_limits.values()) + list(sc_limits.values()))

    # 记录哪些分类/子分类已有标签
    cat_used = {}
    sub_used = {}
    for t in selected:
        c = t.get("category")
        s = t.get("subcategory")
        if c:
            cat_used[c] = True
            if s:
                sub_used[f"{c}|{s}"] = True

    def is_available(en: str) -> bool:
        """标签是否可用（未选取、无冲突）"""
        if en in seen:
            return False
        # 检查身体标签冲突
        group = matches_body_group(en)
        if group:
            for sel in selected:
                sel_en = sel.get("en", "").lower()
                for gt in group:
                    if gt.lower() == sel_en:
                        return False  # 同组已有
        return True

    def pick_from_subcategory(tags_list, cat_name, sub_name, pool_group=None):
        """从子分类的标签列表中随机取一个可用标签"""
        if pool_group and pool_group in used_pools:
            return False
        shuffled = shuffle(tags_list)
        for tag in shuffled:
            en = tag["en"]
            if is_available(en):
                seen.add(en)
                if pool_group:
                    used_pools[pool_group] = True
                selected.append({
                    "en": en,
                    "zh": tag.get("zh", en),
                    "weight": random_weight(rand_weight),
                    "category": cat_name,
                    "subcategory": sub_name,
                })
                return True
        return False

    def pick_from_category(cat, skip_existing=True):
        """从某个分类中随机选一个可用标签"""
        if not allow_nsfw and is_nsfw_category(cat["name"]):
            return False
        if hidden_cats.get(cat["name"]):
            return False

        candidates = []
        for sub in cat.get("subcategories", []):
            key = f"{cat['name']}|{sub['name']}"
            if hidden_subs.get(key):
                continue
            if skip_existing:
                if sub_used.get(key):
                    continue
                if sub.get("poolGroup") and sub["poolGroup"] in used_pools:
                    continue
            for tag in sub.get("tags", []):
                candidates.append({
                    "tag": tag,
                    "subcategory": sub["name"],
                    "pool_group": sub.get("poolGroup"),
                })

        if not candidates:
            return False

        shuffled = shuffle(candidates)
        for cand in shuffled:
            tag = cand["tag"]
            en = tag["en"]
            if is_available(en):
                seen.add(en)
                pg = cand["pool_group"]
                if pg:
                    used_pools[pg] = True
                selected.append({
                    "en": en,
                    "zh": tag.get("zh", en),
                    "weight": random_weight(rand_weight),
                    "category": cat["name"],
                    "subcategory": cand["subcategory"],
                })
                return True
        return False

    # ── 阶段 1: 处理有限制的分类/子分类 ──
    if has_limits:
        for cat in categories:
            if not allow_nsfw and is_nsfw_category(cat["name"]):
                continue
            if hidden_cats.get(cat["name"]):
                continue
            if cat_limits.get(cat["name"]) and (cat_used.get(cat["name"]) or pick_from_category(cat)):
                pass  # 已处理

        for cat in categories:
            if not allow_nsfw and is_nsfw_category(cat["name"]):
                continue
            if hidden_cats.get(cat["name"]):
                continue
            if not cat_limits.get(cat["name"]):
                for sub in cat.get("subcategories", []):
                    key = f"{cat['name']}|{sub['name']}"
                    if hidden_subs.get(key):
                        continue
                    if sub_used.get(key) or sc_limits.get(key):
                        if sc_limits.get(key):
                            pick_from_subcategory(sub.get("tags", []), cat["name"], sub["name"], sub.get("poolGroup"))

        # 剩余填充
        if len(selected) < global_max:
            for cat in categories:
                if len(selected) >= global_max:
                    break
                if not allow_nsfw and is_nsfw_category(cat["name"]):
                    continue
                if hidden_cats.get(cat["name"]):
                    continue
                if cat_limits.get(cat["name"]) or cat_used.get(cat["name"]):
                    continue
                sc_keys = [f"{cat['name']}|{s['name']}" for s in cat.get("subcategories", [])]
                if any(sc_limits.get(k) for k in sc_keys):
                    continue
                pick_from_category(cat)

    # ── 阶段 2: 无限制，随机填充 ──
    else:
        for cat in categories:
            if len(selected) >= global_max:
                break
            if not allow_nsfw and is_nsfw_category(cat["name"]):
                continue
            if hidden_cats.get(cat["name"]):
                continue
            if cat_used.get(cat["name"]):
                continue
            pick_from_category(cat)

    # ── 阶段 3: Boost 填充 ──
    if len(selected) < global_max:
        # 收集已选标签中的触发词
        boost_keywords = set()
        for t in selected:
            en = t.get("en", "").lower()
            for tb in TRIGGER_BOOSTS:
                if tb["trigger"] in en:
                    for b in tb["boost"]:
                        boost_keywords.add(b)

        boost_candidates = []
        other_candidates = []

        for cat in categories:
            if not allow_nsfw and is_nsfw_category(cat["name"]):
                continue
            if hidden_cats.get(cat["name"]):
                continue
            for sub in cat.get("subcategories", []):
                key = f"{cat['name']}|{sub['name']}"
                if hidden_subs.get(key):
                    continue
                if sub.get("poolGroup") and sub["poolGroup"] in used_pools:
                    continue
                for tag in sub.get("tags", []):
                    if not is_available(tag["en"]):
                        continue
                    if matches_body_group(tag["en"]):
                        # 检查是否与已选标签同组
                        group = matches_body_group(tag["en"])
                        if group:
                            conflict = False
                            for sel in selected:
                                sel_en = sel.get("en", "").lower()
                                for gt in group:
                                    if gt.lower() == sel_en and gt.lower() != tag["en"].lower():
                                        conflict = True
                                        break
                                if conflict:
                                    break
                            if conflict:
                                continue
                    cand = {
                        "tag": tag,
                        "category": cat["name"],
                        "subcategory": sub["name"],
                        "pool_group": sub.get("poolGroup"),
                    }
                    if tag["en"].lower() in boost_keywords:
                        boost_candidates.append(cand)
                    else:
                        other_candidates.append(cand)

        # Boost 候选优先，其他候选次之
        merged = shuffle(boost_candidates) + shuffle(other_candidates)
        for cand in merged:
            if len(selected) >= global_max:
                break
            pg = cand["pool_group"]
            if pg and pg in used_pools:
                continue
            tag = cand["tag"]
            en = tag["en"]
            if is_available(en):
                seen.add(en)
                if pg:
                    used_pools[pg] = True
                selected.append({
                    "en": en,
                    "zh": tag.get("zh", en),
                    "weight": random_weight(rand_weight),
                    "category": cand["category"],
                    "subcategory": cand["subcategory"],
                })

    # ── 阶段 4: "no human" 过滤 ──
    if any("no human" in t.get("en", "").lower() for t in selected):
        exclude_cats = {"发型与发色", "五官与表情", "服装与配饰", "动作姿态", "NSFW"}
        selected = [
            t for t in selected
            if t.get("category") not in exclude_cats
            and not (t.get("category") == "人物主体" and t.get("subcategory") != "人数与性别")
        ]

    return selected


# ─── 排序与组装 ──────────────────────────────────────────────

def sort_by_priority(tags: list, nsfw_boost: bool = False) -> list:
    """
    按分类优先级排序
    nsfw_boost=True 时 NSFW 子分类使用特殊优先级
    """
    def _key(t):
        cat_pri = CATEGORY_PRIORITY.get(t.get("category", ""), 90)
        sc_name = t.get("subcategory", "")
        if nsfw_boost and t.get("category") == "NSFW":
            cat_pri = NSFW_SUBCAT_PRIORITY.get(sc_name, 92)
        return (cat_pri, sc_name or "")
    return sorted(tags, key=_key)


def format_tag(text: str, space_mode: int = 0) -> str:
    """
    格式化标签空格模式
    0: 保持原样
    1: 空格 → 下划线 (word embedding 风格)
    2: 下划线 → 空格
    """
    if space_mode == 1:
        return text.replace(" ", "_")
    elif space_mode == 2:
        return text.replace("_", " ")
    return text


def get_label(tag: dict, model: str = "anima") -> str:
    """
    获取标签显示文本
    flux 用中文，anima 用英文
    """
    if model == "flux":
        if tag.get("category") == "NSFW":
            return tag.get("en", "")
        return tag.get("zh", tag.get("en", ""))
    return tag.get("en", "")


def build_prompt(tags: list, model: str = "anima",
                 use_weights: bool = None, use_quality: bool = None,
                 template: str = "", space_mode: int = 0,
                 use_natural_order: bool = True,
                 allow_nsfw: bool = False) -> str:
    """
    组装最终提示词字符串

    参数:
        tags: 标签列表
        model: "anima" | "flux"
        use_weights: 是否使用权重语法
        use_quality: 是否前面加品质标签
        template: 模板字符串（含 {tags} 占位符）
        space_mode: 空格模式 0/1/2
        use_natural_order: 是否按分类优先级排序
        allow_nsfw: 是否允许 NSFW（影响排序）

    返回:
        组装后的提示词字符串
    """
    cfg = MODEL_CONFIGS.get(model, MODEL_CONFIGS["anima"])
    use_weights = cfg["use_weights"] if use_weights is None else use_weights
    use_quality = cfg["use_quality"] if use_quality is None else use_quality
    use_natural_order = cfg["use_natural_order"] if use_natural_order is None else use_natural_order
    sep = cfg["separator"]

    # 排序
    if use_natural_order:
        sorted_tags = sort_by_priority(tags, nsfw_boost=allow_nsfw)
    else:
        sorted_tags = list(tags)

    # 格式化每个标签
    parts = []
    for tag in sorted_tags:
        text = format_tag(get_label(tag, model), space_mode)
        w = tag.get("weight", 1.0)

        if model == "flux":
            # Flux 不用权重语法
            parts.append(text)
        elif abs(w - 1.0) < 0.01:
            # 权重 = 1.0，直接放
            parts.append(text)
        elif w > 1.0:
            parts.append(f"({text}:{w:.1f})")
        else:
            parts.append(f"[{text}:{w:.1f}]")

    prompt = sep.join(parts)

    # 前面加品质标签
    if use_quality and tags:
        if model == "anima":
            q_tags = DEFAULT_QUALITY_TAGS
        else:
            q_tags = QUALITY_TAGS.get(model, [])
        q_str = sep.join(q_tags)
        prompt = q_str + sep + prompt

    # 模板替换
    if template and "{tags}" in template:
        prompt = template.replace("{tags}", prompt)

    return prompt


# ─── 数据加载 ──────────────────────────────────────────────

def load_tag_data(data_dir: str, model: str = "anima") -> Optional[dict]:
    """
    加载标签数据

    参数:
        data_dir: 数据目录路径
        model: "anima" 或 "flux"

    返回:
        {"categories": [...]} 或 None
    """
    fname = f"tags-{model}.json"
    fpath = Path(data_dir) / fname
    if not fpath.exists():
        fpath = Path(data_dir) / "random_prompt" / fname
    if not fpath.exists():
        return None
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def generate_random_prompt(data_dir: str, model: str = "anima",
                           allow_nsfw: bool = False, global_max: int = 10,
                           use_weights: bool = None, use_quality: bool = None,
                           template: str = "", pin_tags: list = None) -> str:
    """
    一键生成随机提示词

    参数:
        data_dir: 数据目录
        model: "anima" | "flux"
        allow_nsfw: 允许 NSFW
        global_max: 最大标签数
        use_weights: 使用权重语法
        use_quality: 使用品质标签
        template: 模板
        pin_tags: 锁定标签列表 [{"en":..., "zh":..., "category":..., "subcategory":...}, ...]

    返回:
        提示词字符串
    """
    data = load_tag_data(data_dir, model)
    if not data:
        return ""
    categories = data.get("categories", [])

    # 将 pin_tags 转换为 proper format
    pinned = []
    for pt in (pin_tags or []):
        if isinstance(pt, str):
            # 字符串 → 尝试在分类中查找
            info = find_tag_in_categories(categories, pt)
            if info:
                pinned.append(info)
        else:
            pinned.append(pt)

    tags = smart_pick(
        categories,
        selected_tags=pinned,
        allow_nsfw=allow_nsfw,
        global_max=global_max,
    )

    if not tags:
        return ""

    return build_prompt(
        tags,
        model=model,
        use_weights=use_weights if use_weights is not None else MODEL_CONFIGS[model]["use_weights"],
        use_quality=use_quality if use_quality is not None else MODEL_CONFIGS[model]["use_quality"],
        template=template,
        allow_nsfw=allow_nsfw,
    )
