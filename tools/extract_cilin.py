"""D2 词林正韵（戈载） → data/cilin.json

对应《数据抽取规格》§2.2 R-09..R-15、§2.3 C-08..C-12。
三个坑（实测）：声调标签与韵部内联同一行、小韵组名带 <半>、注释两种括号且含中文逗号。

C-12 的实现口径（与规格字面不同，已在回归报告记录）：本实现先按声调/小韵标记切分，
组体内不可能残留另一声调段，故「内联吞段」在结构上被排除；规格的「任一组 >60 字即
suspect」对词林大韵（七阳 173 字等）恒真，仅作信息项落盘，真正断言改为
「组体内不得出现声调标记或『通用/独用』关系串」+「第一部【二冬】字数 <60」。

用法：python tools/extract_cilin.py --src <语料根> [--out <目录>]
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (cn_num, dump_json, is_cjk, out_dir, read_lines,
                    sha256_of, skill_data_dir, src_root)

REL_SRC = "六、工具书/词林正韵（戈载）.md"

MARK = re.compile(r"平声：|仄声：|入声：|上声|去声|【([^】]+)】")
# 裁决书 §12.5-E：剥注正则必须覆盖全角/半角两对方括号与圆括号：（）()［］[]。
# 只剥圆括号会漏掉 [挽联] 这类方括号注，把注文里的字当韵字，制造「挽重出」假证据
# （十四愿第七部正确为 21/21；漏剥方括号会得到 23/22）。
NOTE = re.compile(r"[（(]([^）)]*)[）)]|[\[［]([^\]］]*)[\]］]")
# 词林小韵名用繁体/异体，平水韵表用简体；仅用于「小韵名 → 平水韵目」解析
NAME_TRAD = {"禡": "祃", "艷": "艳", "箇": "个"}
NAME_RE = re.compile(r"[一二三四五六七八九十]{1,3}[^\s　:：]")


def names_of(text: str) -> list[str]:
    out = []
    for m in NAME_RE.finditer(text):
        tok = m.group(0)
        if len(tok) >= 2 and tok not in ("通用", "独用"):
            out.append(tok)
    return out


def parse_relation(ctx: str, base_tone: str) -> dict[str, list[str]]:
    """把「上声一董二肿　去声一送二宋通用」解析成 {声调: [韵目]}。"""
    out: dict[str, list[str]] = {}
    seg = re.split(r"(上声|去声)", ctx.replace("　", " "))
    if seg[0].strip():
        out.setdefault(base_tone, []).extend(names_of(seg[0]))
    for i in range(1, len(seg), 2):
        out.setdefault(seg[i][0], []).extend(names_of(seg[i + 1]))
    return out


def main() -> None:
    root = src_root(sys.argv)
    out = out_dir(sys.argv, skill_data_dir())
    path = os.path.join(root, REL_SRC)
    lines = read_lines(path)

    heads = []
    for i, line in enumerate(lines):
        m = re.match(r"^## 第([一二三四五六七八九十]{1,2})部", line.strip())
        if m:
            heads.append((i, cn_num(m.group(1))))
    if len(heads) != 19:
        sys.stdout.write("WARN 部数 %d != 19\n" % len(heads))

    parts, xiaoyun_all = [], []
    label_count = {"平声": 0, "仄声": 0, "入声": 0}
    half_names: dict[str, list[int]] = {}
    big_groups: list[str] = []
    leak: list[str] = []
    orphan_notes: list[str] = []

    for k, (i, no) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        text = "".join(lines[i + 1:end])
        marks = list(MARK.finditer(text))
        tone = None
        label_tone = None          # 最近一个声调标签：其后的关系串在下个标记处收取
        current_rel = ""
        rels: list[tuple[str, str]] = []
        groups = []
        cursor = 0
        for mi, m in enumerate(marks):
            tok = m.group(0)
            seg = text[cursor:m.start()]
            cursor = m.end()
            if label_tone is not None:            # seg 即上一个声调标签的关系串
                rels.append((label_tone, seg.strip()))
                current_rel = seg.strip()
                label_tone = None
            if tok in ("平声：", "仄声：", "入声："):
                label_count[tok[:-1]] += 1
                tone = {"平声：": "平", "仄声：": "仄", "入声：": "入"}[tok]
                label_tone = tone
                continue
            if tok in ("上声", "去声"):
                tone = tok[0]
                label_tone = tone
                continue
            raw = m.group(1).strip()
            name = raw.replace("<半>", "").strip()
            half = "<半>" in raw
            body_end = marks[mi + 1].start() if mi + 1 < len(marks) else len(text)
            body = text[m.end():body_end]
            body_note_free = NOTE.sub("", body)
            for bad in ("平声", "仄声", "入声", "通用", "独用"):
                if bad in body_note_free:
                    leak.append("%s【%s】含结构串 %s" % (no, raw, bad))
            # 逐字 + 逐字注（R-14：注剥离但不得丢字，注归属其前一字）
            items: list[list] = []
            pos = 0
            for nm in NOTE.finditer(body):
                for c in body[pos:nm.start()]:
                    if is_cjk(c):
                        items.append([c, None])
                note_text = (nm.group(1) if nm.group(1) is not None else nm.group(2)) or ""
                if items and items[-1][1] is None:
                    items[-1][1] = note_text
                else:
                    orphan_notes.append("%s【%s】%s" % (no, raw, note_text))
                pos = nm.end()
            for c in body[pos:]:
                if is_cjk(c):
                    items.append([c, None])
            chars = "".join(c for c, _ in items)
            if len(chars) > 60:
                big_groups.append("%s【%s】%d字" % (no, raw, len(chars)))
            xy = {"part": no, "tone": tone, "name": name, "raw_name": raw,
                  "half": half, "half_of": None, "chars": chars,
                  "char_count": len(chars),
                  "char_notes": [[c, n] for c, n in items if n],
                  "note_count": sum(1 for _, n in items if n),
                  "relation_ctx": current_rel}
            if half:
                half_names.setdefault(name, []).append(no)
            xiaoyun_all.append(xy)
            groups.append(xy)

        rel_map: dict[str, str] = {}
        for tone_key, rel_text in rels:
            for nm in names_of(rel_text):
                rel_map.setdefault(nm, tone_key)
        for g in groups:
            if g["name"] in rel_map:
                g["tone"] = rel_map[g["name"]]
        rel, seen = [], set()
        for g in groups:
            key = (g["tone"], g["relation_ctx"])
            if key in seen:
                continue
            seen.add(key)
            ctx = g["relation_ctx"].replace("　", " ").strip()
            rhymes = re.findall(r"[一二三四五六七八九十]{1,3}[^\s　:：]", ctx)
            rel.append({"tone": g["tone"], "declared": ctx,
                        "cross": "通用" in ctx,
                        "rhymes": [r for r in rhymes if len(r) >= 2]})
        parts.append({"no": no,
                      "tone_scope": "入声" if any(g["tone"] == "入" for g in groups) else "平上去",
                      "groups": rel, "xiaoyun_count": len(groups)})

    ps_path = os.path.join(out, "pingshui.json")
    if not os.path.exists(ps_path):
        sys.stderr.write("缺少 %s：请先运行 extract_pingshui.py\n" % ps_path)
        raise SystemExit(2)
    with open(ps_path, "r", encoding="utf-8") as fh:
        doc_ps = json.load(fh)
    base2full = {r["id"][-1]: r["id"] for r in doc_ps["rhymes"]}

    char_index: dict[str, list[dict]] = {}
    rhyme_map: dict[str, list[int]] = {}
    unresolved: list[str] = []
    rank = 0
    for g in xiaoyun_all:
        base = g["name"][-1]
        full = base2full.get(NAME_TRAD.get(base, base))
        g["rhyme_full"] = full
        if full is None:
            unresolved.append(g["raw_name"])
        else:
            rhyme_map.setdefault(full, [])
            if g["part"] not in rhyme_map[full]:
                rhyme_map[full].append(g["part"])
        note_of = dict(g["char_notes"])
        for ch in g["chars"]:
            rank += 1
            char_index.setdefault(ch, []).append({
                "part": g["part"], "tone": g["tone"], "group": g["name"],
                "half": g["half"], "note": note_of.get(ch), "src_rank": rank})
    for ch in char_index:
        char_index[ch].sort(key=lambda r: r["src_rank"])
    for k in rhyme_map:
        rhyme_map[k].sort()

    structure = {"平上去": sum(1 for p in parts if p["tone_scope"] == "平上去"),
                 "入声": sum(1 for p in parts if p["tone_scope"] == "入声")}
    doc = {
        "schema_version": "1.0",
        "source": {"book": "词林正韵（戈载，清）", "file": REL_SRC,
                   "copyright": "公版", "edition": "殆知阁中华古籍库整理本",
                   "sha256": sha256_of(path), "extracted_at": "2026-11-20"},
        "meta": {"part_count": len(parts), "structure": structure,
                 "xiaoyun_count": len(xiaoyun_all), "unique_chars": len(char_index),
                 "label_count": label_count, "half_rhyme_count": len(half_names),
                 "half_rhymes": {n: sorted(v) for n, v in half_names.items()},
                 "structure_leak": leak, "big_groups_over60": big_groups,
                 "orphan_notes": orphan_notes,
                 "rhyme_map_size": len(rhyme_map),
                 "unresolved_group_names": unresolved,
                 "note_total": sum(g["note_count"] for g in xiaoyun_all)},
        "parts": parts, "xiaoyun": xiaoyun_all,
        "char_index": char_index, "rhyme_map": rhyme_map,
    }
    dump_json(doc, os.path.join(out, "cilin.json"))
    sys.stdout.write("parts=%d structure=%s xiaoyun=%d unique=%d labels=%s half=%d rhyme_map=%d notes=%d leak=%d unresolved=%s\n" % (
        len(parts), structure, len(xiaoyun_all), len(char_index), label_count,
        len(half_names), len(rhyme_map), doc["meta"]["note_total"], len(leak), unresolved))
    sys.stdout.write("over60=%d orphan_notes=%s\n" % (len(big_groups), orphan_notes[:5]))
    for probe in ("中", "重", "从", "长", "行"):
        sys.stdout.write("%s -> %s\n" % (probe, [(d["group"], d["tone"], d["note"]) for d in char_index.get(probe, [])]))


if __name__ == "__main__":
    main()
