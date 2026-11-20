"""D4 王力《诗词格律》附録白香词谱 → data/cipu_wang.json

对应《数据抽取规格》§4.2 R-25..R-30、§4.3 C-17..C-20。
版权边界（B12/A23④/A46）：只抽「★条目 + 例词行 + 符号谱 + 注记」，不收录王力论述正文。

用法：python tools/extract_cipu_wang.py --src <语料根> [--out <目录>]
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import dump_json, is_cjk, out_dir, read_lines, rel, sha256_of, skill_data_dir, src_root

REL_SRC = "二、格律入门/诗词格律（王力）.md"

HEAD = re.compile(r"^★\s*(\d{3})([a-d]?)、(.+?)（(.+?)）\s*$")
PATLINE = re.compile(r"^[＋－｜＝＄]")
PUNCT = "．，、。；！？"
SYMBOL = {"－": ("P", 0), "｜": ("Z", 0), "＋": ("*", 0), "＝": ("P", 1), "＄": ("Z", 1)}
NOTE_RE = re.compile(r"（([^）]*)）")
BRACKET_RE = re.compile(r"[（(][^）)]*[）)]")


def parse_note(text: str) -> dict:
    """R-29：注记按中文逗号切段，识别机读词；不可识别段落入 free_note。"""
    out = {"rhyme_shift": None, "repeat": None, "free_note": []}
    for seg in re.split(r"[，、]", text):
        seg = seg.strip()
        if not seg:
            continue
        if seg == "换平韵":
            out["rhyme_shift"] = "P"
        elif seg == "换仄韵":
            out["rhyme_shift"] = "Z"
        elif seg == "叠三字":
            out["repeat"] = "die3"
        elif seg == "叠句":
            out["repeat"] = "dieju"
        else:
            out["free_note"].append(seg)
    return out


def parse_pat(raw: str) -> dict:
    """符号映射 + 韵位掩码 + 标点保留（R-27/R-28/C-20）。"""
    notes = [parse_note(m.group(1)) for m in NOTE_RE.finditer(raw)]
    body = NOTE_RE.sub("", raw).strip()
    pat, rhyme, punct, seg_lens = [], [], [], []
    seg_cur = 0
    for ch in body:
        if ch in SYMBOL:
            p, r = SYMBOL[ch]
            pat.append(p)
            rhyme.append(r)
            seg_cur += 1
        elif ch in PUNCT:
            punct.append(ch)
            if seg_cur:
                seg_lens.append(seg_cur)
            seg_cur = 0
        elif ch in " \t":
            continue
        else:
            raise AssertionError("谱行含非符号字符 %r in %r" % (ch, raw))
    merged = {"rhyme_shift": None, "repeat": None, "free_note": []}
    for n in notes:
        merged["rhyme_shift"] = n["rhyme_shift"] or merged["rhyme_shift"]
        merged["repeat"] = n["repeat"] or merged["repeat"]
        merged["free_note"].extend(n["free_note"])
    if seg_cur:
        seg_lens.append(seg_cur)
    return {"pat": "".join(pat), "rhyme": rhyme, "punct": "".join(punct),
            "seg_lens": seg_lens, "notes": merged, "has_note": bool(notes)}


def ex_segments(example: str) -> list[int]:
    text = BRACKET_RE.sub("", example)
    out, cur = [], 0
    for ch in text:
        if is_cjk(ch):
            cur += 1
        elif ch in PUNCT:
            if cur:
                out.append(cur)
            cur = 0
    if cur:
        out.append(cur)
    return out


def main() -> None:
    root = src_root(sys.argv)
    out = out_dir(sys.argv, skill_data_dir())
    path = os.path.join(root, REL_SRC)
    lines = read_lines(path)

    heads = [(i, m) for i, line in enumerate(lines) for m in [HEAD.match(line.strip())] if m]
    entries = []
    seen_seq: dict[str, int] = {}
    total_pat = total_clean = 0
    exceptions = []
    symbol_count = {"－": 0, "｜": 0, "＋": 0, "＝": 0, "＄": 0, "‖": 0}
    note_count = {"换平韵": 0, "换仄韵": 0, "叠句": 0, "叠三字": 0}

    for k, (i, m) in enumerate(heads):
        num, suffix, head_text, author = m.group(1), m.group(2), m.group(3), m.group(4)
        cipai, _, title = head_text.partition("·")
        seen_seq[num] = seen_seq.get(num, 0) + 1
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        body = lines[i + 1:end]

        rec_lines = []
        pian = 1
        pending_example = None
        for raw in body:
            s = raw.strip()
            if not s or s == "⇑":
                continue
            if s == "‖":
                symbol_count["‖"] += 1
                pian += 1
                continue
            if PATLINE.match(s):
                total_pat += 1
                for ch in s:
                    if ch in symbol_count:
                        symbol_count[ch] += 1
                for nm in NOTE_RE.finditer(s):
                    seg_text = nm.group(1)
                    for kw in note_count:
                        if kw in seg_text:
                            note_count[kw] += 1
                info = parse_pat(s)
                if not info["has_note"]:
                    total_clean += 1
                example = pending_example or ""
                pending_example = None
                ex_chars = "".join(c for c in BRACKET_RE.sub("", example) if is_cjk(c))
                pat_len = len(info["pat"])
                ok = pat_len == len(ex_chars)
                rec = {"pian": pian, "raw_pat": s, "pat": info["pat"],
                       "rhyme": info["rhyme"], "punct": info["punct"],
                       "example": example, "example_chars": ex_chars,
                       "aligned": ok, "notes": info["notes"]}
                if not ok:
                    seg_p = info["seg_lens"]
                    seg_e = ex_segments(example)
                    rec["seg_pat"] = seg_p
                    rec["seg_example"] = seg_e
                    exceptions.append({"entry": "B:%s:%d" % (num, seen_seq[num]),
                                       "num": num + suffix, "line_in_entry": len(rec_lines) + 1,
                                       "example": example, "raw_pat": s,
                                       "pat_len": pat_len, "example_len": len(ex_chars),
                                       "diff": pat_len - len(ex_chars),
                                       "seg_pat": seg_p, "seg_example": seg_e})
                rec_lines.append(rec)
                continue
            if s.startswith("★"):
                continue
            pending_example = s if pending_example is None else pending_example + s

        entries.append({
            "id": "B:%s:%d" % (num, seen_seq[num]),
            "num": num, "suffix": suffix, "seq": seen_seq[num],
            "name": cipai, "title": title or None, "author": author,
            "pians": max(r["pian"] for r in rec_lines) if rec_lines else 1,
            "line_count": len(rec_lines),
            "char_count": sum(len(r["pat"]) for r in rec_lines),
            "lines": rec_lines,
        })

    doc = {
        "schema_version": "1.0",
        "source": {"book": "白香词谱（清·舒梦兰）符号谱，经王力《诗词格律》附録转录",
                   "file": REL_SRC, "copyright": "公版（谱式数据据通行词谱整理；未收录任何现代整理本的论述文字）",
                   "edition": "王力《诗词格律》附録", "sha256": sha256_of(path),
                   "extracted_at": "2026-11-20"},
        "meta": {"entry_count": len(entries), "pat_line_total": total_pat,
                 "pat_line_clean": total_clean,
                 "pat_line_aligned": sum(1 for e in entries for r in e["lines"] if r["aligned"]),
                 "symbol_count": symbol_count, "note_count": note_count,
                 "encoding": "ASCII 紧凑谱串：P=定平 Z=定仄 *=可平可仄；rhyme 掩码等长"},
        "entries": entries,
    }
    dump_json(doc, os.path.join(out, "cipu_wang.json"))
    dump_json({"schema_version": "1.0", "source_file": "cipu_wang.json",
               "count": len(exceptions), "items": exceptions},
              os.path.join(out, "alignment_exceptions_raw.json"))
    sys.stdout.write("entries=%d pat=%d clean=%d aligned=%d exceptions=%d symbols=%s notes=%s\n" % (
        len(entries), total_pat, total_clean,
        doc["meta"]["pat_line_aligned"], len(exceptions), symbol_count, note_count))
    for e in exceptions:
        sys.stdout.write("EXC %s line%s %s | pat=%s(%d) ex=%d seg_p=%s seg_e=%s\n" % (
            e["num"], e["line_in_entry"], e["example"], e["raw_pat"], e["pat_len"],
            e["example_len"], e["seg_pat"], e["seg_example"]))
    sys.stdout.write("src=%s\n" % rel(path, root))


if __name__ == "__main__":
    main()
