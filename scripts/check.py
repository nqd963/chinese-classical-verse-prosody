"""统一命令行入口。

  python scripts/check.py env
  python scripts/check.py char 看
  python scripts/check.py poem --form qijue --text "两个黄鹂鸣翠柳，..."
  python scripts/check.py poem --form wulv --text ... --mode compat --emit-teao
  python scripts/check.py ci --cipai 忆江南 --text "江南好，风景旧曾谙。..."
  python scripts/check.py cipai 浣溪沙
  python scripts/check.py quote --first 空山新雨后
  python scripts/check.py selftest | datacheck | fixtures | regression
  python scripts/check.py chain          # 抽取→校验→回归 一条链跑完（复现性自证）

输出恒为 JSON（`ensure_ascii=False`，键排序），退出码：
  0 通过 ｜ 1 有硬错 ｜ 2 参数/数据错误 ｜ 3 数据不足、不支持、冲突
"""
from __future__ import annotations

import argparse
import json
import sys

import _paths


def _dump(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def _book():
    from tonebook import ToneBook
    return ToneBook()


def cmd_env(_a) -> int:
    out = _paths.env_check()
    _dump(out)
    return 0 if out["ok"] else 2


def cmd_char(a) -> int:
    book = _book()
    norm, how = book.normalize(a.char)
    rs = book.readings(a.char, a.book) if a.book == "cilin" else book.readings(a.char, "pingshui")
    if not rs:
        _dump({"kind": "char", "char": a.char, "found": False, "verdict": "no_data",
               "归一": {"结果": norm, "途径": how},
               "说明": "该字不在随包韵表内，无法给出平仄与韵部"})
        return 3
    payload = {
        "kind": "char", "char": a.char, "found": True, "verdict": "pass",
        "归一": {"韵表字形": norm, "途径": how,
                 "说明": "韵表本体为简体；繁体/异体在查询层归一，不回写韵表"},
        "读法": [{"平仄": r.tone, "声调": r.tone_class, "韵部": r.rhyme,
                  "注": r.note, "韵表序": r.src_rank} for r in rs],
        "平仄": sorted({r.tone for r in rs}),
        "是否入声": "入" in {r.tone_class for r in rs},
        "韵部": sorted({r.rhyme for r in rs}),
        "data_source": {"files": {"%s.json" % a.book: _paths.data_digest("%s.json" % a.book)}},
    }
    _dump(payload)
    return 0


def cmd_poem(a) -> int:
    import poem
    book = _book()
    text = a.text or sys.stdin.read()
    res = poem.check_poem(text, a.form, mode=a.mode, book=book,
                          allow_teao=not a.strict_teao)
    _dump(res.to_dict())
    return res.exit_code()


def cmd_ci(a) -> int:
    import os
    import ci
    book = _book()
    text = a.text or sys.stdin.read()
    res = ci.check_ci(text, a.cipai, book=book, variant=a.variant, mode=a.mode)
    if res.verdict == "no_data":
        # 一等谱未收录 → 回退到「推定谱」（钦定词谱例词反推）。
        # 推定谱有独立的置信等级与披露，绝不冒充一等谱。
        inf_path = _paths.DATA_DIR / "cipu_qinding_inferred.json"
        if os.path.exists(inf_path):
            res = ci.check_ci_inferred(text, a.cipai, book=book, variant=a.variant)
    _dump(res.to_dict())
    return res.exit_code()


def _skeleton_lookup(name: str) -> dict | None:
    """在钦定词谱骨架层查词牌；只供 cipai 检索，不供 ci 校验。

    命中返回 {"variant_count": int, "entries": [...]}；未命中返回 None。
    `catalog_only` 条目 unverified=true，不进检索主路径，避免拿 OCR 目录残片充数。
    """
    import os
    path = _paths.DATA_DIR / "cipu_qinding_skeleton.json"
    if not os.path.exists(path):
        return None
    d = _paths.load_json("cipu_qinding_skeleton.json")
    hits = [e for e in d.get("entries", []) if e.get("name") == name]
    if not hits:
        # 别名命中也算，但只返回正名条目
        hits = [e for e in d.get("entries", [])
                if name in (e.get("aka") or [])]
    if not hits:
        return None
    return {"variant_count": hits[0].get("variant_count"), "entries": hits}


def cmd_cipai(a) -> int:
    import ci
    book = _book()
    cib = ci.CiBook(book)
    entry, how = cib.resolve(a.name)
    if entry is None:
        # 骨架层回退：钦定词谱只有字数/句数/韵式，逐字平仄在转录中已丢失。
        # 这里只回答「这个词牌存在、是什么骨架」，退出码 3 表示不可机器校验。
        # 严禁把 skeleton_only 当成可校验谱——校验器 ci 子命令不消费它。
        sk = _skeleton_lookup(a.name)
        if sk:
            _dump({"kind": "cipai", "cipai": a.name, "found": True,
                   "verdict": "skeleton_only", "evidence_level": "skeleton_only",
                   "说明": "仅见钦定词谱骨架层；有字数/句数/韵式，无逐字平仄谱，不可机器校验",
                   "体数": sk.get("variant_count"),
                   "体式": [{"body": e.get("body"), "字数": e.get("chars"),
                            "句数": e.get("lines"), "韵式": e.get("rhyme_desc"),
                            "原表头": e.get("raw_head")} for e in sk["entries"]],
                   "data_source": {"files": {"cipu_qinding_skeleton.json":
                                             _paths.data_digest("cipu_qinding_skeleton.json")}}})
            return 3
        _dump({"kind": "cipai", "cipai": a.name, "found": False, "verdict": "no_data",
               "说明": "词牌不在随包清单内（主清单以龙榆生《唐宋词格律》为骨架，"
                       "骨架层以钦定词谱为源）"})
        return 3
    _dump({"kind": "cipai", "cipai": entry["name"], "found": True,
           "verdict": "pass" if entry["grade"] == "一等" else "conflict",
           "命中": how, "等级": entry["grade"], "等级依据": entry["basis"],
           "别名": entry["aliases"], "又名": entry["aka"],
           "用韵类型": entry["rhyme_types"],
           "体式": entry["variants"], "冲突": entry.get("conflicts", []),
           "说明": ("等级为一等才作机器校验；二等表示双源冲突未裁定，只能作人工参照"
                    if entry["grade"] != "一等" else "归一比对无冲突，可作机器校验依据"),
           "data_source": {"files": {"cipai_index.json": _paths.data_digest("cipai_index.json"),
                                     "conflicts.json": _paths.data_digest("conflicts.json")}}})
    return 0 if entry["grade"] == "一等" else 3


def cmd_quote(a) -> int:
    rows = _paths.load_jsonl("corpus.jsonl")
    idx = _paths.load_json("examples_index.json")["index"]
    key, table = (a.first, "by_first_line") if a.first else (
        (a.title, "by_title") if a.title else (a.author_title, "by_author_title"))
    ids = idx.get(table, {}).get(key, [])
    hits = [r for r in rows if r["id"] in ids]
    _dump({"kind": "quote", "query": key, "方式": table, "found": bool(hits),
           "verdict": "pass" if hits else "no_data", "count": len(hits),
           "items": [{"id": r["id"], "集": r["集"], "篇名": r["篇名"], "作者": r["作者"],
                      "体裁": r["体裁"], "全文": "".join(r["句子"]),
                      "source": r["source"]} for r in hits],
           "data_source": {"files": {"corpus.jsonl": _paths.data_digest("corpus.jsonl")}}})
    return 0 if hits else 3


def cmd_selftest(_a) -> int:
    import selftest
    return selftest.main()


def cmd_datacheck(_a) -> int:
    import datacheck
    datacheck.main()
    return 0


def cmd_fixtures(_a) -> int:
    import fixtures
    return fixtures.main([])


def cmd_regression(_a) -> int:
    import regression
    return regression.main()


def cmd_chain(_a) -> int:
    """一条链：环境 → 数据断言 → 自测 → 夹具 → 回归，逐段给出退出码。"""
    steps = [("env", cmd_env), ("datacheck", cmd_datacheck), ("selftest", cmd_selftest),
             ("fixtures", cmd_fixtures), ("regression", cmd_regression)]
    codes = {}
    for name, fn in steps:
        codes[name] = fn(None)
    ok = all(v == 0 for v in codes.values())
    _dump({"chain": "抽取 → 校验 → 回归", "步骤退出码": codes, "ok": ok,
           "说明": "全部 0 才算链路通过；任一段非 0 即为失败，不掩盖"})
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="check.py", description="近体诗 / 词 格律校验与查询")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("env", help="环境与数据文件自检").set_defaults(fn=cmd_env)

    s = sub.add_parser("char", help="查单字平仄与韵部")
    s.add_argument("char")
    s.add_argument("--book", default="pingshui", choices=["pingshui", "cilin"])
    s.set_defaults(fn=cmd_char)

    s = sub.add_parser("poem", help="近体诗校验")
    s.add_argument("--form", required=True, choices=["wujue", "qijue", "wulv", "qilv"])
    s.add_argument("--text", default=None, help="缺省从 stdin 读")
    s.add_argument("--mode", default="strict", choices=["strict", "compat"])
    s.add_argument("--strict-teao", action="store_true",
                   help="不识别特拗（按裁决书 §1.2.1 的 33 式严格口径，特拗会报硬错）")
    s.set_defaults(fn=cmd_poem)

    s = sub.add_parser("ci", help="词校验")
    s.add_argument("--cipai", required=True)
    s.add_argument("--variant", default=None, help="如 定格 / 格二")
    s.add_argument("--text", default=None)
    s.add_argument("--mode", default="strict", choices=["strict", "compat"])
    s.set_defaults(fn=cmd_ci)

    s = sub.add_parser("cipai", help="词牌清单查询")
    s.add_argument("name")
    s.set_defaults(fn=cmd_cipai)

    s = sub.add_parser("quote", help="例句定位")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--first")
    g.add_argument("--title")
    g.add_argument("--author-title")
    s.set_defaults(fn=cmd_quote)

    sub.add_parser("selftest", help="自测（8 式两层 + 入声 47 字 + 数据断言）").set_defaults(fn=cmd_selftest)
    sub.add_parser("datacheck", help="仅数据断言").set_defaults(fn=cmd_datacheck)
    sub.add_parser("fixtures", help="20 条违规夹具 + 零误报 + 衬字位").set_defaults(fn=cmd_fixtures)
    sub.add_parser("regression", help="A40 例词回归").set_defaults(fn=cmd_regression)
    sub.add_parser("chain", help="抽取→校验→回归 全链").set_defaults(fn=cmd_chain)

    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except _paths.DataMissing as exc:
        _dump({"kind": a.cmd, "verdict": "error", "ok": False, "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
