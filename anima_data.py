"""
Anima 数据融合模块

数据目录结构 (data/)：
  anima/         - Anima 原始数据（画师、角色、服装）— 运行时从 Anima-Tools JS 读取
  scene/         - 场景/环境
  lighting/      - 光影/色调
  pose_action/   - 动作/姿势/表情
  shot/          - 镜头/构图
  custom/        - 用户自定义

每条数据格式：
  {"name": "显示名", "tags": "逗号分隔的提示词标签", "category": "分类名(可选)", "note": "备注(可选)"}
"""

import json
import os
import re
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

# Anima-Tools JS 数据目录（插件自带，不依赖外部）
_ANIMA_TOOLS_JS_DIR = Path(__file__).resolve().parent / "data" / "anima_tools"

# 萌娘百科风格的角色作品分类中文翻译（覆盖全部已知版权）
_CHARACTER_CATEGORY_CN = {
    # ── 已翻译的 Top 50 ──
    "pokemon": "宝可梦",
    "kantai collection": "舰队Collection",
    "fate (series)": "Fate系列",
    "hololive": "ホロライブ",
    "idolmaster": "偶像大师",
    "touhou": "东方Project",
    "blue archive": "蔚蓝档案",
    "arknights": "明日方舟",
    "azur lane": "碧蓝航线",
    "fire emblem": "火焰纹章",
    "genshin impact": "原神",
    "umamusume": "赛马娘",
    "original": "原创",
    "precure": "光之美少女",
    "nijisanji": "彩虹社",
    "fate/grand order": "Fate/Grand Order",
    "honkai (series)": "崩坏系列",
    "final fantasy": "最终幻想",
    "girls' frontline": "少女前线",
    "granblue fantasy": "碧蓝幻想",
    "girls und panzer": "少女与战车",
    "kemono friends": "兽娘动物园",
    "jojo no kimyou na bouken": "JOJO的奇妙冒险",
    "gundam": "高达",
    "vocaloid": "VOCALOID",
    "league of legends": "英雄联盟",
    "love live!": "LoveLive!",
    "danganronpa (series)": "弹丸论破系列",
    "touken ranbu": "刀剑乱舞",
    "tales of (series)": "传说系列",
    "persona": "女神异闻录",
    "lyrical nanoha": "魔法少女奈叶",
    "yu-gi-oh!": "游戏王",
    "one piece": "海贼王",
    "dragon ball": "龙珠",
    "ragnarok online": "仙境传说",
    "bang dream!": "BanG Dream!",
    "umineko no naku koro ni": "海猫鸣泣之时",
    "boku no hero academia": "我的英雄学院",
    "princess connect!": "公主连结",
    "bishoujo senshi sailor moon": "美少女战士",
    "the legend of zelda": "塞尔达传说",
    "dragon quest": "勇者斗恶龙",
    "project moon": "Project Moon",
    "xenoblade chronicles (series)": "异度神剑系列",
    "zenless zone zero": "绝区零",
    "marvel": "漫威",
    "street fighter": "街头霸王",
    "goddess of victory: nikke": "胜利女神：NIKKE",
    "inazuma eleven (series)": "闪电十一人系列",
    # ── 新增翻译 ──
    "world witches series": "世界魔女系列",
    "mahou shoujo madoka magica": "魔法少女小圆",
    "splatoon (series)": "斯普拉遁系列",
    "toaru majutsu no index": "魔法禁书目录",
    "blazblue": "苍翼默示录",
    "overwatch": "守望先锋",
    "mario (series)": "超级马力欧系列",
    "sword art online": "刀剑神域",
    "dungeon meshi": "迷宫饭",
    "honkai: star rail": "崩坏：星穹铁道",
    "project sekai": "Project SEKAI",
    "ensemble stars!": "偶像梦幻祭",
    "guilty gear": "罪恶装备",
    "omori": "OMORI",
    "neptune (series)": "海王星系列",
    "mega man (series)": "洛克人系列",
    "holostars": "ホロスターズ",
    "shingeki no kyojin": "进击的巨人",
    "saki": "咲-Saki-",
    "tokyo afterschool summoners": "东京放课后召唤师",
    "digimon": "数码宝贝",
    "chainsaw man": "电锯人",
    "dc comics": "DC漫画",
    "axis powers hetalia": "黑塔利亚",
    "assault lily": "Assault Lily",
    "neon genesis evangelion": "新世纪福音战士",
    "code geass": "Code Geass",
    "naruto (series)": "火影忍者系列",
    "jujutsu kaisen": "咒术回战",
    "kimetsu no yaiba": "鬼灭之刃",
    "bleach": "死神",
    "indie virtual youtuber": "个人势VTuber",
    "senran kagura": "闪乱神乐",
    "queen's blade": "女王之刃",
    "kill la kill": "斩服少女",
    "apex legends": "Apex英雄",
    "cookie (touhou)": "点心(东方)",
    "eiyuu densetsu": "英雄传说",
    "tengen toppa gurren lagann": "天元突破",
    "tiger & bunny": "TIGER & BUNNY",
    "macross": "超时空要塞",
    "pretty series": "美妙系列",
    "ace attorney": "逆转裁判",
    "suzumiya haruhi no yuuutsu": "凉宫春日的忧郁",
    "kirby (series)": "星之卡比系列",
    "monogatari (series)": "物语系列",
    "sonic (series)": "刺猬索尼克系列",
    "tsukihime": "月姬",
    "the king of fighters": "拳皇",
    "punishing: gray raven": "战双帕弥什",
    "skullgirls": "骷髅女孩",
    "gochuumon wa usagi desu ka?": "请问您今天要来点兔子吗？",
    "saibou shinkyoku": "工作细胞",
    "angel beats!": "Angel Beats!",
    "little busters!": "Little Busters!",
    "kagerou project": "阳炎Project",
    "reverse:1999": "重返未来：1999",
    "rwby": "RWBY",
    "to heart (series)": "To Heart系列",
    "helltaker": "Helltaker",
    "undertale": "Undertale",
    "twisted wonderland": "迪士尼扭曲仙境",
    "voiceroid": "VOICEROID",
    "hibike! euphonium": "吹响吧！上低音号",
    "osomatsu-san": "阿松",
    "high school dxd": "高校DXD",
    "elsword": "艾尔之光",
    "vshojo": "VShojo",
    "bocchi the rock!": "孤独摇滚！",
    "k-on!": "轻音少女",
    "kono subarashii sekai ni shukufuku wo!": "为美好的世界献上祝福！",
    "sousou no frieren": "葬送的芙莉莲",
    "rozen maiden": "蔷薇少女",
    "watashi ga motenai no wa dou kangaetemo omaera ga warui!": "我不受欢迎怎么想都是你们的错！",
    "senki zesshou symphogear": "战姬绝唱",
    "elden ring": "艾尔登法环",
    "to love-ru": "出包王女",
    "resident evil": "生化危机",
    "kingdom hearts": "王国之心",
    "nanashi inc.": "Nanashi Inc.",
    "sayonara zetsubou sensei": "再见！绝望先生",
    "re:zero kara hajimeru isekai seikatsu": "Re:从零开始的异世界生活",
    "spy x family": "间谍过家家",
    "lucky star": "幸运星",
    "higurashi no naku koro ni": "寒蝉鸣泣之时",
    "atelier (series)": "炼金工房系列",
    "houseki no kuni": "宝石之国",
    "yuru yuri": "摇曳百合",
    "amagami": "圣诞之吻",
    "dead or alive": "死或生",
    "clannad": "CLANNAD",
    "wuthering waves": "鸣潮",
    "made in abyss": "来自深渊",
    "hunter x hunter": "全职猎人",
    "magia record: mahou shoujo madoka magica gaiden": "魔法纪录 魔法少女小圆外传",
    "touqi guaitan": "偷气诡谈",
    "golden kamuy": "黄金神威",
    "puyopuyo": "噗哟噗哟",
    "pikmin (series)": "皮克敏系列",
    "len'en": "连缘Project",
    "vspo!": "ぶいすぽ！",
    "haikyuu!!": "排球少年！！",
    "kirakira precure a la mode": "闪耀！光之美少女",
    "22/7": "22/7",
    "onii-chan wa oshimai!": "别当欧尼酱了！",
    "steins;gate": "命运石之门",
    "kobayashi-san chi no maidragon": "小林家的龙女仆",
    "zero no tsukaima": "零之使魔",
    "little witch academia": "小魔女学园",
    "ranma 1/2": "乱马1/2",
    "gintama": "银魂",
    "disgaea": "魔界战记",
    "zombie land saga": "僵尸乐园萨迦",
    "gakuen idolmaster": "学园偶像大师",
    "aikatsu! (series)": "偶像活动系列",
    "little nuns (diva)": "Little Nuns (DIVA)",
    "maria-sama ga miteru": "圣母在上",
    "monster musume no iru nichijou": "魔物娘的同居日常",
    "mahou sensei negima!": "魔法老师涅吉！",
    "nier (series)": "尼尔系列",
    "utau": "UTAU",
    "vampire (game)": "吸血鬼(游戏)",
    "panty & stocking with garterbelt": "吊带袜天使",
    "date a live": "约会大作战",
    "gegege no kitarou": "鬼太郎",
    "aria (manga)": "水星领航员",
    "fairy tail": "妖精的尾巴",
    "working!!": "WORKING!!迷糊餐厅",
    "azumanga daioh": "阿滋漫画大王",
    "os-tan": "OS娘",
    "kanon": "Kanon",
    "shoujo kageki revue starlight": "少女歌剧 Revue Starlight",
    "sekaiju no meikyuu": "世界树迷宫",
    "kamen rider": "假面骑士",
    "black rock shooter": "黑岩射手",
    "go-toubun no hanayome": "五等分的新娘",
    "ore no imouto ga konna ni kawaii wake ga nai": "我的妹妹不可能那么可爱",
    "girls band cry": "Girls Band Cry",
    "oshi no ko": "我推的孩子",
    "kaguya-sama wa kokurasetai ~tensai-tachi no renai zunousen~": "辉夜大小姐想让我告白",
    "yurucamp": "摇曳露营",
    "yume nikki": "梦日记",
    "fullmetal alchemist": "钢之炼金术师",
    "hidamari sketch": "向阳素描",
    "luo xiaohei zhanji": "罗小黑战记",
    "among us": "Among Us",
    "tekken": "铁拳",
    "transformers": "变形金刚",
    "meitantei conan": "名侦探柯南",
    "nichijou": "日常",
    "hataraku saibou": "工作细胞",
    "mawaru penguindrum": "回转企鹅罐",
    "baldur's gate": "博德之门",
    "durarara!!": "无头骑士异闻录",
    "dorohedoro": "异兽魔都",
    "free!": "Free!",
    "my little pony": "小马宝莉",
    "powerpuff girls z": "飞天小女警Z",
    "cardcaptor sakura": "魔卡少女樱",
    "sono bisque doll wa koi wo suru": "更衣人偶坠入爱河",
    "darling in the franxx": "DARLING in the FRANXX",
    "gridman universe": "古立特宇宙",
    "toradora!": "龙与虎",
    "machikado mazoku": "街角魔族",
    "link! like! love live!": "Link! Like! LoveLive!",
    "hyouka": "冰菓",
    "chuunibyou demo koi ga shitai!": "中二病也要谈恋爱",
    "yuri!!! on ice": "冰上的尤里",
    "yahari ore no seishun lovecome wa machigatteiru.": "我的青春恋爱物语果然有问题",
    "senpai ga uzai kouhai no hanashi": "关于前辈很烦人的事",
    "mushoku tensei": "无职转生",
    "infinite stratos": "IS〈无限斯特拉托斯〉",
    "mob psycho 100": "灵能百分百",
    "sanrio": "三丽鸥",
    "kin-iro mosaic": "黄金拼图",
    "mahou girls precure!": "魔法使光之美少女！",
    "hayate no gotoku!": "旋风管家！",
    "happinesscharge precure!": "幸福充电光之美少女！",
    "avatar legends": "降世神通",
    "gekkan shoujo nozaki-kun": "月刊少女野崎君",
    "tears of themis": "未定事件簿",
    "heartcatch precure!": "心跳光之美少女！",
    "watashi ni tenshi ga maiorita!": "天使降临到我身边！",
    "nekopara": "NEKOPARA",
    "inuyasha": "犬夜叉",
    "delicious party precure": "美味派对光之美少女",
    "nagi no asukara": "凪的明日",
    "galaxy angel": "银河天使",
    "ichigo mashimaro": "草莓棉花糖",
    "youkai watch": "妖怪手表",
    "katawa shoujo": "片轮少女",
    "dungeon and fighter": "地下城与勇士",
    "cevio": "CeVIO",
    "gakkou gurashi!": "学园孤岛",
    "breath of fire": "龙战士",
    "star ocean": "星之海洋",
    "star wars": "星球大战",
    "shuffle!": "Shuffle!",
    "metroid": "密特罗德",
    "dungeon ni deai wo motomeru no wa machigatteiru darou ka": "在地下城寻求邂逅是否搞错了什么",
    "monster hunter (series)": "怪物猎人系列",
    "one-punch man": "一拳超人",
    "alice in wonderland": "爱丽丝梦游仙境",
    "boku wa tomodachi ga sukunai": "我的朋友很少",
    "kara no kyoukai": "空之境界",
    "dokidoki! precure": "心跳！光之美少女",
    "devil may cry (series)": "鬼泣系列",
    "doki doki literature club": "心跳文学部",
    "kamitsubaki studio": "神椿工作室",
    "ano hi mita hana no namae wo bokutachi wa mada shiranai.": "我们仍未知道那天所看见的花的名字",
    "overlord (maruyama)": "OVERLORD",
    "xenosaga": "异度传说",
    "samurai spirits": "侍魂",
    "new game!": "NEW GAME!",
    "tamako market": "玉子市场",
    "metal gear (series)": "合金装备系列",
    "phantasy star": "梦幻之星",
    "gabriel dropout": "珈百璃的堕落",
    "path to nowhere": "无期迷途",
    "minecraft": "我的世界",
    "slam dunk (series)": "灌篮高手系列",
    "magi the labyrinth of magic": "魔笛MAGI",
    "scott pilgrim (series)": "歪小子斯科特系列",
    "taimanin (series)": "对魔忍系列",
    "princess principal": "Princess Principal",
    "goblin slayer!": "哥布林杀手",
    "sakura no sekai": "樱花世界",
    "summer pockets": "Summer Pockets",
    "hazbin hotel": "极恶老大",
    "soulcalibur": "灵魂能力",
    "chrono trigger": "时空之轮",
    "dark souls (series)": "黑暗之魂系列",
    "utawarerumono": "传颂之物",
    "lupin iii": "鲁邦三世",
    "non non biyori": "悠哉日常大王",
    "keroro gunsou": "Keroro军曹",
    "kuroko no basuke": "黑子的篮球",
    "puzzle & dragons": "智龙迷城",
    "sinoalice": "SINoALICE",
    "sengoku basara": "战国BASARA",
    "last origin": "Last Origin",
    "closers": "封印者",
    "lycoris recoil": "莉可丽丝",
    "fatal fury": "饿狼传说",
    "needy girl overdose": "主播女孩重度依赖",
    "eromanga sensei": "情色漫画老师",
    "shakugan no shana": "灼眼的夏娜",
    "kid icarus": "光神话",
    "komi-san wa komyushou desu": "古见同学有交流障碍症",
    "yuuki bakuhatsu bang bravern": "勇气爆发",
    "cyberpunk (series)": "赛博朋克系列",
    "kill me baby": "爱杀宝贝",
    "go! princess precure": "Go！公主光之美少女",
    "tate no yuusha no nariagari": "盾之勇者成名录",
    "animal crossing": "动物森友会",
    "kannagi": "神薙",
    "berserk": "剑风传奇",
    "ib": "Ib",
    "tokyo ghoul": "东京喰种",
    "soul eater": "噬魂师",
    "darker than black": "黑之契约者",
    "mother 2": "地球冒险2",
    "saenai heroine no sodatekata": "路人女主的养成方法",
    "tensei shitara slime datta ken": "关于我转生变成史莱姆这档事",
    "aldnoah.zero": "ALDNOAH.ZERO",
    "dragon's crown": "龙之皇冠",
    "va-11 hall-a": "赛博朋克酒保行动",
    "bloodborne": "血源诅咒",
    "pangya": "魔法飞球",
    "black lagoon": "黑礁",
    "di gi charat": "叮当小魔女",
    "doraemon": "哆啦A梦",
    "slayers": "秀逗魔导士",
    "love plus": "Love Plus",
    "eureka seven (series)": "交响诗篇系列",
    "ryuuou no oshigoto!": "龙王的工作！",
    "aa megami-sama": "我的女神",
    "mahou shoujo ni akogarete": "憧憬成为魔法少女",
    "yama no susume": "向山进发",
    "ganbare douki-chan": "加油吧同期酱",
    "warhammer 40k": "战锤40K",
    "my-hime": "舞-HiME",
    "healin' good precure": "治愈光之美少女",
    "sekai seifuku: bouryaku no zvezda": "世界征服 谋略之星",
    "ikkitousen": "一骑当千",
    "journey to the west": "西游记",
    "deltarune": "Deltarune",
    "cowboy bebop": "星际牛仔",
    "yuyushiki": "悠悠式",
    "odin sphere": "奥丁领域",
    "death note": "死亡笔记",
    "sen to chihiro no kamikakushi": "千与千寻",
    "vividred operation": "Vividred Operation",
    "hellsing": "皇家国教骑士团",
    "aoki hagane no arpeggio": "苍蓝钢铁的琶音",
    "senren banka": "千恋万花",
    "shingeki no bahamut": "巴哈姆特之怒",
    "adventure time": "探险活宝",
    "mon-musu quest!": "魔物娘☆配对！",
    "nanatsu no taizai": "七大罪",
    "brave witches": "强袭魔女",
    "nisekoi": "伪恋",
    "powerpuff girls": "飞天小女警",
    "eizouken ni wa te wo dasu na!": "别对映像研出手！",
    "hirogaru sky! precure": "开阔天空！光之美少女",
    "himouto! umaru-chan": "干物妹！小埋",
    "flcl": "FLCL",
    "minami-ke": "南家三姐妹",
    "ao no exorcist": "青之驱魔师",
    "mahjong soul": "雀魂",
    "under night in-birth": "夜下降生",
    "yakusoku no neverland": "约定的梦幻岛",
    "tantei opera milky holmes": "侦探歌剧 少女福尔摩斯",
    "school rumble": "喧嚣学院",
    "magic knight rayearth": "魔法骑士",
    "blue lock": "蓝色监狱",
    "south park": "南方公园",
    "monster girl encyclopedia": "魔物娘图鉴",
    "muv-luv": "Muv-Luv",
    "amphibia": " amphibia",
    "wild arms": "荒野兵器",
    "yu yu hakusho": "幽游白书",
    "nitroplus": "Nitro+",
    "spice and wolf": "狼与香辛料",
    "urusei yatsura": "福星小子",
    "promare": "Promare",
    "little red riding hood": "小红帽",
    "ijiranaide nagatoro-san": "不要欺负我，长瀞同学",
    "frozen (disney)": "冰雪奇缘",
    "bayonetta (series)": "猎天使魔女系列",
    "voicevox": "VOICEVOX",
    "karakai jouzu no takagi-san": "擅长捉弄的高木同学",
    "poptepipic": "POP TEAM EPIC",
    "seiken densetsu": "圣剑传说",
    "godzilla (series)": "哥斯拉系列",
    "mahou tsukai no yoru": "魔法使之夜",
    "uzaki-chan wa asobitai!": "宇崎酱想要玩耍！",
    "dagashi kashi": "粗点心战争",
    "amagi brilliant park": "甘城光辉游乐园",
    "kimi no na wa.": "你的名字。",
    "call of duty": "使命召唤",
    "boku no kokoro no yabai yatsu": "我心里危险的东西",
    "trigun": "枪神",
    "majo no takkyuubin": "魔女宅急便",
    "shoujo shuumatsu ryokou": "少女终末旅行",
    "shoujo kakumei utena": "少女革命",
    "haiyore! nyaruko-san": "袭来！美少女邪神",
    "the amazing digital circus": "神奇数字马戏团",
    "mother (game)": "地球冒险",
    "kaiji": "赌博默示录",
    "tensei oujo to tensai reijou no mahou kakumei": "转生王女与天才千金的魔法革命",
    "brand new animal": "BNA",
    "tokidoki bosotto roshia-go de dereru tonari no alya-san": "不时轻声地以俄语遮羞的邻座艾莉同学",
    "shokugeki no souma": "食戟之灵",
    "warioware": "瓦力欧制造",
    "yuusha de aru": "勇者系列",
    "no game no life": "游戏人生",
    "yagate kimi ni naru": "终将成为你",
    "wii fit": "Wii Fit",
    "mabinogi": "洛奇",
    "yotsubato!": "四叶妹妹！",
    "warship girls r": "战舰少女R",
    "show by rock!!": "Show By Rock!!",
    "blend s": "调教咖啡厅",
    "dennou coil": "电脑线圈",
    "voms": "VOMS",
    "kanojo okarishimasu": "租借女友",
    "signalis": "信号",
    "kuroshitsuji": "黑执事",
    "miraculous ladybug": "瓢虫雷迪",
    "hataraku maou-sama!": "打工吧！魔王大人",
    "shirobako": "白箱",
    "ryuu ga gotoku (series)": "如龙系列",
    "inu x boku ss": "妖狐×仆SS",
    "donkey kong (series)": "大金刚系列",
    "disney": "迪士尼",
    "katekyo hitman reborn!": "家庭教师",
    "shin megami tensei": "真女神转生",
    "hades (series)": "哈迪斯系列",
    "howl no ugoku shiro": "哈尔的移动城堡",
    "mcdonald's": "麦当劳",
    "gyee": "灵魂潮汐",
    "dog days": "犬勇者物语",
    "dandadan": "胆大党",
    "shinrabanshou": "森罗万象",
    "hanasaku iroha": "花开伊吕波",
    "pani poni dash!": "嬉皮笑园",
    "nu carnival": "新世界狂欢",
    "magic kaito": "怪盗基德",
    "heaven burns red": "Heaven Burns Red",
    "hoozuki no reitetsu": "鬼灯的冷彻",
    "the owl house": "猫头鹰魔法社",
    "mikakunin de shinkoukei": "未确认进行式",
    "omniscient reader's viewpoint": "全知读者视角",
    "danna ga nani wo itte iru ka wakaranai ken": "关于丈夫总是不懂事的那些事",
    "ano natsu de matteru": "在那夏天等待",
    "mitsudomoe": "三胞胎",
    "black clover": "黑色五叶草",
    "hentai ouji to warawanai neko.": "变态王子与不笑猫",
    "rewrite": "Rewrite",
    "suigetsu": "水月",
    "the coffin of andy and leyley": "安迪与莉莉的棺材",
    "harry potter (series)": "哈利波特系列",
    "tenchi muyou!": "天地无用！",
    "riddle joker": "RIDDLE JOKER",
    "silent hill (series)": "寂静岭系列",
    "yoru no kurage wa oyogenai": "夜晚的水母不会游泳",
    "tokyo mew mew": "东京猫猫",
    "d.gray-man": "驱魔少年",
    "shadows house": "影宅",
    "kannazuki no miko": "神无月的巫女",
    "grandia": "格兰蒂亚",
    "full metal panic!": "全金属狂潮",
    "sanoba witch": "秋之回忆",
    "star fox": "星际火狐",
    "7th dragon": "第七龙神",
    "team fortress 2": "军团要塞2",
    "adachi to shimamura": "安达与岛村",
    "sakura trick": "樱Trick",
    "dirty pair": "搞怪拍档",
    "master detective archives: rain code": "超侦探事件簿 雾雨谜宫",
    "shino to ren": "远藤同学",
    "kimi kiss": "君吻",
    "tianguan cifu": "天官赐福",
    "rinne no lagrange": "轮回的拉格朗日",
    "bilibili": "哔哩哔哩",
    "jashin-chan dropkick": "邪神酱厨",
    "sora no otoshimono": "天降之物",
    "zannen onna-kanbu black general-san": "残念女干部布莱克大小姐",
    "flip flappers": "FLIP FLAPPERS",
    "renkin san-kyuu magical pokaan": "炼金三级魔法少女",
    "castlevania (series)": "恶魔城系列",
    "quiz magic academy": "问答魔法学院",
    "high school fleet": "高校舰队",
    "mode aim": "Mode Aim",
    "owari no seraph": "终结的炽天使",
    "zoids": "索斯机械兽",
    "kininatteru hito ga otoko ja nakatta": "在意的人不是男生",
    "ojamajo doremi": "小魔女DoReMi",
    "ben 10": "Ben 10",
    "genshiken": "现视研",
    "hollow knight": "空洞骑士",
    "rosario+vampire": "十字架与吸血鬼",
    "rou-kyuu-bu!": "萝球社！",
    "zootopia": "疯狂动物城",
    "citrus (saburouta)": "Citrus",
    "kami nomi zo shiru sekai": "只有神知道的世界",
    "tenshi souzou re-boot!": "天使创世 Re-boot!",
    "yuusha to maou": "勇者与魔王",
    "love hina": "纯情房东俏房客",
    "fushigiboshi no futago hime": "不可思议星球的双胞胎公主",
    "agent aika": "AIKa",
    "xenogears": "异度装甲",
    "kyoukaisenjou no horizon": "境界线上的地平线",
    "super robot wars": "超级机器人大战",
    "gravity falls": "怪诞小镇",
    "ojisan to marshmallow": "大叔与棉花糖",
    "sono hanabira ni kuchizuke wo": "亲吻那片花瓣",
    "vyugen": "Vyugen",
    "octopath traveler": "歧路旅人",
    "lonely girl ni sakaraenai": "无法抗拒孤独少女",
    "shinryaku! ikamusume": "侵略！乌贼娘",
    "sana channel": "Sana Channel",
    "guilty crown": "罪恶王冠",
    "tsugu (vtuber)": "津云(虚拟主播)",
    "kizuna ai inc.": "Kizuna AI Inc.",
    "air (visual novel)": "AIR",
    "shantae (series)": "香缇系列",
    "nier:automata": "尼尔：自动人形",
    "ado (utaite)": "Ado(唱见)",
    "ghost in the shell": "攻壳机动队",
    "tera online": "TERA",
    "the moon studio": "月亮工作室",
    "god eater": "噬神者",
    "majo no tabitabi": "魔女之旅",
    "seishun buta yarou": "青春猪头少年",
    "violet evergarden (series)": "紫罗兰永恒花园系列",
    "kino no tabi": "奇诺之旅",
    "limbus company": "边狱巴士",
    "serial experiments lain": "玲音",
    "fushigi no umi no nadia": "不可思议的海之娜蒂亚",
    "the ring": "午夜凶铃",
    "youjo senki": "幼女战记",
    "rakuen tsuihou": "乐园追放",
    "nige jouzu no wakagimi": "擅长逃跑的少主",
    "kidou senkan nadesico": "机动战舰",
    "maoyuu maou yuusha": "魔王勇者",
    "warcraft": "魔兽争霸",
    "lord of the mysteries": "诡秘之主",
    "koutetsujou no kabaneri": "甲铁城的卡巴内瑞",
    "jigoku shoujo": "地狱少女",
    "saya no uta": "沙耶之歌",
    "final fight": "快打旋风",
    "denpa onna to seishun otoko": "电波女与青春男",
    "capcom fighting jam": "Capcom Fighting Jam",
    "mirai nikki": "未来日记",
    "gravity daze": "重力眩晕",
    "hinata channel": "Hinata Channel",
    "musaigen no phantom world": "无彩限的幻影世界",
    "senjou no valkyria (series)": "战场女武神系列",
    "charlotte (anime)": "Charlotte",
    "oshiete! galko-chan": "告诉我！辣妹子酱",
    "fukumoto mahjong": "福本麻将",
    "hacka doll": "Hacka Doll",
    "shugo chara!": "守护甜心！",
    "dennou shoujo youtuber siro": "电脑少女Siro",
    "sewayaki kitsune no senko-san": "贤惠幼妻仙狐小姐",
    "mononoke hime": "幽灵公主",
    "fresh precure!": "Fresh光之美少女！",
    "metal slug": "合金弹头",
    "ookami (game)": "大神",
    "bombergirl": "Bombergirl",
    "douluo dalu": "斗罗大陆",
    "new horizon": "New Horizon",
    "arms (game)": "ARMS",
    "make heroine ga oo sugiru!": "败犬女主太多了！",
    "kodomo no jikan": "萌少女的恋爱时光",
    "little witch nobeta": "小魔女诺贝塔",
    "accel world": "加速世界",
    "getsuyoubi no tawawa": "星期一的丰满",
    "highschool of the dead": "学园默示录",
    "yume 2kki": "梦2kki",
    "kyoukai no kanata": "境界的彼方",
    "yosuga no sora": "缘之空",
    "gosick": "GOSICK",
    "chobits": "人形电脑天使心",
    "gate - jieitai ka no chi nite kaku tatakaeri": "GATE 奇幻自卫队",
    "86 -eightysix-": "86-不存在的战区",
    "kusuriya no hitorigoto": "药屋少女的呢喃",
    "blade & soul": "剑灵",
    "yofukashi no uta": "彻夜之歌",
    "kaze no tani no nausicaa": "风之谷",
    "sakura taisen": "樱花大战",
    "strike the blood": "噬血狂袭",
    "drag-on dragoon": "龙背上的骑兵",
    "happiness!": "Happiness!",
    "mirai akari project": "未来光project",
    "occultic;nine": "超自然9人组",
    "school days": "School Days",
    "chrono cross": "穿越时空",
    "cafe stella to shinigami no chou": "星咖与死神蝶",
    "honzuki no gekokujou": "小书痴的下克上",
    "live a hero": "Live a Hero",
    "akame ga kill!": "斩！赤红之瞳",
    "kagura gumi": "神乐组",
    "stellar blade": "星刃",
    "pac-man (game)": "吃豆人",
    "aquarion (series)": "创圣系列",
    "pixiv": "Pixiv",
    "super heroine boy": "超英雄男孩",
    "baka to test to shoukanjuu": "笨蛋测验召唤兽",
    "doom (series)": "毁灭战士系列",
    "fate/zero": "Fate/Zero",
    "soredemo ayumu wa yosetekuru": "就算这样，步还是靠了过来",
    "summon night": "召唤之夜",
    "dorei to no seikatsu ~teaching feeling~": "奴隶的生活～Teaching Feeling～",
    "omamori himari": "守护猫娘绯鞠",
    "soukou akki muramasa": "装甲恶鬼村正",
    "kakegurui": "狂赌之渊",
    "f-zero": "F-Zero",
    "quiz magic academy the world evolve": "问答魔法学院 The World Evolve",
    "my-otome": "舞-乙HiME",
    "tonari no totoro": "龙猫",
    "otome game no hametsu flag shika nai akuyaku reijou ni tensei shite shimatta": "转生成乙女游戏破灭Flag的邪恶大小姐",
    "yatterman": "小双侠",
    "american mcgee's alice": "美国麦基的艾丽丝",
    "sekiro: shadows die twice": "只狼：影逝二度",
    "jahy-sama wa kujikenai!": "贾希大人不气馁！",
    "kumamiko": "熊巫女",
    ".flow": ".flow",
    "the road to el dorado": "勇闯黄金城",
    "dramatical murder": "Dramatical Murder",
    "futari wa precure": "两人是光之美少女",
    "mother 3": "地球冒险3",
    "friday the 13th": "十三号星期五",
    "another": "Another",
    "yoru no yatterman": "夜之小双侠",
    "witchblade": "魔女之刃",
    "black jack (series)": "怪医黑杰克系列",
    "saru getchu": "捉猴啦",
    "the little mermaid": "小美人鱼",
    "satsuriku no tenshi": "杀戮天使",
    "kemomimi oukoku kokuei housou": "兽耳王国国营广播",
    "mahoromatic": "魔力女管家",
    "foster's home for imaginary friends": "Foster的幻想朋友之家",
    "demonbane": "斩魔大圣",
    "library of ruina": "废墟图书馆",
    "rurouni kenshin": "浪客剑心",
    "la pucelle": "圣女贞德",
    "uni create": "Uni Create",
    "shirokami project": "白神Project",
    "record of lodoss war": "罗德斯岛战记",
    "yumekui merry": "食梦者玛莉",
    "puniru wa kawaii slime": "噗妮露是可爱史莱姆",
    "ar tonelico": "魔塔大陆",
    "shigatsu wa kimi no uso": "四月是你的谎言",
    "bamboo blade": "竹剑少女",
    "uchuu senkan yamato": "宇宙战舰大和号",
    "ga-rei": "喰灵",
    "scooby-doo": "史酷比",
    "tomb raider": "古墓丽影",
    "ishuzoku reviewers": "异种族风俗娘评鉴指南",
    "fear & hunger": "恐惧与饥饿",
    "super blackjack": "超级黑杰克",
    "tsukuyomi moonphase": "月咏",
    "hitsugi no chaika": "棺姬嘉依卡",
    "dumbbell nan kilo moteru?": "流汗吧！健身少女",
    "doukutsu monogatari": "洞窟物语",
    "mahouka koukou no rettousei": "魔法科高中的劣等生",
    "top wo nerae 2!": "飞跃巅峰2！",
    "pui pui molcar": "PUI PUI 天竺鼠车车",
    "do it yourself!!": "Do It Yourself!!",
    "princess tutu": "彩梦芭蕾",
    "tsuujou kougeki ga zentai kougeki de ni-kai kougeki no okaasan wa suki desu ka?": "你妈平砍连击带顺劈你喜欢吗？",
    "phase connect": "Phase Connect",
    "soulworker": "灵魂行者",
    "cthulhu mythos": "克苏鲁神话",
    "oboro muramasa": "胧村正",
    "liver city": "Liver City",
    "ame to kimi to": "雨与君",
    "dr. slump": "怪博士与机器娃娃",
    "onegai teacher": "拜托了老师",
    "addams family": "亚当斯一家",
    "saikin yatotta maid ga ayashii": "最近雇佣的女仆有点怪",
    "read or die": "R.O.D",
    "ombok diving and delivery services": "OmBok潜水快递",
    "shiro seijo to kuro bokushi": "白圣女与黑牧师",
    "planetarian": "星之梦",
    "koe no katachi": "声之形",
    "akebi-chan no serafuku": "明美酱的水手服",
    "bravely default (series)": "勇气默示录系列",
    "dororo (tezuka)": "多罗罗",
    "oshiro project:re": "御城Project:RE",
    "top wo nerae!": "飞跃巅峰！",
    "tenki no ko": "天气之子",
    "maou-jou de oyasumi": "在魔王城说晚安",
    "aika (series)": "AIKa系列",
    "sakura-sou no pet na kanojo": "樱花庄的宠物女孩",
    "to heart": "To Heart",
    "uta no prince-sama": "歌之王子殿下",
    "fall guys": "糖豆人",
    "valkyrie profile (series)": "北欧女神系列",
    "7th dragon (series)": "第七龙神系列",
    "egyptian mythology": "埃及神话",
    "samsung": "三星",
    "en'en no shouboutai": "炎炎消防队",
    "shy (series)": "SHY系列",
    "bungou stray dogs": "文豪野犬",
    "arc the lad": "妖精战士",
    "ookami-san": "狼与香辛料",
    "saga": "SAGA",
    "a channel": "A Channel",
    "tangled": "魔发奇缘",
    "c.c. lemon": "C.C.柠檬",
    "ultra series": "奥特曼系列",
    "dracu-riot!": "Dracu-Riot!",
    "dolphin wave": "冲浪少女",
    "funamusea": "Funamusea",
    "tokyo revengers": "东京复仇者",
    "yoake mae yori ruri iro na": "琉璃色的黎明前",
    "ansatsu kyoushitsu": "暗杀教室",
    "urara meirochou": "迷路帖",
    "noripro": "Noripro",
    "kfc": "肯德基",
    "mononoke": "物怪",
    "tenshinranman": "天心烂漫",
    "tensui no sakuna-hime": "天穗之咲稻姬",
    "haibane renmei": "灰羽联盟",
    "summertime render": "夏日重现",
    "aogiri koukou": "青桐高中",
    "log horizon": "记录的地平线",
    "ico": "ICO",
    "strawberry panic!": "草莓狂热",
    "strike witches: suomus misfits squadron": "强袭魔女 芬兰空军",
    "spongebob squarepants (series)": "海绵宝宝系列",
    "mermaid melody pichi pichi pitch": "人鱼旋律",
    "futaba channel": "双叶频道",
    "a hat in time": "时光之帽",
    "kaiten muten-maru": "开天无双丸",
    "bokusatsu tenshi dokuro-chan": "扑杀天使",
    "mushishi": "虫师",
    "alien (series)": "异形系列",
    "youkoso jitsuryoku shijou shugi no kyoushitsu e": "欢迎来到实力至上主义的教室",
    "seikimatsu occult gakuin": "世纪末超自然学院",
    "kimi ga nozomu eien": "你所期望的永远",
    "canaan (series)": "CANAAN系列",
    "himawari-san": "向日葵桑",
    "miru tights": "丝袜视界",
    "ouran high school host club": "樱兰高校男公关部",
    "ano ko wa toshi densetsu": "那个少女是都市传说",
    "mazinger (series)": "魔神系列",
    "w tails cat": "W尾巴猫",
    "ore twintail ni narimasu": "我，要成为双马尾",
    "family guy": "恶搞之家",
    ".live": ".live",
    "helluva boss": "极恶老大",
    "ender lilies quietus of the knights": "终焉之莉莉：骑士寂夜",
    "waktaverse": "Waktaverse",
    "master of eternity": "Master of Eternity",
    "shikanoko nokonoko koshitantan": "鹿乃子乃子虎视眈眈",
    "kuso miso technique": "粪味噌技术",
    "9-nine-": "9-nine-",
    "mega man (classic)": "洛克人(元祖)",
    "manatsu no yo no inmu": "真夏的夜之淫梦",
    "tokyo 7th sisters": "Tokyo 7th Sisters",
    "re:creators": "Re:CREATORS",
    "grimms notes": "格林笔记",
    "snow white and the seven dwarfs": "白雪公主和七个小矮人",
    "otona no tenshi-sama ni itsu no mani ka dame ningen ni sarete ita ken": "邻座的天使大人不知不觉把我惯成了废人",
    "crave saga": "Crave Saga",
    "crawling dreams": "Crawling Dreams",
    "love and deepspace": "恋与深空",
    "given": "Given",
    "fate/samurai remnant": "Fate/Samurai Remnant",
    "mahoujin guruguru": "咕噜咕噜魔法阵",
    "devil summoner": "恶魔召唤师",
    "assassin's creed (series)": "刺客信条系列",
    "mieruko-chan": "看得见的女孩",
    "jinrui wa suitai shimashita": "人类衰退之后",
    "ice climber": "敲冰块",
    "crayon shin-chan": "蜡笔小新",
    "jibaku shounen hanako-kun": "地缚少年花子君",
    "punch-out!!": "拳无虚发",
    "suisei no gargantia": "翠星之加尔刚蒂亚",
    "melonbooks": "Melonbooks",
    "tonari no kyuuketsuki-san": "邻家吸血鬼",
    "battle angel alita": "铳梦",
    "deemo": "Deemo",
    "gake no ue no ponyo": "悬崖上的金鱼姬",
    "talkex": "Talkex",
    "miniskirt pirates": "迷你裙宇宙海贼",
    "shinmai maou no testament": "新妹魔王的契约者",
    "cafe-chan to break time": "咖啡酱的休憩时光",
    "halo (series)": "光环系列",
    "yukijirushi": "雪印",
    "rakudai kishi no cavalry": "落第骑士英雄谭",
    "minecraft youtube": "Minecraft YouTube",
    "mechanical buddy universe": "机械伙伴宇宙",
    "kanokon": "かのこん",
    "gaki kyonyuu": "饿鬼巨乳",
    "taiho shichauzo": "逮捕令",
    "peanuts": "花生漫画",
    "cinderella series": "灰姑娘系列",
    "zhu xian": "诛仙",
    "ryuuko no ken": "龙虎之拳",
    "parasite eve": "寄生前夜",
    "majutsushi orphen": "魔术士欧菲",
    "nazo no kanojo x": "谜样女友X",
    "sk8 the infinity": "SK8 the Infinity",
    "nurse witch komugi-chan": "护士小魔女小麦",
    "kao no nai tsuki": "无颜之月",
    "big hero 6": "超能陆战队",
    "azure striker gunvolt": "苍蓝雷霆",
    "ousama ranking": "国王排名",
    "valkyrie no densetsu": "女武神传说",
    "ys": "伊苏",
    "hoshikuzu witch meruru": "星屑魔女梅露露",
    "valorant": "Valorant",
    "nhk ni youkoso!": "欢迎来到NHK",
    "hokuto no ken": "北斗神拳",
    "predator (series)": "铁血战士系列",
    "lost universe": "宇宙刑警",
    "tetsuwan birdy": "铁腕女警",
    "koihime musou": "恋姬无双",
    "cardfight!! vanguard": "卡片战斗先导者",
    "shadowverse": "影之诗",
    "eoduun badaui deungbul-i doeeo": "成为暗黑之岛的灯火",
    "elfen lied": "妖精的旋律",
    "zettai karen children": "绝对可怜儿童",
    "rance (series)": "兰斯系列",
    "heroman": "超人王",
    "snow white": "白雪公主",
    "hinako note": "雏子的笔记",
    "houkago play": "放学后Play",
    "psycho-pass": "心理测量者",
    "collar x malice": "Collar×Malice",
}

class AnimaDataManager:
    def __init__(self, data_dir: str, user_data_dir: str = ""):
        self.data_dir = Path(data_dir)
        self._user_data_dir = Path(user_data_dir) if user_data_dir else None
        self._datasets: dict[str, list[dict]] = {}
        """{分类名: [{name, tags, ...}]}"""
        self._name_index: dict[str, list[tuple[str, int]]] = {}
        """{关键词: [(分类名, 索引), ...]}"""
        self._loaded = False

    # ------------------------------------------------------------------
    # Anima-Tools JS 直接引用（不存本地，运行时动态读取）
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_js_array(filepath: Path) -> list:
        """从 JS 文件中提取 JSON 数组（去掉 const xxx = 前缀）"""
        if not filepath.exists():
            logger.warning(f"[AnimaData] Anima-Tools JS 不存在: {filepath}")
            return []
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                text = f.read()
            start = text.find('[')
            end = text.rfind(']')
            if start == -1 or end == -1:
                logger.warning(f"[AnimaData] 无法找到数组: {filepath}")
                return []
            return json.loads(text[start:end+1])
        except Exception as e:
            logger.warning(f"[AnimaData] 解析 JS 失败 {filepath}: {e}")
            return []

    def _load_anima_tools_artists(self) -> list[dict]:
        """从 Anima-Tools data.js 加载画师数据（含 CDN 图片）"""
        raw = self._extract_js_array(_ANIMA_TOOLS_JS_DIR / "data.js")
        if not raw:
            return []
        items = []
        seen = set()
        for item in raw:
            name = (item.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            item_id = str(item.get("id", ""))
            partition = item.get("p", 1)
            image_url = (
                f"https://fastly.jsdelivr.net/gh/ThetaCursed/"
                f"Anima-Assets@main/images/{partition}/{item_id}.webp"
            )
            items.append({
                "name": name,
                "tags": f"@{name}",
                "category": "",
                "note": f"作品数: {item.get('post_count', 0)}, "
                        f"独特度: {item.get('uniqueness_score', 0)}",
                "source": "anima_tools",
                "post_count": item.get("post_count", 0),
                "uniqueness_score": item.get("uniqueness_score", 0),
                "image_url": image_url,
                "id": item_id,
                "p": partition,
                "style": "anime",
            })
        logger.info(f"[AnimaData] Anima-Tools 画师: {len(items)} 条 (JS)")
        return items

    def _load_anima_tools_characters(self) -> list[dict]:
        """从 Anima-Tools character_data.js 加载角色数据（含 CDN 图片）"""
        raw = self._extract_js_array(_ANIMA_TOOLS_JS_DIR / "character_data.js")
        if not raw:
            return []
        items = []
        for item in raw:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            copyright_ = (item.get("copyright") or "").strip()
            gender = (item.get("gender") or "").strip()
            hair = (item.get("hair") or "").strip()
            eye = (item.get("eye") or "").strip()
            tags_parts = [name]
            if copyright_:
                tags_parts.append(copyright_)
            if gender:
                tags_parts.append(gender)
            if hair:
                tags_parts.append(f"{hair} hair")
            if eye:
                tags_parts.append(f"{eye} eyes")
            raw_name = f"{name}, {copyright_}" if copyright_ else name
            image_url = (
                f"https://blobs.animadex.net/Outputs/thumbs/"
                f"{quote(raw_name)}.webp"
            )
            items.append({
                "name": name,
                "tags": ", ".join(tags_parts),
                "category": copyright_,
                "note": (f"作品: {copyright_}, 发色: {hair}, 瞳色: {eye}"
                         if copyright_ else f"发色: {hair}, 瞳色: {eye}"),
                "source": "anima_tools",
                "post_count": item.get("post_count", 0),
                "gender": gender,
                "hair": hair,
                "eye": eye,
                "image_url": image_url,
                "name_cn": "",
                "category_cn": _CHARACTER_CATEGORY_CN.get(copyright_, copyright_),
                "style": "anime",
            })
        logger.info(f"[AnimaData] Anima-Tools 角色: {len(items)} 条 (JS)")
        return items

    def _load_anima_tools_clothing(self) -> list[dict]:
        """从 Anima-Tools clothing_data.js 加载服装数据"""
        raw = self._extract_js_array(_ANIMA_TOOLS_JS_DIR / "clothing_data.js")
        if not raw:
            return []
        items = []
        for item in raw:
            name_zh = (item.get("name_zh") or "").strip()
            name_en = (item.get("name") or "").strip()
            display_name = name_zh or name_en
            if not display_name:
                continue
            tags_en = (item.get("tags") or "").strip()
            tags_zh = (item.get("tags_zh") or "").strip()
            tags = tags_en or tags_zh
            categories = item.get("categories") or []
            traits = item.get("traits") or []
            items.append({
                "name": display_name,
                "name_en": name_en,
                "tags": tags,
                "tags_en": tags_en,
                "tags_zh": tags_zh,
                "category": "; ".join(categories) if categories else "",
                "note": " | ".join(traits) if traits else "",
                "source": "anima_tools",
                "image_url": (item.get("preview") or "").strip(),
                "style": "general",
                "categories": categories,
                "traits": traits,
            })
        logger.info(f"[AnimaData] Anima-Tools 服装: {len(items)} 条 (JS)")
        return items

    def _check_anima_tools_fallback(self):
        """检查 anima 分类下是否有本地数据，没有则从 Anima-Tools JS 加载"""
        anima_dir = self.data_dir / "anima"
        anima_jsons = list(anima_dir.rglob("*.json")) if anima_dir.exists() else []

        # 哪些分类需要在 anima 下有数据
        expected = {
            "anima/artists": self._load_anima_tools_artists,
            "anima/characters": self._load_anima_tools_characters,
            "anima/clothing": self._load_anima_tools_clothing,
        }

        for cat_key, loader_fn in expected.items():
            # 本分类已有本地 JSON → 跳过
            has_local = any(str(fp.relative_to(self.data_dir)) == f"{cat_key}.json"
                            for fp in anima_jsons)
            if has_local:
                continue
            # 读取 Anima-Tools JS
            data = loader_fn()
            if data:
                self._datasets[cat_key] = data

    def load_all(self):
        """扫描 data/ 下所有 .json 文件并加载，缺失的 anima 数据从 Anima-Tools JS 补充"""
        self._datasets.clear()
        self._name_index.clear()
        if not self.data_dir.exists():
            logger.warning(f"[AnimaData] 数据目录不存在: {self.data_dir}")
            return

        for fpath in sorted(self.data_dir.rglob("*.json")):
            rel = fpath.relative_to(self.data_dir)
            # 跳过非数据文件
            if rel.parts and rel.parts[0] in ('anima_tools', 'cache', 'prompt_log.json', 'user'):
                continue
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    continue
                category = str(rel.parent / rel.stem)
                self._datasets[category] = data
                logger.info(f"[AnimaData] 加载 {category}: {len(data)} 条")
            except Exception as e:
                logger.warning(f"[AnimaData] 加载失败 {rel}: {e}")

        # 补充缺失的 anima 数据（从 Anima-Tools JS）
        self._check_anima_tools_fallback()

        self._build_index()
        self._loaded = True
        total = sum(len(v) for v in self._datasets.values())
        logger.info(f"[AnimaData] 共加载 {len(self._datasets)} 个分类, {total} 条数据")

    def _build_index(self):
        """构建名称全文索引"""
        for cat, items in self._datasets.items():
            for idx, item in enumerate(items):
                name = (item.get("name") or "").lower()
                tags = (item.get("tags") or "").lower()
                note = (item.get("note") or "").lower()
                full_text = f"{name} {tags} {note}"
                # 按空格/逗号/斜杠分词作为关键词
                words = set(re.split(r'[\s,/]+', full_text))
                for word in words:
                    word = word.strip()
                    if len(word) < 2:
                        continue
                    if word not in self._name_index:
                        self._name_index[word] = []
                    self._name_index[word].append((cat, idx))

    def search(self, keyword: str, top_k: int = 10) -> list[dict]:
        """搜索关键词，返回匹配结果"""
        if not self._loaded or not keyword:
            return []
        kw = keyword.lower().strip()
        if not kw:
            return []

        # 分词搜索
        words = re.split(r'[\s,/]+', kw)
        matched_scores: dict[tuple[str, int], float] = {}

        for word in words:
            word = word.strip()
            if len(word) < 2:
                continue
            # 精确匹配
            if word in self._name_index:
                for cat, idx in self._name_index[word]:
                    key = (cat, idx)
                    matched_scores[key] = matched_scores.get(key, 0) + 2.0
            # 前缀匹配
            for indexed_word, indices in self._name_index.items():
                if indexed_word.startswith(word) and indexed_word != word:
                    for cat, idx in indices:
                        key = (cat, idx)
                        matched_scores[key] = matched_scores.get(key, 0) + 1.0

        # 按评分排序取 top_k
        scored = []
        for (cat, idx), score in matched_scores.items():
            items = self._datasets.get(cat)
            if items is None or idx >= len(items):
                continue
            item = items[idx]
            scored.append((score, cat, item))

        scored.sort(key=lambda x: -x[0])
        results = []
        seen = set()
        for score, cat, item in scored:
            dedup_key = (cat, item.get("name", ""))
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            results.append({
                "category": cat,
                "name": item.get("name", ""),
                "tags": item.get("tags", ""),
                "note": item.get("note", ""),
                "score": round(score, 1),
                "source": item.get("source", "anima"),
            })
            if len(results) >= top_k:
                break

        return results

    def get_statistics(self) -> dict:
        """获取数据统计信息"""
        stats = {}
        for cat, items in self._datasets.items():
            # 兼容 Windows \ 和 Unix / 两种路径分隔符
            parts = cat.split(os.sep)
            if len(parts) < 2:
                # 可能是 Unix 风格分隔符（如 anima/artists）
                parts = cat.split("/")
            if len(parts) < 2:
                continue
            domain = parts[0]
            if domain not in stats:
                stats[domain] = {"count": 0, "files": 0}
            stats[domain]["count"] += len(items)
            stats[domain]["files"] += 1
        return stats

    @property
    def is_loaded(self) -> bool:
        return self._loaded


# ====================================================================
# 模块级函数：供 main.py 的魔导书 API 直接调用（运行时加载 Anima-Tools JS）
# ====================================================================

_ANIMA_LOADER_CACHE: dict[str, list[dict]] = {}
"""{source_name: items} — 全局缓存，避免每次请求都解析 JS"""


def _loader_extract_js_array(filepath: Path) -> list:
    """从 JS 文件中提取 JSON 数组"""
    if not filepath.exists():
        return []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            text = f.read()
        start = text.find('[')
        end = text.rfind(']')
        if start == -1 or end == -1:
            return []
        return json.loads(text[start:end+1])
    except Exception:
        return []


def load_anima_tools_source(source_name: str) -> list[dict]:
    """从 Anima-Tools JS 加载指定源的数据（artists / characters / clothing）"""
    # 缓存命中
    if source_name in _ANIMA_LOADER_CACHE:
        return _ANIMA_LOADER_CACHE[source_name]

    js_dir = _ANIMA_TOOLS_JS_DIR
    items = []

    if source_name == "artists":
        raw = _loader_extract_js_array(js_dir / "data.js")
        seen = set()
        for item in raw:
            name = (item.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            item_id = str(item.get("id", ""))
            partition = item.get("p", 1)
            image_url = (
                f"https://fastly.jsdelivr.net/gh/ThetaCursed/"
                f"Anima-Assets@main/images/{partition}/{item_id}.webp"
            )
            items.append({
                "name": name,
                "tags": f"@{name}",
                "category": "",
                "note": f"作品数: {item.get('post_count', 0)}, "
                        f"独特度: {item.get('uniqueness_score', 0)}",
                "source": "anima_tools",
                "post_count": item.get("post_count", 0),
                "uniqueness_score": item.get("uniqueness_score", 0),
                "image_url": image_url,
                "id": item_id,
                "p": partition,
                "style": "anime",
            })

    elif source_name == "characters":
        raw = _loader_extract_js_array(js_dir / "character_data.js")
        for item in raw:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            copyright_ = (item.get("copyright") or "").strip()
            gender = (item.get("gender") or "").strip()
            hair = (item.get("hair") or "").strip()
            eye = (item.get("eye") or "").strip()
            tags_parts = [name]
            if copyright_:
                tags_parts.append(copyright_)
            if gender:
                tags_parts.append(gender)
            if hair:
                tags_parts.append(f"{hair} hair")
            if eye:
                tags_parts.append(f"{eye} eyes")
            raw_name = f"{name}, {copyright_}" if copyright_ else name
            image_url = (
                f"https://blobs.animadex.net/Outputs/thumbs/"
                f"{quote(raw_name)}.webp"
            )
            items.append({
                "name": name,
                "tags": ", ".join(tags_parts),
                "category": copyright_,
                "note": (f"作品: {copyright_}, 发色: {hair}, 瞳色: {eye}"
                         if copyright_ else f"发色: {hair}, 瞳色: {eye}"),
                "source": "anima_tools",
                "post_count": item.get("post_count", 0),
                "gender": gender,
                "hair": hair,
                "eye": eye,
                "image_url": image_url,
                "name_cn": "",
                "category_cn": _CHARACTER_CATEGORY_CN.get(copyright_, copyright_),
                "style": "anime",
            })

    elif source_name == "clothing":
        raw = _loader_extract_js_array(js_dir / "clothing_data.js")
        for item in raw:
            name_zh = (item.get("name_zh") or "").strip()
            name_en = (item.get("name") or "").strip()
            display_name = name_zh or name_en
            if not display_name:
                continue
            tags_en = (item.get("tags") or "").strip()
            tags_zh = (item.get("tags_zh") or "").strip()
            tags = tags_en or tags_zh
            categories = item.get("categories") or []
            traits = item.get("traits") or []
            items.append({
                "name": display_name,
                "name_en": name_en,
                "tags": tags,
                "tags_en": tags_en,
                "tags_zh": tags_zh,
                "category": "; ".join(categories) if categories else "",
                "note": " | ".join(traits) if traits else "",
                "source": "anima_tools",
                "image_url": (item.get("preview") or "").strip(),
                "style": "general",
                "categories": categories,
                "traits": traits,
            })

    _ANIMA_LOADER_CACHE[source_name] = items
    return items


def _is_anima_source(source_path: str) -> bool:
    """判断某个 source path 是否是 Anima-Tools 只读源"""
    normal = source_path.replace('\\', '/').lower()
    # 去掉 .json 后缀再比较
    if normal.endswith('.json'):
        normal = normal[:-5]
    for name in ("artists", "characters", "clothing"):
        if normal == name or normal.endswith(f"/{name}") or normal.endswith(f"/anima/{name}"):
            return True
    return False


_ANIMA_SOURCE_NAMES = [
    ("anima/artists", "artists", "画师"),
    ("anima/characters", "characters", "角色"),
    ("anima/clothing", "clothing", "服装"),
]
