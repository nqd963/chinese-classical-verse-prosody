"""导出 `variant_map.json`：A 源「带括号注体式头」与 B 源独立条目的配对表。

裁决书 §8.4 的硬要求：这 19 条（裁决书写 20，实测 19 —— 差 1 待稽）**必须人工裁定**，
禁止自动猜。自动配对的后果是造假冲突 → 触发 D13 降级 → 把一等伪造成二等，
与本项目要防的「二等伪装成一等」同属诚信失守。

本脚本的做法：
  - 配对表 `DECIDED` 由实现者逐条裁定并写明依据（`decided_by` + `basis`）；
  - 未列入 `DECIDED` 的，一律 `paired: null`，理由写明（绝大多数是「体式描述，非别名词牌名」）；
  - 前置校验：被配对的两侧名称必须满足裁定依据（`exact_name` 须逐字相等），
    校验不通过即整脚本报错退出（退出码 2），不允许静默降级。
"""
from __future__ import annotations

import json
import os
import re
import sys

from common import dump_json, out_dir, sha256_of, skill_data_dir, src_root

REL_LONG = "三、填词专修/唐宋词格律（龙榆生）.md"
REL_WANG = "二、格律入门/诗词格律（王力）.md"

# ── 人工裁定表 ───────────────────────────────────────────────
# key = "A源词牌/体式标签"
# paired = B 源词牌名；basis_class 决定前置校验方式
DECIDED = {
    "浣溪沙/格二（摊破浣溪沙）": {
        "paired": "摊破浣溪沙", "basis_class": "exact_name",
        "basis": "括号注名「摊破浣溪沙」与 B 源独立条目名逐字相等；两源字数同为 42 字、同为双调",
        "decided_by": "柯德健（实现方人工裁定，2026-11-20）",
    },
    "木兰花/格三（减字木兰花）": {
        "paired": "减字木兰花", "basis_class": "exact_name",
        "basis": "括号注名「减字木兰花」与 B 源独立条目名逐字相等；两源同为 44 字双调",
        "decided_by": "柯德健（实现方人工裁定，2026-11-20）",
    },
}

# 明确「不配对」的裁定（写明为什么不能配）
REFUSED = {
    "千秋岁/格二（千秋岁引）": "B 源仅收《千秋岁》正格，未收《千秋岁引》别体；两调字数不同，"
                            "强行配对会造出假冲突并把《千秋岁》降级",
    "烛影摇红/格一（忆故人）": "B 源有《桃源忆故人》，但与《忆故人》字数、句读均不同，非同一调；"
                            "按名近配对属猜，不采纳",
    "木兰花/格五（木兰花慢）": "B 源未收《木兰花慢》，无对应独立条目",
    "木兰花/格四（偷声木兰花）": "B 源未收《偷声木兰花》，无对应独立条目",
    "踏莎行/格二（转调踏莎行）": "B 源仅收《踏莎行》正格；《转调踏莎行》为转调别体，配过去会造成假冲突",
}

DESC_WORDS = ("七言绝句式", "双调小令", "商调慢曲", "慢调", "添字", "仙吕调慢曲",
              "入声韵", "平仄韵互叶", "平仄韵换叶", "慢曲", "仄韵换韵格", "仄韵定格")


def main(argv: list[str]) -> int:
    src = src_root(argv)
    out = out_dir(argv, skill_data_dir())
    long_doc = json.load(open(os.path.join(out, "cipu_long.json"), encoding="utf-8"))
    wang_doc = json.load(open(os.path.join(out, "cipu_wang.json"), encoding="utf-8"))
    b_names = {e["name"] for e in wang_doc["entries"]}
    b_by_name: dict[str, list[dict]] = {}
    for e in wang_doc["entries"]:
        b_by_name.setdefault(e["name"], []).append(e)

    heads = [e for e in long_doc["entries"]
             if e["variant_kind"] != "main" and re.search(r"[（(][^）)]*[）)]", e["variant"])]
    rows = []
    for e in heads:
        inner = re.search(r"[（(]([^）)]*)[）)]", e["variant"]).group(1)
        key = "%s/%s" % (e["name"], e["variant"])
        row = {"key": key, "cipai": e["name"], "variant": e["variant"],
               "paren": inner, "a_id": e["id"], "a_chars": e["char_total"],
               "paired": None, "b_ids": [], "b_chars": [], "decided_by": "未裁定",
               "basis": "", "basis_class": None,
               "consequence": "不参与 A/B 比对，不产生冲突，不给降级"}
        if key in DECIDED:
            d = DECIDED[key]
            row["paired"] = d["paired"]
            row["basis_class"] = d["basis_class"]
            row["basis"] = d["basis"]
            row["decided_by"] = d["decided_by"]
            row["consequence"] = "参与 A/B 归一比对；有冲突才降级"
        elif key in REFUSED:
            row["decided_by"] = "柯德健（实现方人工裁定，2026-11-20）"
            row["basis"] = "拒绝配对：" + REFUSED[key]
            row["consequence"] = "不参与 A/B 比对，不产生冲突，不给降级"
        elif inner in DESC_WORDS:
            row["basis"] = "括号注为体式描述（分片/字数/用韵/宫调），非别名词牌名，B 源无对应独立条目"
            row["consequence"] = "不参与 A/B 比对，不产生冲突，不给降级"
        else:
            row["basis"] = "括号注名在 B 源无同名条目，且无充分依据认定同调"
            row["consequence"] = "不参与 A/B 比对，不产生冲突，不给降级"
        if row["paired"]:
            row["b_ids"] = [x["id"] for x in b_by_name.get(row["paired"], [])]
            row["b_chars"] = [x["char_count"] for x in b_by_name.get(row["paired"], [])]
        rows.append(row)

    # 前置校验：exact_name 类配对必须逐字相等，否则整脚本报错（不静默降级）
    problems = []
    for r in rows:
        if r["basis_class"] == "exact_name" and r["paired"] != r["paren"]:
            problems.append("exact_name 配对名称不等：%s vs %s" % (r["paired"], r["paren"]))
        if r["paired"] and r["paired"] not in b_names:
            problems.append("配对目标在 B 源不存在：%s（来自 %s）" % (r["paired"], r["key"]))
        if r["paired"] and len(r["b_ids"]) > 1:
            problems.append("配对目标在 B 源有多条同名条目，需再裁定：%s" % r["paired"])
    if problems:
        for p in problems:
            sys.stderr.write("断言失败：%s\n" % p)
        return 2

    # 未裁定的 auto 来源一律记 0 —— 本条用于 A43 的口径自证
    auto_pairs = sum(1 for r in rows if r["decided_by"] == "auto")
    doc = {
        "schema_version": "1.0",
        "source": {"a_file": REL_LONG, "b_file": REL_WANG,
                   "a_sha256": sha256_of(os.path.join(src, REL_LONG)),
                   "b_sha256": sha256_of(os.path.join(src, REL_WANG))},
        "meta": {
            "heads_with_paren": len(rows),
            "paired": sum(1 for r in rows if r["paired"]),
            "refused": sum(1 for r in rows if r["decided_by"].startswith("柯德健") and not r["paired"]),
            "unpaired_by_rule": sum(1 for r in rows if not r["paired"]
                                    and not r["decided_by"].startswith("柯德健")),
            "auto_pairs": auto_pairs,
            "裁决书口径": 20,
            "实测": len(rows),
            "差": 20 - len(rows),
            "policy": "禁止 auto 配对；auto_pairs 必须为 0，否则相关词牌降二等",
        },
        "rows": rows,
    }
    dump_json(doc, os.path.join(out, "variant_map.json"))
    m = doc["meta"]
    sys.stdout.write("括号注体式头 %d（裁决书 20，差 %d）｜已裁定配对 %d｜人工拒绝配对 %d｜按规则不配对 %d｜auto %d\n" % (
        m["heads_with_paren"], m["差"], m["paired"], m["refused"], m["unpaired_by_rule"], auto_pairs))
    for r in rows:
        if r["paired"]:
            sys.stdout.write("  配对 %s → B:%s（%s）\n" % (r["key"], ",".join(r["b_ids"]), r["basis_class"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
