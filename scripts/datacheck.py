"""数据层断言（A7 / A8 / A9 / A10 / 繁简归一 / 半部韵范围）。

**方法规矩（写入本文件注释，供后续维护者遵守）**：
判定一个文件有无杂质，**不得依据 `head -c` / `tail -c` 这类字节截断输出的观感**——
按字节截断会把汉字切断，产生 `ç`、`é`、`»` 之类的显示伪影，看着像文件里有杂质，其实没有。
必须整文件读入后用程序扫描。本项目曾因误把伪影当杂质，白写过三条「注定通过」的假断言。

本模块的每条断言都必须**能失败**：先构造一个已知的坏输入验证它会报错，
再对真实数据跑。做不到这一点的断言一律不进本文件。
"""
from __future__ import annotations

import json
import random
import re

import _paths

# A10 素材一所列入声清单（47 字，逐字断言为仄且 tone_class == 入）
RUSHENG_47 = (
    "一七八十百亿"          # 数字类
    "白黑赤碧绿"            # 颜色类
    "出入得失读学习答"      # 动作类
    "发节绝别折结洁格阁国菊竹烛逐独福服熟叔属束术鹿禄录宿肃速"   # 其他
)
assert len(RUSHENG_47) == 47, "入声清单必须是 47 字，实为 %d" % len(RUSHENG_47)

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# A9 增补：词林正韵 header 词组——出现在字串里即说明「仄声：…通用」串进了字表
HEADER_WORDS = ("平声", "仄声", "入声", "通用", "独用", "<半>", "半>")
HEADER_CHARS = set("平仄上去入声通用独半")

# 裁决书 §9.11.1：逐组基线，容差 0；主键 (部, 韵目)（半部韵在两部各一块，字表不同）。
# 基线量自施工源 D2《词林正韵》原文，经 extract_cilin.py 现地重抽逐字节复核（115 组，leak=0）。
BASELINE_RAW = {
    (1, "一东"): 73,
    (1, "一董"): 12,
    (1, "一送"): 20,
    (1, "二冬"): 52,
    (1, "二宋"): 15,
    (1, "二肿"): 24,
    (2, "七阳"): 173,
    (2, "三江"): 17,
    (2, "三绛"): 5,
    (2, "三讲"): 6,
    (2, "二十三漾"): 63,
    (2, "二十二养"): 64,
    (3, "九泰<半>"): 16,
    (3, "五尾"): 19,
    (3, "五微"): 41,
    (3, "五未"): 25,
    (3, "八荠"): 25,
    (3, "八霁"): 98,
    (3, "八齐"): 54,
    (3, "十一队<半>"): 28,
    (3, "十灰<半>"): 39,
    (3, "十贿<半>"): 14,
    (3, "四寘"): 125,
    (3, "四支"): 185,
    (3, "四纸"): 113,
    (4, "七虞"): 149,
    (4, "七遇"): 95,
    (4, "七麌"): 103,
    (4, "六御"): 30,
    (4, "六语"): 50,
    (4, "六鱼"): 56,
    (5, "九佳<半>"): 31,
    (5, "九泰<半>"): 19,
    (5, "九蟹"): 9,
    (5, "十一队<半>"): 29,
    (5, "十卦<半>"): 34,
    (5, "十灰<半>"): 27,
    (5, "十贿<半>"): 20,
    (6, "十一真"): 91,
    (6, "十一轸"): 32,
    (6, "十三元<半>"): 42,
    (6, "十三问"): 20,
    (6, "十三阮<半>"): 21,
    (6, "十二吻"): 15,
    (6, "十二文"): 36,
    (6, "十二震"): 46,
    (6, "十四愿<半>"): 17,
    (7, "一先"): 129,
    (7, "十七霰"): 80,
    (7, "十三元<半>"): 36,
    (7, "十三阮<半>"): 20,
    (7, "十五删"): 38,
    (7, "十五潸"): 21,
    (7, "十五翰"): 63,
    (7, "十六谏"): 26,
    (7, "十六铣"): 57,
    (7, "十四寒"): 78,
    (7, "十四愿<半>"): 21,
    (7, "十四旱"): 32,
    (8, "三肴"): 41,
    (8, "二十号"): 34,
    (8, "二萧"): 101,
    (8, "十七筱"): 44,
    (8, "十九效"): 21,
    (8, "十九皓"): 48,
    (8, "十八啸"): 45,
    (8, "十八巧"): 18,
    (8, "四豪"): 66,
    (9, "二十一个"): 31,
    (9, "二十哿"): 40,
    (9, "五歌"): 75,
    (10, "九佳<半>"): 7,
    (10, "二十一马"): 30,
    (10, "二十二禡"): 53,
    (10, "六麻"): 75,
    (10, "十卦<半>"): 3,
    (11, "九青"): 64,
    (11, "二十三梗"): 51,
    (11, "二十五径"): 39,
    (11, "二十四敬"): 38,
    (11, "二十四迥"): 18,
    (11, "八庚"): 120,
    (11, "十蒸"): 58,
    (12, "二十五有"): 78,
    (12, "二十六宥"): 79,
    (12, "十一尤"): 119,
    (13, "二十七沁"): 17,
    (13, "二十六寝"): 25,
    (13, "十二侵"): 50,
    (14, "三十陷"): 10,
    (14, "二十七感"): 22,
    (14, "二十九艳"): 20,
    (14, "二十九豏"): 11,
    (14, "二十八俭"): 31,
    (14, "二十八勘"): 14,
    (14, "十三覃"): 44,
    (14, "十五咸"): 18,
    (14, "十四盐"): 43,
    (15, "一屋"): 99,
    (15, "二沃"): 40,
    (16, "三觉"): 34,
    (16, "十药"): 105,
    (17, "十一陌"): 116,
    (17, "十三职"): 68,
    (17, "十二锡"): 53,
    (17, "十四缉"): 44,
    (17, "四质"): 69,
    (18, "七曷"): 39,
    (18, "九屑"): 86,
    (18, "五物"): 28,
    (18, "八黠"): 25,
    (18, "六月"): 51,
    (18, "十六叶"): 47,
    (19, "十七洽"): 31,
    (19, "十五合"): 33,
}
BASELINE_SOURCE = {
    "file": "六、工具书/词林正韵（戈载）.md",
    "method": "剥全/半角括号注 + 切声调段 + 按组切，chars_raw 口径（含组内重出）",
    "measured_at": "2026-11-20",
}



def _ok(results: list, name: str, passed: bool, detail) -> bool:
    results.append({"断言": name, "通过": bool(passed), "实测": detail})
    return bool(passed)


def check_pingshui(ps: dict, sus: dict) -> list:
    """A7 + A8。"""
    r = []
    chars = ps["chars"]
    all_chars = "".join(chars.keys())
    non_cjk = sorted({c for c in all_chars if not CJK_RE.match(c)})
    _ok(r, "A7① 平水韵字表非 CJK 字符命中数 = 0", len(non_cjk) == 0,
        {"命中": non_cjk, "扫描字数": len(all_chars),
         "方法": "整文件读入后逐字扫描（不用 head -c 观感判定）"})

    # A7②：四寘不得挂入 术/瑟/德（污染须剔除，不是「登记后保留」）
    sizhen = next((x for x in ps["rhymes"] if x["id"] == "去声四寘"), None)
    bad = [c for c in "术瑟德" if sizhen and c in sizhen["chars"]]
    _ok(r, "A7② 去声四寘字表不含 术/瑟/德", not bad,
        {"残留": bad, "四寘字数": len(sizhen["chars"]) if sizhen else None})

    # 同一批字必须已在 suspect_queue 留证，且不得在其它韵部重复挂四寘
    logged = set()
    for x in sus.get("items", []):
        if x.get("char"):
            logged.add(x["char"])
        for c in x.get("removed", []) or []:
            logged.add(c)
    need = {"术", "瑟", "德", "始", "近"}
    _ok(r, "A7③ 四寘污染 5 字已登记 suspect_queue.json", need <= logged,
        {"已登记": sorted(logged & need), "缺": sorted(need - logged)})
    for c in "术瑟德":
        rhymes = [t[1] for t in chars.get(c, [])]
        _ok(r, "A7④ %s 不在去声四寘（仅入声）" % c,
            "去声四寘" not in rhymes, {"实际韵部": rhymes})

    _ok(r, "A8 平水韵去重字数 = 4281", len(chars) == 4281, {"实测": len(chars)})
    _ok(r, "A8 韵目数 = 106", len(ps["rhymes"]) == 106, {"实测": len(ps["rhymes"])})
    struct = {}
    for x in ps["rhymes"]:
        struct[x["part"]] = struct.get(x["part"], 0) + 1
    _ok(r, "A8 结构 = 上平15/下平15/上29/去30/入17",
        struct == {"上平": 15, "下平": 15, "上": 29, "去": 30, "入": 17}, struct)
    return r


def check_rusheng(book) -> list:
    """A10：47 字逐字 + 入声 17 部反抽 200 字。"""
    r = []
    hits = []
    for ch in RUSHENG_47:
        cs = book.tone_classes(ch)
        hits.append((ch, "入" in cs, "仄" in {x.tone for x in book.readings(ch)}))
    bad = [c for c, is_ru, is_ze in hits if not (is_ru and is_ze)]
    _ok(r, "A10① 素材清单 47 字全部判仄且属入声", not bad,
        {"一致": "%d/47" % (47 - len(bad)), "异常字": bad})

    # 反抽：入声 17 部全字中固定种子抽 200 字，须全部 tone_class == 入
    ru_chars = []
    for x in book.ps["rhymes"]:
        if x["part"] == "入":
            ru_chars.extend(list(x["chars"]))
    rng = random.Random(20261120)
    sample = rng.sample(ru_chars, min(200, len(ru_chars)))
    bad2 = [c for c in sample if "入" not in book.tone_classes(c)]
    _ok(r, "A10② 入声 17 部反抽 200 字一致率 100%", not bad2,
        {"样本": len(sample), "异常": bad2[:20], "种子": 20261120,
         "入声总字数": len(ru_chars)})
    return r


def check_cilin(cl: dict) -> list:
    """A9 原三条 + §8.1 增补三条（截断断言）。"""
    r = []
    _ok(r, "A9① 词林正韵 19 部齐备", len(cl["parts"]) == 19, {"实测": len(cl["parts"])})
    tones = {}
    for p in cl["parts"]:
        for g in p["groups"]:
            tones[g["tone"]] = tones.get(g["tone"], 0) + 1
    _ok(r, "A9② 结构 = 平上去 14 部 + 入声 5 部",
        tones.get("平", 0) == 14 and tones.get("入", 0) == 5, tones)

    notes = [(x["name"], x["char_notes"]) for x in cl["xiaoyun"] if x.get("char_notes")]
    _ok(r, "A9③ 抽样 20 条带注字可查", len(notes) >= 20, {"带注组数": len(notes)})

    # §8.1 增补：组体必须先截断，header 词不得串入字表
    leaked = []
    for x in cl["xiaoyun"]:
        s = x["chars"]
        for w in HEADER_WORDS:
            if w in s:
                leaked.append({"组": x["name"], "串入": w, "字串": s[:40]})
    _ok(r, "A9④ 无组体含 header 词组（平声/仄声/通用/独用/<半>）", not leaked,
        {"泄漏": leaked[:5], "泄漏组数": len(leaked)})

    # §9.11 / §9.11.1：逐组字数精确相等（容差 0），主键 (部, 韵目)。
    # 旧稿的「≤65/≤45 统一上界」作废：上界过宽等于没设（正确值 39/27 有 4 倍余量）。
    mism = []
    seen = set()
    for x in cl["xiaoyun"]:
        key = (x["part"], x["raw_name"])       # 半部韵在两部各一块，字表不同
        seen.add(key)
        exp = BASELINE_RAW.get(key)
        if exp is None or x["char_count"] != exp:
            mism.append({"部": key[0], "韵目": key[1], "实测": x["char_count"],
                         "基线": exp})
    missing_base = sorted(set(BASELINE_RAW) - seen)
    _ok(r, "A9⑤ 逐组 chars_raw == baseline_raw（容差 0，主键 部/韵目）",
        not mism and not missing_base,
        {"组数": len(cl["xiaoyun"]), "基线表条目": len(BASELINE_RAW),
         "不符": mism[:8], "基线中未被抽到": missing_base[:8],
         "基线来源": BASELINE_SOURCE})
    # 定点哨兵（裁决书 §9.11 明列）：一东 73 / 二冬 52 / 十灰 39,27 / 十四愿 17,21
    sentinel = {(1, "一东"): 73, (1, "二冬"): 52, (3, "十灰<半>"): 39,
                (5, "十灰<半>"): 27, (6, "十四愿<半>"): 17, (7, "十四愿<半>"): 21}
    actual = {(x["part"], x["raw_name"]): x["char_count"] for x in cl["xiaoyun"]}
    bad_sent = {k: (actual.get(k), v) for k, v in sentinel.items() if actual.get(k) != v}
    _ok(r, "A9⑥ 定点字数哨兵（一东73/二冬52/十灰39·27/十四愿17·21）",
        not bad_sent, {"不符": bad_sent, "实测": {str(k): actual.get(k) for k in sentinel}})

    # 关键假阴性专项：去/声/上/入 不得被登记为十灰（平声）韵字
    idx = cl["char_index"]
    deadly = []
    for ch in "去声上入":
        for rec in idx.get(ch, []):
            if rec["group"] in ("十灰", "九佳", "八齐") and rec["tone"] == "平":
                deadly.append({"字": ch, "组": rec["group"], "tone": rec["tone"]})
    _ok(r, "A9⑦ 「去/声/上/入」未被登记为灰/佳/齐平声韵字（假阴性专项）", not deadly,
        {"命中": deadly})
    return r


def check_tradmap(book) -> list:
    """繁简归一：韵表保持简体；t2s 键集与韵表键集必须为空交集。"""
    r = []
    inter = sorted(set(book.t2s) & set(book.ps_chars))
    _ok(r, "繁简① pingshui 键集 ∩ t2s 键集 = 空", not inter,
        {"交集": inter[:20], "交集数": len(inter), "t2s": len(book.t2s)})
    inter2 = sorted(set(book.alias) & set(book.ps_chars))
    _ok(r, "繁简② pingshui 键集 ∩ 异体 alias 键集 = 空", not inter2,
        {"交集": inter2[:20], "alias": len(book.alias)})
    # 功效：繁体例字必须能归一命中
    samples = {"無": "无", "雲": "云", "風": "风", "東": "东", "詩": "诗"}
    miss = [k for k, v in samples.items() if book.normalize(k)[0] != v]
    _ok(r, "繁简③ 繁体例字可归一命中", not miss, {"未命中": miss})
    return r


def check_linvun(lv: dict) -> list:
    """邻韵：半部字级硬判仅限 元/阮/愿；开关存在且默认严格。"""
    r = []
    fine = sorted(k for k, v in lv.get("half_chars", {}).items() if v["resolution"] == "fine")
    _ok(r, "邻韵① 半部字级可拆仅 元/阮/愿 三目",
        set(fine) <= {"上平十三元", "上声十三阮", "去声十四愿"}, {"fine": fine})
    cfg = lv.get("config", {})
    _ok(r, "邻韵② 默认严格模式且邻韵默认关闭",
        cfg.get("default_mode") == "strict" and cfg.get("neighbor_allowed_in_strict") is False,
        cfg)
    _ok(r, "邻韵③ 「整部宽松」开关存在（改配置不改代码）",
        "half_resolution_switch" in cfg or "half_resolution" in cfg,
        {"half_resolution": cfg.get("half_resolution"),
         "switch": cfg.get("half_resolution_switch")})
    return r


def run(book) -> dict:
    ps = book.ps
    sus = _paths.load_json("suspect_queue.json")
    cl = book.cl
    lv = book.lv
    rows = []
    rows += check_pingshui(ps, sus)
    rows += check_rusheng(book)
    rows += check_cilin(cl)
    rows += check_tradmap(book)
    rows += check_linvun(lv)
    passed = sum(1 for x in rows if x["通过"])
    return {"assertions": rows, "passed": passed, "total": len(rows),
            "ok": passed == len(rows)}


def main() -> None:
    from tonebook import ToneBook
    out = run(ToneBook())
    print(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True))
    raise SystemExit(0 if out["ok"] else 1)


if __name__ == "__main__":
    main()
