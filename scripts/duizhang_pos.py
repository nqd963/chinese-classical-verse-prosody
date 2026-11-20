"""对仗 L3 证据层：基于内置出处词典的义类/工拙软判。

铁律（承 裁决书 §三 L3「只能软判，给证据与建议」）：
- 零第三方依赖；不做通用分词/词性标注。
- 词典覆盖不到的字 → unable，绝不猜词性。
- 只输出「哪条有证据、哪条查不了」，绝不输出「对仗已通过」。
- 全部软判，不参与 hard fail，不否定用户的艺术判断。

用法（模块）：from duizhang_pos import judge_couplet
"""
from __future__ import annotations

import json
import os

import _paths

_FILES = ("duizhang_category.json", "duizhang_precedent.json",
          "duizhang_borrow.json", "duizhang_advanced.json")
_CACHE: dict | None = None
_MISSING: str | None = None

# 判断一个门是否占主导；占比不足则视为多门歧义 → unable
_DOMINANT_MIN = 0.60


def _load() -> dict | None:
    global _CACHE, _MISSING
    if _CACHE is not None or _MISSING is not None:
        return _CACHE
    data = {}
    for name in _FILES:
        p = os.path.join(_paths.DATA_DIR, name)
        if not os.path.exists(p):
            _MISSING = "缺少 %s：对仗 L3 证据层不可用（不猜测、不降级为通过）" % name
            return None
        with open(p, "r", encoding="utf-8") as fh:
            data[name] = json.load(fh)
    _CACHE = data
    return _CACHE


def _reset() -> None:
    """仅供自测：强制重载。"""
    global _CACHE, _MISSING
    _CACHE, _MISSING = None, None


def _dominant(counts: dict) -> str | None:
    if not counts:
        return None
    total = sum(counts.values())
    men, n = max(counts.items(), key=lambda kv: kv[1])
    return men if total and n / total >= _DOMINANT_MIN else None


def _build_index(data: dict) -> dict:
    cat = data["duizhang_category.json"]
    pre = data["duizhang_precedent.json"]
    rules = data["duizhang_borrow.json"]

    single_cat = cat["单字义类"]
    # 紧凑格式：{门类: "定长双字串拼接"}；还原为 {词: 门类} 后与旧格式等价。
    # 只在加载时还原一次，查询路径不变。
    if "双字义类_紧凑" in cat:
        double_cat = {
            w: category
            for category, blob in cat["双字义类_紧凑"].items()
            for w in (blob[i:i + 2] for i in range(0, len(blob), 2))
        }
    else:
        double_cat = cat["双字义类"]

    pre_single, pre_double = set(), set()
    pre_src = {}
    for rec in pre["先例"].values():
        a, b = rec["左"], rec["右"]
        key = (a, b) if a <= b else (b, a)
        if len(a) == 1:
            pre_single.add(key)
        elif len(a) == 2:
            pre_double.add(key)
        pre_src[key] = {"书": "、".join(rec["books"]), "原文": "；".join(rec["原文"][:2])}

    borrow_single, borrow_double = set(), set()
    borrow_src = {}
    for rec in rules["借对实例"]:
        a, b = rec["对"]
        key = (a, b) if a <= b else (b, a)
        (borrow_single if len(a) == 1 else borrow_double).add(key)
        borrow_src[key] = {"借字": rec["借字"], "本字": rec["本字"], "类型": rec["类型"],
                           "例句": rec["例句"], "出处": rec["出处"]}

    # 第二轮扩充：借对实例、同义合掌组、反向哨兵、流水对实例
    adv = data["duizhang_advanced.json"]
    for rec in adv["借对实例_扩充"]:
        a, b = rec["对"]
        key = (a, b) if a <= b else (b, a)
        (borrow_single if len(a) == 1 else borrow_double).add(key)
        borrow_src[key] = {"借字": rec["借字"], "本字": rec["本字"], "类型": rec["类型"],
                           "例句": rec["例句"], "出处": rec["出处"]}

    syn_groups = []
    for rec in adv["合掌组_同义异字"]:
        a, b = rec["对"]
        syn_groups.append({"a": a, "b": b, "类型": rec["类型"],
                           "例句": rec["例句"], "出处": rec["出处"]})
    sentinels = set()
    for rec in adv["合掌反向哨兵"]:
        a, b = rec["对"]
        sentinels.add((a, b) if a <= b else (b, a))
    liushui = {(tuple(rec["对"])) for rec in adv["流水对_实例"]}
    dangju_exact = {(tuple(rec["对"])) for rec in adv["当句对_判据"]["当句对实例"]}

    neighbor = set()
    for rec in rules["邻对组合"]:
        a, b = rec["门类"]
        neighbor.add((a, b))
        neighbor.add((b, a))

    return {
        "single_cat": single_cat, "double_cat": double_cat,
        "pre_single": pre_single, "pre_double": pre_double, "pre_src": pre_src,
        "borrow_single": borrow_single, "borrow_double": borrow_double,
        "borrow_src": borrow_src, "neighbor": neighbor,
        "syn_groups": syn_groups, "sentinels": sentinels, "liushui": liushui,
        "dangju_exact": dangju_exact,
        "工拙判据": rules["工拙判据"], "合掌判据": rules["合掌判据"],
        "流水对_判据": adv["流水对_判据"], "当句对_判据": adv["当句对_判据"],
        "扇对_判据": adv["扇对_判据"],
    }


def _evidence(book: str, text: str) -> list[dict]:
    return [{"书": book, "据": text}]


def _judge_char(a: str, b: str, ix: dict) -> dict:
    """单字对位判定。优先级：合掌 > 借对 > 先例 > 义类 > unable。"""
    key = (a, b) if a <= b else (b, a)
    base = {"出句": a, "对句": b}

    # 1 合掌：对位同字，且为骈字类编所收实字（虚字同字不作合掌，避免误伤）
    if a == b and a in ix["single_cat"]:
        return dict(base, grade="合掌", basis="对位同字（同辞重句）",
                    evidence=_evidence("文心雕龙·镕裁／蕙风词话",
                                       "『同辞重句，文之癫赘也』；『对偶句要……不合掌』"))

    # 2 借对：命中即降级，不报宽对/失类
    if key in ix["borrow_single"]:
        e = ix["borrow_src"][key]
        return dict(base, grade="借对", basis="借对实例（降级，不判失类）",
                    evidence=_evidence(e["出处"], "%s：借「%s」为「%s」（%s）"
                                       % (e["例句"], e["借字"], e["本字"], e["类型"])))

    # 3 先例：王力《诗词格律》第 727/729 行——平列与反义词也算工对
    if key in ix["pre_single"]:
        e = ix["pre_src"][key]
        return dict(base, grade="工对", basis="历代工对先例（反义/平列）",
                    evidence=_evidence(e["书"], e["原文"]))

    # 4 义类：骈字类编门类；同门=工对，邻门=邻对，异门=宽对；多门歧义=unable
    ma, mb = _dominant(ix["single_cat"].get(a, {})), _dominant(ix["single_cat"].get(b, {}))
    if ma and mb:
        if ma == mb:
            return dict(base, grade="工对", basis="同类相对（同门）",
                        evidence=_evidence("骈字类编／诗词格律",
                                           "「%s」与「%s」同属%s；『同类的词相对，叫做工对』"
                                           % (a, b, ma)))
        if (ma, mb) in ix["neighbor"]:
            return dict(base, grade="邻对", basis="邻近事类相对",
                        evidence=_evidence("诗词格律 第733行", "%s 对 %s（邻近事类，工对与宽对之间）"
                                           % (ma, mb)))
        return dict(base, grade="宽对", basis="异类相对（不同门）",
                    evidence=_evidence("诗词格律 第732行／骈字类编",
                                       "「%s」属%s、「%s」属%s；『形式服从于内容』，宽对不判错"
                                       % (a, ma, b, mb)))

    # 5 覆盖不到：铁律——标 unable，不猜
    miss = [c for c in (a, b) if c not in ix["single_cat"]]
    return dict(base, grade="unable", basis="词典未覆盖，不猜词性",
                evidence=[{"书": "—", "据": "未覆盖字：" + "、".join(miss)}])


def _judge_phrase(a: str, b: str, ix: dict) -> dict | None:
    """双字窗口证据；无证据返回 None（不给噪音）。"""
    key = (a, b) if a <= b else (b, a)
    base = {"出句": a, "对句": b}
    if a == b:
        return dict(base, grade="合掌", basis="对位同词",
                    evidence=_evidence("文心雕龙·镕裁", "『同辞重句，文之癫赘也』"))
    if key in ix["borrow_double"]:
        e = ix["borrow_src"][key]
        return dict(base, grade="借对", basis="借对实例（降级）",
                    evidence=_evidence(e["出处"], e["例句"]))
    if key in ix["pre_double"]:
        e = ix["pre_src"][key]
        return dict(base, grade="工对", basis="历代工对先例",
                    evidence=_evidence(e["书"], e["原文"]))
    ma, mb = ix["double_cat"].get(a), ix["double_cat"].get(b)
    if ma and mb:
        if ma == mb:
            return dict(base, grade="工对", basis="同类双字（同门）",
                        evidence=_evidence("骈字类编", "「%s」「%s」同属%s" % (a, b, ma)))
        if (ma, mb) in ix["neighbor"]:
            return dict(base, grade="邻对", basis="邻近事类相对",
                        evidence=_evidence("诗词格律 第733行", "%s 对 %s" % (ma, mb)))
        return dict(base, grade="宽对", basis="异类双字（不同门）",
                    evidence=_evidence("骈字类编", "「%s」属%s、「%s」属%s" % (a, ma, b, mb)))
    return None


def _greedy_tokens(line: str, ix: dict) -> dict[int, str]:
    """自带词典 + 最大匹配：双字头词优先，命中即吞两字；不跨词滑窗，不猜。"""
    out: dict[int, str] = {}
    j = 0
    while j < len(line):
        w = line[j:j + 2]
        if len(w) == 2 and w in ix["double_cat"]:
            out[j] = w
            j += 2
        else:
            out[j] = line[j]
            j += 1
    return out


def _detect_synonym_hezhang(line_a: str, line_b: str, ix: dict) -> list[dict]:
    """同义异字型合掌：跨行扫描，只在诗话明列的合掌组上精确命中，先过反向哨兵。"""
    out = []
    for g in ix["syn_groups"]:
        a, b = g["a"], g["b"]
        key = (a, b) if a <= b else (b, a)
        if key in ix["sentinels"]:
            continue
        hit_ab = a in line_a and b in line_b
        hit_ba = b in line_a and a in line_b
        if hit_ab or hit_ba:
            confidence = "低（原书有争议）" if "争议" in g["类型"] else "中（原书明列）"
            out.append({"出句词": a if hit_ab else b, "对句词": b if hit_ab else a,
                        "类型": g["类型"], "置信": confidence,
                        "basis": "诗话明列的同义合掌组（跨行精确匹配）",
                        "evidence": _evidence(g["出处"], g["例句"])})
    return out


def _detect_dangju(line: str, ix: dict) -> list[dict]:
    """当句对候选：句中两个双字词同门（字面不同型）或共享一字（一字相同型）。"""
    toks = [(j, w) for j, w in _greedy_tokens(line, ix).items() if len(w) == 2]
    out = []
    for i in range(len(toks)):
        for k in range(i + 1, len(toks)):
            (ja, wa), (jb, wb) = toks[i], toks[k]
            if wa == wb:
                continue
            shared = set(wa) & set(wb)
            ma, mb = ix["double_cat"].get(wa), ix["double_cat"].get(wb)
            if len(shared) == 1:
                out.append({"词": [wa, wb], "类型": "一字相同", "共享字": "".join(sorted(shared)),
                            "evidence": _evidence("诗词例话 第2823、8931行",
                                                  "『戎马』对『归马』式：相对的辞中有一字相同")})
            elif ma and mb and ma == mb:
                out.append({"词": [wa, wb], "类型": "字面不同（同门）", "门类": ma,
                            "evidence": _evidence("诗词例话 第2823行／王力 第177、188行",
                                                  "『小院』对『回廊』式：一句中自成对偶")})
    # 词典未覆盖时，只认诗话明列的当句对实例（不靠「看起来像」扩充）
    for pair in ix["dangju_exact"]:
        a, b = pair
        if a in line and b in line:
            out.append({"词": [a, b], "类型": "原书明列实例（精确匹配）",
                        "evidence": _evidence("诗词例话 第2823、8931行",
                                              "一句中自成对偶：『%s』对『%s』" % (a, b))})
    return out


def judge_sandui(lines: list[str], tone_of=None) -> dict:
    """扇对（隔句对）：判据是纯位置规则（1↔3、2↔4），但经反事实实测**不可机械化**。

    实测反证：正常律诗因「首联相对 + 二三联相粘」，1↔3 与 2↔4 的二四六平仄
    同样两两相反，与真正扇对无法区分；加义类打分后两者得分亦无判别力（覆盖率仅
    0–0.4）。故不输出候选，以免把每一首正常律诗都误报为扇对。宁可交付 unable。
    """
    return {
        "status": "unable",
        "判据": "律诗有扇对格，第一与第三句对，第二与第四对",
        "出处": "《苕溪渔隐丛话》第 431 行；《诗人玉屑》第 188 行",
        "原因": "判据虽是位置规则，但正常律诗同样满足 1↔3、2↔4 的二四六平仄相对"
                "（首联相对＋二三相粘所致），义类打分亦无判别力；实测会把正常律诗误报为扇对。",
        "处置": "不猜测、不输出候选；扇对留人工判断。",
    }


def judge_couplet(line_a: str, line_b: str) -> dict:
    """对一句对仗（出句、对句）做 L3 软判。任一数据缺失即 not_supported。"""
    data = _load()
    if data is None:
        return {"status": "not_supported", "reason": _MISSING,
                "label": "查不了就说查不了，不猜测、不判通过"}
    ix = _build_index(data)

    n = min(len(line_a), len(line_b))
    positions = [_judge_char(line_a[j], line_b[j], ix) for j in range(n)]
    for j, row in enumerate(positions):
        row["col"] = j + 1

    # 双字证据只在「两行同一位置都切成双字头词」时给，避免跨词滑窗造假证据
    ta, tb = _greedy_tokens(line_a, ix), _greedy_tokens(line_b, ix)
    phrase = []
    for j in range(n - 1):
        a, b = ta.get(j), tb.get(j)
        if a and b and len(a) == 2 and len(b) == 2:
            hit = _judge_phrase(a, b, ix)
            if hit:
                hit["col"] = j + 1
                phrase.append(hit)

    # 第二轮：同义异字合掌（跨行精确匹配 + 反向哨兵）、当句对候选、流水对精确匹配
    syn_hezhang = _detect_synonym_hezhang(line_a, line_b, ix)
    dangju = {"出句": _detect_dangju(line_a, ix), "对句": _detect_dangju(line_b, ix)}
    liushui_hit = None
    if (line_a, line_b) in ix["liushui"] or (line_b, line_a) in ix["liushui"]:
        liushui_hit = {"basis": "原书流水对实例精确匹配",
                       "evidence": _evidence("诗词格律 第744行／诗词格律概要 第3240行",
                                             "『出句独立起来没有意义，至少是意义不全』"),
                       "label": "精确匹配原书例，不构成通用识别能力"}

    covered = [r for r in positions if r["grade"] != "unable"]
    unknown = sorted({r["出句"] for r in positions if r["grade"] == "unable"
                      and r["出句"] not in ix["single_cat"]}
                     | {r["对句"] for r in positions if r["grade"] == "unable"
                        and r["对句"] not in ix["single_cat"]})
    grades = {}
    for r in positions:
        grades[r["grade"]] = grades.get(r["grade"], 0) + 1

    return {
        "status": "partial",
        "method": "内置出处词典证据匹配（骈字类编门类 + 佩文韵府对语 + 声律启蒙/笠翁对韵 + 王力判据）；"
                  "非通用分词，非词性标注",
        "label": "证据提示，非定论：软判，不作『对仗已通过』",
        "coverage": {
            "字数": n, "可判位置": len(covered),
            "覆盖率": round(len(covered) / n, 4) if n else 0.0,
            "未覆盖字": unknown,
        },
        "分级统计": grades,
        "positions": positions,
        "双字证据": phrase,
        "同义异字合掌": syn_hezhang,
        "当句对候选": dangju,
        "流水对": liushui_hit,
        "unable_positions": [r["col"] for r in positions if r["grade"] == "unable"],
        "判据出处": {"工拙": ix["工拙判据"], "合掌": ix["合掌判据"],
                 "流水对": ix["流水对_判据"], "当句对": ix["当句对_判据"],
                 "扇对": ix["扇对_判据"]},
        "data_source": {"files": {f: _paths.data_digest(f) for f in _FILES}},
    }


def not_supported(reason: str) -> dict:
    return {"status": "not_supported", "reason": reason,
            "label": "查不了就说查不了，不猜测、不判通过"}
