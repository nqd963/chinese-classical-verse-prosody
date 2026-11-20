# -*- coding: utf-8 -*-
"""推定谱只读展开工具。

数据文件里的 confidence 为紧凑格式：

  confidence[i] = {"level": ..., "tones": "P"|"Z"|"ZP"|""}
  level ∈ {single_reading, multi_reading_any, unresolvable, cross_validated_free}
  tones: P=平, Z=仄, 多读并列如 ZP, 空串=无读法

推导规则（逐位等价，已由生成器断言）：
  char[i] = [c for c in example if CJK][i]
  pat[i]  = pat[i]
  len(confidence) == chars == len(pat) == len(example_CJK)

本工具按位展开成可读形式，**不修改任何文件**，只打印 JSON。

用法：
  python tools/inspect_inferred.py --name 渔父引
  python tools/inspect_inferred.py --id Q:0001
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "cipu_qinding_inferred.json"

TONE_ZH = {"P": "平", "Z": "仄", "": "无读法", "?": "不可判"}
LEVEL_ZH = {
    "single_reading": "单读，声调确定",
    "multi_reading_any": "多读任一成立，不约束",
    "unresolvable": "韵表查不到，无法判定",
    "cross_validated_free": "多首同调例词交叉判出的自由位",
}


def cjk_only(s: str) -> list[str]:
    return [c for c in s if "\u4e00" <= c <= "\u9fff"]


def expand(entry: dict) -> dict:
    chars = cjk_only(entry.get("example", ""))
    pat = entry.get("pat", "")
    conf = entry.get("confidence", [])

    rows = []
    for i, c in enumerate(conf):
        level = c.get("level", "")
        tones = c.get("tones", "")
        if isinstance(tones, str):
            tone_zh = "".join(TONE_ZH.get(t, t) for t in tones) or "无读法"
        elif isinstance(tones, list):
            tone_zh = "".join(TONE_ZH.get(t, t) for t in tones) or "无读法"
        else:
            tone_zh = str(tones)
        rows.append({
            "位": i + 1,
            "字": chars[i] if i < len(chars) else None,
            "谱位": pat[i] if i < len(pat) else None,
            "level": level,
            "level_说明": LEVEL_ZH.get(level, "未知枚举"),
            "声调": tone_zh,
        })

    problems = []
    if not (len(rows) == len(chars) == len(pat)):
        problems.append(f"长度不一致：confidence={len(rows)} example_CJK={len(chars)} pat={len(pat)}")
    for r in rows:
        if r["level"] == "single_reading" and len(r["声调"]) != 1:
            problems.append(f"第 {r['位']} 位 level=single_reading 但声调={r['声调']}")
    return {
        "id": entry.get("id"),
        "name": entry.get("name"),
        "source_line": entry.get("source_line"),
        "字数": entry.get("chars"),
        "pat": pat,
        "逐位展开": rows,
        "一致性问题": problems,
        "说明": "本展开由 tools/inspect_inferred.py 只读生成；原数据文件未被修改",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="推定谱只读展开（人类可读）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--name", help="按词牌名查")
    g.add_argument("--id", help="按条目 id 查，如 Q:0001")
    ap.add_argument("--limit", type=int, default=3, help="同名时最多展开几条，默认 3")
    a = ap.parse_args()

    if not DATA.exists():
        sys.stdout.write(json.dumps({"error": f"数据文件不存在：{DATA}"}, ensure_ascii=False, indent=1))
        return 2
    doc = json.loads(DATA.read_text(encoding="utf-8"))
    if a.id:
        hits = [e for e in doc["entries"] if e.get("id") == a.id]
    else:
        hits = [e for e in doc["entries"] if e.get("name") == a.name]

    out = {
        "kind": "inferred_expand",
        "query": a.id or a.name,
        "命中数": len(hits),
        "evidence_level": doc.get("evidence_level"),
        "disclosure": doc.get("disclosure"),
        "展开": [expand(e) for e in hits[:a.limit]],
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0 if hits else 3


if __name__ == "__main__":
    raise SystemExit(main())
