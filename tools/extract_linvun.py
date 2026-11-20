"""D5 邻韵（王力《诗词格律》第 918–945 行） → data/linvun.json

对应《数据抽取规格》§5.2 R-31..R-35、§5.3 R-36..R-39。
半部韵（元/阮/愿）不作记忆编表：字级划分取《词林正韵》对应韵目的两组 <半> 小韵组，
并与平水韵该部字数交叉核对（R-37）；核对不过则退回整部口径 half_resolution="coarse"。
裁决书 §7.7 要求预留开关：改配置即改代码，故落 `config` 段。

用法：python tools/extract_linvun.py --src <语料根> [--out <目录>]
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import dump_json, is_cjk, out_dir, read_lines, sha256_of, skill_data_dir, src_root

REL_SRC = "二、格律入门/诗词格律（王力）.md"
LINE_FROM, LINE_TO = 918, 945

CN = "一二三四五六七八九十"
TRAD = {"艷": "艳", "禡": "祃", "箇": "个"}
# 源文异写修正（登记 suspect_queue）：第十五类上声「咸俭豏」中「咸」为平声韵目
TYPO_FIX = {("上", "咸"): ("感", "第十五类上声当作『感俭豏』：『咸』属下平十五咸，与同类的去声『勘艷陷』不类")}

SEG_LABEL = re.compile(r"(平声|上声|去声)")


def tokenize(seg: str) -> list[tuple[str, bool]]:
    """把「真文及元半」切成 [(真,False),(文,False),(元,True)]。"""
    out = []
    for ch in seg:
        if not is_cjk(ch) or ch in "及":
            continue
        if ch == "半":
            if out:
                out[-1] = (out[-1][0], True)
            continue
        if ch in "。；，、":
            continue
        out.append((ch, False))
    return out


def main() -> None:
    root = src_root(sys.argv)
    out = out_dir(sys.argv, skill_data_dir())
    path = os.path.join(root, REL_SRC)
    lines = read_lines(path)
    block = "\n".join(lines[LINE_FROM - 1:LINE_TO])

    ps_path = os.path.join(out, "pingshui.json")
    cl_path = os.path.join(out, "cilin.json")
    for need in (ps_path, cl_path):
        if not os.path.exists(need):
            sys.stderr.write("缺少 %s：请先抽取 pingshui / cilin\n" % need)
            raise SystemExit(2)
    with open(ps_path, "r", encoding="utf-8") as fh:
        ps = json.load(fh)
    with open(cl_path, "r", encoding="utf-8") as fh:
        cl = json.load(fh)

    by_base: dict[str, str] = {}
    for r in ps["rhymes"]:
        by_base[r["id"][-1]] = r["id"]
    rhymechars = {r["id"]: r["chars"] for r in ps["rhymes"]}

    flat, _, rusheng_part = block.partition("入声可分为八类")
    classes, suspend = [], []

    def resolve(tok: str, tone: str, cls_label: str) -> str | None:
        tok2 = TRAD.get(tok, tok)
        if (tone, tok2) in TYPO_FIX:
            fixed, why = TYPO_FIX[(tone, tok2)]
            suspend.append({"kind": "rhyme_name_typo", "class": cls_label,
                            "as_written": tok, "used": fixed, "reason": why,
                            "source_line_range": "%d-%d" % (LINE_FROM, LINE_TO)})
            tok2 = fixed
        full = by_base.get(tok2)
        want = {"平": ("上平", "下平"), "上": ("上",), "去": ("去",), "入": ("入",)}[tone]
        if full is not None and full[:len(want[0])] in want:
            return full
        # 韵目同名跨声调（如「月」只在入声）：按声调域精确找
        for r in ps["rhymes"]:
            if r["id"][-1] == tok2 and r["part"] in want:
                return r["id"]
        suspend.append({"kind": "unresolved_rhyme_name", "class": cls_label,
                        "as_written": tok, "tone": tone})
        return None

    # ---- 平上去十五类 ----
    marks = [(m.start(), m.group(1)) for m in re.finditer(r"第([一二三四五六七八九十]+)类：", flat)]
    for i, (pos, cn) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(flat)
        body_text = flat[pos:end]
        cls = "第%s类" % cn
        item = {"class": cn, "rusheng": False, "ping": [], "shang": [], "qu": [],
                "half": [], "_source": {"file": REL_SRC,
                                        "line_range": "%d-%d" % (LINE_FROM, LINE_TO)}}
        segs = list(SEG_LABEL.finditer(body_text))
        for j, sm in enumerate(segs):
            tone = sm.group(1)[0]
            seg_end = segs[j + 1].start() if j + 1 < len(segs) else len(body_text)
            for tok, half in tokenize(body_text[sm.end():seg_end]):
                full = resolve(tok, tone, cls)
                if full is None:
                    continue
                key = {"平": "ping", "上": "shang", "去": "qu"}[tone]
                item[key].append(full + ("<half>" if half else ""))
                if half:
                    item["half"].append(full)
        classes.append(item)

    # ---- 入声八类 ----
    marks2 = [(m.start(), m.group(1)) for m in re.finditer(r"第([一二三四五六七八九十]+)类：", rusheng_part)]
    rusheng = []
    for i, (pos, cn) in enumerate(marks2):
        end = marks2[i + 1][0] if i + 1 < len(marks2) else len(rusheng_part)
        body_text = rusheng_part[pos:end]
        body_text = body_text.split("：", 1)[1] if "：" in body_text else body_text
        members = []
        for tok, half in tokenize(body_text.split("注意")[0]):
            full = resolve(tok, "入", "入-%s" % cn)
            if full:
                members.append(full + ("<half>" if half else ""))
        rusheng.append({"class": "入-" + cn, "rusheng": True, "members": members,
                        "_source": {"file": REL_SRC,
                                    "line_range": "%d-%d" % (LINE_FROM, LINE_TO)}})

    # ---- 独用七韵 ----
    m = re.search(r"这七个韵是：\s*\n?([^\n]*)", block)
    standalone = []
    if m:
        for ch in m.group(1):
            if is_cjk(ch) and ch not in "韵是":
                full = by_base.get(ch)
                if full:
                    standalone.append(full)
    if not standalone:
        m2 = re.search(r"这七个韵是：([^\n]+)\n([^\n]+)", block)
        if m2:
            for ch in m2.group(2):
                if is_cjk(ch):
                    full = by_base.get(ch)
                    if full:
                        standalone.append(full)

    # ---- 半部韵字级划分（取词林 <半> 组） ----
    half_chars = {}
    half_groups: dict[str, dict[int, str]] = {}
    for g in cl["xiaoyun"]:
        if not g["half"]:
            continue
        full = g["rhyme_full"]
        half_groups.setdefault(full, {})[g["part"]] = g["chars"]
    detail = {}
    for full, groups in sorted(half_groups.items()):
        union = set("".join(groups.values()))
        own = set(rhymechars.get(full, ""))
        diff = sorted(own - union)
        extra = sorted(union - own)
        status = "pass" if len(diff) <= 3 else "partial"
        detail[full] = {
            "groups": {str(k): v for k, v in sorted(groups.items())},
            "union_size": len(union), "rhyme_size": len(own),
            "only_in_rhyme": diff, "only_in_cilin": extra,
            "crosscheck": status,
            "resolution": "fine" if status == "pass" else "coarse",
            "source": "词林正韵 %s 的两组 <半> 小韵组（非平水韵、非王力原表）" % full,
        }
    # R-39：王力表只对元/阮/愿标「半」，其余 6 个词林半部韵目不进 linvun 判定表
    HALF_IN_LINVUN = ("上平十三元", "上声十三阮", "去声十四愿")
    half_chars = {k: v for k, v in detail.items() if k in HALF_IN_LINVUN}
    half_chars_other = {k: {"groups": v["groups"], "crosscheck": v["crosscheck"],
                            "note": "王力邻韵表未标「半」，按整部处理；字级分组保留于 cilin.json 供词用"}
                        for k, v in detail.items() if k not in HALF_IN_LINVUN}
    fine_list = [k for k, v in half_chars.items() if v["resolution"] == "fine"]
    coarse_list = [k for k, v in half_chars.items() if v["resolution"] == "coarse"]
    crosscheck_all = "pass" if not coarse_list else "partial"

    config = {"default_mode": "strict",
              "neighbor_allowed_in_strict": False,
              "neighbor_allowed_in_compat": True,
              "compat_neighbor_scope": "仅首句（孤雁出群格）",
              "half_resolution": "fine" if not coarse_list else "coarse",
              "half_resolution_fine": fine_list,
              "half_resolution_coarse": coarse_list,
              "half_resolution_switch": "改本项为 coarse 即以整部口径判定（改配置不改代码）"}

    doc = {
        "schema_version": "1.0",
        "source": {"book": "诗词格律（王力）", "chapter": "第三章 古体诗·用韵",
                   "file": REL_SRC, "line_range": "%d-%d" % (LINE_FROM, LINE_TO),
                   "copyright": "仅抽取公版韵表事实（十五类/八类/独用七韵），未收录论述文字",
                   "sha256": sha256_of(path), "extracted_at": "2026-11-20"},
        "meta": {"class_count": len(classes), "rusheng_class_count": len(rusheng),
                 "total_class_count": len(classes) + len(rusheng),
                 "standalone_count": len(standalone),
                 "half_rhyme_count": len(half_chars),
                 "half_rhyme_fine": fine_list, "half_rhyme_coarse": coarse_list,
                 "half_crosscheck": crosscheck_all,
                 "half_rule": "仅元/阮/愿三韵做字级拆分；词林另 6 个半部韵目（灰/佳/贿/泰/卦/队）王力表未标半，按整部处理"},
        "classes": classes,
        "rusheng_classes": rusheng,
        "standalone": standalone,
        "half_chars": half_chars,
        "half_chars_other": half_chars_other,
        "config": config,
    }
    dump_json(doc, os.path.join(out, "linvun.json"))
    sq_path = os.path.join(out, "suspect_queue.json")
    with open(sq_path, "r", encoding="utf-8") as fh:
        sq = json.load(fh)
    known = {(i.get("kind"), i.get("as_written")) for i in sq["items"]}
    for item in suspend:
        if (item.get("kind"), item.get("as_written")) in known:
            continue
        sq["items"].append(item)
    dump_json(sq, os.path.join(out, "suspect_queue.json"))
    sys.stdout.write("classes=%d rusheng=%d total=%d standalone=%s half=%d crosscheck=%s\n" % (
        len(classes), len(rusheng), len(classes) + len(rusheng), standalone,
        len(half_chars), crosscheck_all))
    for c in classes:
        sys.stdout.write("  第%s类 p=%s s=%s q=%s\n" % (c["class"], c["ping"], c["shang"], c["qu"]))
    for r in rusheng:
        sys.stdout.write("  %s %s\n" % (r["class"], r["members"]))
    sys.stdout.write("suspend=%d\n" % len(suspend))


if __name__ == "__main__":
    main()
