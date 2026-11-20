"""补全平水韵表：以《词林正韵》小韵组为第二源，补「缺字」与「缺读」。

## 为什么要补

随包 `pingshui.json` 的源文件是「常用字表」，不是全字表：近体诗语料里 109 种字
（242 次）查不到，最频的 `何` 出现 47 次。缺字会让整句平仄判不了、韵脚判不了，
进而**级联**出假错（杜甫《登高》曾因此被判 12 处硬错）。缺读同理：`为` 只有平声一读，
王维/骆宾王用仄读时被判平仄错位。

## 口径（裁决书 §9.7 / §8.1 配对断言）

1. **原表不改一字**。`pingshui.json` 仍是 4281 字；补全单列 `supplement.json`，
   运行时由 `scripts/tonebook.py` 合并加载。两个数分别落盘，不得只写一个总数。
2. **每条补入都留痕**：字 / 韵部 / 来源（词林第 N 部 + 韵目）/ 括号注原文 /
   源文件字符偏移（可回溯到字节位置，不是只给个数）。
3. **缺读必须审查，不得盲补**。多读按「任一所读成立即通过」生效，错误的多读
   比缺读更危险（会直接放过违规）。四类不补：
     同韵目已有        —— 去重，不计拒
     同声调另见他韵    —— 只是韵部互见，不构成异读
     括号注为韵目互见  —— 「歌韵同」这类注是源书自己的互见标记，无义项证据
     繁简转写污染      —— 如 `征(角征)`，原文当作「角徵」（宫商角徵羽）
   被拒的进 `reading_suspect_queue.json`，并计入「被拒条数」。
"""
from __future__ import annotations

import json
import os
import re
import sys

from common import (cjk_only, dump_json, out_dir, read_text, sha256_of,
                    skill_data_dir, src_root)

SRC_CILIN = "六、工具书/词林正韵（戈载）.md"

# 声调：韵目 part → (平仄, 声调字)
PART_TONE = {"上平": ("平", "平"), "下平": ("平", "平"),
             "上": ("仄", "上"), "去": ("仄", "去"), "入": ("仄", "入")}

# 括号注为源书自己的「韵目互见」标记，不构成义项证据
META_NOTE_PAT = re.compile(r"^(.*韵同|.*韵通|.*韵收.*|音.*)$")

# 繁简转写污染：源书是简体转录本，少数括号注里的字在繁体是另一个字。
# 补入会凭空造出一个平仄读法，而多读按「任一成立即通过」会放过真正的违规。
POLLUTION_NOTES = {
    ("征", "角征"): ("徵", "「角徵」为五音（宫商角徵羽）之徵，繁体作「徵」，"
                           "简体转录时误作「征」；补仄读会让「征」凭空多一个上声读法"),
    ("发", "头发"): ("髮", "「头发」繁体作「頭髮」，与「發」同简作「发」，非同一字"),
}


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _samples_by_reason(rejected: list, per: int = 1) -> list:
    """每类拒绝理由各取若干条样例——只给「前 3 条」会被同一类占满，看不出审查维度。"""
    out, seen = [], {}
    for x in rejected:
        k = x["拒绝理由"]
        if seen.get(k, 0) >= per:
            continue
        seen[k] = seen.get(k, 0) + 1
        out.append(x)
    return out


def build_rhyme_index(ps: dict) -> dict:
    """韵目 id → (部名, 韵目名, 平仄, 声调)。"""
    out = {}
    for r in ps["rhymes"]:
        tone, tclass = PART_TONE[r["part"]]
        out[r["id"]] = (r["part"], r["name"], tone, tclass)
    return out


def locate_groups(srctext: str, xiaoyun: list) -> list[dict]:
    """把每个小韵组定位到源文件的字符区间。

    源文件恰好 115 个 `【…】` 头，与 `cilin.json` 的 115 个小韵组一一对应，
    故按出现顺序对齐，并逐组校验 raw_name 一致（不一致即报错，不静默错位）。
    组体结束点取「下一个 `【`」与「下一个声调标签」中先出现者（裁决书 R-10.1 第 2 条）。
    """
    heads = list(re.finditer(r"【[^】]*】", srctext))
    if len(heads) != len(xiaoyun):
        raise SystemExit("源文件组头数 %d 与小韵组数 %d 不一致，拒绝按顺序对齐"
                         % (len(heads), len(xiaoyun)))
    labels = list(re.finditer(r"(平声：|仄声：|入声：)", srctext))
    out = []
    for k, (m, g) in enumerate(zip(heads, xiaoyun)):
        raw = m.group(0)[1:-1]
        if raw != g["raw_name"]:
            raise SystemExit("第 %d 组对齐失败：源 %r ≠ JSON %r" % (k + 1, raw, g["raw_name"]))
        start = m.end()
        end = heads[k + 1].start() if k + 1 < len(heads) else len(srctext)
        nxt_label = next((x.start() for x in labels if x.start() > start), None)
        if nxt_label is not None and nxt_label < end:
            end = nxt_label
        out.append({"start": start, "end": end, "raw": raw,
                    "offset": m.start()})
    return out


def note_map(g: dict) -> dict:
    return {a: b for a, b in g.get("char_notes", [])}


def main(argv: list[str]) -> int:
    src = src_root(argv)
    out = out_dir(argv, skill_data_dir())
    ps = load_json(os.path.join(out, "pingshui.json"))
    cl = load_json(os.path.join(out, "cilin.json"))
    ts = load_json(os.path.join(out, "trad_simp.json"))
    t2s, alias = ts["t2s"], ts["alias"]
    ps_chars = ps["chars"]
    ridx = build_rhyme_index(ps)

    cl_path = os.path.join(src, SRC_CILIN)
    srctext = read_text(cl_path)
    spans = locate_groups(srctext, cl["xiaoyun"])

    def norm(c: str):
        if c in ps_chars:
            return c, "asis"
        if t2s.get(c) in ps_chars:
            return t2s[c], "t2s"
        if alias.get(c) in ps_chars:
            return alias[c], "alias"
        return None, "miss"

    added_chars: dict[str, list] = {}
    entries: list[dict] = []
    rejected: list[dict] = []
    already = 0
    unreadable = 0

    for g, span in zip(cl["xiaoyun"], spans):
        rf = g["rhyme_full"]
        if rf not in ridx:
            raise SystemExit("词林韵目 %r 在平水韵 106 韵目里找不到同名部" % rf)
        part, name, tone, tclass = ridx[rf]
        notes = note_map(g)
        body = srctext[span["start"]:span["end"]]
        for c in g["chars"]:
            if not cjk_only(c):
                continue
            off = span["start"] + body.find(c)
            if off < span["start"] or srctext[off:off + len(c)] != c:
                raise SystemExit("字 %r 在源文件组 %r 内定位失败，拒绝写不可回溯的补入" % (c, g["raw_name"]))
            ctx = srctext[max(span["start"], off - 12):off + len(c) + 12]
            note = notes.get(c)
            base = {"字": c, "韵部": rf, "平仄": tone, "声调": tclass,
                    "来源": "词林第%d部%s" % (g["part"], g["raw_name"]),
                    "部分": part, "源文件": SRC_CILIN, "字符偏移": off,
                    "上下文": ctx, "括号注": note}
            nc, how = norm(c)
            if nc is None:
                added_chars.setdefault(c, []).append([tone, rf, tclass, off])
                entries.append(dict(base, 类型="new_char",
                                    依据="平水韵表未收该字；词林【%s】字表含之" % g["raw_name"]))
                continue
            if nc != c:
                unreadable += 1
                continue                      # 繁体/异体：查询层归一已能命中，不新增字
            existing = ps_chars[nc]
            if rf in {t[1] for t in existing}:
                already += 1
                continue
            # 补读审查
            if (c, note) in POLLUTION_NOTES:
                trad, why = POLLUTION_NOTES[(c, note)]
                rejected.append(dict(base, 类型="extra_reading", 处置="不补",
                                     拒绝理由="繁简转写污染",
                                     说明="繁体当作「%s」；%s" % (trad, why)))
                continue
            if tone in {t[0] for t in existing}:
                rejected.append(dict(base, 类型="extra_reading", 处置="不补",
                                     拒绝理由="同声调另见他韵",
                                     说明="该字在平水韵已有%s声读法（%s）；"
                                          "词林再收于【%s】只是韵部互见，不构成异读"
                                          % (tone,
                                             "、".join(sorted({t[1] for t in existing if t[0] == tone})),
                                             g["raw_name"])))
                continue
            if note and META_NOTE_PAT.match(note):
                rejected.append(dict(base, 类型="extra_reading", 处置="不补",
                                     拒绝理由="括号注为韵目互见",
                                     说明="注文「%s」是源书的韵目互见标记，不是义项证据；"
                                          "无义项证据不支持新增异读" % note))
                continue
            entries.append(dict(base, 类型="extra_reading",
                                依据="平水韵该字现仅 %s；词林【%s】%s声收之%s"
                                     % ("、".join("%s%s" % (t[0], t[1]) for t in existing),
                                        g["raw_name"], tclass,
                                        "（注：%s）" % note if note else "")))
            added_chars[nc] = added_chars.get(nc, []) + [[tone, rf, tclass, off]]

    # 逐字汇总（供断言与人工复核）
    per_char = []
    for c in sorted(added_chars):
        recs = added_chars[c]
        before = [[t[0], t[1]] for t in ps_chars.get(c, [])]
        after = before + [[r[0], r[1]] for r in recs if [r[0], r[1]] not in before]
        per_char.append({"字": c, "类型": "new_char" if not before else "extra_reading",
                         "补前读法": before, "补后读法": after,
                         "新增条数": len(after) - len(before), "来源": recs and [r[1] for r in recs],
                         "首条偏移": recs[0][3]})

    new_char_n = sum(1 for e in entries if e["类型"] == "new_char")
    extra_n = len(entries) - new_char_n
    total_after = len(ps_chars) + len({e["字"] for e in entries
                                       if e["类型"] == "new_char"})

    # ── 缺读嫌疑清单：被拒条目 + 词林有而平水表全无的字（21 类）──
    missing_entirely = sorted({e["字"] for e in entries if e["类型"] == "new_char"})
    suspect = {
        "schema_version": "1.0",
        "purpose": "「缺读嫌疑清单」：判定出硬错时，若涉字在此清单内，需做反事实重判"
                   "（假设它取另一声调），不得据未知读音断言作者出律。",
        "rejected_count": len(rejected),
        "rejected": rejected,
        "rejected_samples": _samples_by_reason(rejected),
        "accepted_count": extra_n,
        "accepted": [e for e in entries if e["类型"] == "extra_reading"],
        "note": "被拒条数不得为 0；0 说明审查未执行（裁决书 §8.1 反向哨兵通则）。",
    }

    doc = {
        "schema_version": "1.0",
        "source": {"file": SRC_CILIN, "sha256": sha256_of(cl_path),
                   "edition": "戈载《词林正韵》（殆知阁中华古籍库整理本）",
                   "copyright": "清代作品，公有领域；仅取韵目字表，不收注文释义"},
        "policy": "pingshui.json 原表 4281 字不改一字；本文件为增量，运行时合并加载。"
                  "两数分别落盘：original_chars + added_chars = total_after。",
        "original_chars": len(ps_chars),
        "added_chars": len({e["字"] for e in entries if e["类型"] == "new_char"}),
        "added_readings": extra_n,
        "total_after": total_after,
        "already_present": already,
        "normalized_skip": unreadable,
        "rejected_count": len(rejected),
        "entries": entries,
        "per_char": per_char,
    }
    dump_json(doc, os.path.join(out, "supplement.json"))
    dump_json(suspect, os.path.join(out, "reading_suspect_queue.json"))
    sys.stdout.write("平水韵原表 %d 字；补字 %d；补读 %d；合计 %d\n"
                     % (len(ps_chars), doc["added_chars"], extra_n, total_after))
    sys.stdout.write("补入条目 %d（已存在同韵目 %d；繁简归一跳过 %d）；被拒 %d\n"
                     % (len(entries), already, unreadable, len(rejected)))
    reasons = {}
    for x in rejected:
        reasons[x["拒绝理由"]] = reasons.get(x["拒绝理由"], 0) + 1
    sys.stdout.write("被拒分布: %s\n" % dict(sorted(reasons.items())))
    for x in rejected[:3]:
        sys.stdout.write("  拒例：%s 拟补%s（%s）→ %s\n"
                         % (x["字"], x["韵部"], x["括号注"], x["拒绝理由"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
