"""
重新整理魔导书数据：大分类 + 子分类
"""
import json
from pathlib import Path

SRC = r"D:\win下载\SmartComfy-v1.0.9\Vue\dist\random-prompt\tags-anima.json"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# 加载原始数据建立 en -> {zh, category, subcategory} 映射
with open(SRC, "r", encoding="utf-8") as f:
    raw = json.load(f)

tag_map = {}
for cat in raw.get("categories", []):
    for sub in cat.get("subcategories", []):
        for tag in sub.get("tags", []):
            en = tag["en"]
            zh = tag.get("zh", en)
            tag_map[en] = {"zh": zh, "cat": cat["name"], "sub": sub["name"]}

# 新结构：大分类 -> [(子分类, [tag列表])]
STRUCTURE = {
    "画质与渲染": [
        ("品质保证", [
            "masterpiece", "best quality", "highres", "ultra-detailed",
            "intricate details", "official art", "detailed background", "sharp focus",
        ]),
        ("光影效果", [
            "volumetric lighting", "cinematic lighting", "soft lighting",
            "dramatic lighting", "rim lighting", "backlighting",
            "sunlight", "moonlight", "neon lights", "lens flare",
        ]),
    ],
    "艺术风格": [
        ("动漫风格", [
            "anime style", "manga style", "anime screencap", "key visual",
            "promotional art", "game cg", "visual novel style",
            "retro anime style", "90s anime style", "chibi",
            "makoto shinkai style", "studio ghibli style",
            "kyoto animation style", "ufotable style", "trigger style",
        ]),
        ("画风技法", [
            "digital art", "cel shading", "watercolor", "pastel color",
            "sketch", "line art",
        ]),
        ("渲染技术", [
            "unreal engine 5", "octane render", "blender cycles",
            "arnold render", "redshift render", "3d render", "vray render",
            "corona render", "physically based rendering", "PBR texturing",
            "ray tracing", "global illumination", "path tracing",
            "ambient occlusion", "screen space reflections",
            "subsurface scattering", "SSS", "translucent material",
            "volume scattering", "light transmission", "metallic roughness",
            "sharp specular", "soft highlight", "micro detail",
            "high detail skin", "fresnel effect", "anisotropic reflection",
            "ultra realistic", "photorealistic", "hyperdetailed",
            "cinematic render", "hdr lighting",
        ]),
    ],
    "人物特征": [
        ("体型", [
            "small breasts", "medium breasts", "large breasts", "flat chest",
            "curvy", "slender", "muscular", "tall", "short", "petite", "slim",
            "athletic", "fit", "toned", "voluptuous", "busty",
            "broad shoulders", "narrow waist", "wide hips", "hourglass figure",
            "muscular female", "thick thighs", "thick body", "delicate body",
            "pear-shaped", "apple-shaped",
        ]),
        ("肤质", ["pale skin", "tanned", "dark skin"]),
        ("年龄段", ["teen", "adult", "mature", "young", "twenties", "thirties"]),
    ],
    "发型发色": [
        ("发型", [
            "long hair", "short hair", "medium hair", "very long hair",
            "ponytail", "twintails", "braid", "side braid", "bob cut",
            "hime cut", "ahoge", "bangs", "side ponytail", "low twintails",
            "wavy hair", "curly hair", "straight hair",
        ]),
        ("发色", [
            "blonde hair", "brown hair", "black hair", "silver hair",
            "white hair", "red hair", "blue hair", "green hair",
            "purple hair", "pink hair", "orange hair", "aqua hair",
            "multicolored hair", "gradient hair",
        ]),
    ],
    "面部特征": [
        ("眼色", [
            "blue eyes", "red eyes", "green eyes", "purple eyes",
            "yellow eyes", "heterochromia", "aqua eyes", "pink eyes",
            "large eyes",
        ]),
        ("表情", [
            "smile", "grin", "blush", "embarrassed", "angry", "sad",
            "surprised", "expressionless", "wink", "pout",
            "seductive smile", "tears",
            "eye contact", "looking at viewer", "looking away",
            "looking up", "looking down",
            "closed eyes", "parted lips", "open mouth",
            "lipstick", "mole under eye",
        ]),
    ],
    "服装配饰": [
        ("上衣", [
            "shirt", "blazer", "sweater", "hoodie", "t-shirt", "tank top",
            "cardigan", "off shoulder", "strapless", "crop top", "corset",
            "turtleneck", "coat", "jacket", "vest", "denim jacket",
            "leather jacket", "bomber jacket", "trench coat", "cape", "cloak",
            "shawl", "sailor uniform", "blazer uniform", "lab coat",
            "cheongsam", "qipao", "puffy sleeves", "short sleeves",
            "long sleeves", "rolled-up sleeves", "sleeveless",
            "frills", "ruffles", "lace trim",
        ]),
        ("下装", [
            "skirt", "pleated skirt", "miniskirt", "shorts", "jeans",
            "trousers", "leggings", "sweatpants", "hotpants", "long skirt",
            "overalls", "suspenders",
        ]),
        ("连衣裙", [
            "dress", "sundress", "apron", "gothic lolita", "lolita fashion",
            "maid headdress", "santa costume",
        ]),
        ("内衣泳装", ["bikini", "swimsuit", "lingerie", "bodysuit", "armor"]),
        ("鞋袜", [
            "thighhighs", "kneehighs", "socks", "pantyhose", "barefoot",
            "boots", "high heels", "sneakers", "sandals", "loafers",
        ]),
        ("配饰", [
            "hair ribbon", "hair bow", "hairband", "hairclip",
            "hair ornament", "earrings", "necklace", "choker", "gloves",
            "detached sleeves", "ribbon", "bracelet", "watch", "ring",
            "belt", "hat", "beret", "baseball cap", "cowboy hat", "beanie",
            "scarf", "bag", "wings", "tail", "halo", "horns", "animal ears",
            "tiara", "crown", "veil", "blindfold", "eye mask", "collar",
            "bow tie", "necktie", "ascot", "pendant",
            "headphones", "animal costume",
        ]),
    ],
    "材质质感": [
        ("金属", [
            "polished metal", "chrome", "gold texture", "matte metal",
            "brushed metal",
        ]),
        ("布料", [
            "satin silk", "sheer translucent", "lace fabric", "silk ribbon",
            "chiffon", "tulle", "matte cloth", "thick velvet", "cotton texture",
            "wool fabric", "linen fabric", "denim texture", "canvas",
        ]),
        ("皮革", [
            "gloss leather", "matte leather", "patent leather",
            "leather texture", "latex", "vinyl",
        ]),
        ("表面", [
            "glossy", "matte", "smooth", "rough", "translucent",
            "opaque", "reflective", "mirror surface",
        ]),
    ],
    "动作姿态": [
        ("站坐卧", [
            "standing", "sitting", "lying", "kneeling", "leaning forward",
            "posing",
        ]),
        ("腿部动作", [
            "crossed legs", "walking", "running", "jumping", "dancing",
            "sprinting", "jogging", "skipping", "leaping",
            "diving", "climbing", "sneaking", "dashing",
        ]),
        ("手臂动作", [
            "arms behind back", "arms crossed", "hand on hip",
            "hand on face", "hand on chin", "holding hands", "salute",
            "reaching out", "pointing", "thumbs up", "peace sign",
            "holding", "praying hands", "clapping",
            "stretching", "waving", "head tilt",
        ]),
        ("全身动作", [
            "fighting stance", "flying", "swimming", "falling", "floating",
            "looking back", "holding weapon",
            "spinning", "twirling",
            "kicking", "punching",
            "archery", "shooting", "aiming",
            "eating", "drinking",
            "playing instrument", "typing", "writing", "drawing", "painting",
            "cooking", "cleaning",
        ]),
    ],
    "场景构图": [
        ("自然环境", [
            "outdoors", "nature", "forest", "mountain", "beach", "ocean",
            "river", "lake", "flower field", "cherry blossoms",
        ]),
        ("建筑场景", [
            "city", "street", "building", "school", "classroom",
            "bedroom", "kitchen", "library", "cafe", "shop",
        ]),
        ("天气时间", [
            "clear sky", "cloudy", "rain", "snow", "fog",
            "night", "sunset", "sunrise", "full moon", "starry sky",
        ]),
        ("色彩氛围", [
            "vibrant colors", "pastel colors", "monochrome",
            "warm colors", "cool colors", "high contrast", "muted colors",
            "romantic", "mysterious", "peaceful", "dramatic",
            "dreamy", "cozy", "energetic",
        ]),
        ("构图景别", [
            "portrait", "upper body", "cowboy shot", "full body",
            "close-up", "extreme close-up",
            "from front", "from behind", "from side",
            "from above", "from below", "dutch angle",
            "centered", "symmetry", "rule of thirds",
        ]),
        ("镜头效果", [
            "depth of field", "bokeh", "anamorphic lens",
            "worm's eye view", "bird's eye view",
            "low angle shot", "high angle shot",
            "over the shoulder", "pov shot",
            "strong bokeh", "shallow depth of field",
            "deep depth of field",
            "lens flare", "chromatic aberration",
            "vignette", "motion blur", "film grain",
        ]),
    ],
}

# 旧文件清理
for f in DATA_DIR.glob("sc_*.json"):
    f.unlink()
    print(f"  删旧: {f.name}")

# 生成新文件
total = 0
for main_cat, subs in STRUCTURE.items():
    entries = []
    for sub_name, tags in subs:
        for en in tags:
            info = tag_map.get(en, {})
            zh = info.get("zh", en)
            entries.append({
                "name": zh,
                "tags": en,
                "style": sub_name,  # 子分类名
                "cat": main_cat,    # 大分类名
            })
    fname = f"sc_{main_cat}.json"
    fpath = DATA_DIR / fname
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    sub_list = ", ".join(s[0] for s in subs)
    print(f"  {fname}: {len(entries)}条 [{sub_list}]")
    total += len(entries)

print(f"\n总计: {total}条, {len(STRUCTURE)}个大类, 含{sum(len(s) for _, s in STRUCTURE.items())}个子分类")
