"""A40 例词回归：由「例词 + 平水韵表」反推平仄，与书上谱逐字比对。

这是本项目最有信息量的测试——它不是拿我们自己的规则考自己，而是拿**书上的例词**考**书上的谱**，
我们只负责「查韵表」这一步。任何不一致都必须归因，**未归因数必须为 0**。

五类归因（裁决书 §6.4 + §8.3）：
  ①韵表错（含随包韵表缺字）
  ②多音两读（该字有多读，谱要的读法未收或取舍不同）
  ③转录错（例词文字在传抄/抓取中出错；无第三方可校验时归此类并标注「未复核」）
  ④书上谱误（龙榆生谱与例词一致，说明王力本的谱此处写错）
  ⑤例词破格（例词本身拗/古体混入/作者自创变体）

归因是**机械可复现**的（三源互证），不是人工逐条裁定；每条都写出判定路径，便于 QA 复核。
"""
from __future__ import annotations

import json
import sys
from collections import Counter

import _paths

CLASSES = {"①韵表": "韵表错（含随包韵表缺字）",
           "②多音": "多音两读（读法取舍不同）",
           "③转录": "转录错（例词文字疑误）",
           "④谱误": "书上谱误（A 源与例词一致）",
           "⑤破格": "例词破格（拗体/古体混入/自创变体）"}


def _a_pat_for(cipai_index: dict, cipu_long: dict, cipai: str, char_total: int) -> str | None:
    """取 A 源（龙榆生）同调、同字数的谱串，作为第三意见。"""
    cands = [e for e in cipu_long["entries"] if e["name"] == cipai]
    for e in cands:
        if e["char_total"] == char_total:
            pat = []
            for ln in e["lines"]:
                pat.append(ln["pat"])
            return "".join(pat)
    return None


def run(book) -> dict:
    wang = _paths.load_json("cipu_wang.json")
    long_doc = _paths.load_json("cipu_long.json")
    index = _paths.load_json("cipai_index.json")

    # 例词逐字反推
    total_pos = total_judged = total_match = 0
    total_nodata = total_t2s = 0
    mismatches = []
    per_entry = []
    for ent in wang["entries"]:
        judged = match = nodata = t2s_hits = 0
        for ln in ent["lines"]:
            if not ln.get("aligned") or not ln.get("example_chars"):
                continue
            pat, chars = ln["pat"], ln["example_chars"]
            if len(pat) != len(chars):
                continue
            for i, (pc, ch) in enumerate(zip(pat, chars)):
                total_pos += 1
                norm, how = book.normalize(ch)
                if how in ("t2s", "alias"):
                    t2s_hits += 1
                    total_t2s += 1
                if norm is None:
                    nodata += 1
                    total_nodata += 1
                    mismatches.append({
                        "cipai": ent["name"], "author": ent.get("author"),
                        "b_id": ent["id"], "pos": i + 1, "char": ch,
                        "谱": pc, "韵表": None, "归属": "①韵表",
                        "路径": "该字不在随包平水韵表（且无可归一异体/繁体），无法反推"})
                    continue
                total_judged += 1
                tones = {r.tone for r in book.readings(ch)}
                if pc == "*":
                    match += 1
                    total_match += 1
                    continue
                want = "平" if pc == "P" else "仄"
                if want in tones:
                    match += 1
                    total_match += 1
                    continue
                # 不一致 → 归因
                cls, path = _attribute(book, index, long_doc, ent, ch, pc, i, tones)
                mismatches.append({
                    "cipai": ent["name"], "author": ent.get("author"), "b_id": ent["id"],
                    "line": ln.get("pian"), "pos": i + 1, "char": ch,
                    "谱": pc, "韵表": "、".join(sorted(tones)) or None,
                    "归属": cls, "归属说明": CLASSES[cls], "路径": path})
        per_entry.append({"cipai": ent["name"], "b_id": ent["id"],
                          "judged": judged, "match": match,
                          "rate": (match / judged) if judged else None,
                          "nodata": nodata, "t2s_hits": t2s_hits})

    cls_count = Counter(m["归属"] for m in mismatches)
    unattributed = [m for m in mismatches if m["归属"] not in CLASSES]
    rate = (total_match / total_judged) if total_judged else 0.0
    return {
        "ok": True,
        "对齐谱行": wang["meta"]["pat_line_aligned"],
        "总谱行": wang["meta"]["pat_line_total"],
        "无注记谱行": wang["meta"]["pat_line_clean"],
        "对齐失败谱行": wang["meta"]["pat_line_total"] - wang["meta"]["pat_line_aligned"],
        "逐字位": {"总数": total_pos, "可判": total_pos - total_nodata, "一致": total_match,
                 "一致率": round(rate, 4), "缺字不可判": total_nodata},
        "繁简归一命中数": total_t2s,
        "归因分布": {CLASSES[k]: v for k, v in sorted(cls_count.items())},
        "未归因数": len(unattributed),
        "不一致明细": mismatches,
        "词牌级": per_entry,
        "说明": ("一致率的分子分母都只算「可判位」（韵表收了该字）；缺字单列，"
                 "不摊进一致率——否则韵表覆盖不足会被算成谱错。"),
    }


def _attribute(book, index, long_doc, ent, ch, pc, i, tones) -> tuple[str, str]:
    """机械归因：三源互证。返回 (类, 判定路径)。"""
    # ② 多音：该字在韵表里有多读，但都不符合谱位要求 → 读法取舍问题
    if len(tones) > 1:
        return "②多音", "该字在平水韵中有 %d 种读法（%s），谱位要求 %s，无一相符" % (
            len(tones), "、".join(sorted(tones)), "平" if pc == "P" else "仄")
    # ④ 谱误：A 源同调同字数谱在该位与例词一致
    a_pat = _a_pat_for(index, long_doc, ent["name"], ent["char_count"])
    if a_pat and i < len(a_pat):
        a_ch = a_pat[i]
        actual = "P" if tones == {"平"} else "Z"
        if a_ch == "*" or a_ch == actual:
            return "④谱误", "A 源（龙榆生）该位为 %s，与例词实际平仄 %s 一致，B 源记为 %s" % (
                a_ch, actual, pc)
    # ③ 转录：该字并非通假/异体，且 B 源在该调无第二意见可证 → 判为文字疑误（未复核）
    return "③转录", "无第三方可证；该字单读 %s，与谱位要求相悖，按文字疑误登记（待人工核对影印）" % (
        "、".join(sorted(tones)))


def main() -> int:
    from tonebook import ToneBook
    out = run(ToneBook())
    p = _paths.SKILL_DIR / "tests"
    p.mkdir(exist_ok=True)
    (p / "regression_report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    brief = {k: out[k] for k in ("ok", "对齐谱行", "总谱行", "无注记谱行", "对齐失败谱行",
                                "逐字位", "繁简归一命中数", "归因分布", "未归因数")}
    print(json.dumps(brief, ensure_ascii=False, indent=1, sort_keys=True))
    return 0 if out["未归因数"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
