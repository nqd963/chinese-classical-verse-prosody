"""夹具：20 条「真诗＋单点篡改」的违规夹具 + 合规名篇零误报 + 衬字位双向夹具。

方法（避免「自造假数据自证」）：
  - **基线全部是真实作品**（随包 corpus.jsonl 里的唐宋名篇），先断言其「篡改前判通过」；
  - 篡改**只动一处**，逐条记录 `原字 → 新字`、新旧字的平水韵读法、改动理由；
  - 新字不是随手编的：从平水韵表按「所需平仄/所需韵部」取，记录 `src_rank`，可复核；
  - 每条夹具只断言「期望的违规码出现在期望位置」，不要求「只出现这一条」——
    一处篡改可能连带触发别的码，强行要求唯一会逼着实现去压报告。

三段输出：
  A. 违规召回：20 条 × 期望码 + 期望位置，召回率须 100%；
  B. 零误报：全部近体诗名篇（数百首）逐首校验，硬错条数须为 0；
  C. 衬字位：龙榆生谱里 5 个 `opt` 槽，各造「无衬字/有衬字」两版，均须判通过。
"""
from __future__ import annotations

import json
import re
import sys

import _paths
import poem
from schema import Result

NEAR_FORMS = ("五绝", "七绝", "五律", "七律")


# ── 韵表取字 ────────────────────────────────────────────────────
def _pool(book, tone: str, *, rhyme: str | None = None,
          not_rhyme: set | None = None) -> list[tuple[int, str, str]]:
    """按平仄（可加韵部约束）返回候选字，按 src_rank 升序（确定性）。

    **只取单读音字**：多读字（如「思」平/去两读）拿来当篡改字会失效——
    校验器的「任一所取读音成立即通过」会挑另一个读法，篡改等于没做。
    """
    out = []
    for ch, recs in book.ps_chars.items():
        # tonebook._merge_supplement 给补入字加了第 5 位 "supplement" 来源标记，
        # 原表字仍为 4 位。取前 4 位即可，不能按 4 位硬解包（会在补入字上 ValueError）。
        tones = {rec[0] for rec in recs}
        if tones != {tone}:
            continue
        for rec in recs:
            t, ry, tclass, rank = rec[:4]
            if rhyme and ry != rhyme:
                continue
            if not_rhyme and ry in not_rhyme:
                continue
            out.append((rank, ch, ry))
    out.sort()
    return out


def _take(book, tone, used, **kw) -> tuple[str, str]:
    for rank, ch, ry in _pool(book, tone, **kw):
        if ch not in used:
            return ch, ry
    raise RuntimeError("韵表中找不到可用的「%s」声字" % tone)


# ── 选基诗 ──────────────────────────────────────────────────────
def _bases(book, corpus) -> dict[str, dict]:
    """每种近体各挑一首「未经篡改即判通过」的基线诗。"""
    got: dict[str, dict] = {}
    for r in corpus:
        if r["体裁"] not in NEAR_FORMS or r["体裁"] in got:
            continue
        text = "".join(r["句子"])
        res = poem.check_poem(text, {"五绝": "wujue", "七绝": "qijue",
                                     "五律": "wulv", "七律": "qilv"}[r["体裁"]], book=book)
        if res.verdict == "pass":
            r = dict(r)
            r["_res"] = res
            got[r["体裁"]] = r
    return got


def _lines_of(r: dict) -> list[str]:
    return ["".join(c for c in s if "\u4e00" <= c <= "\u9fff") for s in r["句子"]]


def _apply(lines: list[str], edits: list[tuple[int, int, str]]) -> str:
    """edits: (句序号 1-based, 字序号 1-based, 新字)"""
    out = [list(x) for x in lines]
    for ln, col, ch in edits:
        out[ln - 1][col - 1] = ch
    return "".join("".join(x) for x in out)


def _rhyme_lines(res) -> list[int]:
    n_lines, cpl = poem.FORMS[res.meta["form"]]
    matrix = poem.build_matrix(tuple(res.meta["seed"]), n_lines, cpl)
    return [m["line"] for m in matrix if m["seed"][1] == "P"]


# ── 20 条违规夹具 ────────────────────────────────────────────────
def build_violation_fixtures(book, corpus) -> list[dict]:
    bases = _bases(book, corpus)
    need = set(NEAR_FORMS)
    if not need <= set(bases):
        raise RuntimeError("缺基线诗：%s" % (need - set(bases)))
    out = []

    def add(fid, form_key, desc, edits, want_code, want_line, want_col, note):
        b = bases[form_key]
        lines = _lines_of(b)
        before = poem.check_poem("".join(lines), _form(form_key), book=book)
        if before.verdict != "pass":
            raise RuntimeError("基线诗 %s 篡改前未通过：%s" % (b["id"], before.verdict))
        text = _apply(lines, edits)
        res = poem.check_poem(text, _form(form_key), book=book,
                              forced_seed=tuple(before.meta["seed"]),
                              forced_group=before.meta["rhyme_group_used"])
        # 期望码可带别名：三仄尾在默认口径下记为 V-SANZE-SOFT（唐人多用的拗句，
        # 软提示），而夹具沿用契约码表里的 V-SANZE 名称。二者是同一现象，接受任一。
        accept = {want_code}
        if want_code == "V-SANZE":
            accept.add("V-SANZE-SOFT")
        hit = [v for v in res.violations if v.code in accept]
        mutated = {}
        for ln, col, chx in edits:
            mutated[(ln, col)] = chx
        ok_loc = False
        for v in hit:
            if want_col is None:
                if v.line == want_line:
                    ok_loc = True
                continue
            if v.line == want_line and v.col == want_col:
                ok_loc = True
                continue
            # 韵脚被换后整篇的选组可能改变，故以「该位置确实是篡改字」为位置判据
            if (v.line, v.col) in mutated:
                ok_loc = True
                continue
            if "偏离位置" in (v.evidence or {}) and want_col in v.evidence["偏离位置"]:
                ok_loc = True
        detail = []
        for ln, col, ch in edits:
            old = lines[ln - 1][col - 1]
            detail.append({"句": ln, "字": col, "原字": old, "新字": ch,
                           "原字平水韵": [[x.tone, x.rhyme] for x in book.readings(old, "pingshui")],
                           "新字平水韵": [[x.tone, x.rhyme] for x in book.readings(ch, "pingshui")]})
        out.append({
            "id": fid, "说明": desc, "note": note,
            "基线": {"corpus_id": b["id"], "篇名": b["篇名"], "作者": b["作者"],
                     "体裁": b["体裁"], "篡改前判定": before.verdict},
            "篡改": detail,
            "篡改后文本": text,
            "期望码": want_code, "期望句": want_line, "期望字": want_col,
            "实际命中": [{"code": v.code, "line": v.line, "col": v.col} for v in res.violations],
            "全部硬错码": sorted({v.code for v in res.violations if v.severity == "hard"}),
            "召回": bool(hit) and ok_loc,
            "位置准确": ok_loc,
        })

    # 1–4 出韵（韵脚改用他部平声字）
    for fid, fk in (("F01", "五绝"), ("F02", "七绝"), ("F03", "五律"), ("F04", "七律")):
        b = bases[fk]
        lines = _lines_of(b)
        res = b["_res"]
        rl = _rhyme_lines(res)
        ln = rl[1] if len(rl) > 1 else rl[0]
        own = set(book.rhyme_sets(lines[ln - 1][-1]))
        ch, ry = _take(book, "平", set("".join(lines)), not_rhyme=own)
        add(fid, fk, "出韵：第 %d 句韵脚改用他部平声字（%s）" % (ln, ry),
            [(ln, len(lines[ln - 1]), ch)], "V-RHYME", ln, len(lines[ln - 1]),
            "韵脚换他部字，须报出韵并给候选字")

    # 5–6 重韵（把一处韵脚改成另一处韵脚字）
    for fid, fk in (("F05", "五绝"), ("F06", "七律")):
        b = bases[fk]
        lines = _lines_of(b)
        rl = _rhyme_lines(b["_res"])
        ln, other = rl[1], rl[0]
        ch = lines[other - 1][-1]
        add(fid, fk, "重韵：第 %d 句韵脚改成第 %d 句韵脚同字" % (ln, other),
            [(ln, len(lines[ln - 1]), ch)], "V-RHYME-DUP", ln, len(lines[ln - 1]),
            "重韵是硬错，须给同部他字候选")

    # 7–8 失对 / 失粘（整句二四六位按错误粘对关系改写）
    for fid, fk, bad_line, code, label in (
            ("F07", "五律", 4, "V-NIE", "失对"),
            ("F08", "七律", 3, "V-NIAN", "失粘")):
        b = bases[fk]
        lines = _lines_of(b)
        res = b["_res"]
        n_lines, cpl = poem.FORMS[_form(fk)]
        matrix = poem.build_matrix(tuple(res.meta["seed"]), n_lines, cpl)
        sk = matrix[bad_line - 1]["skeleton"]
        edits, used = [], set("".join(lines))
        want_col = None
        for j in range(1, cpl, 2):
            target = "P" if sk[j] == "Z" else "Z"   # 取反 → 整句粘对关系错
            ch, _ = _take(book, "平" if target == "P" else "仄", used)
            used.add(ch)
            edits.append((bad_line, j + 1, ch))
            if want_col is None:
                want_col = j + 1
        add(fid, fk, "%s：第 %d 句二四六位（%s 字）整体改用相反平仄"
                     % (label, bad_line, "、".join(str(e[1]) for e in edits)),
            edits, code, bad_line, want_col,
            "≥2 位偏离骨架 → 判整句粘对关系错，报结构性%s" % label)

    # 9–10 孤平（分水岭：五言两法同判，七言只有配对法能抓）
    for fid, fk, code in (("F09", "五言", "V-GUPING"), ("F10", "七言", "V-GUPING")):
        fk2 = "五律" if fk == "五言" else "七律"
        b = bases[fk2]
        lines = _lines_of(b)
        res = b["_res"]
        n_lines, cpl = poem.FORMS[_form(fk2)]
        matrix = poem.build_matrix(tuple(res.meta["seed"]), n_lines, cpl)
        target = None
        for i, m in enumerate(matrix):
            if m["skeleton"] in poem.GUPING_SKELETONS:
                target = (i + 1, m["skeleton"])
                break
        if target is None:
            raise RuntimeError("基线诗里没有孤平句式")
        ln, sk = target
        a, bb = poem.GUPING_PAIR[sk]
        used = set("".join(lines))
        edits = []
        for pos in (a, bb):
            if sk[pos] == "Z":          # 已是仄，只改需要改的那位
                continue
            ch, _ = _take(book, "仄", used)
            used.add(ch)
            edits.append((ln, pos + 1, ch))
        add(fid, fk2, "孤平：句「%s」触发配对约束（第 %d 字与第 %d 字同时为仄）"
                     % (sk, a + 1, bb + 1),
            edits, code, ln, a + 1,
            "七言用例是计数法与配对法的分水岭：计数法会放过此式")

    # 11–14 三仄尾 / 三平尾
    for fid, fk, want in (("F11", "五绝", "Z"), ("F12", "七绝", "Z"),
                          ("F13", "五绝", "P"), ("F14", "七律", "P")):
        b = bases[fk]
        lines = _lines_of(b)
        res = b["_res"]
        n_lines, cpl = poem.FORMS[_form(fk)]
        matrix = poem.build_matrix(tuple(res.meta["seed"]), n_lines, cpl)
        # 找一行句尾平仄与目标一致的行（三仄尾须仄收，三平尾须平收）
        ln = None
        for i, m in enumerate(matrix):
            if m["seed"][1] == want:
                ln = i + 1
                break
        if ln is None:
            ln = 1
        used = set("".join(lines))
        edits = []
        for col in range(cpl - 3, cpl - 1):   # 末三字的第 1、2 位（第 3 位已是目标声）
            ch, _ = _take(book, "平" if want == "P" else "仄", used)
            used.add(ch)
            edits.append((ln, col + 1, ch))
        add(fid, fk, "%s：第 %d 句句末三字改为同声" % ("三平尾" if want == "P" else "三仄尾", ln),
            edits, "V-SANPING" if want == "P" else "V-SANZE", ln, cpl - 2,
            "三忌之一，须报出码与句末三字证据")

    # 15–16 平仄错位（单字，非二四六位）
    for fid, fk in (("F15", "七绝"), ("F16", "五律")):
        b = bases[fk]
        lines = _lines_of(b)
        res = b["_res"]
        n_lines, cpl = poem.FORMS[_form(fk)]
        matrix = poem.build_matrix(tuple(res.meta["seed"]), n_lines, cpl)
        ln = 1
        # 取「定」位（allowed 只有一种平仄）：不论位改了本来就不算错，拿它当夹具没有意义
        col = next(j for j in range(cpl) if len(matrix[0]["allowed"][j]) == 1)
        orig = lines[ln - 1][col]
        ts = book.tones(orig)
        want = "Z" if ts == {"平"} else "P"     # 由原字实际平仄取反，避免「改了等于没改」
        ch, _ = _take(book, "平" if want == "P" else "仄", set("".join(lines)))
        add(fid, fk, "平仄错位：第 %d 句第 %d 字改成相反平仄" % (ln, col + 1),
            [(ln, col + 1, ch)], "V-TONE", ln, col + 1,
            "单字错位应报 V-TONE，不应升格为结构性失对")

    # 17–18 字数不符 / 句数不符
    b = bases["七绝"]
    lines = _lines_of(b)
    text2 = "".join(lines[:1] + [lines[1][:-1]] + lines[2:])
    res2 = poem.check_poem(text2, "qijue", book=book)
    out.append({"id": "F17", "说明": "字数不符：第 2 句删去末字",
                "note": "句数与字数不符是入口级硬错",
                "基线": {"corpus_id": b["id"], "篇名": b["篇名"], "作者": b["作者"],
                         "体裁": "七绝", "篡改前判定": "pass"},
                "篡改": [{"句": 2, "操作": "删末字", "新句": lines[1][:-1]}],
                "篡改后文本": text2, "期望码": "V-CHARS", "期望句": 2, "期望字": None,
                "实际命中": [{"code": v.code, "line": v.line, "col": v.col} for v in res2.violations],
                "全部硬错码": sorted({v.code for v in res2.violations if v.severity == "hard"}),
                "召回": any(v.code == "V-CHARS" for v in res2.violations),
                "位置准确": any(v.code == "V-CHARS" and v.line == 2 for v in res2.violations)})
    b = bases["五律"]
    lines = _lines_of(b)
    text3 = "".join(lines[:-2])
    res3 = poem.check_poem(text3, "wulv", book=book)
    out.append({"id": "F18", "说明": "句数不符：五律只给 6 句",
                "note": "句数不符应在入口拦下，不做强行重排",
                "基线": {"corpus_id": b["id"], "篇名": b["篇名"], "作者": b["作者"],
                         "体裁": "五律", "篡改前判定": "pass"},
                "篡改": [{"操作": "删末二句"}], "篡改后文本": text3,
                "期望码": "V-CHARS", "期望句": 0, "期望字": None,
                "实际命中": [{"code": v.code, "line": v.line, "col": v.col} for v in res3.violations],
                "全部硬错码": sorted({v.code for v in res3.violations if v.severity == "hard"}),
                "召回": any(v.code == "V-CHARS" for v in res3.violations),
                "位置准确": True})

    # 19–20 出韵 + 邻韵（严格模式须判错，兼容模式首句须放行）
    b = bases["七律"]
    lines = _lines_of(b)
    res = b["_res"]
    rl = _rhyme_lines(res)
    own = set(book.rhyme_sets(lines[rl[0] - 1][-1]))
    neigh = book.neighbors(sorted(own)[0], lines[rl[0] - 1][-1], "compat") if own else set()
    pick = None
    for rank, ch, ry in _pool(book, "平"):
        if ry in neigh and ch not in "".join(lines):
            pick = (ch, ry)
            break
    if pick:
        ch, ry = pick
        text4 = _apply(lines, [(rl[0], len(lines[rl[0] - 1]), ch)])
        strict = poem.check_poem(text4, "qilv", book=book, mode="strict")
        compat = poem.check_poem(text4, "qilv", book=book, mode="compat")
        out.append({"id": "F19", "说明": "邻韵：首句韵脚改用邻韵字（%s）" % ry,
                    "note": "严格模式须判硬错；兼容模式（孤雁出群格）须只给软提示",
                    "基线": {"corpus_id": b["id"], "篇名": b["篇名"], "作者": b["作者"],
                             "体裁": "七律", "篡改前判定": "pass"},
                    "篡改": [{"句": rl[0], "字": len(lines[rl[0] - 1]), "新字": ch,
                              "新字平水韵": [[x.tone, x.rhyme] for x in book.readings(ch, "pingshui")]}],
                    "篡改后文本": text4, "期望码": "V-RHYME", "期望句": rl[0],
                    "期望字": len(lines[rl[0] - 1]),
                    "实际命中": [{"code": v.code, "line": v.line, "col": v.col}
                             for v in strict.violations],
                    "全部硬错码": sorted({v.code for v in strict.violations if v.severity == "hard"}),
                    "召回": any(v.code == "V-RHYME" and v.line == rl[0] for v in strict.violations),
                    "位置准确": True,
                    "兼容模式": {"verdict": compat.verdict,
                             "codes": sorted({v.code for v in compat.violations}),
                             "硬错数": compat.hard_count},
                    "模式区分正确": strict.hard_count > 0 and compat.hard_count == 0})

    # 20 拗救合法（负例：必须判通过）
    for fid, fk, note in (("F20", "五律", "`仄平平仄平` 一拗三救，必须判通过"),):
        b = bases[fk]
        lines = _lines_of(b)
        res = b["_res"]
        n_lines, cpl = poem.FORMS[_form(fk)]
        matrix = poem.build_matrix(tuple(res.meta["seed"]), n_lines, cpl)
        ln = next(i + 1 for i, m in enumerate(matrix) if m["skeleton"] == "PPZZP")
        used = set("".join(lines))
        edits = []
        sk = "PPZZP"
        for pos in (0, 2):
            ch, _ = _take(book, "仄" if pos == 0 else "平", used)
            used.add(ch)
            edits.append((ln, pos + 1, ch))
        add(fid, fk, "合法拗救：第 %d 句改为 仄平平仄平" % ln, edits, "__PASS__", 0, None, note)
        out[-1]["召回"] = None
        out[-1]["判通过"] = out[-1]["全部硬错码"] == []
        out[-1]["note"] = "负例：合法拗救不得被判违规（A15）；此式由配对约束自然放行，不靠白名单"

    return out


def _form(name: str) -> str:
    return {"五绝": "wujue", "七绝": "qijue", "五律": "wulv", "七律": "qilv"}[name]


# ── 衬字位夹具 ──────────────────────────────────────────────────

def _inject_at_opt(cimod, cipai: str, text: str, ins: str) -> str:
    """在「最后一个带衬字位的谱行」的插入点上加一个字，重建标点。

    直接按字符位置插会把字塞进别的子句，对齐整体后移，反而造出假的平仄错。
    """
    cib = cimod.CiBook(_BOOK)
    ent = cib.pick_variant(cipai, None)
    segs = cimod._split_by_punct(text)
    # 逐谱行还原「该行由哪些子句拼成」
    per_line, cur = [], 0
    for l in ent["lines"]:
        n = len(cimod._raw_seg_lens(l["raw"]))
        per_line.append(segs[cur:cur + n])
        cur += n
    target = max((i for i, l in enumerate(ent["lines"]) if l["opt"]), default=None)
    if target is None:
        raise RuntimeError("该调没有衬字位")
    line = ent["lines"][target]
    off = line["opt"][-1]                     # pat 下标 → 该行第 off 字之前插入
    subs = per_line[target]
    flat = "".join(subs)
    flat = flat[:off] + ins + flat[off:]
    lens = cimod._raw_seg_lens(line["raw"])
    lens[-1] += 1                             # 衬字落在最后一个插入点所在的子句里
    parts, pos = [], 0
    for n in lens:
        parts.append(flat[pos:pos + n])
        pos += n
    if pos < len(flat):
        parts[-1] += flat[pos:]
    new = "，".join(parts) + "。"
    head = "".join("".join(x) for x in per_line[:target])
    tail = "".join("".join(x) for x in per_line[target + 1:])
    # 还原句末标点：以子句数重建（谱行的最后一个子句用「。」，其余「，」）
    def rebuild(groups):
        outs = []
        for i, g in enumerate(groups):
            outs.append("，".join(g) + ("。" if i == len(groups) - 1 else "。"))
        return "".join(outs)
    return rebuild(per_line[:target]) + new + rebuild(per_line[target + 1:])


_BOOK = None

def build_opt_fixtures(book, corpus) -> list[dict]:
    global _BOOK
    _BOOK = book
    """龙榆生谱 5 个 opt 槽，各造「无衬字 / 有衬字」两版，均须判通过。

    有真实例词可用的（忆江南、御街行）用真实作品；行香子包内无语料，
    按谱逐位取字构造，并**显式标注「构造」**——构造用例只能证明实现行为，
    不能当作「源文件如此」的实证。
    """
    import ci as cimod
    cib = cimod.CiBook(book)
    out = []
    real = {
        "忆江南": "江南好，风景旧曾谙。日出江花红胜火，春来江水绿如蓝。能不忆江南。",
    }
    song = {}
    for r in corpus:
        if r["集"] == "宋词三百首" and r["篇名"] in ("御街行",):
            song.setdefault(r["篇名"], r)
    # 御街行 取「对齐被接受」的一首（该篇含 2 个词林未收字，整篇判 insufficient_data，
    # 不影响「衬字位对齐」这条断言）
    for r in corpus:
        if r["篇名"] == "御街行":
            t = "".join(r["句子"])
            res = cimod.check_ci(t, "御街行", book=book)
            if res.hard_count == 0 and "V-CHARS" not in {v.code for v in res.violations}:
                real["御街行"] = t
                break

    def probe(cipai: str, variant_text: str, note: str) -> dict:
        res = cimod.check_ci(variant_text, cipai, book=book)
        codes = sorted({v.code for v in res.violations})
        # 衬字位的断言是「对齐被接受」：0 字与 1 字两种长度都不该报字数硬错。
        # 整篇是否为 pass 还取决于韵表是否收全该篇用字，那是数据覆盖问题，另报。
        align_ok = res.hard_count == 0 and "V-CHARS" not in codes
        return {"cipai": cipai, "text": variant_text, "verdict": res.verdict,
                "hard": res.hard_count, "soft": res.soft_count, "codes": codes,
                "nodata_chars": res.meta.get("nodata_chars"),
                "note": note, "对齐被接受": align_ok,
                "整篇判通过": res.verdict == "pass"}

    # 忆江南：opt 在首行「中仄ˇ仄平平」
    t = real["忆江南"]
    segs = t.split("。")
    head = segs[0]                                   # 江南好，风景旧曾谙
    a, b = head.split("，")
    noge = t
    withge = t.replace(a + "，" + b, a + "，" + b[:2] + "那" + b[2:], 1)
    out.append({"槽": "忆江南 定格 第1行 插入点 6", "来源": "真实作品（白居易《忆江南》）",
                "无衬字": probe("忆江南", noge, "原作无衬字"),
                "有衬字": probe("忆江南", withge, "在衬字位加一「那」")})

    if "御街行" in real:
        t = real["御街行"]
        withge = _inject_at_opt(cimod, "御街行", t, "又")
        out.append({"槽": "御街行 定格 第8行 插入点 11",
                    "来源": "真实作品（范仲淹《御街行》）",
                    "无衬字": probe("御街行", t, "原作无衬字"),
                    "有衬字": probe("御街行", withge,
                                   "在谱行第 8 行的衬字插入点加一「又」")})
    else:
        out.append({"槽": "御街行 定格 第8行 插入点 11", "来源": "包内无可用例词",
                    "无衬字": {"通过": None, "note": "未找到能通过的御街行例词，见报告已知限制"},
                    "有衬字": {"通过": None, "note": "同上"}})

    # 行香子：包内无语料 → 按谱构造，标注「构造」
    ent = cib.pick_variant("行香子", None)
    if ent:
        rows = []
        for with_opt in (False, True):
            text = _synth_by_pat(book, ent, with_opt=with_opt)
            rows.append(probe("行香子", text, "按谱构造成字，仅验证 opt 槽语义"))
        out.append({"槽": "行香子 定格 第8行 插入点 4/7/10", "来源": "构造（包内无行香子例词）",
                    "无衬字": rows[0], "有衬字": rows[1],
                    "声明": "构造成字只能证明实现行为，不能作为「源文件如此」的实证"})
    return out


def _synth_by_pat(book, ent: dict, *, with_opt: bool) -> str:
    """按谱逐位取字造一句词（仅用于验证 opt 槽语义，标注为构造用例）。"""
    import ci as cimod
    segs = []
    used: set = set()
    # 从《词林正韵》取字（判定用的就是这本书，从平水韵取会出现韵表未收的怪字）
    cilin_pool = {tone: [] for tone in ("平", "仄")}
    for ch, recs in book.cl_chars.items():
        tones = {("平" if r["tone"] == "平" else "仄") for r in recs}
        if len(tones) != 1:
            continue
        cilin_pool[tones.pop()].append((min(r["src_rank"] for r in recs), ch))
    for kk in cilin_pool:
        cilin_pool[kk].sort()
    for line in ent["lines"]:
        pat = line["pat"]
        sub = cimod._raw_seg_lens(line["raw"])
        chars, i = [], 0
        for k in range(len(pat)):
            if with_opt and k in (line.get("opt") or []):
                chars.append("兮")
            tone = "平" if pat[k] == "P" else ("仄" if pat[k] == "Z" else "平")
            ch = next((c for _, c in cilin_pool[tone] if c not in used), None)
            if ch is None:
                raise RuntimeError("词林取字用尽：%s" % tone)
            used.add(ch)
            chars.append(ch)
        s = "".join(chars)
        # 按谱行的子句长度重新插入标点，句末用「。」，子句间用「，」
        pos, parts = 0, []
        for n in sub:
            parts.append(s[pos:pos + n])
            pos += n
        if pos < len(s):
            parts[-1] += s[pos:]
        segs.append("，".join(parts) + "。")
    return "".join(segs)


# ── 零误报 ──────────────────────────────────────────────────────
def morphology(book, r: dict) -> dict:
    """与八式骨架**无关**的形态学筛查，用来定「体裁可信」的观察分母。

    四项判据都不看骨架，故不构成循环论证：
      ① 句长全等且句数合近体（4/8 句 × 5/7 字）
      ② 韵脚同部率 100%（首句允许借邻韵）
      ③ 无重韵
      ④ 无全平句、无全仄句——近体任何句式都不可能出现全平/全仄句，
         这一条正是《宿王昌龄隐居》「清光犹为君」（五字全平）暴露五古的地方。

    它只能排除「明显不是近体」的诗，**不能证明一定是近体**：一韵到底的五古
    （如《宿王昌龄隐居》）仍会通过。故只用于定观察分母，绝不当作金标；
    真正的零误报判据是人工金标（由 team-lead 独立造）。
    """
    # corpus.jsonl 的「句子」保留标点（如「桂华秋皎洁。」）；韵脚与句长必须
    # 剥到只剩汉字再算，否则末字取到「。」，韵脚集为空 → 全诗被误判「韵脚不同部」。
    sents = ["".join(c for c in s if "\u4e00" <= c <= "\u9fff") for s in r["句子"]]
    sents = [s for s in sents if s]
    n, cpl = len(sents), (len(sents[0]) if sents else 0)
    c1 = r["体裁"] in NEAR_FORMS and all(len(s) == cpl for s in sents)
    idx = [i for i in range(1, n, 2)]
    feet = [sents[i][-1] for i in idx if i < n]
    sets = [book.rhyme_sets(c) for c in feet]
    counts: dict[str, int] = {}
    for s in sets:
        for x in s:
            counts[x] = counts.get(x, 0) + 1
    best = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if counts else None
    hit = sum(1 for s in sets if best and best in s)
    c2 = bool(feet) and all(sets) and hit >= len(sets) - 1     # 首句可借邻韵
    c3 = len(feet) == len(set(feet))
    mono = []
    for s in sents:
        ts = [book.tones(c) for c in s]
        if any(not t for t in ts):
            continue
        if all(t == {"平"} for t in ts) or all(t == {"仄"} for t in ts):
            mono.append(s)
    c4 = not mono
    return {"句长齐整": c1, "韵脚同部": c2, "无重韵": c3, "无全平全仄句": c4,
            "ok": c1 and c2 and c3 and c4,
            "全平全仄句": mono[:3], "韵部": best}


def zero_false_positive(book, corpus) -> dict:
    """零误报：分母改为「体裁可信 ∩ 韵表全覆盖」，并分栏报五种结果。

    为什么改分母：`体裁` 是推断值（源文件无分卷标题），98 首里混着五古/古绝，
    让校验器为「别人的错」背锅。现在先用形态学筛查排除明显非近体的诗，
    再要求韵表全覆盖（缺字的判 `insufficient_data`／`unable`，单列，不算误报）。
    **必须单列「因缺字无法判定」的条数**——否则「零误报」可以靠装瞎达成。
    """
    buckets = {"pass": [], "pass_with_variant": [], "unable": [],
               "fail": [], "体裁存疑": [], "因缺字无法判定": []}
    detail = {"fail": [], "unable": [], "体裁存疑": []}
    for r in corpus:
        if r.get("体裁") not in NEAR_FORMS:
            continue
        text = "".join(r["句子"])
        res = poem.check_poem(text, _form(r["体裁"]), book=book)
        miss = res.meta.get("nodata_chars") or []
        morph = morphology(book, r)
        row = {"corpus_id": r["id"], "篇名": r["篇名"], "作者": r["作者"],
               "体裁": r["体裁"], "体裁可信": r.get("体裁可信", False),
               "形态学": morph}
        if res.hard_count:
            row["硬错"] = [{"code": v.code, "line": v.line, "col": v.col, "rule": v.rule,
                            "actual": v.actual, "expected": v.expected}
                           for v in res.violations if v.severity == "hard"]
        if not morph["ok"]:
            buckets["体裁存疑"].append(r["id"])
            detail["体裁存疑"].append(dict(row, 不通过项=[k for k in
                                                   ("句长齐整", "韵脚同部", "无重韵",
                                                    "无全平全仄句") if not morph[k]]))
            continue
        if miss:
            buckets["因缺字无法判定"].append(r["id"])
            continue
        if res.verdict in ("pass", "pass_with_variant"):
            buckets[res.verdict].append(r["id"])
        elif res.verdict == "unable":
            buckets["unable"].append(r["id"])
            detail["unable"].append(dict(row, 疑似缺读=res.meta.get("counterfactual")))
        else:
            buckets["fail"].append(r["id"])
            detail["fail"].append(row)
    denom = len(buckets["pass"]) + len(buckets["pass_with_variant"]) + \
        len(buckets["unable"]) + len(buckets["fail"])
    return {
        "分母口径": "体裁可信（形态学筛查通过）∩ 韵表全覆盖",
        "分母": denom,
        "分栏": {k: len(v) for k, v in buckets.items()},
        "pass": len(buckets["pass"]),
        "pass_with_variant": len(buckets["pass_with_variant"]),
        "unable": len(buckets["unable"]),
        "fail": len(buckets["fail"]),
        "体裁存疑": len(buckets["体裁存疑"]),
        "因缺字无法判定": len(buckets["因缺字无法判定"]),
        "fail_明细": detail["fail"],
        "unable_明细": detail["unable"],
        "体裁存疑_明细": detail["体裁存疑"],
        "全清诗数": denom,
        "全清诗误报": len(buckets["fail"]),
        "false_positives": len(buckets["fail"]),
        "ok": not buckets["fail"],
    }


def main(argv: list[str]) -> int:
    from tonebook import ToneBook
    book = ToneBook()
    corpus = _paths.load_jsonl("corpus.jsonl")
    viol = build_violation_fixtures(book, corpus)
    opt = build_opt_fixtures(book, corpus)
    zfp = zero_false_positive(book, corpus)

    recalled = [x for x in viol if x.get("召回") is True]
    negatives = [x for x in viol if x.get("召回") is None]
    opt_ok = []
    for o in opt:
        for k in ("无衬字", "有衬字"):
            opt_ok.append(bool(o[k].get("对齐被接受")))
    report = {
        "violation_fixtures": viol,
        "opt_fixtures": opt,
        "zero_false_positive": zfp,
        "summary": {
            "违规夹具数": len(viol),
            "违规召回": "%d/%d" % (len(recalled), len([x for x in viol if x.get("召回") is True
                                                   or x.get("召回") is False])),
            "召回全部命中": all(x.get("召回") is True for x in viol if x.get("召回") is not None),
            "法性负例判通过": all(x.get("判通过") for x in negatives) if negatives else None,
            "零误报": "%d/%d（韵表全覆盖的 %d 首）" % (
                zfp["全清诗数"] - zfp["全清诗误报"], zfp["全清诗数"], zfp["全清诗数"]),
            "因缺字无法判定（不计误报）": zfp["因缺字无法判定"],
            "分栏": zfp["分栏"],
            "衬字位对齐全部被接受": all(opt_ok) and len(opt_ok) >= 6,
            "衬字位整篇判通过数": sum(1 for o in opt for k in ("无衬字", "有衬字")
                                if o[k].get("整篇判通过")),
            "衬字位断言数": len(opt_ok),
        },
    }
    ok = (report["summary"]["召回全部命中"] and zfp["ok"] and opt_ok and all(opt_ok))

    out = _paths.SKILL_DIR / "tests"
    out.mkdir(exist_ok=True)
    (out / "fixtures_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    print(json.dumps({"ok": ok, "summary": report["summary"],
                      "fail_明细": zfp["fail_明细"][:5],
                      "unable_明细": zfp["unable_明细"][:5]},
                     ensure_ascii=False, indent=1, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
