"""A/B 双源归一比对 → `conflicts.json`，并把等级回填进 `cipai_index.json`。

三条前置（缺一条比对即无效，裁决书 §6.3 / §6.4 / §8.4）：
  1. **先归一，再比对**。统一表示为每字位 ∈ {定平 P, 定仄 Z, 允平允仄 *}，外加韵位掩码。
     - A 源：`平`→P，`仄`→Z，`中`→`*`；`去/上/入` 声调提示 → Z（精确声调另存 `tone_hint`）
     - B 源：`－`→P，`｜`→Z，`＋`→`*`
     - A 源 `『』`（对仗标记）、`〖〗`（叠句标记）**不是平仄语义**，归一时已剥离并单存结构标记；
       B 源无对仗标记属**体例缺失，不是冲突**。
  2. **片界未定不算冲突**。两源分片口径不同（B 以 ‖ 分段），片界只记录差异，不判冲突。
  3. **配对须经人工裁定**。读 `variant_map.json`，`paired` 为空的一律不比对；
     `auto` 来源视为未裁定（本实现 auto 数恒为 0）。

等级（D13，禁止「择一为准」）：
  一等 = A 源有谱，且与 B 源归一后无冲突（B 未收该调不构成冲突）
  二等 = 归一后发现冲突，且未人工裁定 → 降级，冲突逐条公开
"""
from __future__ import annotations

import json
import os
import sys

from common import dump_json, out_dir, sha256_of, skill_data_dir, src_root

REL_LONG = "三、填词专修/唐宋词格律（龙榆生）.md"
REL_WANG = "二、格律入门/诗词格律（王力）.md"
MAX_POS_REPORT = 12


def flat(entry: dict) -> tuple[str, list[int]]:
    """把 A 源谱行拼成单串 + 全局韵位下标。"""
    pat, rhyme = [], []
    for ln in entry["lines"]:
        base = len(pat)
        pat.append(ln["pat"])
        # A 源 rhyme 是等长 0/1 掩码（不是下标集合）——按下标读会把每个 0 当成第 0 字
        rhyme.extend(base + i for i, m in enumerate(ln["rhyme"]) if m)
    return "".join(pat), sorted(rhyme)


def flat_b(entry: dict) -> tuple[str, list[int]]:
    pat, rhyme = [], []
    for ln in entry["lines"]:
        base = len(pat)
        pat.append(ln["pat"])
        rhyme.extend(base + i for i, m in enumerate(ln["rhyme"]) if m)
    return "".join(pat), sorted(rhyme)


def compare(a_pat: str, a_rhyme: list[int], b_pat: str, b_rhyme: list[int]) -> list[dict]:
    out = []
    if len(a_pat) != len(b_pat):
        out.append({"kind": "字数不一致",
                    "位置": None,
                    "A 源判定": "%d 字" % len(a_pat),
                    "B 源判定": "%d 字" % len(b_pat),
                    "处置": "降级（D13：不择一为准）",
                    "裁定人": "未裁定"})
        return out
    pos = []
    for i, (x, y) in enumerate(zip(a_pat, b_pat)):
        if x == "*" or y == "*":
            continue
        if x != y:
            pos.append((i, x, y))
    for i, x, y in pos[:MAX_POS_REPORT]:
        out.append({"kind": "平仄不一致",
                    "位置": {"序": i + 1, "字位": "第 %d 字" % (i + 1)},
                    "A 源判定": {"symbol": x, "义": "定平" if x == "P" else "定仄"},
                    "B 源判定": {"symbol": y, "义": "定平" if y == "P" else "定仄"},
                    "处置": "降级（D13：不择一为准）",
                    "裁定人": "未裁定"})
    if len(pos) > MAX_POS_REPORT:
        out.append({"kind": "平仄不一致（余略）", "位置": None,
                    "A 源判定": "共 %d 处" % len(pos), "B 源判定": "",
                    "处置": "降级", "裁定人": "未裁定"})
    if len(a_rhyme) != len(b_rhyme):
        out.append({"kind": "韵位数不一致", "位置": None,
                    "A 源判定": "%d 韵" % len(a_rhyme),
                    "B 源判定": "%d 韵" % len(b_rhyme),
                    "处置": "降级（韵位属归一表示的一部分）",
                    "裁定人": "未裁定"})
    return out


def main(argv: list[str]) -> int:
    src = src_root(argv)
    out = out_dir(argv, skill_data_dir())
    long_doc = json.load(open(os.path.join(out, "cipu_long.json"), encoding="utf-8"))
    wang_doc = json.load(open(os.path.join(out, "cipu_wang.json"), encoding="utf-8"))
    vmap = json.load(open(os.path.join(out, "variant_map.json"), encoding="utf-8"))
    index_path = os.path.join(out, "cipai_index.json")
    index = json.load(open(index_path, encoding="utf-8"))

    b_by_name: dict[str, list[dict]] = {}
    for e in wang_doc["entries"]:
        b_by_name.setdefault(e["name"], []).append(e)

    # A 源正格：优先 【定格】；源未标定格时取该调首个体式头（并注明）
    a_main: dict[str, dict] = {}
    for e in long_doc["entries"]:
        if e["variant_kind"] == "main":
            a_main.setdefault(e["name"], e)
    for e in long_doc["entries"]:
        a_main.setdefault(e["name"], e)

    rows = []
    stats = {"两源同名": 0, "可比对": 0, "字数冲突": 0, "平仄冲突": 0,
             "韵位冲突": 0, "无冲突": 0, "B 未收": 0, "B 多条目跳过": 0,
             "体式不对应": 0}
    pair_keys = {"%s/%s" % (r["cipai"], r["variant"]) for r in vmap["rows"] if r["paired"]}
    a_variants: dict[str, list[dict]] = {}
    for e in long_doc["entries"]:
        a_variants.setdefault(e["name"], []).append(e)

    for c in index["cipai"]:
        name = c["name"]
        ae = a_main.get(name)
        bs = b_by_name.get(name, [])
        grade, basis, detail = "一等", "", []
        if not ae:
            grade, basis = "二等", "A 源无谱（仅清单登记），不得作为机器校验依据"
        elif not bs:
            stats["B 未收"] += 1
            basis = "A 源有谱；B 源未收该调，无从比对，不构成冲突"
        else:
            stats["两源同名"] += 1
            if len(bs) > 1:
                stats["B 多条目跳过"] += 1
                grade = "二等"
                basis = "B 源有同名多条条目（%d 条），体式对应关系未裁定，暂降级" % len(bs)
            else:
                stats["可比对"] += 1
                b_pat, b_rh = flat_b(bs[0])
                # 先按字数在所有 A 体式里找候选：B 收的可能是该调另一体，不是冲突
                cands = []
                for ae2 in a_variants[name]:
                    a_pat, a_rh = flat(ae2)
                    if len(a_pat) != len(b_pat):
                        continue
                    score = sum(1 for x, y in zip(a_pat, b_pat)
                                if x != "*" and y != "*" and x != y)
                    cands.append((score, ae2, a_pat, a_rh))
                cands.sort(key=lambda t: (t[0], t[1]["variant_kind"] != "main", t[1]["id"]))
                if not cands:
                    stats["字数冲突"] += 1
                    grade = "二等"
                    basis = ("B 源 %d 字，A 源该调 %d 个体式字数均不符（%s）→ 降级（D13）"
                             % (len(b_pat), name and len(a_variants[name]),
                                "/".join(str(e["char_total"]) for e in a_variants[name])))
                    detail.append({"kind": "字数不一致", "词牌": name, "体式": "定格",
                                   "位置": None,
                                   "A 源判定": "/".join(e["variant"] + " " + str(e["char_total"])
                                                     for e in a_variants[name]),
                                   "B 源判定": "%d 字（%s）" % (len(b_pat), bs[0]["id"]),
                                   "处置": "降级（D13：不择一为准）", "裁定人": "未裁定"})
                else:
                    score, best, a_pat, a_rh = cands[0]
                    if score == 0 and best["variant_kind"] == "main":
                        same_rhyme = len(a_rh) == len(b_rh)
                        if same_rhyme:
                            stats["无冲突"] += 1
                            basis = ("归一比对无平仄/字数/韵位冲突（A:%s ↔ B:%s）"
                                     % (best["id"], bs[0]["id"]))
                        else:
                            stats["韵位冲突"] += 1
                            grade = "二等"
                            basis = "平仄一致但韵位数 %d≠%d → 降级" % (len(a_rh), len(b_rh))
                            detail.append({"kind": "韵位数不一致", "词牌": name, "体式": best["variant"],
                                           "位置": None, "A 源判定": "%d 韵" % len(a_rh),
                                           "B 源判定": "%d 韵" % len(b_rh),
                                           "处置": "降级", "裁定人": "未裁定"})
                    elif score == 0 and best["variant_kind"] != "main":
                        # B 收的是该调另一体，与 A 定格不同 → 体式不对应，**不是冲突**
                        stats["体式不对应"] += 1
                        basis = ("B 源收的是该调《%s》（%d 字），与 A 源《定格》不同体，"
                                 "不构成平仄冲突" % (best["variant"], best["char_total"]))
                    else:
                        stats["平仄冲突"] += 1
                        grade = "二等"
                        diff = compare(a_pat, a_rh, b_pat, b_rh)
                        basis = ("归一比对发现 %d 处平仄差异（最接近体式《%s》），未裁定 → 降级（D13）"
                                 % (score, best["variant"]))
                        for d in diff:
                            detail.append(dict(d, 词牌=name, 体式=best["variant"],
                                               A源体式=best["variant"], B源条目=bs[0]["id"]))
                        if any(d["kind"] == "韵位数不一致" for d in diff):
                            stats["韵位冲突"] += 1
        c["grade"] = grade
        c["basis"] = basis
        if detail:
            c["conflicts"] = detail
        rows.extend(detail)

    # 括号注体式的配对条目也参与比对（仅已裁定配对者）
    for r in vmap["rows"]:
        if not r["paired"]:
            continue
        key = "%s/%s" % (r["cipai"], r["variant"])
        if key not in pair_keys:
            continue
        ae = next((e for e in long_doc["entries"] if e["id"] == r["a_id"]), None)
        bs = b_by_name.get(r["paired"], [])
        if not ae or not bs:
            continue
        a_pat, a_rh = flat(ae)
        b_pat, b_rh = flat_b(bs[0])
        diff = compare(a_pat, a_rh, b_pat, b_rh)
        r["compare"] = {"diff_count": len(diff),
                        "result": "无冲突" if not diff else "有冲突（降该词牌二等）"}
        for d in diff:
            rows.append(dict(d, 词牌=r["cipai"], 体式=r["variant"],
                             A源体式=ae["variant"], B源条目=bs[0]["id"]))

    # 配对条目有冲突 → 相应词牌降级
    conflicted = {x["词牌"] for x in rows}
    for c in index["cipai"]:
        if c["name"] in conflicted and c["grade"] == "一等":
            c["grade"] = "二等"
            c["basis"] = (c["basis"] + "；另：已裁定的括号注体式与 B 源比对不一致") if c["basis"] else \
                "已裁定的括号注体式与 B 源比对不一致"

    for c in index["cipai"]:
        c.setdefault("conflicts", [])

    dump_json(index, index_path)
    doc = {
        "schema_version": "1.0",
        "source": {"a_file": REL_LONG, "b_file": REL_WANG,
                   "a_sha256": sha256_of(os.path.join(src, REL_LONG)),
                   "b_sha256": sha256_of(os.path.join(src, REL_WANG))},
        "normalization": {
            "A 源": {"平": "P", "仄": "Z", "中": "*", "去/上/入": "Z（精确声调另存 tone_hint）"},
            "B 源": {"－": "P", "｜": "Z", "＋": "*"},
            "剥离标记": ["『』（A 源对仗）", "〖〗（A 源叠句）"],
            "不计冲突项": ["片界（两源分片口径不同，B 以 ‖ 分段）",
                          "B 源无对仗标记（体例缺失，不是冲突）"],
            "说明": "比对前必须归一；直接拿原始符号比出的差异一律无效（裁决书 §6.3）",
        },
        "meta": dict(stats, conflict_rows=len(rows),
                     grade1=sum(1 for c in index["cipai"] if c["grade"] == "一等"),
                     grade2=sum(1 for c in index["cipai"] if c["grade"] == "二等"),
                     cipai_total=len(index["cipai"])),
        "rows": rows,
        "policy": "D13：冲突即降级，禁止择一为准；冲突行必须可查（词牌/体式/位置/A/B/处置/裁定人）",
    }
    dump_json(doc, os.path.join(out, "conflicts.json"))
    m = doc["meta"]
    sys.stdout.write("A/B 归一比对：两源同名 %d｜可比对 %d｜无冲突 %d｜冲突行 %d\n" % (
        m["两源同名"], m["可比对"], m["无冲突"], m["conflict_rows"]))
    sys.stdout.write("冲突分类：字数 %d｜平仄 %d｜韵位 %d｜体式不对应(非冲突) %d｜B 未收 %d｜B 多条目跳过 %d\n" % (
        m["字数冲突"], m["平仄冲突"], m["韵位冲突"], m["体式不对应"],
        m["B 未收"], m["B 多条目跳过"]))
    sys.stdout.write("等级：一等 %d｜二等 %d｜合计 %d\n" % (m["grade1"], m["grade2"], m["cipai_total"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
