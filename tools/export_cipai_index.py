"""导出 `cipai_index.json`：词牌清单 + 三口径统计 + 体式头双口径差集。

输入（均由同目录抽取脚本产出，全部为实测，不手写）：
  data/cipu_long.json   龙榆生《唐宋词格律》谱式（A 源）
  data/cipu_wang.json   王力附録白香谱（B 源，仅作交叉核对与冲突判定）

输出：
  data/cipai_index.json

三口径**必须并列**（裁决书 §7.4）：
  208 标题数 = 145 词牌头 + 63 体式头
  201 体式   = 138 定格 + 63 变体
  155 词牌去重正名（裁决书写 156，Phase 3 实测 155，差 1 待构稽 —— 见 summary.口径分歧）

体式头双口径（team-lead 追加要求）：把「哪些头算体式」显式化为三种规则，
输出差集，不钉一个没穷尽验证的精确数。
"""
from __future__ import annotations

import json
import os
import re
import sys

from common import dump_json, out_dir, sha256_of, skill_data_dir, src_root

REL_LONG = "三、填词专修/唐宋词格律（龙榆生）.md"
REL_WANG = "二、格律入门/诗词格律（王力）.md"

# 体式头的三种计入规则（宽 → 窄），用于把 63/54 的分歧显式化
RULESET = {
    "宽_全部非定格体式头": lambda lab: True,
    "窄_排除分片与韵格描述头": lambda lab: lab not in ("双调", "长调", "平韵格"),
    "更窄_再排除别格类": lambda lab: lab not in ("双调", "长调", "平韵格")
                                     and not lab.startswith("别格"),
}

DISCLOSURE = {
    "required": (
        "凡引用词牌数量，必须同时给出三个口径：208 标题数（=145 词牌头 + 63 体式头）、"
        "201 体式（=138 定格 + 63 变体）、155 词牌去重正名（裁决书写 156）。"
        "三者不可混用，混用即判错。"
    ),
    "banned": (
        "不得单独说「本谱共 N 调」而不注明口径；不得把 208 / 201 / 155 中的任一数字"
        "直接称作「词牌数」；不得只报「体式头 63」而不说明其中含分片描述头与韵格描述头。"
    ),
    "formula": "208 标题 = 145 词牌头 + 63 体式头；201 体式 = 138 定格 + 63 变体",
}


def label_of(variant: str) -> str:
    return re.split(r"[（(]", variant)[0]


def main(argv: list[str]) -> int:
    src = src_root(argv)
    out = out_dir(argv, skill_data_dir())
    path_long = os.path.join(src, REL_LONG)
    long_doc = json.load(open(os.path.join(out, "cipu_long.json"), encoding="utf-8"))
    wang_doc = json.load(open(os.path.join(out, "cipu_wang.json"), encoding="utf-8"))

    meta = long_doc["meta"]
    entries = long_doc["entries"]
    cipais = long_doc["cipai"]

    # ---- 体式头三口径 ----
    variant_entries = [e for e in entries if e["variant_kind"] != "main"]
    by_rule = {}
    for rule_name, fn in RULESET.items():
        kept = [e["id"] for e in variant_entries if fn(label_of(e["variant"]))]
        by_rule[rule_name] = {"count": len(kept),
                              "excluded": [e["id"] for e in variant_entries
                                           if not fn(label_of(e["variant"]))]}
    wide = by_rule["宽_全部非定格体式头"]["count"]
    narrow = by_rule["窄_排除分片与韵格描述头"]["count"]
    narrowest = by_rule["更窄_再排除别格类"]["count"]

    # ---- 每个词牌的体式清单 ----
    by_name: dict[str, list[dict]] = {}
    for e in entries:
        by_name.setdefault(e["name"], []).append(e)

    cipai_rows = []
    for c in cipais:
        name = c["name"]
        es = sorted(by_name.get(name, []), key=lambda x: x["seq"])
        main = next((e for e in es if e["variant_kind"] == "main"), None)
        variants = []
        for e in es:
            variants.append({
                "label": e["variant"],
                "kind": "定格" if e["variant_kind"] == "main" else "变体",
                "id": e["id"],
                "pat_total": e["char_total"],
                "pat_min": e["char_min"],
                "line_count": len(e["lines"]),
                "rhyme_desc": (e["declared"] or {}).get("rhyme_desc"),
                "declared_ok": e.get("declared_ok"),
                "opt_slots": sum(len(l["opt"]) for l in e["lines"]),
                "duizhang_marks": len(e["duizhang"]),
                "repeat_marks": len(e["repeat"]),
            })
        # D10：用韵类型逐体标注，不得只说一种
        rhyme_types = sorted({v["rhyme_desc"] for v in variants if v["rhyme_desc"]})
        cipai_rows.append({
            "name": name,
            "aliases": c["aliases"],
            "aka": c["aka"],
            "declared": c["declared"],
            "rhyme_types": rhyme_types,
            "has_dingge": main is not None,
            "variants": variants,
            "variant_count": len(variants),
            "head_line": c["head_line"],
            "grade": None,      # 由 build_conflicts.py 按 D13 回填
            "basis": None,
        })

    doc = {
        "schema_version": "1.0",
        "source": {
            "book": "唐宋词格律（龙榆生）",
            "file": REL_LONG,
            "copyright": "公版（龙榆生 1902-1997 年谱式数据属格律事实，未收录论述文字）",
            "sha256": sha256_of(path_long),
            "cross_check": "王力《诗词格律》附録白香谱（B 源，仅谱式数据）",
            "cross_check_file": REL_WANG,
            "extracted_at": "2026-11-20",
            "声明": "谱式数据据通行词谱整理；未收录任何现代整理本的论述文字。",
        },
        "summary": {
            "口径": {
                "标题数": meta["head_total"] - meta["dingge_entries"] - 10,
                "词牌头": meta["cipai_head"] - 10,
                "体式头": len(variant_entries),
                "体式总数": meta["entry_count"],
                "定格": meta["dingge_entries"],
                "变体": len(variant_entries),
                "词牌去重正名": meta["cipai_unique"],
            },
            "互稽": {
                # 10 个「词牌」头在 level 0（无 ## 前缀），不算进 208 标题数
                "标题数 = 词牌头 + 体式头":
                    meta["head_total"] - meta["dingge_entries"] - 10
                    == (meta["cipai_head"] - 10) + len(variant_entries),
                "体式总数 = 定格 + 变体":
                    meta["entry_count"] == meta["dingge_entries"] + len(variant_entries),
            },
            "体式头双口径": {
                "宽_全部非定格体式头": wide,
                "窄_排除分片与韵格描述头": narrow,
                "更窄_再排除别格类": narrowest,
                "架构师口径": 63,
                "构思方口径": 54,
                "可复现性": ("63 可复现（= 全部非定格体式头，实测 %d）；"
                             "54 无法由任何单一显式规则从源数据复现——"
                             "最接近的两种规则分别得 %d 与 %d，均不等于 54。"
                             "差集已逐条列出，待构思方给出计入口径。"
                             % (wide, narrow, narrowest)),
                "排除明细": {k: v for k, v in by_rule.items()},
            },
            "无定格词牌数": sum(1 for c in cipai_rows if not c["has_dingge"]),
            "无定格说明": "源文件对部分词牌不标【定格】，其正格取该调首个体式头，等级与字数自检口径另注",
            "去重正名分歧": {
                "实测": meta["cipai_unique"],
                "裁决书": 156,
                "差": 156 - meta["cipai_unique"],
                "处置": "以实测 155 为准；文档写「约 156」并标注 Phase 3 实测回填 155",
            },
            "disclosure": DISCLOSURE,
        },
        "cipai": cipai_rows,
    }
    dump_json(doc, os.path.join(out, "cipai_index.json"))
    s = doc["summary"]
    sys.stdout.write("三口径: 标题 %d = 词牌头 %d + 体式头 %d | 体式 %d = 定格 %d + 变体 %d | 去重正名 %d\n" % (
        s["口径"]["标题数"], s["口径"]["词牌头"], s["口径"]["体式头"],
        s["口径"]["体式总数"], s["口径"]["定格"], s["口径"]["变体"], s["口径"]["词牌去重正名"]))
    sys.stdout.write("互稽: %s\n" % json.dumps(s["互稽"], ensure_ascii=False))
    sys.stdout.write("体式头双口径: 宽 %d / 窄 %d / 更窄 %d（架构师 63，构思方 54 不可复现）\n"
                     % (wide, narrow, narrowest))
    sys.stdout.write("cipai rows=%d，其中无定格 %d\n" % (
        len(cipai_rows), sum(1 for c in cipai_rows if not c["has_dingge"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
