"""对仗 L3 证据层数据抽取 → data/duizhang_*.json

三份输出，各自带 source（书名/版本/sha256）与 evidence_level；覆盖不到的字不猜。

- duizhang_category.json  骈字类编 427 门类 → 单字/双字义类（首字归门，仅取无歧义）
- duizhang_precedent.json 佩文韵府「對語」+ 声律启蒙 + 笠翁对韵 → 历代工对先例
- duizhang_borrow.json    王力《诗词格律》借对/工对/宽对/邻对/合掌判据（人工摘录，逐条带行号）

用法：python tools/extract_duizhang_lexicon.py --src <语料根> [--out <目录>]
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (cjk_only, dump_json, is_cjk, out_dir, read_text,
                    rel, sha256_of, skill_data_dir, src_root)

REL_PIAN = "六、工具书/骈字类编（张廷玉等奉敕编）.md"
REL_PEIWEN = "六、工具书/佩文韵府（张玉书等奉敕编）.md"
REL_SHENGLV = "一、语感打底/声律启蒙（车万育）.md"
REL_LIWENG = "一、语感打底/笠翁对韵（李渔）.md"
REL_GELO = "二、格律入门/诗词格律（王力）.md"
REL_WENXIN = "五、诗话词话/文心雕龙（刘勰）.md"
REL_HUIFENG = "五、诗话词话/蕙风词话（况周颐）.md"

# 门名异写归一（骈字类编原表「珍寳/珍宝」「噐物/器物」并存）
MEN_ALIAS = {"珍寳门": "珍宝门", "噐物门": "器物门"}
MEN_HEAD = re.compile(r"###\s*([\u4e00-\u9fff]+门[一二三四五六七八九十百]+)")
BODY_START = "### 御定骈字类编卷一"


def norm_word(word: str, t2s: dict) -> str:
    """繁→简归一；查不到的字原样保留，不臆造映射。"""
    return "".join(t2s.get(ch, ch) for ch in word)


def base_men(name: str) -> str:
    name = re.sub(r"[一二三四五六七八九十百]+$", "", name)
    return MEN_ALIAS.get(name, name)


def build_category(root: str, out: str, t2s: dict) -> dict:
    path = os.path.join(root, REL_PIAN)
    text = read_text(path)
    pos = text.find(BODY_START)
    body = text[pos:] if pos >= 0 else text
    heads = list(MEN_HEAD.finditer(body))

    # 单字义类：按双字条目的首字归门（骈字类编凡例：门以首字字面分类，虚字不采）
    single: dict[str, dict[str, int]] = {}
    double: dict[str, list[str]] = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        seg = body[m.start():end]
        men = base_men(m.group(1))
        for w in re.findall(r"([\u4e00-\u9fff]{2})【", seg):
            single.setdefault(w[0], {}).setdefault(men, 0)
            single[w[0]][men] += 1
            double.setdefault(norm_word(w, t2s), set()).add(men)

    # 双字只留唯一归门者；一字多门（如「秋」）留给单字层按占比裁决
    double_clean = {w: sorted(v)[0] for w, v in double.items() if len(v) == 1}
    ambiguous = {w: sorted(v) for w, v in double.items() if len(v) > 1}

    # 紧凑格式（运行时 duizhang_pos 自动还原）：按门拼接定长双字串，省去每词一份键名开销。
    compact: dict[str, str] = {}
    for w, men in sorted(double_clean.items()):
        compact[men] = compact.get(men, "") + w

    # 还原自证：紧凑格式 → {词: 门类} 必须与抽取结果全等，不等则拒绝写盘。
    restored = {w: men for men, blob in compact.items()
                for w in (blob[i:i + 2] for i in range(0, len(blob), 2))}
    assert restored == double_clean, (
        "紧凑格式还原自证失败：还原 %d 条，原抽取 %d 条"
        % (len(restored), len(double_clean)))

    # 逐词条 raw/source_line 证据不丢：由同源抽取的 pianzi_categories.json 承载，
    # 本文件以紧凑形式存义类映射，并在此回指可逐条核对的证据文件。
    evidence_ref = {}
    ev_path = os.path.join(out, "pianzi_categories.json")
    if os.path.exists(ev_path):
        evidence_ref = {
            "file": "pianzi_categories.json",
            "sha256": sha256_of(ev_path),
            "说明": "本文件每个双字词的 raw 原文与 source_line 行号逐条见该文件『词条』字段；"
                    "两文件同源（骈字类编同一 sha256），可回指核对。",
        }

    payload = {
        "schema_version": "1.0",
        "source": {
            "book": "御定骈字类编（张廷玉等奉敕编）",
            "copyright": "公版",
            "edition": "殆知阁中华古籍库（daizhige.org）清洗本",
            "file": rel(path, root),
            "sha256": sha256_of(path),
            "extracted_at": "2026-11-20",
            "evidence_level": "classical_reference",
            "说明": "凡例明言『除虚字不采外……分为一十二门』，故本表只覆盖实字；"
                    "门以首字字面归类，虚字/动词多不收，覆盖不到处标 unable。",
        },
        "门名归一": MEN_ALIAS,
        "统计": {
            "正文门标题数": len(heads),
            "单字头数": len(single),
            "双字头数_归一后": len(double),
            "双字头_唯一归门": len(double_clean),
            "双字头_多门歧义": len(ambiguous),
        },
        "双字义类编码": {
            "格式": "双字义类_紧凑: {门类: 定长双字串拼接}；每 2 个字符为一个词，按门分组。",
            "还原方法": "{w: men for men, blob in 双字义类_紧凑.items() "
                    "for w in (blob[i:i+2] for i in range(0, len(blob), 2))}",
            "自证": "生成器写出前已断言还原结果 == 抽取结果（词数 %d），不等即拒绝写盘。" % len(double_clean),
            "选择理由": "收敛到生成器格式，避免生成器与手工回退互相覆盖；运行时 duizhang_pos.py "
                    "同时兼容紧凑与完整 dict 两种格式。",
        },
        "证据回指": evidence_ref,
        "单字义类": single,
        "双字义类_紧凑": compact,
        "双字多门歧义": ambiguous,
    }
    dump_json(payload, os.path.join(out, "duizhang_category.json"))
    return payload


def build_precedent(root: str, out: str, t2s: dict) -> dict:
    pairs: dict[str, dict] = {}

    def add(a: str, b: str, book: str, raw: str) -> None:
        a, b = cjk_only(a), cjk_only(b)
        if not (1 <= len(a) <= 4 and 1 <= len(b) <= 4):
            return
        if len(a) != len(b):
            return
        if not (is_cjk(a[0]) and is_cjk(b[0])):
            return
        na, nb = norm_word(a, t2s), norm_word(b, t2s)
        if na == nb:
            return
        key = "|".join(sorted((na, nb)))
        rec = pairs.setdefault(key, {"左": na, "右": nb, "books": [], "原文": []})
        if book not in rec["books"]:
            rec["books"].append(book)
        if raw not in rec["原文"] and len(rec["原文"]) < 3:
            rec["原文"].append(raw)

    # 佩文韵府「對語」：A/B　C/D ... 直到「摘句」或换行
    p = os.path.join(root, REL_PEIWEN)
    text = read_text(p)
    for m in re.finditer(r"對語(.+?)(?:摘句|\n)", text):
        for item in re.split(r"[\u3000\s]+", m.group(1)):
            if "/" in item:
                a, b = item.split("/", 1)
                add(a, b, "佩文韵府", item)

    # 声律启蒙 / 笠翁对韵：`X对Y`
    for name, relp in (("声律启蒙", REL_SHENGLV), ("笠翁对韵", REL_LIWENG)):
        t = read_text(os.path.join(root, relp))
        for a, b in re.findall(r"([\u4e00-\u9fff]{1,4})对([\u4e00-\u9fff]{1,4})", t):
            if a == name[:2] or b == "韵":   # 剔除标题「笠翁对韵」自身
                continue
            add(a, b, name, a + "对" + b)

    single = {k: v for k, v in pairs.items() if len(v["左"]) == 1}
    double = {k: v for k, v in pairs.items() if len(v["左"]) == 2}
    payload = {
        "schema_version": "1.0",
        "source": {
            "books": [
                {"book": "佩文韵府（张玉书等奉敕编）", "file": rel(p, root),
                 "sha256": sha256_of(p)},
                {"book": "声律启蒙（车万育）", "file": rel(os.path.join(root, REL_SHENGLV), root),
                 "sha256": sha256_of(os.path.join(root, REL_SHENGLV))},
                {"book": "笠翁对韵（李渔）", "file": rel(os.path.join(root, REL_LIWENG), root),
                 "sha256": sha256_of(os.path.join(root, REL_LIWENG))},
            ],
            "copyright": "公版",
            "evidence_level": "classical_reference",
            "说明": "先例只作『古人如此对』的证据，不作硬判；未收录的对不判为错，标 unable。",
        },
        "统计": {"先例总数": len(pairs), "单字先例": len(single), "双字先例": len(double)},
        "先例": pairs,
    }
    dump_json(payload, os.path.join(out, "duizhang_precedent.json"))
    return payload


def build_rules(root: str, out: str) -> dict:
    """借对、邻对、工拙、合掌判据——逐条引包内原文行号，人工摘录。"""
    def src(p: str, lines: list[int], note: str) -> dict:
        fp = os.path.join(root, p)
        return {"book": p.split("/")[-1], "file": rel(fp, root),
                "sha256": sha256_of(fp), "lines": lines, "note": note}

    payload = {
        "schema_version": "1.0",
        "source": {
            "books": [
                src(REL_GELO, [177, 727, 728, 729, 732, 733, 2508],
                    "工对/邻对/宽对标准与借对实例"),
                src(REL_WENXIN, [155], "『一意两出，义之骈枝也；同辞重句，文之癫赘也』"),
                src(REL_HUIFENG, [2185], "『对偶句要浑成……要不合掌』"),
            ],
            "evidence_level": "classical_reference",
            "摘录方式": "人工逐条摘录并标注行号，未做自动分词；不在表内的一律 unable。",
        },
        "工拙判据": {
            "工对": "凡同类的词相对，叫做工对；同一小类更是工对；经常平列（天地/诗酒/花鸟）与反义词也算工对。",
            "邻对": "邻近的事类相对：天文对时令，地理对宫室，颜色对方位，同义词对连绵字。工对与宽对之间。",
            "宽对": "形式服从于内容；不同类而可相对者。",
            "出处": "《诗词格律》王力，第 727、728、732、733 行",
        },
        "邻对组合": [
            {"门类": ["天地门", "时令门"], "说明": "天文对时令", "出处": "《诗词格律》第 733 行"},
            {"门类": ["山水门", "居处门"], "说明": "地理对宫室", "出处": "《诗词格律》第 733 行"},
            {"门类": ["采色门", "方隅门"], "说明": "颜色对方位", "出处": "《诗词格律》第 733 行"},
        ],
        "借对实例": [
            {"对": ["杨梅", "鸡黍"], "借字": "杨", "本字": "羊", "类型": "借音",
             "例句": "厨人具鸡黍，稚子摘杨梅", "出处": "《诗词格律》第 2508 行，引《沧浪诗话》"},
            {"对": ["石楠", "云母"], "借字": "楠", "本字": "男", "类型": "借音",
             "例句": "水舂云母碓，风扫石楠花", "出处": "《诗词格律》第 2508 行，引《沧浪诗话》"},
            {"对": ["竹叶", "菊花"], "借字": "叶", "本字": "（竹叶为酒名，借意）", "类型": "借意",
             "例句": "竹叶于人既无分，菊花从此不须开", "出处": "《诗词格律》第 2508 行，引《沧浪诗话》"},
            {"对": ["朱云", "白日"], "借字": "云", "本字": "（朱云为人名，借意）", "类型": "借意",
             "例句": "当时物议朱云小，后代声名白日长", "出处": "《诗词格律》第 2508 行，引沈括《梦溪笔谈》"},
            {"对": ["沧", "蓝"], "借字": "沧", "本字": "苍", "类型": "借音",
             "例句": "沧海月明珠有泪，蓝田日暖玉生烟", "出处": "《诗词格律》第 2508 行"},
            {"对": ["沧", "白"], "借字": "沧", "本字": "苍", "类型": "借音",
             "例句": "东郭沧江合，西山白雪高", "出处": "《诗词格律》第 2508 行"},
            {"对": ["沧", "青"], "借字": "沧", "本字": "苍", "类型": "借音",
             "例句": "一卧沧江惊岁晚，几回青琐点朝班", "出处": "《诗词格律》第 2508 行"},
            {"对": ["汉", "湘"], "借字": "汉", "本字": "（借汉水之『汉』）", "类型": "借意",
             "例句": "汉文有道恩犹薄，湘水无情吊岂知", "出处": "《诗词格律》第 2508 行"},
        ],
        "合掌判据": {
            "可机械化": "对位同字（出句与对句同一位置出现同一个实字）",
            "判据": [
                "《文心雕龙·镕裁》：『一意两出，义之骈枝也；同辞重句，文之癫赘也』（第 155 行）",
                "《蕙风词话》：『对偶句要浑成，要色泽相称，要不合掌』（第 2185 行）",
            ],
            "不可机械化": "同义异字型合掌无同字可据，包内诗话只给零星实例（见 duizhang_advanced.json 的"
                          "『合掌组_同义异字』），未给出通用同义词表；表外不猜测，标 unable。",
        },
    }
    dump_json(payload, os.path.join(out, "duizhang_borrow.json"))
    return payload


def build_advanced(root: str, out: str) -> dict:
    """第二轮：诗话明列的同义合掌实例、反向哨兵、借对扩充、流水对/当句对/扇对判据。

    全部人工逐条摘录并带行号；不做「看起来像」的自动扩充，不从韻藻/门类推导同义词。
    """
    def src(p: str, lines: list[int], note: str) -> dict:
        fp = os.path.join(root, p)
        return {"book": p.split("/")[-1], "file": rel(fp, root),
                "sha256": sha256_of(fp), "lines": lines, "note": note}

    payload = {
        "schema_version": "1.0",
        "source": {
            "books": [
                src(REL_WENXIN, [155, 167], "丽辞四对、游雁/归鸿与宣尼/孔丘骈枝"),
                src(REL_GELO, [742, 744, 728], "借音借义实例、流水对定义、句中自对"),
                src("二、格律入门/诗词例话（周振甫）.md",
                    [2807, 2823, 2830, 2834, 2835, 8917, 8931],
                    "当句对判据、合掌实例与反向哨兵"),
                src("二、格律入门/学诗浅说（瞿蜕园·周紫宜）.md", [2993], "合掌＝呆板重复"),
                src("五、诗话词话/诗人玉屑（魏庆之）.md", [188, 189, 380],
                    "十字对/十四字对/当句对实例、斜阳暮重出"),
                src("五、诗话词话/苕溪渔隐丛话（胡仔）.md", [431], "扇对格：1对3、2对4"),
            ],
            "evidence_level": "classical_reference",
            "摘录方式": "人工逐条摘录，每条带书名+行号+原文；表外一律 unable，不做近似推断。",
            "反向哨兵说明": "下列组合看似同义但原文明确不判合掌；命中哨兵时压制合掌信号，防误伤。",
        },
        "合掌组_同义异字": [
            {"对": ["游雁", "归鸿"], "类型": "同物异名（雁/鸿）",
             "例句": "游雁比翼翔，归鸿知接翮", "出处": "《文心雕龙·丽辞》第 167 行：『若斯重出，即对句之骈枝也』"},
            {"对": ["宣尼", "孔丘"], "类型": "同人异称（皆指孔子）",
             "例句": "宣尼悲获麟，西狩涕孔丘", "出处": "《文心雕龙·丽辞》第 167 行；《诗词例话》第 2835 行"},
            {"对": ["悲", "涕"], "类型": "同义动词",
             "例句": "宣尼悲获麟，西狩涕孔丘", "出处": "《诗词例话》第 2835 行：『「悲」就是「涕」』"},
            {"对": ["获麟", "西狩"], "类型": "同一事件",
             "例句": "宣尼悲获麟，西狩涕孔丘", "出处": "《诗词例话》第 2835 行：『「获麟」和「西狩」是一件事』"},
            {"对": ["朝", "昼"], "类型": "部分同义（有争议）",
             "例句": "蚕屋朝寒闭，田家昼雨闲", "出处": "谢榛《四溟诗话》引于《诗词例话》第 2830 行；"
                                              "周振甫同页反驳『未免立论太苛刻』，故仅作低置信提示"},
        ],
        "合掌反向哨兵": [
            {"对": ["蝉噪", "鸟鸣"], "原因": "内容不同、用意相同，非合掌",
             "例句": "蝉噪林逾静，鸟鸣山更幽", "出处": "《诗词例话》第 2834 行：『一讲蝉噪，一讲鸟鸣……要是两句内容相同就成为合掌了』"},
            {"对": ["缘", "为"], "原因": "虚词同义不算缺点",
             "例句": "花径不曾缘客扫，蓬门今始为君开", "出处": "《诗词格律》第 731 行：『因为它们是虚词（介词），不是实词，所以不算缺点』"},
            {"对": ["斜阳", "暮"], "原因": "句内重出，非对仗合掌",
             "例句": "杜鹃声里斜阳暮", "出处": "《诗人玉屑》第 380 行：东坡语『既云斜阳，又云暮，则重出也』"},
        ],
        "借对实例_扩充": [
            {"对": ["篮", "蓝"], "借字": "篮", "本字": "蓝", "类型": "借音",
             "例句": "（王力举颜色借对通例）", "出处": "《诗词格律》第 742 行：『借音多见于颜色对，如借「篮」为「蓝」』"},
            {"对": ["皇", "黄"], "借字": "皇", "本字": "黄", "类型": "借音",
             "例句": "（王力举颜色借对通例）", "出处": "《诗词格律》第 742 行"},
            {"对": ["珠", "朱"], "借字": "珠", "本字": "朱", "类型": "借音",
             "例句": "（王力举颜色借对通例）", "出处": "《诗词格律》第 742 行"},
            {"对": ["清", "青"], "借字": "清", "本字": "青", "类型": "借音",
             "例句": "（王力举颜色借对通例）", "出处": "《诗词格律》第 742 行"},
            {"对": ["清", "白"], "借字": "清", "本字": "青", "类型": "借音",
             "例句": "思家步月清宵立，忆弟看云白日眠", "出处": "《诗词格律》第 742 行：『以「清」对「白」，就是这种情况』"},
            {"对": ["李", "茅"], "借字": "李", "本字": "桃李之李（非行李之李）", "类型": "借意",
             "例句": "行李淹吾舅，诛茅问老翁", "出处": "《诗词格律》第 742 行"},
            {"对": ["寻常", "七十"], "借字": "寻常", "本字": "八尺为寻、两寻为常（长度）", "类型": "借意",
             "例句": "酒债寻常行处有，人生七十古来稀", "出处": "《诗词格律》第 742 行"},
            {"对": ["雉", "雁"], "借字": "雉", "本字": "雉本义为山鸡（鸟）", "类型": "借本义",
             "例句": "雉城……雁塞", "出处": "《诗词格律》第 9313 行：『借其本义来与「雁」构成对仗』"},
            {"对": ["杨", "李"], "借字": "杨", "本字": "（杨朱对李白，兼人名与植物）", "类型": "借意",
             "例句": "杨朱与李白相对", "出处": "《诗词格律》第 8784 行：『既是人名相对，又是一种借对』"},
            {"对": ["朱", "白"], "借字": "朱", "本字": "（颜色相对）", "类型": "借意",
             "例句": "杨朱与李白相对", "出处": "《诗词格律》第 8784 行：『「朱」对「白」是颜色相对』"},
        ],
        "流水对_实例": [
            {"对": ["即从巴峡穿巫峡", "便下襄阳向洛阳"], "出处": "《诗词格律》第 744 行；第 2505 行"},
            {"对": ["人怜巧语情虽重", "鸟忆高飞意不同"], "出处": "《诗词格律》第 744 行"},
            {"对": ["塞上长城空自许", "镜中衰鬓已先斑"], "出处": "《诗词格律》第 744 行"},
            {"对": ["海内存知己", "天涯若比邻"], "出处": "《诗词格律概要》第 3240 行"},
            {"对": ["玉玺不缘归日角", "锦帆应是到天涯"], "出处": "《诗词格律概要》第 3240 行"},
        ],
        "流水对_判据": {
            "原文": "一般是平行的两句话，它们各有独立性。但是，也有一种对仗是一句话分成两句话，"
                    "其实十个字或十四个字只是一个整体，出句独立起来没有意义，至少是意义不全。这叫流水对。",
            "出处": "《诗词格律》第 744 行；《诗词格律概要》第 3240 行",
            "可机械化": "否——『出句独立起来没有意义』是语义判据，无确定性句法器；"
                        "本实现只做上列原书实例的精确匹配，不构成通用识别能力。",
        },
        "当句对_判据": {
            "原文": "唐人诗文或于一句中自成对偶，谓之当句对。",
            "出处": "《诗词例话》第 2807、2823、8917、8931 行；王力《诗词格律》第 177、188、728 行",
            "两类": [
                {"类型": "字面不同", "例": "小院/回廊、浴凫/飞鹭、桂酒/椒浆",
                 "出处": "《诗词例话》第 2823 行"},
                {"类型": "一字相同", "例": "戎马/归马、巴峡/巫峡、襄阳/洛阳",
                 "出处": "《诗词例话》第 2823、8931 行"},
            ],
            "可机械化": "部分——句中两个双字词同属一门（字面不同型）或共享一字（一字相同型），可给 soft 候选；"
                        "是否真为当句对仍须人工确认。词典未覆盖时只做下表明列实例的精确匹配。",
            "当句对实例": [
                {"对": ["小院", "回廊"], "类型": "字面不同", "出处": "《诗词例话》第 2823 行"},
                {"对": ["浴凫", "飞鹭"], "类型": "字面不同", "出处": "《诗词例话》第 2823 行"},
                {"对": ["桂酒", "椒浆"], "类型": "字面不同", "出处": "《诗词例话》第 2823 行"},
                {"对": ["孤云", "独鸟"], "类型": "字面不同", "出处": "《诗词例话》第 2823 行"},
                {"对": ["万里", "千山"], "类型": "字面不同", "出处": "《诗词例话》第 2823 行"},
                {"对": ["戎马", "归马"], "类型": "一字相同", "出处": "《诗词例话》第 2823 行"},
                {"对": ["巴峡", "巫峡"], "类型": "一字相同", "出处": "《诗词例话》第 8931 行"},
                {"对": ["襄阳", "洛阳"], "类型": "一字相同", "出处": "《诗词例话》第 8931 行"},
                {"对": ["千家", "百家"], "类型": "一字相同", "出处": "《诗词例话》第 2823 行"},
            ],
        },
        "扇对_判据": {
            "原文": "律诗有扇对格，第一与第三句对，第二与第四对。",
            "出处": "《苕溪渔隐丛话》第 431 行；《诗人玉屑》第 188 行（隔句对：第一句对第三句，第二句对第四句）",
            "可机械化": "可——纯位置规则（1↔3、2↔4），给 soft 候选；不判 fail。",
        },
    }
    dump_json(payload, os.path.join(out, "duizhang_advanced.json"))
    return payload


def main() -> None:
    root = src_root(sys.argv)
    out = out_dir(sys.argv, skill_data_dir())
    ts_path = os.path.join(out, "trad_simp.json")
    if not os.path.exists(ts_path):
        sys.stderr.write("缺少 %s：请先抽取 trad_simp.json\n" % ts_path)
        raise SystemExit(2)
    with open(ts_path, "r", encoding="utf-8") as fh:
        t2s = json.load(fh)["t2s"]
    c = build_category(root, out, t2s)
    p = build_precedent(root, out, t2s)
    r = build_rules(root, out)
    adv = build_advanced(root, out)
    print("类别: %s；先例: %s；判据: %d 条借对；高级: 合掌组 %d / 哨兵 %d / 借对扩充 %d / 流水对 %d" % (
        c["统计"], p["统计"], len(r["借对实例"]),
        len(adv["合掌组_同义异字"]), len(adv["合掌反向哨兵"]),
        len(adv["借对实例_扩充"]), len(adv["流水对_实例"])))


if __name__ == "__main__":
    main()
