"""D3 唐宋词格律（龙榆生） → data/cipu_long.json

对应《数据抽取规格》§3.2 R-16..R-24、§3.3 C-13..C-16。
裁决书 §7.1：`ˇ` = 衬字标记（图例「"ˇ"表可用衬字处」），实现为 opt 槽位（该位 0 或 1 字皆合法），
不得当残缺删除。裁决书 §7.2：（句）（读）实测 0 处，句读由行尾标点确定。

用法：python tools/extract_cipu_long.py --src <语料根> [--out <目录>]
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (cn_num, dump_json, out_dir, read_lines, rel, sha256_of,
                    skill_data_dir, src_root)

REL_SRC = "三、填词专修/唐宋词格律（龙榆生）.md"

HEAD = re.compile(r"^(#{0,3})\s*【(.+?)】")
# R-16/C-15 体式白名单（含实测补充项：格X（…）、变格（…）、平韵格/仄韵格）
BODY_NAME = re.compile(
    r"^(定格|双调|长调|又一体|平韵格|仄韵格|格[一二三四五六七八九]|别格[一二三四五六七八九]?"
    r"|变格[一二三四五六七八九]?)(（.*）)?$")
# C-13 网站噪声标题白名单
NOISE = re.compile(r"《词学十讲》|选调须声意相谐|流丽和婉的曲调|曲子词之短调小令|"
                   r"短调小令中去声字的处理|小令短调中的谐婉曲调|唐宋歌词的特殊形式|"
                   r"词学十讲|第一讲|第二讲|第三讲|第四讲|第五讲|第六讲|第七讲|第八讲|第九讲|第十讲")
PAT_STRIP = re.compile(r"（[^）]*）|『|』|〖|〗")
PAT_PUNCT = "。，、"
VALID_PAT = re.compile(r"^[平中仄ˇ]+$")
# 谱行起始字：平/中/仄 + 声调提示（去/上/入，龙榆生用它们标「须用此声」）+ 结构标记。
# 漏掉 去/上/入 会整行丢失（长亭怨慢首行「去平仄、平平平仄」曾因此少算 18 字）。
PAT_START = r"^[平中仄上去入〖『]"
CHARS_RE = re.compile(r"(\d+|廿[一二三四五六七八九]?|卅[一二三四五六七八九]?|[一二三四五六七八九十百零]{1,4})字")
RHYME_RE = re.compile(r"[一二三四五六七八九十廿卅]+(平|仄|入声)?韵")
CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn2int(s: str) -> int | None:
    """字数解析：支持阿拉伯数字、「廿六」「卅六」与「十六」「四十二」。"""
    if s.isdigit():
        return int(s)
    if s.startswith("廿"):
        return 20 + CN.get(s[1:], 0)
    if s.startswith("卅"):
        return 30 + CN.get(s[1:], 0)
    try:
        return cn_num(s)
    except Exception:
        return None


def parse_pat(raw: str) -> dict | None:
    """谱行 → ASCII 谱串 + 韵位掩码 + 衬字位 + 标点。非法谱行返回 None。"""
    rhyme = []
    zengyun = []
    seg = []
    # 逐段扫描，识别 （韵）/（增韵） 与句读标点
    i = 0
    core = []
    while i < len(raw):
        ch = raw[i]
        if ch in "『〖":
            core.append({"mark": ch})
            i += 1
            continue
        if ch in "』〗":
            core.append({"mark": ch})
            i += 1
            continue
        if ch == "（":
            j = raw.find("）", i)
            token = raw[i + 1:j]
            core.append({"note": token})
            i = j + 1
            continue
        if ch in PAT_PUNCT:
            core.append({"punct": ch})
            i += 1
            continue
        core.append({"char": ch})
        i += 1

    pat, rhy, opt, punct = [], [], [], []
    tone_hint: list[list] = []
    marks = {"dz": 0, "repeat": 0}
    buf = ""
    for item in core:
        if "char" in item:
            ch = item["char"]
            if ch not in "平中仄ˇ去上入":
                return None
            if ch == "ˇ":
                opt.append(len(pat))
                buf += "ˇ"
                continue
            if ch in "去上入":                 # 源文「须用去声/上声/入声」提示位，平仄语义为仄
                tone_hint.append([len(pat), ch])
                pat.append("Z")
                rhy.append(0)
                buf += ch
                continue
            pat.append({"平": "P", "中": "*", "仄": "Z"}[ch])
            rhy.append(0)
            buf += ch
        elif "note" in item:
            if item["note"] == "韵":
                if rhy:
                    rhy[-1] = 1
            elif item["note"] == "增韵":
                if rhy:
                    rhy[-1] = 1
                    zengyun.append(len(rhy) - 1)
            elif item["note"] in ("叠句", "叠三字"):
                marks["repeat"] += 1
            else:
                return None
        elif "punct" in item:
            punct.append(item["punct"])
        elif "mark" in item:
            if item["mark"] in "『":
                marks["dz"] = marks.get("dz", 0) + 1
    if not pat:
        return None
    return {"pat": "".join(pat), "rhyme": rhy, "opt": opt, "tone_hint": tone_hint,
            "punct": "".join(punct), "zengyun": zengyun,
            "duizhang_mark": "『" in raw, "repeat_mark": "〖" in raw}


def main() -> None:
    root = src_root(sys.argv)
    out = out_dir(sys.argv, skill_data_dir())
    path = os.path.join(root, REL_SRC)
    lines = read_lines(path)

    heads = []
    for i, line in enumerate(lines):
        m = HEAD.match(line.strip())
        if not m:
            continue
        name = m.group(2)
        if name == "注":                       # C-14：`【注】…` 是注行不是标题
            continue
        heads.append((i, len(m.group(1)), name))

    classify = []
    for i, lvl, name in heads:
        first = name.split("、")[0]
        classify.append((i, lvl, name, bool(BODY_NAME.match(first))))

    cipais: list[dict] = []
    entries: list[dict] = []
    noise_dropped: list[dict] = []
    stray_pat: list[dict] = []
    variant_index: dict[str, int] = {}

    current_cipai = None
    current_entry = None
    pending_notes: list[str] = []
    pending_editor: list[str] = []

    head_at = {i: (lvl, name, is_body) for i, lvl, name, is_body in classify}
    idx_sorted = sorted(head_at)

    for pos, i in enumerate(idx_sorted):
        lvl, name, is_body = head_at[i]
        nxt = idx_sorted[pos + 1] if pos + 1 < len(idx_sorted) else len(lines)
        block = lines[i + 1:nxt]
        if is_body:
            if current_cipai is None:
                noise_dropped.append({"line": i + 1, "head": name, "why": "体式头无归属词牌"})
                continue
            seq = variant_index.get(current_cipai["name"], 0) + 1
            variant_index[current_cipai["name"]] = seq
            first = name.split("、")[0]
            kind = ("main" if first.startswith("定格")
                    else "sub" if re.match(r"^(格[一二三四五六七八九]|别格|变格)", first)
                    else "alias_variant")
            current_entry = {"id": "A:%s:%d" % (current_cipai["name"], seq),
                             "name": current_cipai["name"], "variant": name,
                             "variant_kind": kind, "seq": seq,
                             "declared": current_cipai["declared"],
                             "lines": [], "notes": [], "editor_notes": [],
                             "duizhang": [], "repeat": [],
                             "raw_ref": {"file": REL_SRC, "line": i + 1}}
            entries.append(current_entry)
            continue

        # 词牌头
        parts = [p for p in name.split("、") if p]
        desc_lines = []
        cur_variant_seen = False
        for raw in block:
            s = raw.strip()
            if HEAD.match(s) and s.startswith("###") and not s.startswith("####"):
                mm = HEAD.match(s)
                if mm and mm.group(2) == "注":
                    continue
            if not s:
                continue
            if s.startswith("#"):
                if NOISE.search(s):
                    noise_dropped.append({"line": i + 1, "head": s[:24], "why": "网站噪声标题（C-13）"})
                continue
            if s.startswith("琴注") or s.startswith("注："):
                pending_editor.append(s)
                continue
            if s.startswith("【注】"):
                pending_notes.append(s[3:].strip())
                continue
            if re.match(PAT_START, s):
                continue
            desc_lines.append(s)
        decl = {"chars": None, "chars_raw": None, "rhyme_desc": None, "candidates": []}
        desc_text = "".join(desc_lines)
        # 「N字」在说明段里可能出现多次（如兰陵王「管色用大凡字、大一字、勾字…一百三十字」），
        # 取第一处会取到噪声。全部收下，等谱行统计完成后按 char_total 就近择一。
        for mc in CHARS_RE.finditer(desc_text):
            v = cn2int(mc.group(1))
            if v:
                decl["candidates"].append([mc.group(0), v])
        if decl["candidates"]:
            decl["chars_raw"], decl["chars"] = decl["candidates"][0]
        mr = RHYME_RE.search(desc_text)
        if mr:
            decl["rhyme_desc"] = mr.group(0)
        aka = re.findall(r"又名《([^》]+)》", desc_text)
        current_cipai = {"name": parts[0], "aliases": parts[1:], "aka": aka,
                         "declared": decl, "desc": desc_text,
                         "head_line": i + 1, "level": lvl}
        cipais.append(current_cipai)
        current_entry = None

    # 第二遍：按「词牌 → 体式」把谱行/注/标记归位（上面只建壳，这里填内容）
    name2cipai = {c["name"]: c for c in cipais}
    entry_by_head: dict[int, dict] = {}
    for pos, i in enumerate(idx_sorted):
        lvl, name, is_body = head_at[i]
        if not is_body:
            continue
        nxt = idx_sorted[pos + 1] if pos + 1 < len(idx_sorted) else len(lines)
        for e in entries:
            if e["raw_ref"]["line"] == i + 1:
                entry_by_head[i] = e
                break

    owner: dict[int, dict] = {}
    for pos, i in enumerate(idx_sorted):
        lvl, name, is_body = head_at[i]
        nxt = idx_sorted[pos + 1] if pos + 1 < len(idx_sorted) else len(lines)
        for ln in range(i + 1, nxt):
            owner[ln] = (-1 if not is_body else i)

    current_entry = None
    pat_total = 0
    stop_at = len(lines)
    for ln, raw in enumerate(lines):
        t = raw.strip()
        if t.startswith("### 致谢") or t.startswith("### 重校后记") or t == "索引":
            stop_at = ln
            break
    for ln, raw in enumerate(lines):
        if ln >= stop_at:
            break
        if ln in head_at:
            lvl, name, is_body = head_at[ln]
            if is_body:
                current_entry = entry_by_head.get(ln)
            else:
                current_entry = None
            continue
        if current_entry is None:
            continue
        s = raw.strip()
        if not s:
            continue
        if s.startswith("琴注") or s.startswith("注："):
            current_entry["editor_notes"].append(s)
            continue
        if s.startswith("【注】"):
            current_entry["notes"].append(s[3:].strip())
            continue
        if s.startswith("#"):
            continue
        if re.match(PAT_START, s) and re.search(r"[。，、]$", s):
            info = parse_pat(s)
            pat_total += 1
            if info is None:
                stray_pat.append({"entry": current_entry["id"], "line": ln + 1, "text": s})
                continue
            rec = {"i": len(current_entry["lines"]) + 1, "pian": 1,
                   "pat": info["pat"], "rhyme": info["rhyme"], "opt": info["opt"],
                   "punct": info["punct"], "zengyun": info["zengyun"],
                   "tone_hint": info["tone_hint"],
                   "raw": s, "raw_ref": {"file": REL_SRC, "line": ln + 1}}
            for _ in range(s.count("『")):
                current_entry["duizhang"].append({"line": rec["i"], "kind": "ju_pair"})
            for _ in range(s.count("〖")):
                current_entry["repeat"].append({"line": rec["i"], "kind": "dieju_or_die3"})
            current_entry["lines"].append(rec)
            continue
        if re.match(PAT_START, s) and re.search(r"[。，、]$", s):
            stray_pat.append({"entry": current_entry["id"], "line": ln + 1, "text": s})

    # ---- 统计与自检 ----
    for e in entries:
        e["char_total"] = sum(len(l["pat"]) for l in e["lines"])
        e["char_min"] = sum(len(l["pat"]) - len(l["opt"]) for l in e["lines"])
        # 说明段自报字数：多候选时就近取（噪声如「大一字」会被 char_total 排除）
        decl = e["declared"]
        cands = decl.get("candidates") or []
        if cands:
            best = min(cands, key=lambda kv: (abs(kv[1] - e["char_total"]), -kv[1]))
            decl["chars_raw"], decl["chars"] = best
            decl["picked_by"] = "nearest_to_pat_total"
        d = decl.get("chars")
        # C-16 字数自检只对「定格」成立：说明段的自报字数是该调正格字数，变体字数另计
        if not d or e["variant_kind"] != "main":
            e["declared_ok"] = None
        else:
            e["declared_ok"] = e["char_min"] <= d <= e["char_total"]
            if not e["declared_ok"]:
                e["declared_gap"] = e["char_total"] - d
    unique_names = sorted({c["name"] for c in cipais})
    alias_total = sum(len(c["aliases"]) for c in cipais)
    body_heads = sum(1 for _, _, _, b in classify if b)
    dingge = sum(1 for e in entries if e["variant"].startswith("定格"))
    doc = {
        "schema_version": "1.0",
        "source": {"book": "唐宋词格律（龙榆生）", "file": REL_SRC, "copyright": "公版",
                   "edition": "龙榆生先生纪念网站电子版",
                   "sha256": sha256_of(path), "extracted_at": "2026-11-20"},
        "meta": {
            "head_total": len(classify),
            "cipai_head": len(classify) - body_heads,
            "body_head": body_heads,
            "dingge_entries": dingge,
            "variant_heads": body_heads - dingge,
            "cipai_entries": len(cipais),
            "cipai_unique": len(unique_names),
            "alias_total": alias_total,
            "entry_count": len(entries),
            "pat_line_total": pat_total,
            "opt_lines": sum(1 for e in entries for l in e["lines"] if l["opt"]),
            "opt_total": sum(len(l["opt"]) for e in entries for l in e["lines"]),
            "duizhang_marks": sum(1 for e in entries for _ in e["duizhang"]),
            "repeat_marks": sum(1 for e in entries for _ in e["repeat"]),
            "zengyun_marks": sum(len(l["zengyun"]) for e in entries for l in e["lines"]),
            "declared_mismatch": sum(1 for e in entries if e["declared_ok"] is False),
            "declared_checked": sum(1 for e in entries if e["declared_ok"] is not None),
            "noise_dropped": len(noise_dropped), "stray_pat": len(stray_pat),
        },
        "cipai": cipais,
        "entries": entries,
    }
    dump_json(doc, os.path.join(out, "cipu_long.json"))
    dump_json({"schema_version": "1.0", "source_file": "cipu_long.json",
               "noise_dropped": noise_dropped, "stray_pat": stray_pat,
               "declared_mismatches": [
                   {"id": e["id"], "declared": e["declared"].get("chars"),
                    "pat_total": e["char_total"], "gap": e.get("declared_gap")}
                   for e in entries if e["declared_ok"] is False]},
              os.path.join(out, "cipu_long_dropped.json"))
    m = doc["meta"]
    sys.stdout.write("heads=%d = cipai %d + body %d | 定格 %d + 变体 %d = %d | cipai_entries %d unique %d alias %d\n" % (
        m["head_total"], m["cipai_head"], m["body_head"], m["dingge_entries"],
        m["variant_heads"], m["dingge_entries"] + m["variant_heads"],
        m["cipai_entries"], m["cipai_unique"], m["alias_total"]))
    sys.stdout.write("entries=%d pat_lines=%d opt_lines=%d opt_total=%d dz=%d repeat=%d zengyun=%d\n" % (
        m["entry_count"], m["pat_line_total"], m["opt_lines"], m["opt_total"],
        m["duizhang_marks"], m["repeat_marks"], m["zengyun_marks"]))
    sys.stdout.write("declared mismatch=%d (checked %d) noise=%d stray=%d\n" % (
        m["declared_mismatch"], m["declared_checked"], m["noise_dropped"], m["stray_pat"]))
    sys.stdout.write("fake-name check: %s\n" % [n for n in unique_names if BODY_NAME.match(n)])
    for s in stray_pat[:5]:
        sys.stdout.write("STRAY %s %s\n" % (s["line"], s["text"][:40]))
    sys.stdout.write("src=%s\n" % rel(path, root))


if __name__ == "__main__":
    main()
