"""抽取 `corpus.jsonl`（公版诗词汇编）+ `examples_index.json`（首句/篇名/作者反查索引）。

依据（裁决书 §7.8）：只收正文（作者 / 篇名 / 诗句），**不收任何注、评、译**；
来源与版权状态在 `data/corpus_meta.json` 标注；体积计入 A37 总预算。

两个源：
  四、选本/唐诗三百首（蘅塘退士）.md   —— `## 篇名` + `> 作者：X` + `### 原文`
  四、选本/宋词三百首（朱孝臧）.md     —— `### 词牌　作者` + 正文段

同时产出 `examples_index.json`：A28 要求「例词在包内可定位，命中率 100%」，
故索引键覆盖 首句 / 篇名 / 作者＋篇名 三种查法，全部指向 corpus id。
"""
from __future__ import annotations

import json
import os
import re
import sys

from common import dump_json, dump_jsonl, out_dir, sha256_of, skill_data_dir, src_root

SRC_TANG = "四、选本/唐诗三百首（蘅塘退士）.md"
SRC_SONG = "四、选本/宋词三百首（朱孝臧）.md"
# 句读单位：近体诗以「，」「。」断句（一行五言/七言）；词亦以逗号句号断句。
# 裁决书 §7.2 已证「（句）（读）」实测 0 处，句读只能由标点确定。
END_PUNCT = "。！？"
ALL_PUNCT = "。！？，、；"
INNER_PUNCT = "："


def clean_line(s: str) -> str:
    """去 markdown 强调符与空白，只留汉字与标点。"""
    s = s.replace("**", "").replace("*", "")
    s = re.sub(r"<[^>]+>", "", s)
    return s.strip()


def split_sentences(text: str) -> list[str]:
    out, cur = [], ""
    for ch in text:
        cur += ch
        if ch in ALL_PUNCT:
            out.append(cur.strip())
            cur = ""
    if cur.strip():
        out.append(cur.strip())
    return [s for s in out if re.search(r"[\u4e00-\u9fff]", s)]


def cjk_len(s: str) -> int:
    return sum(1 for c in s if "\u4e00" <= c <= "\u9fff")


def classify_shi(sents: list[str]) -> tuple[str, str]:
    """推断体裁，返回 (体裁, 依据)。

    **源文件没有分卷标题**：`四、选本/唐诗三百首（蘅塘退士）.md` 的结构只有
    `## 篇名` + `### 原文/译文/注释`，全文无「卷一」「五言古诗」这类体裁标题（已 grep 核实）。
    所以体裁**只能按句数×句长推断**，同一形态既可能是律也可能是古：
    五言八句 40 字里既有《山居秋暝》（律）也有《宿王昌龄隐居》（五古）。
    因此每行都带 `体裁可信: false`，`corpus_meta.json` 声明该字段为 inferred。

    原先「五言古体/排律」把两类混在一起（排律是近体、古体不是），无法用于任何统计，
    现按句长是否齐整拆开：
      句长全等且句数 > 8 → 五言/七言长体（排律或古体，仍需人工定）
      句长不齐 / 句长非 5、7 → 古体（杂言或句长不齐）
    """
    if not sents:
        return "其他", "无句子"
    lens = {cjk_len(s) for s in sents}
    n = len(sents)
    if len(lens) == 1:
        cpl = lens.pop()
        table = {(5, 4): "五绝", (7, 4): "七绝", (5, 8): "五律", (7, 8): "七律"}
        if (cpl, n) in table:
            return table[(cpl, n)], "句数%d×句长%d" % (n, cpl)
        if cpl in (5, 7) and n > 8:
            return ("%s言长体" % ("五" if cpl == 5 else "七"),
                    "句长齐整 %d×%d，排律与古体未分" % (n, cpl))
        return "古体", "句长齐整 %d 字但句数 %d 不合近体" % (cpl, n)
    return "古体", "句长不齐 %s，杂言或古体" % sorted(lens)


def parse_tang(path: str, rel_path: str) -> list[dict]:
    lines = open(path, encoding="utf-8").read().split("\n")
    rows, cur, i = [], None, 0
    while i < len(lines):
        t = lines[i].strip()
        if t.startswith("## "):
            cur = {"篇名": t[3:].strip(), "作者": None, "body": [], "line": i + 1}
            rows.append(cur)
        elif t.startswith("> 作者") and cur is not None:
            cur["作者"] = t.split("：", 1)[-1].strip() if "：" in t else t.split(":", 1)[-1].strip()
        elif t.startswith("### "):
            if cur is not None and t[4:].strip() == "原文":
                j = i + 1
                while j < len(lines):
                    s = lines[j].strip()
                    if s.startswith("#"):
                        break
                    if s and not s.startswith("audio:"):
                        cur["body"].append(clean_line(s))
                    j += 1
                i = j
                continue
        i += 1
    out = []
    for r in rows:
        joined = "".join(r["body"])
        sents = split_sentences(joined)
        if not sents:
            continue
        genre, why = classify_shi(sents)
        out.append({"集": "唐诗三百首", "篇名": r["篇名"], "作者": r["作者"],
                    "句子": sents, "体裁": genre,
                    "体裁可信": False, "体裁来源": "推断", "体裁依据": why,
                    "source": {"file": rel_path, "line": r["line"]}})
    return out


def parse_song(path: str, rel_path: str) -> list[dict]:
    lines = open(path, encoding="utf-8").read().split("\n")
    heads = []
    for i, raw in enumerate(lines):
        t = raw.strip()
        if t.startswith("### "):
            heads.append((i, t[4:].strip()))
    out = []
    for k, (i, head) in enumerate(heads):
        nxt = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        # 「词牌　作者」（全角空格分隔）；副题会另起一个 ### 头，故取首个 ### 头含分隔符者
        parts = [p for p in re.split(r"[\u3000\s]+", head) if p]
        if len(parts) < 2:
            continue
        cipai, author = parts[0], parts[1]
        body = []
        for j in range(i + 1, nxt):
            t = lines[j].strip()
            if not t or t.startswith("#") or t.startswith("audio:"):
                continue
            body.append(clean_line(t))
        sents = split_sentences("".join(body))
        if not sents:
            continue
        out.append({"集": "宋词三百首", "篇名": cipai, "作者": author,
                    "句子": sents, "体裁": "词",
                    "体裁可信": True, "体裁来源": "源文件结构",
                    "体裁依据": "宋词三百首 以「### 词牌　作者」分条，体裁可确认为词",
                    "source": {"file": rel_path, "line": i + 1}})
    return out


def main(argv: list[str]) -> int:
    src = src_root(argv)
    out = out_dir(argv, skill_data_dir())
    tang = parse_tang(os.path.join(src, SRC_TANG), SRC_TANG)
    song = parse_song(os.path.join(src, SRC_SONG), SRC_SONG)
    corpus = []
    for k, r in enumerate(tang):
        r["id"] = "T%03d" % (k + 1)
        corpus.append(r)
    for k, r in enumerate(song):
        r["id"] = "S%03d" % (k + 1)
        corpus.append(r)
    for r in corpus:
        r["首句"] = r["句子"][0] if r["句子"] else ""
        r["句数"] = len(r["句子"])
        r["字数"] = sum(cjk_len(s) for s in r["句子"])

    index = {"by_first_line": {}, "by_title": {}, "by_author_title": {}}
    for r in corpus:
        index["by_first_line"].setdefault(r["首句"], []).append(r["id"])
        index["by_title"].setdefault(r["篇名"], []).append(r["id"])
        index["by_author_title"].setdefault("%s/%s" % (r["作者"], r["篇名"]), []).append(r["id"])

    dump_jsonl(corpus, os.path.join(out, "corpus.jsonl"))
    dump_json({"schema_version": "1.0", "count": len(corpus), "index": index,
               "note": "键为 首句 / 篇名 / 作者＋篇名；同键多值表示同题异首或异体"},
              os.path.join(out, "examples_index.json"))
    meta = {
        "schema_version": "1.0",
        "sources": [
            {"file": SRC_TANG, "rows": len(tang), "sha256": sha256_of(os.path.join(src, SRC_TANG)),
             "edition": "蘅塘退士编《唐诗三百首》313 首（开放数据集整理本）",
             "copyright": "诗歌正文为唐代作品，公有领域；仅收正文，未收译注评"},
            {"file": SRC_SONG, "rows": len(song), "sha256": sha256_of(os.path.join(src, SRC_SONG)),
             "edition": "朱孝臧编《宋词三百首》（殆知阁中华古籍库整理本）",
             "copyright": "宋代作品，公有领域；仅收正文，未收注评"},
        ],
        "total": len(corpus),
        "fields": ["id", "集", "篇名", "作者", "体裁", "体裁可信", "体裁来源",
                   "体裁依据", "句子", "首句", "句数", "字数", "source"],
        "policy": "只收作者/篇名/诗句；不收任何注、评、译（裁决书 §7.8）",
        "体裁字段口径": {
            "status": "inferred",
            "可信度": "低",
            "说明": "唐诗三百首源文件无分卷/体裁标题（结构只有 ## 篇名 + ### 原文），"
                    "体裁只能按句数×句长推断，同一形态律/古并存（五言八句 40 字里"
                    "既有律也有古），故逐行标 体裁可信=false。**不得把该字段当作金标**；"
                    "零误报的真正判据是人工金标。",
            "已拆开": "原「五言古体/排律」混合类已拆为 五言长体 / 古体（句长不齐）",
        },
    }
    dump_json(meta, os.path.join(out, "corpus_meta.json"))
    from collections import Counter
    c = Counter(r["体裁"] for r in tang)
    sys.stdout.write("corpus=%d（诗 %d / 词 %d）\n" % (len(corpus), len(tang), len(song)))
    sys.stdout.write("诗体分布: %s\n" % dict(sorted(c.items(), key=lambda kv: -kv[1])))
    sys.stdout.write("索引键: 首句 %d / 篇名 %d / 作者＋篇名 %d\n"
                     % (len(index["by_first_line"]), len(index["by_title"]),
                        len(index["by_author_title"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
