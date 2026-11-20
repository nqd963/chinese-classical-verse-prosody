# -*- coding: utf-8 -*-
"""从钦定词谱例词反推平仄谱（推定谱）。零第三方依赖。

本版新增（保持 team-lead 的 confidence 紧凑格式 {level, tones}，不改字段表示）：
  - 缺口1：跨行重组——表头在行末、例词在后续行行首；严格判据 = 字数对齐 +
    句数对齐 + 后随表头匹配骨架下一条（归属验证），拼不出留空。
  - 缺口2：表头真实句数解析——修复骨架 lines「前后段各N句→N」抽取错误；
    例词字数对齐 + 标记数==表头真值才采用。
  - 缺口3：多源交叉区分可平可仄位——多首同调例词声调分歧 / 王谱 pat 的 * 位，
    降级为 cross_validated_free，仍标 inferred，不升级一等谱。

原则：抽不出一律不写入（不硬凑）；单读→P/Z，多读/缺字→*；每条回指源文件行号。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
import _paths
from tonebook import ToneBook

SRC_REL = "../../../../../三、填词专修/钦定词谱（王奕清等奉敕编）.md"
SOURCE_FILE = "三、填词专修/钦定词谱（王奕清等奉敕编）.md"

MARK = set("韵句读叠叶卟")
END_MARK = set("韵句叠叶卟")
RHYME_MARK = set("韵叶卟")
NEXT_BODY = re.compile(r"【(?:单调|双调|三段|四段)[^】]*?字[^】]*?句[^】]*?韵")
CN = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}


def _han(ch): return "\u4e00" <= ch <= "\u9fff"


def _mark_in(content):
    for c in content[:3]:
        if c in MARK: return c
    return None


def _cn2num(s):
    if s.isdigit(): return int(s)
    if s in CN: return CN[s]
    if s.startswith("十"): return 10 + CN.get(s[1:], 0)
    if "十" in s:
        a, b = s.split("十", 1)
        return CN.get(a, 1) * 10 + (CN.get(b, 0) if b else 0)
    return None


def true_lines(raw_head):
    """从表头【】解析真实句数（修复骨架「前后段各N句→N」抽取错误）。"""
    m = re.search(r"【([^】]*)】", raw_head or "")
    if not m: return None
    c = re.sub(r"\s+", "", m.group(1))
    nums = [(x.start(), _cn2num(x.group(1))) for x in re.finditer(
        r"([一二三四五六七八九十百\d]+)[^一二三四五六七八九十百\d]{0,3}?句", c)]
    nums = [(p, n) for p, n in nums if n]
    if not nums: return None
    if len(nums) == 1 and re.search(r"[各][一二三四五六七八九十百\d]+句", c):
        n = nums[0][1]
        seg = 2 if ("前后" in c or "前后叚" in c) else (
              3 if "三段" in c else (4 if "四段" in c else 1))
        return n * seg
    if len(nums) >= 2: return sum(n for p, n in nums)
    if "单调" in c: return nums[0][1]
    return None


def parse_frag(frag):
    chars, marks, buf = [], [], []
    i = 0
    for mm in re.finditer(r"【([^】]*)】", frag):
        buf.extend(c for c in frag[i:mm.start()] if _han(c))
        mk = _mark_in(mm.group(1))
        if mk:
            chars.extend(buf); marks.append((mk, len(chars) - 1)); buf = []
        i = mm.end()
    return "".join(chars), marks


def segment(raw_head, line, window=320):
    pos = line.find(raw_head)
    if pos < 0: return None
    start = pos + len(raw_head)
    nm = NEXT_BODY.search(line, start)
    end = nm.start() if nm else min(len(line), start + window)
    body, marks = parse_frag(line[start:end])
    return {"body": body, "marks": marks} if marks else None


def align(ex, marks, e, want_lines=None):
    """按骨架字数 + 句数（可用表头真值）对齐。返回 (body, marks, author) 或 None。"""
    diff = len(ex) - e["chars"]
    if diff < 0 or diff > 4: return None
    body, marks = ex[diff:], [(mk, p - diff) for mk, p in marks]
    if any(p < 0 or p >= len(body) for mk, p in marks): return None
    end = sum(1 for mk, p in marks if mk in END_MARK)
    want = want_lines if want_lines is not None else e.get("lines")
    if want and end != want: return None
    if len(body) != e["chars"]: return None
    return body, marks, ex[:diff]


def _core(head):
    m = re.search(r"【([^】]*)】", head or "")
    if m: return m.group(1)
    return (head or "").lstrip("【") or None


def _core_match(hn, en):
    a, b = _core(hn), _core(en.get("raw_head", ""))
    return bool(a and b and (a == b or a in b or b in a))


def rejoin(e, lines, ents, idx, want_lines=None, verify_next=True, maxd=10):
    """跨行重组：表头在行末→向后找行首例词；严格判据含后随表头归属验证。"""
    hl = e["head_line"]
    en = ents[idx[e["id"]] + 1] if idx[e["id"]] + 1 < len(ents) else None
    for d in range(1, maxd + 1):
        j = hl - 1 + d
        if j >= len(lines): break
        l = lines[j]
        if not l.strip() or l.startswith("###"): continue
        m = NEXT_BODY.search(l)
        if not m: continue
        if verify_next and en and not _core_match(m.group(0), en): continue
        body, marks = parse_frag(l[:m.start()])
        r = align(body, marks, e, want_lines)
        if r: return (j + 1, _core(m.group(0)), r)
    return None


def cross_free_positions(name, wang_by_name, tb, chars):
    """交叉验证可平可仄位：返回 (多首例词分歧位, 王谱pat为*的位)。"""
    multi_free, star_free = set(), set()
    same_len = []
    for ex, pat in wang_by_name.get(name, []):
        if len(ex) == chars and len(pat) == chars:
            same_len.append((ex, pat))
            for i, pc in enumerate(pat):
                if pc == "*": star_free.add(i)
    if len(same_len) >= 2:
        for i in range(chars):
            tones = set()
            for ex, pat in same_len:
                tones |= tb.tones(ex[i])
            if len(tones) >= 2: multi_free.add(i)
    return multi_free, star_free


def build():
    src = os.path.abspath(os.path.join(_HERE, SRC_REL))
    with open(src, encoding="utf-8") as fh: lines = fh.read().split("\n")
    with open(src, "rb") as fh: sha = hashlib.sha256(fh.read()).hexdigest()

    sk = _paths.load_json("cipu_qinding_skeleton.json")
    tb = ToneBook()
    wang = _paths.load_json("cipu_wang.json")
    wang_by_name = defaultdict(list)
    for e in wang["entries"]:
        ex = "".join(l.get("example_chars", "") for l in e.get("lines", []))
        pat = "".join(l.get("pat", "") for l in e.get("lines", []))
        if ex and len(ex) == len(pat): wang_by_name[e["name"]].append((ex, pat))

    ents = sk["entries"]
    idx = {e["id"]: i for i, e in enumerate(ents)}
    stats = {"ok_direct": 0, "rejoin": 0, "true_lines": 0, "built": 0,
             "left_no_seg": 0, "left_misalign": 0,
             "conf_single": 0, "conf_multi": 0, "conf_unres": 0,
             "cross_multi_positions": 0, "cross_star_positions": 0,
             "cross_downgraded": 0, "with_rhyme_tone": 0,
             "skeleton_lines_wrong": 0, "rhyme_positions_dropped": 0}

    entries = []
    for e in ents:
        hl, rh, chars = e.get("head_line"), e.get("raw_head", ""), e.get("chars")
        if not hl or hl > len(lines) or not rh or not chars:
            stats["left_no_seg"] += 1; continue

        seg = segment(rh, lines[hl - 1])
        r, basis, evidence = None, None, {}
        if seg:
            r = align(seg["body"], seg["marks"], e)
            if r:
                basis = "direct"; stats["ok_direct"] += 1
                evidence = {"source_line": hl, "align": "同行逐字对齐"}
        if r is None:
            rr = rejoin(e, lines, ents, idx, verify_next=True)
            if rr:
                r, basis = rr[2], "rejoin"; stats["rejoin"] += 1
                evidence = {"source_line": rr[0], "align": "跨行重组",
                            "next_head_core": rr[1],
                            "next_head_matches_skeleton_next": True}
        if r is None:
            tl = true_lines(rh)
            if tl and tl != e.get("lines") and seg:
                r2 = align(seg["body"], seg["marks"], e, tl)
                if r2:
                    r, basis = r2, "true_lines"
                    stats["true_lines"] += 1; stats["skeleton_lines_wrong"] += 1
                    evidence = {"source_line": hl, "align": "表头真实句数复核",
                                "skeleton_lines": e.get("lines"), "true_lines": tl}
        if r is None:
            stats["left_no_seg" if seg is None else "left_misalign"] += 1
            continue

        ex, marks, author = r
        mf, sf = cross_free_positions(e["name"], wang_by_name, tb, chars)
        stats["cross_multi_positions"] += len(mf)
        stats["cross_star_positions"] += len(sf)
        # cross_validated_free 仅由「两首以上同调例词在该位声调不同」产生（mf）。
        # 王谱 pat 的 * 位只作参考记录 wang_star_positions，不降级、不改 level，
        # 因为它是单个谱符号而非「两首例词分歧」，不满足该 level 的语义约束。
        free = mf

        pat, conf = [], []
        for i, ch in enumerate(ex):
            tones = tb.tones(ch)
            if len(tones) == 1:
                t = next(iter(tones)); p = "P" if t == "平" else "Z"
                lvl = "single_reading"; stats["conf_single"] += 1
            elif len(tones) > 1:
                p = "*"; lvl = "multi_reading_any"; stats["conf_multi"] += 1
            else:
                p = "*"; lvl = "unresolvable"; stats["conf_unres"] += 1
            if i in free and p in "PZ":
                p = "*"; lvl = "cross_validated_free"; stats["cross_downgraded"] += 1
            tone_code = "".join("P" if t == "平" else "Z" for t in sorted(tones))
            conf.append({"level": lvl, "tones": tone_code})
            pat.append(p)

        rd = e.get("rhyme_desc") or ""
        if "平韵" in rd and "仄韵" not in rd: rhyme_tone = "平"
        elif "仄韵" in rd and "平韵" not in rd: rhyme_tone = "仄"
        else: rhyme_tone = None
        if rhyme_tone: stats["with_rhyme_tone"] += 1
        # 只取「韵」标记位硬判：「叶」位声调可不同于平/仄韵，不硬判。
        rhyme_positions = sorted(set(p for mk, p in marks if mk == "韵"))
        # 例词自身韵脚字声调与韵式不符的位不可靠（多为转录错），剔除以免误伤用户。
        if rhyme_tone:
            rhyme_positions = [p for p in rhyme_positions
                               if p < len(ex) and rhyme_tone in tb.tones(ex[p])]
        stats["rhyme_positions_dropped"] += sum(
            1 for mk, p in marks if mk in RHYME_MARK and p not in rhyme_positions)

        entries.append({
            "id": e["id"], "name": e["name"],
            "is_variant": e.get("is_variant", False),
            "body_type": e.get("body"), "chars": chars, "lines": e.get("lines"),
            "true_lines": true_lines(rh), "rhyme_desc": rd,
            "source_line": evidence["source_line"], "align_basis": basis,
            "align_evidence": evidence,
            "author": author, "example": ex,
            "mark_seq": [{"mark": mk, "pos": p} for mk, p in marks],
            "pat": "".join(pat), "confidence": conf,
            "rhyme_positions": rhyme_positions, "rhyme_tone": rhyme_tone,
            "cross_free_positions": sorted(free),
            "wang_star_positions": sorted(sf),   # 参考：王谱 pat 为 * 的位，不触发降级
            "disclosure": "由例词反推，非原书符号谱；仅作参考，不作权威判定",
        })
        stats["built"] += 1

    out = {
        "schema_version": 2,
        "source": {"file": SOURCE_FILE, "sha256": sha,
                   "note": "例词所在行号见每条 source_line（跨行重组者指向例词实际行）"},
        "evidence_level": "inferred_from_examples",
        "disclosure": ("由钦定词谱例词 + 随包平水韵表反推，非原书逐字平仄符号谱；"
                       "单读字位仅代表例词用字声调，原谱该位可能本为「可平可仄」，"
                       "故校验时非韵脚位只作软提示；仅作参考，不作权威判定。"),
        "method": {
            "segment": "【韵/句/读/叠/叶/卟】切分；剔除和声与按语；骨架字数剥离作者名",
            "rejoin": "表头在行末→向后找行首例词；字数+句数对齐，且后随表头匹配骨架下一条（归属验证），拼不出留空",
            "true_lines": "骨架 lines 抽错时以表头解析真实句数复核（每段各N句→段数×N）",
            "infer": "平水韵单读→P/Z；多读或缺字→*（任一读成立、不约束）",
            "cross": ("仅『两首以上同调例词在该位声调不同』才降级为 cross_validated_free；"
                      "王谱 pat 为 * 的位只记入 wang_star_positions 供参考，不降级、不改 level"),
            "confidence_encoding": ("紧凑格式：confidence[i]={level,tones}；"
                                    "char 可由 example 仅取 CJK 后按序推导，pat 可由 pat 串按位取。"),
            "tones_encoding": ("tones 为字符串：单读 P/Z，多读如 ZP（按 Unicode 序），"
                               "无读法为空串；中文声调名为 平=P、仄=Z。"),
        },
        "level_semantics": {
            "single_reading": "例词该字在平水韵表单读，反推为固定 P/Z",
            "multi_reading_any": "例词该字多读，任一读成立，标 * 不约束",
            "unresolvable": "字不在随包韵表，标 * 不约束",
            "cross_validated_free": ("仅当两首以上同调例词在该位声调不同时降级为 *；"
                                     "绝不由单首例词推导；仍属 inferred，不是一等谱"),
        },
        "stats": stats, "entries": entries,
    }
    assert_schema(entries)   # 任一失败不落盘
    out_path = os.environ.get("CIPU_INFERRED_OUT")
    path = Path(out_path) if out_path else (_paths.DATA_DIR / "cipu_qinding_inferred.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(json.dumps({"path": str(path), "stats": stats,
                      "entries": len(entries), "assertions": "passed"}, ensure_ascii=False, indent=1))


def assert_schema(entries):
    """写盘前逐条断言，任一失败不落盘（team-lead 三条等价条件）。"""
    levels = {"single_reading", "multi_reading_any", "unresolvable",
              "cross_validated_free"}
    errs = []
    for e in entries:
        cjk_n = len([c for c in e["example"] if _han(c)])
        n = len(e["confidence"])
        if not (n == e["chars"] == len(e["pat"]) == cjk_n):
            errs.append("%s 长度不一致 conf=%d chars=%d pat=%d cjk=%d" % (
                e["id"], n, e["chars"], len(e["pat"]), cjk_n))
        for i, c in enumerate(e["confidence"]):
            if c["level"] not in levels:
                errs.append("%s[%d] 非法 level=%s" % (e["id"], i, c["level"]))
            if any(x not in "PZ" for x in c["tones"]):
                errs.append("%s[%d] 非法 tones=%s" % (e["id"], i, c["tones"]))
    if errs:
        raise AssertionError("schema 断言失败（%d 条）：\n%s" % (
            len(errs), "\n".join(errs[:20])))
    return True


if __name__ == "__main__":
    build()
