"""类书素材抽取 → data/peiwen_*.json、data/pianzi_categories.json

三份输出，全部只作创作素材与对仗参考证据，不参与平仄硬判。
- peiwen_duiyu.json     佩文韵府「對語」→ 工对先例库（A/B 对仗组）
- peiwen_ju.json        佩文韵府「摘句」→ 诗句范例库（单句 / 对句）
- pianzi_categories.json 骈字类编 → 门类词库（双字词 + 门类 + 行号）

硬规矩：
1. 每条带 source_line 与 raw，可回指核对；
2. 剥注四种括号全剥（）()［］[]；
3. 抽不出留空不猜；序文/按语/残缺标 uncertain；
4. 零第三方依赖，仅 Python 标准库；
5. 不写 scripts/，本文件是唯一抽取逻辑。

用法：python tools/extract_peiwen.py --src <语料根> [--out <目录>]
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (cjk_only, dump_json, is_cjk, out_dir, read_lines,
                    rel, sha256_of, skill_data_dir, src_root)

REL_PEIWEN = "六、工具书/佩文韵府（张玉书等奉敕编）.md"
REL_PIAN = "六、工具书/骈字类编（张廷玉等奉敕编）.md"

# 四种剥注括号（圆/方、全角/半角），连同注文一并剥除
ANNOT_RE = re.compile(r"（[^）]*）|\([^)]*\)|［[^］]*］|\[[^\]]*\]")
# 反切/次条头污染：头字(1) + 反切上字 + 反切下字 + 切 → 从「切」前回溯最多 3 个汉字整段切掉
FANQIE_RE = re.compile(r"[\u4e00-\u9fff]{3}切|[\u4e00-\u9fff]{2}切")
# 页脚/版权混入正文的标记
DIRTY_MARKERS = (
    "标题：", "标题:", "題：", "題:", "许可协议", "許可協議", "附加信息",
    "github", "daizhige", "目录 >", "目錄 >", "ID:", "底本:", "原标题",
    "歡迎提交", "欢迎提交", "数据来自", "數據來自", "友情维护", "漢籍リポジトリ",
    "原始日期", "Copyright", "CC BY-SA",
)
# 韵目标题：### 上平聲　　　一東韻一
YUN_HEAD_RE = re.compile(
    r"^### (上平聲|下平聲|上聲|去聲|入聲)[\u3000\s]*([一二三四五六七八九十百]+[^\s韻]+)韻"
)
KEYWORDS = ("韻藻", "對語", "摘句")
# 骈字类编门标题
MEN_HEAD_RE = re.compile(r"^###\s*([\u4e00-\u9fff]+门[一二三四五六七八九十百]+)")
BODY_START = "### 御定骈字类编卷一"
MEN_ALIAS = {"珍寳门": "珍宝门", "噐物门": "器物门"}


def strip_annot(s: str) -> str:
    """剥除四种括号及其注文，可叠剥多层。"""
    prev = None
    while prev != s:
        prev = s
        s = ANNOT_RE.sub("", s)
    return s


def cut_region(region: str) -> str:
    """把区间截到真正的对语/诗句结束处：反切头与页脚版权一律切掉。"""
    cuts = [len(region)]
    m = FANQIE_RE.search(region)
    if m:
        cuts.append(m.start())
    low = region.lower()
    for mark in DIRTY_MARKERS:
        j = low.find(mark.lower())
        if j != -1:
            cuts.append(j)
    return region[:min(cuts)]


def next_keyword_pos(line: str, pos: int) -> int:
    best = len(line)
    for kw in KEYWORDS:
        j = line.find(kw, pos)
        if j != -1 and j < best:
            best = j
    return best


def find_regions(lines: list[str], keyword: str):
    """逐行产出 (行号, 韵目, 声调, 区间原文)。"""
    yunmu, tone = None, None
    for i, line in enumerate(lines):
        hm = YUN_HEAD_RE.match(line)
        if hm:
            tone, yunmu = hm.group(1), hm.group(2)
            continue
        for m in re.finditer(keyword, line):
            s = m.end()
            e = next_keyword_pos(line, s)
            yield i + 1, yunmu, tone, line[s:e]


def simplify(text: str, t2s: dict, alias: dict) -> str | None:
    """繁→简：t2s 优先、alias 次之；查不到的字原样保留（不臆造映射）。
    「/」作结构分隔符保留；含其它非汉字残留时整条标 None，不给看似干净其实错的字形。"""
    out = []
    for ch in text:
        if ch == "/":
            out.append(ch)
            continue
        if not is_cjk(ch):
            return None
        out.append(t2s.get(ch) or alias.get(ch) or ch)
    return "".join(out)


def side_clean(side: str) -> str:
    return cjk_only(strip_annot(side))


def has_placeholder(s: str) -> bool:
    """佩文韵府以「丨」代本条韵字，带此符号者必是韻藻注文串入，非对语/诗句。"""
    return "丨" in s


def build_duiyu(lines, t2s, alias):
    """對語：A/B 对仗组。清组直接收；并组（缺全角空格）按首尾字切分后标 uncertain。"""
    merged: dict[tuple[str, str], dict] = {}
    stat = {"标记区间": 0, "丢弃_无对语": 0, "丢弃_格式": 0, "丢弃_占位符": 0,
            "清晰组": 0, "并组": 0, "并组切分成功": 0}
    for ln, yunmu, tone, raw_region in find_regions(lines, "對語"):
        stat["标记区间"] += 1
        region = cut_region(raw_region)
        groups = [g for g in re.split(r"[\u3000\s]+", region) if g]
        if not groups or "/" not in groups[0]:
            stat["丢弃_无对语"] += 1
            continue
        # 首字（韵字）：首个组的右端
        head = side_clean(groups[0].split("/")[1])[-1:] or None
        if not head or not is_cjk(head):
            stat["丢弃_格式"] += 1
            continue

        for group in groups:
            strip = strip_annot(group)
            nslash = strip.count("/")
            if nslash == 0:
                stat["丢弃_格式"] += 1
                continue
            uncertain = nslash > 1
            # A/B 直接正则提取：B 必以韵字收；reges finditer 不重叠，天然拆开缺全角空格的并组
            # （海右/朔東平北/安東 → 海右/朔東 + 平北/安東）
            pat = re.compile(r"([^/\u3000]{1,9})/([^/\u3000]{1,9}" + re.escape(head) + r")")
            found = list(pat.finditer(strip))
            if not found:
                stat["丢弃_格式"] += 1
                continue
            if uncertain:
                stat["并组"] += 1
            for fm in found:
                a, b = side_clean(fm.group(1)), side_clean(fm.group(2))
                if not (1 <= len(a) <= 9 and 1 <= len(b) <= 9) or a == b or not b:
                    stat["丢弃_格式"] += 1
                    continue
                if has_placeholder(a) or has_placeholder(b):
                    stat["丢弃_占位符"] += 1
                    continue
                flag = uncertain
                if uncertain:
                    stat["并组切分成功"] += 1
                else:
                    stat["清晰组"] += 1
                key = (a, b)
                raw_pair = group if not uncertain else group
                rec = merged.get(key)
                if rec is None:
                    rec = {
                        "左": a,
                        "右": b,
                        "韵目": yunmu,
                        "声调": tone,
                        "source_line": ln,
                        "source_lines": [ln],
                        "raw": raw_pair,
                        "simplified": {
                            "左": simplify(a, t2s, alias),
                            "右": simplify(b, t2s, alias),
                        },
                        "freq": 1,
                        "uncertain": flag,
                    }
                    merged[key] = rec
                else:
                    rec["freq"] += 1
                    if ln not in rec["source_lines"]:
                        rec["source_lines"].append(ln)
                    if flag and not rec["uncertain"]:
                        rec["uncertain"] = True

    entries = sorted(merged.values(), key=lambda r: (r["source_line"], r["左"], r["右"]))
    payload = {
        "schema_version": "1.0",
        "source": {
            "book": "佩文韵府（张玉书等奉敕编）",
            "file": REL_PEIWEN,
            "sha256": sha256_of(os.path.join(SRC_ROOT, REL_PEIWEN)),
            "copyright": "公版（清代官修）",
            "evidence_level": "classical_reference",
            "说明": "只作创作素材与工对先例证据，不参与平仄硬判。每条带 source_line/raw 可回指。"
                    "剥注四种括号（）()［］[]；头字居中的并组按韵字切分后标 uncertain。"
                    "simplified 用包内 trad_simp.json 的 t2s/alias 归一，查不到的字原样保留（不臆造映射）。",
        },
        "统计": dict(stat, 去重后条数=len(entries),
                   不确定条数=sum(1 for e in entries if e["uncertain"]),
                   并入次数=sum(e["freq"] - 1 for e in entries)),
        "对语": entries,
    }
    return payload


def build_ju(lines, t2s, alias):
    """摘句：诗句范例。含 / 者为对句（A/B 皆以韵字收），否则单句；序文/无诗征者丢弃。"""
    entries = []
    stat = {"标记区间": 0, "丢弃_序文或无诗征": 0, "丢弃_格式": 0,
            "丢弃_占位符": 0, "单句": 0, "对句": 0}
    for ln, yunmu, tone, raw_region in find_regions(lines, "摘句"):
        stat["标记区间"] += 1
        region = cut_region(raw_region)
        if "/" not in region:
            stat["丢弃_序文或无诗征"] += 1
            continue
        # 头字：首个组 A 端（首个 / 之前）的末字
        first = strip_annot(region.split("/")[0])
        head = side_clean(first)[-1:] or None
        if not head or not is_cjk(head) or len(side_clean(first)) > 12:
            stat["丢弃_格式"] += 1
            continue

        # 逐「全角空格组」处理，组内按顺序抽出所有以韵字收的诗句，并记下该组原文
        for raw_group in re.split(r"\u3000", region):
            if not raw_group or "/" not in raw_group:
                continue
            clean_group = strip_annot(raw_group)
            phrases: list[str] = []
            for token in re.split(r"[/]", clean_group):
                token = side_clean(token)
                if not token:
                    continue
                if head not in token:
                    continue
                # 逐韵字切：仅在「已积 ≥5 字」处才认作上一句句末，避免把
                # 「未遂歸農又勸農」（一句内两见韵字）错切成 4+3 两句。
                rest = token
                while True:
                    cuts = [i for i, ch in enumerate(rest) if ch == head and i + 1 >= 5]
                    if not cuts:
                        if len(rest) >= 3:
                            phrases.append(rest)
                        break
                    k = cuts[0]
                    phrases.append(rest[: k + 1])
                    rest = rest[k + 1 :]

            # 相邻两两成对；奇数末尾一句作单句
            k = 0
            while k < len(phrases):
                if k + 1 < len(phrases):
                    a, b = phrases[k], phrases[k + 1]
                    kind = "对句"
                    k += 2
                else:
                    a, b = phrases[k], None
                    kind = "单句"
                    k += 1
                if not (3 <= len(a) <= 12) or (b is not None and not (3 <= len(b) <= 12)):
                    stat["丢弃_格式"] += 1
                    continue
                if has_placeholder(a) or (b is not None and has_placeholder(b)):
                    stat["丢弃_占位符"] += 1
                    continue
                pair_raw = a if b is None else a + "/" + b
                uncertain = not a.endswith(head) or (b is not None and not b.endswith(head))
                rec = {
                    "句": pair_raw,
                    "上句": a,
                    "下句": b,
                    "kind": kind,
                    "韵目": yunmu,
                    "声调": tone,
                    "source_line": ln,
                    "raw": raw_group[:180],
                    "simplified": simplify(pair_raw, t2s, alias),
                    "uncertain": uncertain,
                }
                entries.append(rec)
                stat[kind] += 1

    payload = {
        "schema_version": "1.0",
        "source": {
            "book": "佩文韵府（张玉书等奉敕编）",
            "file": REL_PEIWEN,
            "sha256": sha256_of(os.path.join(SRC_ROOT, REL_PEIWEN)),
            "copyright": "公版（清代官修）",
            "evidence_level": "classical_reference",
            "说明": "摘句为诗句范例，非格律判据。/ 前后皆以韵字收者记 kind=对句，否则单句；"
                    "序文「摘句附於其末」等无诗征者丢弃；残缺/形态不合者标 uncertain。"
                    "每条带 source_line/raw 可回指；剥注四种括号；simplified 同 peiwen_duiyu.json 规则。",
        },
        "统计": dict(stat, 总条数=len(entries),
                   不确定条数=sum(1 for e in entries if e["uncertain"])),
        "诗句": entries,
    }
    return payload


def build_pianzi(lines, t2s, alias):
    """骈字类编：门标题下的双字词，逐词带行号。"""
    pos = next((i for i, l in enumerate(lines) if l.startswith(BODY_START)), 0)
    body = lines[pos:]
    heads = [(i + pos, MEN_HEAD_RE.match(l).group(1))
             for i, l in enumerate(body) if MEN_HEAD_RE.match(l)]

    entries = []
    for hi, (head_line, men_full) in enumerate(heads):
        end_line = heads[hi + 1][0] if hi + 1 < len(heads) else len(lines)
        base = re.sub(r"[一二三四五六七八九十百]+$", "", men_full)
        base = MEN_ALIAS.get(base, base)
        for ln in range(head_line, end_line):
            line = lines[ln]
            for m in re.finditer(r"([\u4e00-\u9fff]{2})【", line):
                word = m.group(1)
                raw = line[m.start(): m.start() + 60]
                entries.append({
                    "词": word,
                    "门类": men_full,
                    "门类_基": base,
                    "source_line": ln + 1,
                    "raw": raw,
                    "simplified": simplify(word, t2s, alias),
                })

    # 与现有 duizhang_category.json 比对（现有按 t2s 归一后存词，门名已归一）
    compare = {}
    dc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           os.pardir, "data", "duizhang_category.json")
    if os.path.exists(dc_path):
        with open(dc_path, "r", encoding="utf-8") as fh:
            dc = json.load(fh)
        old_words = set()
        for v in dc.get("双字义类_紧凑", {}).values():
            old_words.update(v[i:i + 2] for i in range(0, len(v), 2))
        old_words.update(dc.get("双字多门歧义", {}).keys())
        old_men = set(dc.get("双字义类_紧凑", {}).keys())
        old_stat = dc.get("统计", {})
        new_words = {"".join(t2s.get(c, c) for c in e["词"]) for e in entries}
        new_men = {e["门类_基"] for e in entries}
        compare = {
            "现有_门标题数": old_stat.get("正文门标题数"),
            "现有_门基类数_紧凑": len(old_men),
            "本次_门标题数": len(heads),
            "本次_门基类数": len(new_men),
            "新增门基类": sorted(new_men - old_men),
            "现有_双字数": len(old_words),
            "本次_双字数_条目": len(entries),
            "本次_双字数_去重": len(new_words),
            "新增双字数_归一后": len(new_words - old_words),
            "重合双字数_归一后": len(new_words & old_words),
        }

    payload = {
        "schema_version": "1.0",
        "source": {
            "book": "御定骈字类编（张廷玉等奉敕编）",
            "file": REL_PIAN,
            "sha256": sha256_of(os.path.join(SRC_ROOT, REL_PIAN)),
            "copyright": "公版（清代官修）",
            "evidence_level": "classical_reference",
            "说明": "门标题按正文「### 天地门一」计（236 个）；此前报告所称 427 = 236 门标题 + 191 卷标题，"
                    "卷标题与门标题同指一门，不重复计。词条仅取「双字【」式，逐条带行号与 raw 可回指。",
        },
        "统计": {
            "门标题数": len(heads),
            "双字条目数": len(entries),
            "去重词数": len({e["词"] for e in entries}),
        },
        "与现有duizhang_category比对": compare,
        "词条": entries,
    }
    return payload


SRC_ROOT = ""


def main() -> None:
    global SRC_ROOT
    root = src_root(sys.argv)
    SRC_ROOT = root
    out = out_dir(sys.argv, skill_data_dir())
    ts_path = os.path.join(out, "trad_simp.json")
    with open(ts_path, "r", encoding="utf-8") as fh:
        ts = json.load(fh)
    t2s, alias = ts["t2s"], ts["alias"]

    pei_lines = read_lines(os.path.join(root, REL_PEIWEN))
    pian_lines = read_lines(os.path.join(root, REL_PIAN))

    dy = build_duiyu(pei_lines, t2s, alias)
    ju = build_ju(pei_lines, t2s, alias)
    pz = build_pianzi(pian_lines, t2s, alias)

    dump_json(dy, os.path.join(out, "peiwen_duiyu.json"))
    dump_json(ju, os.path.join(out, "peiwen_ju.json"))
    dump_json(pz, os.path.join(out, "pianzi_categories.json"))
    print("对语: %s" % dy["统计"])
    print("摘句: %s" % ju["统计"])
    print("门类: %s" % pz["统计"])
    print("比对: %s" % pz["与现有duizhang_category比对"])


if __name__ == "__main__":
    main()
