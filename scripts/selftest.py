"""自测总入口：环境 → 数据断言 → 句式两层断言 → 入声专项 → 孤平分水岭。

跑法：`python scripts/selftest.py`（退出码 0 = 全绿，1 = 有断言失败，2 = 数据缺失）。

设计原则：每一条断言都要**能失败**。故意做坏一个输入就必须报错，
否则那条断言只是装饰。本文件末尾的 `_negative_controls()` 就是这个自证。
"""
from __future__ import annotations

import json
import sys

import _paths


def _negative_controls() -> list[dict]:
    """反证：把规则故意写坏，确认断言会红。「能失败的断言才是断言」。"""
    import poem

    out = []

    # 1) 计数式孤平（已作废的旧写法）会把七言仄起平收算成 7 式 → 必须能区分
    def count_style(skeleton, cells):
        if skeleton not in poem.GUPING_SKELETONS:
            return False
        return sum(1 for c in cells[:-1] if c == "P") < 2

    free = [i for i in range(0, 7, 2) if i != 6]
    from itertools import product
    n_count = 0
    for combo in product("PZ", repeat=len(free)):
        cells = list(poem.BASE[(7, "仄", "平")])
        for i, c in zip(free, combo):
            cells[i] = c
        if poem.violates_R1_end3(cells) or count_style("ZZPPZZP", cells):
            continue
        n_count += 1
    out.append({"反证": "计数式孤平 vs 配对式孤平（七言仄起平收）",
                "计数式得": n_count, "配对式得": poem.EXPECT_LEGAL["ZZPPZZP"],
                "可分水岭": n_count != poem.EXPECT_LEGAL["ZZPPZZP"],
                "结论": "计数式会多 1 式（放过 `平仄仄平仄仄平` 的拗而未救），配对式正确"})

    # 2) 去掉 skeleton 参数的孤平判定会把 `仄仄仄平平` 误判
    bad = poem.violates_R2_guping(list("ZZZPP"), "PPZZP")
    good = poem.violates_R2_guping(list("ZZZPP"), "ZZZPP")
    out.append({"反证": "孤平必须绑定骨架身份",
                "传错骨架": bad, "传自身骨架": good,
                "可分水岭": (not bad) and (not good),
                "结论": "`仄仄仄平平` 是平收但非孤平句式；判定必须按骨架查表"})
    return out


def main() -> int:
    try:
        from tonebook import ToneBook
    except _paths.DataMissing as exc:      # pragma: no cover
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=1))
        return 2

    env = _paths.env_check()
    if env["missing"]:
        print(json.dumps({"ok": False, "stage": "env", "env": env},
                         ensure_ascii=False, indent=1, sort_keys=True))
        return 2

    book = ToneBook()
    import datacheck
    import poem

    data = datacheck.run(book)
    schema = poem.selftest_report()
    neg = _negative_controls()

    # §13.7 首句特拗首字仄：王力脚注两例须判 pass_with_variant，
    # evidence.王力举例=true；非举例的同形句 soft 但标记 false；不并入普通 V-TEAO。
    import poem as _pm
    _t1 = _pm.check_poem("故人具鸡黍，邀我至田家。绿树村边合，青山郭外斜。"
                         "开轩面场圃，把酒话桑麻。待到重阳日，还来就菊花。", "wulv", book=book)
    _ev1 = [v.evidence for v in _t1.violations if v.code == "V-TEAO-FIRST"]
    _t2 = _pm.check_poem("苍苍竹林寺，杳杳钟声晚。荷笠带斜阳，青山独归远。", "wujue", book=book)
    _ev2 = [v.evidence for v in _t2.violations if v.code == "V-TEAO-FIRST"]
    # §13.7.1：王力对「昔闻洞庭水」两处归类矛盾——1873 脚注列首字仄特拗例外、
    # 2512 行又归入「用古体诗的平仄」例外。裁决：并列记录、不择一。
    # 该句报 V-TEAO-FIRST，evidence 两字段均 true；不报 V-SANZE/hard（末三字 ZPZ 非三仄尾）。
    _t3 = _pm.check_poem("昔闻洞庭水，今上岳阳楼。吴楚东南坼，乾坤日夜浮。"
                         "亲朋无一字，老病有孤舟。戎马关山北，凭轩涕泗流。", "wulv", book=book)
    _ev3 = [v.evidence for v in _t3.violations if v.code == "V-TEAO-FIRST"]
    _t3_bad = {v.code for v in _t3.violations
               if v.code in ("V-SANZE", "V-SANPING") or v.severity == "hard"}
    teao_first_ok = (
        len(_ev1) == 1 and _ev1[0].get("王力举例") is True
        and _t1.verdict == "pass_with_variant" and _t1.hard_count == 0
        and (not _ev2 or _ev2[0].get("王力举例") is False)
        and len(_ev3) == 1
        and _ev3[0].get("王力举例") is True                 # 1873 脚注
        and _ev3[0].get("王力另归古体例外") is True         # 2512 行，并列
        and _ev3[0].get("王力举例") == _ev3[0].get("王力另归古体例外")
        and not _t3_bad
    )

    a = schema["A层_单点翻转"]
    b = schema["B层_多比特"]
    checks = [
        ("环境：Python ≥3.8 且 8 个数据文件齐备", env["ok"], {"python": env["python"]}),
        ("数据断言全绿（A7/A8/A9/A10/繁简/邻韵）", data["ok"],
         {"passed": data["passed"], "total": data["total"]}),
        ("句式 A 层：8 式单点翻转与裁决书 §1.2 逐位一致",
         a["all_match"], {"checked": a["checked"]}),
        ("句式 B 层：生成集合 ⊇ §1.2 单比特集合", b["subset_ok"],
         {"生成": b["生成集合式数合计"], "单比特": b["单比特集合式数合计"]}),
        ("句式 B 层：差集每式给出「哪一字拗、哪一字救」",
         all("拗位" in x for x in b["差集明细"]) and b["差集式数"] == len(b["差集明细"]),
         {"差集式数": b["差集式数"]}),
        ("允许式数符合仲裁表（§13.1 三仄尾部分合法后 4/2/3/3 + 6/6/8/4 = 36）",
         b["式数符合仲裁表"] and b["生成集合式数合计"] == 36,
         {"合计": b["生成集合式数合计"], "旧口径合计": 33,
          "变化": "PPPZZ 2→3、ZZPPPZZ 4→6（合法三仄尾收进允许集合）"}),
        ("分水岭：七言仄起平收 = 6 式（计数式会得 7）",
         poem.EXPECT_LEGAL["ZZPPZZP"] == 6 and neg[0]["计数式得"] == 7,
         {"配对式": 6, "计数式": neg[0]["计数式得"]}),
        ("分水岭：孤平绑定骨架（`仄仄仄平平` 不判孤平）", not neg[1]["传自身骨架"],
         {"传错骨架是否误判": neg[1]["传错骨架"]}),
        ("§13.7/.1 首句特拗首字仄：两例并列记录 / 昔闻洞庭水不报三仄尾·无硬错",
         teao_first_ok,
         {"王力原句判定": _ev1[0].get("王力举例") if _ev1 else None,
          "过故人庄 verdict": _t1.verdict,
          "非举例标记": _ev2[0].get("王力举例") if _ev2 else "未命中",
          "昔闻洞庭水_王力举例": _ev3[0].get("王力举例") if _ev3 else None,
          "昔闻洞庭水_另归古体": _ev3[0].get("王力另归古体例外") if _ev3 else None,
          "昔闻洞庭水硬错/三忌": sorted(_t3_bad)}),
    ]
    rows = [{"断言": n, "通过": bool(ok), "实测": d} for n, ok, d in checks]
    ok_all = all(r["通过"] for r in rows)
    report = {
        "ok": ok_all,
        "checks": rows,
        "env": {k: env[k] for k in ("python", "python_ok", "files", "missing")},
        "句式枚举": {
            "逐式": [{k: r[k] for k in ("skeleton", "允许式数", "期望式数", "单点翻转禁止位",
                                        "§1.2_硬约束位", "A层一致", "差集")}
                     for r in a["rows"]],
            "合计": b["生成集合式数合计"],
            "差集": b["差集明细"],
        },
        "数据断言": data["assertions"],
        "反证": neg,
    }
    print(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True))
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
