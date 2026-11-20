"""D1 平水韵（刘渊） → data/pingshui.json

抽取规则编号对应《数据抽取规格》§1.2 R-01..R-08、§1.3 C-01..C-07。
裁决书 §7.3 覆盖 C-06：去声「四寘」尾部串入的「二近始术瑟德」中，
术/瑟/德（入声）、始（仅上声）、近（不属四寘）一律**从四寘剔除**并登记
suspect_queue.json；「二」为去声四寘正字，保留。

用法：python tools/extract_pingshui.py --src <语料根> [--out <目录>]
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (cn_num, dump_json, is_cjk, out_dir, read_lines, rel,
                    sha256_of, skill_data_dir, src_root)

REL_SRC = "六、工具书/平水韵（刘渊）.md"

# R-02 韵目字表（106 部全部韵目名的末字，另含源文件异写「琰」）
RHYME_HEADS = (
    "东冬江支微鱼虞齐佳灰真文元寒删先萧肴豪歌麻阳庚青蒸尤侵覃盐咸"
    "董肿讲纸尾语麌荠蟹贿轸吻阮旱潸铣筱巧皓哿马养梗迥有寝感俭豏琰"
    "送宋绛寘未御遇霁泰卦队震问愿翰谏霰啸效号个祃漾敬径宥沁勘艳陷"
    "屋沃觉质物月曷黠屑药陌锡职缉合叶洽"
)

SECTIONS = [("上平声", "上平", 15), ("下平声", "下平", 15),
            ("上声", "上", 29), ("去声", "去", 30), ("入声", "入", 17)]

# C-05 韵目别名映射（源文件异写 → 标准韵目名）。来源：R-06 连续性校验失败项。
ALIAS = {"上声二十八琰": "上声二十八俭"}

# 裁决书 §7.3：污染字剔除表（韵目全称 → (原始簇, 待剔除字)）
POLLUTED = {"去声四寘": ("二近始术瑟德", "术瑟德始近")}

PREFIX = {"上平": "上平", "下平": "下平", "上": "上声", "去": "去声", "入": "入声"}

TONE_OF_PART = {"上平": ("平", "平"), "下平": ("平", "平"),
                "上": ("仄", "上"), "去": ("仄", "去"), "入": ("仄", "入")}


def split_section(line: str) -> list[tuple[str, str, str]]:
    """R-02/R-03/R-04：按韵目切分，返回 [(数字串, 韵目末字, 该部字串)]。"""
    look = re.compile(r"(?<![一二三四五六七八九十])"
                      r"(?=([一二三四五六七八九十]{1,3})(" + "|".join(RHYME_HEADS) + r")，)")
    marks = [(m.start(), m.group(1), m.group(2)) for m in look.finditer(line)]
    out = []
    for i, (pos, num, name) in enumerate(marks):
        head_len = len(num) + len(name) + 1  # 序号 + 韵目末字 + 全角逗号
        end = marks[i + 1][0] if i + 1 < len(marks) else len(line)
        out.append((num, name, line[pos + head_len:end]))
    return out


def main() -> None:
    root = src_root(sys.argv)
    out = out_dir(sys.argv, skill_data_dir())
    path = os.path.join(root, REL_SRC)
    lines = read_lines(path)

    rhymes = []
    char_raw: dict[str, list[tuple]] = {}
    rank = 0
    raw_entries = 0
    dup_dropped: list[dict] = []
    non_cjk: list[dict] = []
    suspect: list[dict] = []
    problems: list[str] = []

    pending = None  # (part, limit)：`## 【X声】` 标题行之后的首个非空行才是韵目数据行
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("说明："):      # C-01 截断
            break
        m = re.match(r"^## 【(.+?)】$", stripped)
        if m:
            title = m.group(1)
            part = next((p for t, p, _ in SECTIONS if t == title), None)
            pending = None if part is None else (part, {"上平": 15, "下平": 15, "上": 29,
                                                        "去": 30, "入": 17}[part])
            continue
        if pending is None or not stripped:
            continue
        part, limit = pending
        pending = None
        segs = split_section(line)
        if len(segs) != limit:
            problems.append("%s 节切出 %d 部（应为 %d）" % (part, len(segs), limit))
        ordinals = [cn_num(num) for num, _, _ in segs]
        if ordinals != list(range(1, limit + 1)):
            missing = [n for n in range(1, limit + 1) if n not in ordinals]
            problems.append("%s 节序数不连续，缺 %s" % (part, missing))

        for num, name, body in segs:
            full = ALIAS.get(PREFIX[part] + num + name, PREFIX[part] + num + name)
            body = body.rstrip("　 \t")
            race: list[str] = []
            for ch in body:
                if is_cjk(ch):
                    race.append(ch)
                elif ch in "，、【】# \t　":
                    continue
                else:                          # C-04 非 CJK 计数（须为 0）
                    non_cjk.append({"rhyme": full, "char": ch,
                                    "codepoint": "U+%04X" % ord(ch)})
            drop = POLLUTED.get(full, ("", ""))[1]
            dropped_here = [c for c in race if c in drop]
            kept = [c for c in race if c not in drop]
            if dropped_here:
                suspect.append({
                    "kind": "polluted_cluster",
                    "rhyme": full,
                    "raw_cluster": POLLUTED[full][0],
                    "removed": dropped_here,
                    "kept": [c for c in POLLUTED[full][0] if c not in dropped_here],
                    "source_line": lineno,
                    "action": "从该韵部剔除（裁决书 §7.3），其余字符保留",
                    "reason": "术/瑟/德属入声、始仅上声、近不属去声四寘；多读判定「任一读成立即通过」会放过本应判错的违规",
                })
            seen = set()
            final = []
            for ch in kept:
                if ch in seen:                 # R-08 同部重复去重并计数
                    dup_dropped.append({"rhyme": full, "char": ch})
                    continue
                seen.add(ch)
                final.append(ch)
            rhymes.append({"id": full, "part": part, "ordinal": cn_num(num),
                           "name": num + name, "alias": [], "chars": "".join(final),
                           "char_count": len(final)})
            tone, tone_class = TONE_OF_PART[part]
            for ch in final:
                raw_entries += 1
                rank += 1
                char_raw.setdefault(ch, []).append((rank, tone, full, tone_class))

    chars = {}
    for ch, reads in char_raw.items():
        reads.sort()
        chars[ch] = [[tone, rhyme, tclass, rk] for rk, tone, rhyme, tclass in reads]

    structure = {p: sum(1 for r in rhymes if r["part"] == p) for p in ("上平", "下平", "上", "去", "入")}

    doc = {
        "schema_version": "1.0",
        "source": {"book": "平水韵（刘渊，旧题宋）", "file": REL_SRC,
                   "copyright": "公版",
                   "edition": "清风网《月沐学堂·格律诗写作入门》第七讲整理本",
                   "sha256": sha256_of(path), "extracted_at": "2026-11-20"},
        "meta": {"rhyme_count": len(rhymes), "structure": structure,
                 "unique_chars": len(chars), "raw_entries": raw_entries,
                 "dedup_policy": "按字去重，多读保留全部读法；同字同部重复只留首次",
                 "dup_dropped": len(dup_dropped), "non_cjk_hits": len(non_cjk),
                 "suspect_count": len(suspect),
                 "part_tone": {p: v[0] for p, v in TONE_OF_PART.items()},
                 "tone_class_rule": "上平/下平→平；上→上；去→去；入→入（平仄由 part 派生）"},
        "problems": problems,
        "rhymes": rhymes,
        "chars": chars,
    }
    dump_json(doc, os.path.join(out, "pingshui.json"))
    dump_json({"schema_version": "1.0", "source_file": "pingshui.json",
               "non_cjk_hits": non_cjk, "dup_dropped": dup_dropped},
              os.path.join(out, "pingshui_dropped.json"))
    dump_json({"schema_version": "1.0", "source_file": "pingshui.json",
               "items": suspect}, os.path.join(out, "suspect_queue.json"))

    sys.stdout.write("rhymes=%d structure=%s unique=%d raw=%d noncjk=%d dup=%d suspect=%d\n" % (
        len(rhymes), structure, len(chars), raw_entries, len(non_cjk),
        len(dup_dropped), len(suspect)))
    for p in problems:
        sys.stdout.write("PROBLEM %s\n" % p)
    ids = {r["id"] for r in rhymes}
    for need in ("上声二十八俭", "去声二十一个", "上声二十一马", "去声四寘", "入声十七洽"):
        sys.stdout.write("assert %s -> %s\n" % (need, need in ids))
    sys.stdout.write("风 -> %s\n" % chars.get("风"))
    sys.stdout.write("src=%s\n" % rel(path, root))


if __name__ == "__main__":
    main()
