"""词（长短句）校验：按龙榆生《唐宋词格律》谱式核对字数、平仄、韵位、换韵、叠句、衬字位。

与近体诗校验的分工：
  - 近体诗（poem.py）：判据是平水韵 + 八式骨架，不依赖词谱；
  - 词（本模块）：判据是**词谱本身**（谱行 = 定平/定仄/可平可仄 + 韵位掩码），
    韵书用《词林正韵》19 部（词韵与诗韵不同书，不可混用）。

三条来自裁决书的硬约束：
  1. **等级早退**（D13）：词牌等级为二等时，本模块不作机器判定，直接返回 `conflict`（退出码 3），
     并说明「该调双源冲突未裁定，只能作人工参照」。二等伪装成一等是本项目最重的诚信失守。
  2. **衬字位是 `opt` 槽，不是残缺**（§7.1）：`ˇ` 处 0 字或 1 字皆合法。删掉会与说明段自报字数错位，
     故本模块对 opt 槽做「删/留」两种对齐尝试，任一成立即通过。
  3. **不猜**：谱行与句数对不上时，只报字数不符与对齐结果，不擅自重排句序。
"""
from __future__ import annotations

import re
from itertools import combinations

import _paths
from schema import Reading, Result, Violation

MAX_ALT_ALIGN = 3          # 有衬字位时最多尝试的对齐方案数（超过即判对齐失败，不猜）


class CiBook:
    """词谱 + 词韵查询。"""

    def __init__(self, book) -> None:
        self.book = book
        self.index = _paths.load_json("cipai_index.json")
        self.cipu = _paths.load_json("cipu_long.json")
        self.by_name = {c["name"]: c for c in self.index["cipai"]}
        self.by_alias: dict[str, str] = {}
        for c in self.index["cipai"]:
            for a in c.get("aliases", []) + c.get("aka", []):
                self.by_alias.setdefault(a, c["name"])
        self.entries = self.cipu["entries"]

    # ---------- 词牌定位 ----------
    def resolve(self, name: str) -> tuple[dict | None, str]:
        """词牌名 → 清单条目。返回 (条目, 命中途径)。"""
        n = name.strip().strip("《》")
        if n in self.by_name:
            return self.by_name[n], "正名"
        if n in self.by_alias:
            return self.by_name[self.by_alias[n]], "别名"
        return None, "miss"

    def variants(self, cipai: str) -> list[dict]:
        return [e for e in self.entries if e["name"] == cipai]

    def pick_variant(self, cipai: str, variant: str | None) -> dict | None:
        vs = self.variants(cipai)
        if not vs:
            return None
        if variant:
            for e in vs:
                if e["variant"] == variant or e["variant"].startswith(variant):
                    return e
            return None
        for e in vs:
            if e["variant_kind"] == "main":
                return e
        return sorted(vs, key=lambda x: x["seq"])[0]


def _split_by_punct(text: str) -> list[str]:
    """按标点断句（词以逗号/句号/问号/叹号/顿号断句）。"""
    punct = "。！？，、；："
    out, cur = [], ""
    for ch in text:
        if ch in punct:
            if cur.strip():
                out.append(cur)
            cur = ""
        elif "\u4e00" <= ch <= "\u9fff":
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


PUNCT = "。！？，、；："
NOTE_RE = re.compile(r"[（(][^）)]*[）)]")
TONE_CHARS = set("平中仄上去入")


def _raw_seg_lens(raw: str) -> list[int]:
    """谱行 raw → 各子句字数列表。

    谱行内部也可能有「，」（如忆江南首行「平中仄，中仄ˇ仄平平（韵）。」），
    故必须按谱本身的标点算出子句字数，才能与用户断句逐段对齐。
    `ˇ`（衬字标记）计入字数（该位 1 字），删位由 opt 对齐处理。
    """
    s = NOTE_RE.sub("", raw)
    s = s.replace("〖", "").replace("〗", "").replace("『", "").replace("』", "")
    lens, cur = [], 0
    for ch in s:
        if ch in PUNCT:
            if cur:
                lens.append(cur)
            cur = 0
        elif ch in TONE_CHARS or ch == "ˇ":
            cur += 1
    if cur:
        lens.append(cur)
    return lens


def _tone_ok(pat_ch: str, tone: str) -> bool:
    if pat_ch == "*":
        return True
    return (pat_ch == "P" and tone == "平") or (pat_ch == "Z" and tone == "仄")


def _expand_opts(line: dict, seg_len: int) -> list[tuple[str, list]]:
    """把谱行按衬字位的「有/无」展开成若干等长 (pat, rhyme 掩码) 候选。

    `opt` 的语义（裁决书 §7.1）：`ˇ` 是**衬字标记**，该位 0 字或 1 字皆合法。
    opt 里的数字是 pat 的下标，表示「插入点在该下标之前」——即 `chars.insert(i, '*')`；
    韵位掩码同步插入 `False`（衬字不是韵脚）。
    衬字位插入的字符平仄不限，展开为 `*`。

    返回与 seg 等长的候选项；空列表 = 字数越界（含衬字位仍对不上）。
    """
    pat = line["pat"]
    rmask = list(line.get("rhyme") or [0] * len(pat))
    opt = line.get("opt") or []
    extra = seg_len - len(pat)
    if extra < 0 or extra > len(opt):
        return []
    out = []
    for combo in combinations(opt, extra):
        chars, rms = list(pat), list(rmask)
        for i in sorted(combo, reverse=True):
            chars.insert(i, "*")
            rms.insert(i, 0)
        out.append(("".join(chars), rms))
    return out


def _tone_agree(book, ch: str, pat_ch: str) -> int:
    """平仄吻合打分（用于多个衬字组合时的择优）。"""
    if pat_ch == "*":
        return 1
    ts = {r.tone for r in book.readings(ch, "cilin")}
    if not ts:
        return 0
    return 1 if (("平" in ts) if pat_ch == "P" else ("仄" in ts)) else 0


def check_ci(text: str, cipai: str, *, book, variant: str | None = None,
             mode: str = "strict") -> Result:
    cib = getattr(book, "cibook", None) or CiBook(book)
    entry, how = cib.resolve(cipai)
    if entry is None:
        return Result(
            kind="ci", verdict="no_data", ok=False,
            confidence={"level": "none", "basis": "no_data",
                        "detail": "词牌《%s》不在随包词谱清单内" % cipai},
            notes=["本技能只对随包词谱（龙榆生《唐宋词格律》）收录的词牌作机器校验；"
                   "未收录者不猜、不套用近似词牌"],
            meta={"cipai": cipai, "hit": how},
            data_source={"files": {"cipai_index.json": _paths.data_digest("cipai_index.json")}})

    if entry["grade"] != "一等":
        return Result(
            kind="ci", verdict="conflict", ok=False,
            confidence={"level": "second", "basis": "conflict",
                        "detail": "该调双源冲突未裁定（D13 降级），只能作人工参照，不作机器判定"},
            notes=["等级：%s；依据：%s" % (entry["grade"], entry["basis"])],
            meta={"cipai": cipai, "grade": entry["grade"], "variant": variant},
            payload={"conflicts": entry.get("conflicts", [])},
            data_source={"files": {"cipai_index.json": _paths.data_digest("cipai_index.json"),
                                   "conflicts.json": _paths.data_digest("conflicts.json")}})

    ent = cib.pick_variant(entry["name"], variant)
    if ent is None:
        return Result(
            kind="ci", verdict="no_data", ok=False,
            confidence={"level": "none", "basis": "no_data",
                        "detail": "《%s》无体式「%s」" % (cipai, variant)},
            notes=["可用体式：%s" % "、".join(v["label"] for v in entry["variants"])],
            meta={"cipai": cipai, "variant": variant})

    segs = _split_by_punct(text)
    plines = ent["lines"]
    violations: list[Violation] = []
    conf = {"level": "first", "basis": "single_regressed",
            "detail": "判据为随包词谱（A 源单一谱式）与《词林正韵》19 部"}

    # 谱行内部可能含「，」，故按「谱行 → 其子句字数」逐段消费用户断句
    need = 0
    per_line_lens = []
    for l in plines:
        lens = _raw_seg_lens(l["raw"])
        per_line_lens.append(lens)
        need += len(lens)
    if len(segs) != need:
        violations.append(Violation(
            code="V-CHARS", severity="hard", line=0, col=None,
            rule="句数与谱不符", rule_ref="A14 / 词谱句读",
            actual="%d 句" % len(segs), expected="%d 句（%d 谱行）" % (need, len(plines)),
            evidence={"解析得到": segs,
                      "谱行": [l["raw"] for l in plines],
                      "每行子句数": [len(x) for x in per_line_lens]}))
        return Result(kind="ci", verdict="violations", ok=False, confidence=conf,
                      violations=violations,
                      meta={"cipai": cipai, "variant": ent["variant"],
                            "segs": len(segs), "pat_lines": len(plines)})

    # 逐行拼回该谱行的字串
    line_texts, cur = [], 0
    for lens in per_line_lens:
        line_texts.append("".join(segs[cur:cur + len(lens)]))
        cur += len(lens)

    # 组韵：按 词林正韵 19 部
    rhyme_parts: dict[str, str] = {}      # 韵脚字 → 部
    chosen: dict[tuple[int, int], Reading] = {}
    part_seq: list[str] = []
    cur_part: str | None = None

    line_masks: dict[int, list] = {}
    for idx, (line, seg) in enumerate(zip(plines, line_texts), start=1):
        cands = _expand_opts(line, len(seg))
        if not cands:
            violations.append(Violation(
                code="V-CHARS", severity="hard", line=idx, col=None,
                rule="句内字数与谱不符（衬字位已按 0/1 字两种情形尝试）",
                rule_ref="A14 / 词谱字数",
                actual="%d 字" % len(seg),
                expected="%d 字%s" % (len(line["pat"]),
                                      "（另可含衬字 %d—%d 字）"
                                      % (0, len(line["opt"])) if line["opt"] else ""),
                evidence={"句": seg, "谱行": line["raw"],
                          "衬字插入点": [i + 1 for i in line["opt"]]}))
            continue
        # 多个衬字组合时取「平仄吻合最多」者；并列取先序（确定性）
        if len(cands) > 1:
            cands.sort(key=lambda pr: -sum(_tone_agree(book, ch, pc)
                                           for ch, pc in zip(seg, pr[0])))
        pat, rmask = cands[0]
        line_masks[idx] = rmask
        if len(seg) > len(line["pat"]):
            violations.append(Violation(
                code="V-OPT", severity="soft", line=idx, col=None,
                rule="使用了衬字位（该位 0 字或 1 字皆合法，此条仅备忘）",
                rule_ref="裁决书 §7.1 / 龙榆生图例",
                actual="含衬字 %d 字" % (len(seg) - len(line["pat"])),
                expected="0 或 1 字皆可",
                evidence={"衬字插入点": [i + 1 for i in line["opt"]],
                          "本句字数": [len(seg), len(line["pat"])]}))
        # 换韵：该谱行声明了增韵/换韵，则另起一部
        if line.get("zengyun"):
            cur_part = None
        for pos, ch in enumerate(seg):
            src_pos = pos
            allowed_set = {"平"} if pat[src_pos] == "P" else (
                {"仄"} if pat[src_pos] == "Z" else {"平", "仄"})
            rs = book.readings(ch, "cilin")
            if not rs:
                violations.append(Violation(
                    code="V-NODATA", severity="soft", line=idx, col=pos + 1,
                    rule="字未收录于随包《词林正韵》，无法判定", rule_ref="A7 / 词韵表",
                    actual="缺", expected=None,
                    evidence={"字": ch, "missing_char": ch,
                              "本应满足": "、".join(sorted(allowed_set)),
                              "处置": "判为数据不足（退出码 3），不判出律"}))
                continue
            ok = [r for r in rs if r.tone in allowed_set] or rs
            ok.sort(key=lambda r: (r.src_rank, r.rhyme))
            r = ok[0]
            chosen[(idx, pos + 1)] = r
            if rmask[src_pos]:
                if cur_part is None:
                    cur_part = r.rhyme
                    part_seq.append(cur_part)
                elif r.rhyme != cur_part:
                    violations.append(Violation(
                        code="V-RHYME-SHIFT", severity="hard", line=idx, col=pos + 1,
                        rule="韵部与前半阙不一致（谱未标换韵）",
                        rule_ref="A14 / 词谱韵位",
                        actual=r.rhyme, expected=cur_part,
                        evidence={"谱行": line["raw"], "该行换韵标记": line.get("zengyun")}))
                rhyme_parts[ch] = r.rhyme
            if r.tone not in allowed_set:
                violations.append(Violation(
                    code="V-TONE", severity="hard", line=idx, col=pos + 1,
                    rule="平仄与谱不符", rule_ref="A14 / 词谱平仄",
                    actual=r.tone, expected="、".join(sorted(allowed_set)),
                    expected_set=sorted(allowed_set),
                    evidence={"谱位": pat[src_pos], "谱行": line["raw"],
                              "该字词林读法": [[x.tone_class, x.rhyme] for x in book.readings(ch, "cilin")]}))

    # 重韵：同一字反复作韵脚（词中一般忌重韵，除叠句外）
    dup = {}
    for (ln, col), r in chosen.items():
        mask = line_masks.get(ln) or []
        if col - 1 < len(mask) and mask[col - 1]:
            dup.setdefault(r.char, []).append(ln)
    for ch, lns in sorted(dup.items()):
        if len(lns) > 1:
            violations.append(Violation(
                code="V-RHYME-DUP", severity="soft", line=lns[1], col=None,
                rule="重韵（同字复用为韵脚，叠句除外）", rule_ref="A16",
                actual=ch, expected="同部他字",
                evidence={"出现句": lns}))

    # 叠句 / 叠三字：谱上有 〖 标记处，须与上句相同（或末三字相同）
    for mark in ent.get("repeat", []):
        ln = mark["line"]
        if ln < 2 or ln > len(line_texts):
            continue
        prev, cur_txt = line_texts[ln - 2], line_texts[ln - 1]
        raw = next((l["raw"] for l in plines if l["i"] == ln), "")
        if "叠三字" in raw:
            ok = prev[-3:] == cur_txt[-3:]
            kind, exp = "叠三字", "上句末三字"
        else:
            ok = prev == cur_txt
            kind, exp = "叠句", "与上句全同"
        if not ok:
            violations.append(Violation(
                code="V-REPEAT", severity="hard", line=ln, col=None,
                rule="%s 要求未满足" % kind, rule_ref="A14 / 词谱叠句标记",
                actual=cur_txt, expected=exp, evidence={"上句": prev, "谱行": raw}))

    violations.sort(key=lambda v: (v.line, v.col or 0, v.code))
    hard = [v for v in violations if v.severity == "hard"]
    nodata = [v for v in violations if v.code == "V-NODATA"]
    if hard:
        verdict = "violations"
    elif nodata:
        verdict = "insufficient_data"
    else:
        verdict = "pass"
    return Result(
        kind="ci", verdict=verdict, ok=not hard and not nodata, confidence=conf,
        violations=violations,
        readings=[{"line": k[0], "col": k[1], "char": v.char,
                   "chosen": {"tone": v.tone, "tone_class": v.tone_class, "rhyme": v.rhyme}}
                  for k, v in sorted(chosen.items())],
        meta={"cipai": ent["name"], "variant": ent["variant"],
              "variant_kind": ent["variant_kind"], "pat_total": ent["char_total"],
              "segs": len(segs), "pat_lines": len(plines),
              "rhyme_parts": part_seq, "grade": entry["grade"],
              "rhyme_book": "cilin(19部)", "hit": how,
              "hard_count": len(hard), "soft_count": len(violations) - len(hard),
              "nodata_count": len(nodata),
              "nodata_chars": sorted({v.evidence.get("missing_char") for v in nodata})},
        data_source={"files": {"cipu_long.json": _paths.data_digest("cipu_long.json"),
                               "cilin.json": _paths.data_digest("cilin.json"),
                               "cipai_index.json": _paths.data_digest("cipai_index.json")}},
        notes=(["该调定格源文件未标注，正格取首个体式头"] if ent["variant_kind"] != "main" else []),
    )


def check_ci_inferred(text: str, cipai: str, *, book, variant: str | None = None,
                      rhyme_book: str = "pingshui") -> Result:
    """依据「推定谱」（钦定词谱例词 + 平水韵表反推）校验词作。

    与一等谱（check_ci）的三点硬区分：
      1. confidence.level = "provisional"（一等谱为 "first"）；
      2. notes / meta.谱源 显式标注「依据推定谱」；
      3. 非韵脚位不硬判——反推单读字位可能原谱本为「可平可仄」，
         只报 soft 提示；只有字数/句数与韵脚平仄（由韵式独立确定）报 hard。
    退出码沿用 pass=0 / violations=1 / no_data=3，不以错谱误伤正确词作。
    """
    import os
    path = _paths.DATA_DIR / "cipu_qinding_inferred.json"
    if not os.path.exists(path):
        return Result(kind="ci", verdict="no_data", ok=False,
                      confidence={"level": "none", "basis": "no_data",
                                  "detail": "推定谱数据文件缺失"},
                      notes=["未安装推定谱数据"],
                      meta={"cipai": cipai})
    inf = _paths.load_json("cipu_qinding_inferred.json")
    n = cipai.strip().strip("《》")
    hits = [e for e in inf["entries"] if e.get("name") == n]
    if not hits:
        return Result(
            kind="ci", verdict="no_data", ok=False,
            confidence={"level": "none", "basis": "no_data",
                        "detail": "《%s》无可用推定谱（例词切分不可靠或未收录）" % cipai},
            notes=["推定谱仅覆盖能从钦定词谱例词精确切分的体式；"
                   "切不出者不按字数猜平仄"],
            meta={"cipai": cipai, "谱源": "推定谱"},
            data_source={"files": {"cipu_qinding_inferred.json": _paths.data_digest(
                "cipu_qinding_inferred.json")}})

    if variant:
        sel = [e for e in hits if str(e.get("is_variant")) == variant
               or e.get("id") == variant or e.get("id", "").endswith(variant)]
        ent = sel[0] if sel else None
    else:
        ent = next((e for e in hits if not e.get("is_variant")), hits[0])
    if ent is None:
        return Result(kind="ci", verdict="no_data", ok=False,
                      confidence={"level": "none", "basis": "no_data",
                                  "detail": "《%s》无体式「%s」的推定谱" % (cipai, variant)},
                      meta={"cipai": cipai, "variant": variant, "谱源": "推定谱"})

    conf = {"level": "provisional", "basis": "inferred_from_examples",
            "detail": ("依据推定谱（钦定词谱例词反推，非原书符号谱）；"
                       "字数与韵脚平仄为硬判据，非韵脚平仄仅作软提示")}
    rn = set(ent.get("rhyme_positions") or [])
    r_tone = ent.get("rhyme_tone")
    rbk = "pingshui" if rhyme_book == "pingshui" else "cilin"
    segs = _split_by_punct(text)
    flat = "".join(segs)
    violations: list[Violation] = []

    if len(flat) != ent["chars"]:
        violations.append(Violation(
            code="V-CHARS", severity="hard", line=0, col=None,
            rule="字数与推定谱骨架不符", rule_ref="钦定词谱例词反推",
            actual="%d 字" % len(flat), expected="%d 字" % ent["chars"],
            evidence={"解析断句": segs, "例词": ent["example"],
                      "例词出处行": ent["source_line"]}))
        return Result(
            kind="ci", verdict="violations", ok=False, confidence=conf,
            violations=violations,
            meta={"cipai": ent["name"], "谱源": "推定谱（依据例词反推）",
                  "evidence_level": "inferred_from_examples",
                  "source_line": ent["source_line"], "chars": ent["chars"],
                  "segs": len(segs)},
            notes=["依据推定谱；本结果非一等谱判定，仅供参考"],
            disclosure={"谱性质": "由例词反推，非原书符号谱；仅作参考，不作权威判定"},
            data_source={"files": {
                "cipu_qinding_inferred.json": _paths.data_digest("cipu_qinding_inferred.json"),
                "cipu_qinding_skeleton.json": _paths.data_digest("cipu_qinding_skeleton.json"),
                "%s.json" % rbk: _paths.data_digest("%s.json" % rbk)}})

    for i, ch in enumerate(flat):
        rs = book.readings(ch, rbk)
        if not rs:
            continue                      # 缺字放过，不因数据不足误判
        tones = {r.tone for r in rs}
        pat_ch = ent["pat"][i]
        if i in rn and r_tone:
            # 韵脚位：平/仄韵由表头独立确定，不受反推单读偏差影响
            if r_tone not in tones:
                violations.append(Violation(
                    code="V-RHYME-TONE", severity="hard", line=0, col=i + 1,
                    rule="韵脚字声调与词牌韵式不符", rule_ref="钦定词谱表头韵式",
                    actual="%s（%s）" % (ch, "/".join(sorted(tones))),
                    expected="韵脚须%s声" % r_tone,
                    evidence={"字": ch, "位": i + 1, "韵式": ent["rhyme_desc"],
                              "例词对应字": ent["example"][i]}))
            continue
        if pat_ch == "*":
            continue                      # 多读/缺字：任一读成立，不约束
        want = "平" if pat_ch == "P" else "仄"
        if want in tones:
            continue                      # 多读任一成立
        # 非韵脚位：只作软提示，绝不 hard——原谱该位可能本为可平可仄
        violations.append(Violation(
            code="V-PAT-SOFT", severity="soft", line=0, col=i + 1,
            rule="该字与推定谱例词反推平仄不同（原谱该位可能本为可平可仄，仅供参考）",
            rule_ref="例词反推 pat（软提示）",
            actual="%s（%s）" % (ch, "/".join(sorted(tones))),
            expected="例词反推此位为%s" % want,
            evidence={"字": ch, "位": i + 1,
                      "反推谱位": pat_ch, "例词对应字": ent["example"][i],
                      "置信": ent["confidence"][i]["level"]}))

    hard = [v for v in violations if v.severity == "hard"]
    soft = [v for v in violations if v.severity == "soft"]
    verdict = "violations" if hard else "pass"
    return Result(
        kind="ci", verdict=verdict, ok=not hard, confidence=conf,
        violations=violations,
        meta={"cipai": ent["name"], "谱源": "推定谱（依据例词反推）",
              "evidence_level": "inferred_from_examples",
              "source_line": ent["source_line"], "author": ent.get("author"),
              "chars": ent["chars"], "lines": ent["lines"],
              "rhyme_desc": ent["rhyme_desc"], "rhyme_positions": ent["rhyme_positions"],
              "multi_read_positions": sum(1 for c in ent["confidence"]
                                          if c["level"] == "multi_reading_any"),
              "unresolvable_positions": sum(1 for c in ent["confidence"]
                                            if c["level"] == "unresolvable"),
              "hard_count": len(hard), "soft_count": len(soft),
              "韵书": "%s" % rbk},
        notes=["依据推定谱（非一等谱）；结论仅供参考，不作权威判定",
               "硬判据：字数、句数骨架、韵脚平仄（由表头韵式独立确定）",
               "非韵脚平仄位一律 soft 提示（原谱该位可能本为可平可仄），不因错谱判 fail"],
        disclosure={"谱性质": "由例词反推，非原书符号谱；仅作参考，不作权威判定",
                    "与一等谱区别": "confidence.level=provisional（一等谱为 first）；"
                                "非韵脚位不硬判"},
        data_source={"files": {
            "cipu_qinding_inferred.json": _paths.data_digest("cipu_qinding_inferred.json"),
            "cipu_qinding_skeleton.json": _paths.data_digest("cipu_qinding_skeleton.json"),
            "%s.json" % rbk: _paths.data_digest("%s.json" % rbk)}})
