"""意境风险扫描器（否定性检查，不是鉴赏器）。

铁律（与 references/07-意境与忌讳.md 一致）：
  1. 只输出 soft 风险提示，**绝不输出「意境合格/不合格」**；verdict 取值只有
     risks_found / no_risks_detected / error，不得出现 pass_meaning 一类字段。
  2. 每条提示必须带：病名 / 原文依据（书名+行号）/ 触发位置 / 可复核证据。
  3. 短语表全部有出处（data/yijing_cliche.json），只收有明标的连续短语；
     无明标的单字泛词一律不入库——防「青山绿水」套语误伤「青山郭外斜」实景。
  4. 反向哨兵由 selftest 实测保证；名作回归由 regress 保证（硬门槛：零误报）。
  5. 零第三方依赖，仅标准库。

能机器做的只有两件窄事：陈词套语/蹈袭（有出处的连续短语复现）、
合掌中最粗的「同辞重句」层（对句同位置同字）。其余（空泛/凑韵/警策/兴比）
都只给带出处的 prompt，不机器判定——详见 prompts_for_human()。
点化白名单（data/yijing_dianhua.json）只输出「此句有原文点化/溯源评点」
这一事实，不免除 risk、不判意境好坏。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _paths  # noqa: E402

CLICHE_FILE = "yijing_cliche.json"
DIANHUA_FILE = "yijing_dianhua.json"

# verdict 中禁止出现的字符串（自检用）：不得伪装成品鉴判定
FORBIDDEN_VERDICT_TOKENS = ("合格", "不合格", "pass_meaning", "pass_yijing", "yijing_pass")


def _load_cliche() -> dict:
    d = _paths.load_json(CLICHE_FILE)
    for e in d.get("entries", []):
        # 二字词仅在原文明标「已成为套语」且实测名作零命中时收（暖红/寒碧）；
        # 单字泛词（青山/绿水/明月/春风）无明标，一律不入库。
        if len(e.get("短语", "")) < 2:
            raise ValueError("套语表不得收录单字词（反向哨兵）：%s" % e)
        src = e.get("出处", {})
        if not src.get("文件") or not src.get("行号"):
            raise ValueError("套语条目缺出处文件/行号：%s" % e)
    return d


def _load_dianhua() -> dict:
    d = _paths.load_json(DIANHUA_FILE)
    for e in d.get("entries", []):
        if len(e.get("句", "")) < 5:
            raise ValueError("点化条目句过短，易误匹配：%s" % e)
        src = e.get("出处", {})
        if not src.get("文件") or not src.get("行号"):
            raise ValueError("点化条目缺出处文件/行号：%s" % e)
    return d


def scan_dianhua(lines: list[str], table: dict) -> list[dict]:
    """点化识别：只报『此句有原文点化/溯源评点』，附书名+行号+原文；不免除 risk。"""
    hits = []
    for e in table["entries"]:
        needle = e["句"]
        if len(needle) < 5:
            continue
        for li, line in enumerate(lines, 1):
            if needle not in line:
                continue
            hits.append({
                "句序": li,
                "类别": e["类别"],
                "命中句": line,
                "完整出处句": e["完整出处句"],
                "源句": e.get("源句", "（源头名作，本身无更早源句）"),
                "评语": e["评语"],
                "原文依据": {
                    "书名": e["出处"]["书名"], "行号": e["出处"]["行号"],
                    "原文": e["原文"],
                },
                "说明": "有原文评点为点化/溯源；机器只报此事实，不判意境好坏，也不免除套语 risk。",
            })
    return hits


def attach_trace(risks: list[dict], lines: list[str], dianhua: dict) -> None:
    """套语命中若落在溯源名作上，附『源头』证据，避免把名作当蹈袭。
    注意：不删除、不降级 risk，只加一条可回指的信息。"""
    traces = [e for e in dianhua["entries"] if e.get("关联套语")]
    for r in risks:
        if r["code"] != "YJ-CLICHE":
            continue
        li = r["触发位置"]["句"]
        line = lines[li - 1]
        for e in traces:
            if e["关联套语"] != r["证据"]["命中短语"] or e["句"] not in line:
                continue
            r["证据"]["溯源"] = {
                "关联套语": e["关联套语"],
                "评语": e["评语"],
                "原文依据": {"书名": e["出处"]["书名"], "行号": e["出处"]["行号"],
                           "原文": e["原文"]},
            }
            r["复核建议"] = ("soft 提示，非判定：此句原文有溯源评点（见证据.溯源），"
                          "可能为套语源头名作而非蹈袭；请人工复核。")


def _split_lines(text: str) -> list[str]:
    """按中文标点切句；不做字数推断（意境扫描不依赖体裁）。"""
    punct = "。！？；\n，、,!?;"
    out, cur = [], ""
    for ch in text:
        if ch in punct:
            if cur.strip():
                out.append(cur.strip())
            cur = ""
        elif "\u4e00" <= ch <= "\u9fff":
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return [x for x in out if x]


def _couplets(n_lines: int) -> list[tuple[int, int]]:
    """只对八句律诗的中间两联做合掌检查（蕙风:2185 的对象是『对偶句』）。
    四句绝句无必对之联，不机器检查——避免把非对偶句误当对偶。"""
    if n_lines == 8:
        return [(3, 4), (5, 6)]
    return []


def scan_cliche(lines: list[str], table: dict) -> list[dict]:
    """连续精确匹配有出处的套语短语；命中只报 soft 风险。"""
    risks = []
    for entry in table["entries"]:
        phrase = entry["短语"]
        for li, line in enumerate(lines, 1):
            start = 0
            while True:
                k = line.find(phrase, start)
                if k < 0:
                    break
                risks.append({
                    "code": "YJ-CLICHE",
                    "severity": "soft",
                    "病名": entry["病名"],
                    "原文依据": {
                        "书名": entry["出处"]["书名"],
                        "作者": entry["出处"]["作者"],
                        "行号": entry["出处"]["行号"],
                        "原文": entry["原文"],
                    },
                    "触发位置": {"句": li, "字起": k + 1, "字止": k + len(phrase)},
                    "证据": {
                        "命中短语": phrase,
                        "匹配方式": "连续精确子串匹配（非散字命中）",
                        "所在句": line,
                        "按语": entry["按语"],
                        "反向哨兵": table["反向哨兵"]["结构哨兵"],
                    },
                    "复核建议": "soft 提示，非判定：核对该处是否为『袭而愈工』『反其意而用之』的点化（苕溪:845 / 诗人玉屑:1798）。",
                })
                start = k + 1
    return risks


def _in_dstack(line: str, j: int) -> bool:
    """该位置是否落在叠字连续段内（年年/萧萧之类是修辞，不是合掌）。"""
    return (j > 0 and line[j - 1] == line[j]) or (j + 1 < len(line) and line[j + 1] == line[j])


def scan_hezhang(lines: list[str]) -> list[dict]:
    """合掌的『同辞重句』层：对句同位置同字。只比两句之间，不比句内叠字。"""
    risks = []
    for a, b in _couplets(len(lines)):
        la, lb = lines[a - 1], lines[b - 1]
        same = []
        for j in range(min(len(la), len(lb))):
            if la[j] != lb[j]:
                continue
            if _in_dstack(la, j) or _in_dstack(lb, j):
                continue
            same.append({"字": la[j], "字序": j + 1,
                         "上句字": la[j], "下句字": lb[j]})
        if same:
            risks.append({
                "code": "YJ-HEZHANG",
                "severity": "soft",
                "病名": "合掌（同辞重句层）",
                "原文依据": {
                    "书名": "文心雕龙", "作者": "刘勰", "文件": "文心雕龙（刘勰）.md", "行号": 155,
                    "原文": "意两出，义之骈枝也；同辞重句，文之疣赘也。",
                    "旁证": {"书名": "蕙风词话", "行号": 2185,
                           "原文": "对偶句要浑成，要色泽相称，要不合掌。"},
                },
                "触发位置": {"对句": [a, b], "上句": la, "下句": lb},
                "证据": {
                    "同字位置": same,
                    "匹配方式": "律诗颔联/颈联同位置逐字比对；句内叠字位置已排除",
                    "可复核": "逐字核对 '%s' 与 '%s' 的第 %s 字"
                              % (la, lb, "、".join(str(x["字序"]) for x in same)),
                },
                "复核建议": "soft 提示，非判定：同字重出也可能有意（如复用关键字呼应）；"
                            "且本检查只覆盖『同辞』层，同义换词的合掌（如『游雁/归鸿』）机器检不出，须人工判断。",
            })
    return risks


def prompts_for_human() -> list[dict]:
    """机器做不到的部分：只给带出处的提示，绝不据此定合格/不合格。"""
    return [
        {
            "病名": "空泛（遮题测试）",
            "原文依据": {"书名": "蕙风词话", "行号": 205,
                       "原文": "起处不宜泛写景，宜实不宜虚，便当笼罩全阕，它题便挪移不得。"},
            "为什么只能提示": "『把题目字屏蔽后算主题词重合度』这一代理已实测：gold30 中 G14《渡汉江》、G21《赤壁》、G27《阁夜》、G30《贫女》正文与题字零重合却都是名作——机械代理会误伤 4/30，故不实现为检查。",
            "人工动作": "遮住题目读一遍：全篇能否安到别的题目上？能，则空泛，回炉重写起句。",
        },
        {
            "病名": "凑韵（古名趁韵）",
            "原文依据": {"书名": "诗人玉屑", "行号": 1958,
                       "原文": "若是用此，则于前后诗意无相干，且趁『苹』字韵而已。",
                       "旁证": [
                           {"书名": "苕溪渔隐丛话", "行号": 1113,
                            "原文": "亦有趁韵而撰造非事实者，若『珊瑚破高齐，作婢舂黄糜』是也。初无糜字。"},
                           {"书名": "蕙风词话", "行号": 213,
                            "原文": "用其不必用，不甚合者以就韵，乃至涉尖新，近牵强，损风格。"},
                           {"书名": "随园诗话", "行号": 6819,
                            "原文": "近今诗人贪押韵，又贪叠韵，遂不得已而往往单用之，此大谬也。"},
                           {"书名": "随园诗话", "行号": 7047,
                            "原文": "以一题排挨上下平作三十首，敷衍凑拍，满纸浮词。"},
                       ]},
            "为什么只能提示": "二轮穷搜『趁韵/凑韵/凑拍/韵脚』：古人判据核心是『与前后诗意无相干』『撰造非事实』『近牵强损风格』，全属语义/事实/风格判断；随园6819『双字单用』、7047『排挨上下平』虽带形式色彩，前者会把『悠然见南山』一类名作误伤、后者是组诗行为，都不构成可定位到某个韵脚字的形式判据。包内无带出处的语义场词典，机器只能定位韵脚字（格律引擎已做），不能判相干与否，故仍不机器实现。",
            "人工动作": "遮住韵脚字问：去掉这个字句意是否一样成立？若一样成立且更顺，即凑韵。",
        },
        {
            "病名": "警策缺失（一诗之眼）",
            "原文依据": {"书名": "苕溪渔隐丛话", "行号": 439,
                       "原文": "立片言以居要，乃一篇之警策。文章无警策，则不足以传世。",
                       "旁证": {"书名": "文心雕龙", "行号": 189,
                              "原文": "秀也者，篇中之独拔者也；雕削取巧，虽美非秀。"}},
            "为什么只能提示": "『抽掉哪句全篇失色』是价值判断，无形式特征可机检；且文心雕龙明言刻意求警策反成『雕削』，机器更不该下结论。",
            "人工动作": "定稿前指认本篇警策句；答不出则继续改，但不要为求警策而雕削。",
        },
        {
            "病名": "兴与比",
            "原文依据": {"书名": "迦陵论诗丛稿", "行号": 313,
                       "原文": "『兴』的作用大多是『物』的触引在先，而『心』的情意之感发在后；『比』则大多是已有『心』的情意在先，而借比为『物』在后。"},
            "为什么只能提示": "判据是『心/物谁在先』，需读解诗意先后，机器无法从字面确定；边界处原文明说只能标『可兴可比』，不可强判。",
            "人工动作": "问：是先见物才动心（兴），还是先有情再借物（比）？短章宜兴，长篇咏物宜比。",
        },
        {
            "病名": "句内同义重出（如『斜阳暮』）",
            "原文依据": {"书名": "诗人玉屑", "行号": 380,
                       "原文": "东坡曰：此词高妙，但既云『斜阳』，又云『暮』，则重出也。"},
            "为什么只能提示": "判据是同义而非同字，需同义词表与语义判断；包内无带出处的同义词表，不得凭感觉列（随园诗话6929 注者按语即指出『《沧浪诗话》并无此语』，可见引语归属尚须核实，更不能自造词表）。",
            "人工动作": "检查一句内是否有语义重叠的相邻词（如斜阳+暮）。",
        },
    ]


def scan(text: str, title: str = "") -> dict:
    table = _load_cliche()
    dianhua = _load_dianhua()
    lines = _split_lines(text)
    risks = scan_cliche(lines, table) + scan_hezhang(lines)
    attach_trace(risks, lines, dianhua)
    risks.sort(key=lambda r: (r["触发位置"].get("句", 0), r["code"]))
    dianhua_hits = scan_dianhua(lines, dianhua)
    out = {
        "kind": "yijing_risk",
        "扫描对象": {"题目": title, "句": lines},
        "verdict": "risks_found" if risks else "no_risks_detected",
        "ok": True,
        "点化识别": {"命中数": len(dianhua_hits), "hits": dianhua_hits},
        "能力边界": {
            "机器可检出（仅 soft 否定性提示）": [
                "陈词套语/蹈袭：有出处的连续短语精确复现（表：%s，%d 条）"
                % (CLICHE_FILE, len(table["entries"])),
                "合掌·同辞重句层：律诗对句同位置同字",
                "点化/溯源识别：只报『此句有原文点化评点』这一事实（表：%s，%d 条），不免除 risk、不判好坏"
                % (DIANHUA_FILE, len(dianhua["entries"])),
            ],
            "仅人工提示（机器不判）": [p["病名"] for p in prompts_for_human()],
        },
        "risks": risks,
        "risk_count": len(risks),
        "prompts_for_human": prompts_for_human(),
        "notes": [
            "本输出只做否定性风险提示，绝不输出『意境合格/不合格』；"
            "verdict=no_risks_detected 仅表示『未检出上述两类窄风险』，不等于意境合格。",
            "所有 risk 的 severity 恒为 soft；命中出处为诗话原文，复核责任在用户。",
            "点化识别只输出带原文出处的溯源事实，不判意境好坏、不免除套语 risk。",
        ],
        "data_source": {"files": {
            CLICHE_FILE: _paths.data_digest(CLICHE_FILE),
            DIANHUA_FILE: _paths.data_digest(DIANHUA_FILE),
        }},
    }
    for token in FORBIDDEN_VERDICT_TOKENS:
        assert token not in out["verdict"], "verdict 含禁用词 %s" % token
    return out


def _risks_of(text_lines: list[str]) -> tuple[list[dict], dict]:
    """内部用：给定已切好的句列表返回结果（regress 用，避免重复切句）。"""
    table = _load_cliche()
    risks = scan_cliche(text_lines, table) + scan_hezhang(text_lines)
    risks.sort(key=lambda r: (r["触发位置"].get("句", 0), r["code"]))
    return risks, table


def selftest() -> dict:
    """反向哨兵实测：套语表不误伤实景，句内叠字不误报合掌，扩表新词与点化均能检出。"""
    table = _load_cliche()
    dianhua = _load_dianhua()
    cases = [
        {
            "名": "实景句『青山郭外斜』不得命中套语表",
            "输入": ["青山郭外斜"],
            "期望": {"cliche": 0, "hezhang": 0},
        },
        {
            "名": "未入表短语『青山绿水』不得命中（无出处不入库）",
            "输入": ["青山绿水"],
            "期望": {"cliche": 0, "hezhang": 0},
        },
        {
            "名": "有出处套语『人面桃花相映红』须命中，且因崔护原句溯源而不免除risk",
            "输入": ["人面桃花相映红"],
            "期望": {"cliche": 1, "hezhang": 0, "dianhua": 1, "溯源": 1},
        },
        {
            "名": "构造合掌『青山横北郭/青山绕东城』置于颔联须命中同辞重句",
            "输入": ["青山横北郭", "白水绕东城", "青山横北郭", "青山绕东城",
                     "浮云游子意", "落日故人情", "挥手自兹去", "萧萧班马鸣"],
            "期望": {"cliche": 0, "hezhang": 1},
        },
        {
            "名": "叠字『年年/归不归』不得误报合掌（G10 原句）",
            "输入": ["山中相送罢", "日暮掩柴扉", "春草年年绿", "王孙归不归"],
            "期望": {"cliche": 0, "hezhang": 0},
        },
        {
            "名": "扩表·花间套语『画屏金鹧鸪』须命中（玉屑/词十七讲1665）",
            "输入": ["画屏金鹧鸪"],
            "期望": {"cliche": 1, "hezhang": 0},
        },
        {
            "名": "扩表·无益之语『十月寒』须命中（玉屑1691+苕溪353）",
            "输入": ["十月寒"],
            "期望": {"cliche": 1, "hezhang": 0},
        },
        {
            "名": "扩表·二字明标套语『暖红』须命中（诗词例话8375）",
            "输入": ["暖红"],
            "期望": {"cliche": 1, "hezhang": 0},
        },
        {
            "名": "点化识别·『恰似一江春水向东流』须命中溯源且不免除套语risk",
            "输入": ["恰似一江春水向东流"],
            "期望": {"cliche": 1, "hezhang": 0, "dianhua": 1, "溯源": 1},
        },
        {
            "名": "点化识别·介甫『一鸟不鸣山更幽』须命中反用评点",
            "输入": ["一鸟不鸣山更幽"],
            "期望": {"cliche": 0, "hezhang": 0, "dianhua": 1},
        },
    ]
    rows = []
    for c in cases:
        cr = scan_cliche(c["输入"], table)
        hr = scan_hezhang(c["输入"])
        dh = scan_dianhua(c["输入"], dianhua)
        attach_trace(cr, c["输入"], dianhua)
        actual = {"cliche": len(cr), "hezhang": len(hr), "dianhua": len(dh),
                  "溯源": sum(1 for r in cr if r["证据"].get("溯源"))}
        expected = dict(c["期望"])
        expected.setdefault("dianhua", 0)
        expected.setdefault("溯源", 0)
        rows.append({
            "名": c["名"], "输入": c["输入"],
            "期望": expected, "实测": actual,
            "通过": actual == expected,
            "证据": [r["证据"] for r in cr + hr],
            "点化证据": [{"类别": h["类别"], "原文依据": h["原文依据"]} for h in dh],
        })
    all_pass = all(r["通过"] for r in rows)
    return {
        "kind": "yijing_selftest",
        "哨兵": table["反向哨兵"],
        "套语表条数": len(table["entries"]),
        "点化表条数": len(dianhua["entries"]),
        "cases": rows,
        "all_pass": all_pass,
        "结论": "反向哨兵全部通过：套语表不误伤『青山郭外斜』『青山绿水』，叠字不误报合掌；"
                "扩表新词（金鹧鸪/十月寒/暖红）与点化溯源均能检出，且溯源不免除 risk。"
                if all_pass else "存在哨兵失败，禁止交付",
    }


_GOLD_REL = Path("04-QA") / "gold30.json"


def regress(gold_path: str | None = None) -> dict:
    """名作回归：gold30 只读。名作被报风险须逐条列出，供人工判定误报/有据。"""
    if gold_path:
        candidates = [Path(gold_path)]
    else:
        # gold30 是工作区 QA 产物，不随技能包分发。按几种常见布局依次找：
        # ① 源码布局 ③ 从 zip 解压后的发布目录 ② 任意上级目录
        root = _paths.SKILL_DIR
        candidates = [root.parent.parent / _GOLD_REL]  # 03-源码/<skill> -> studio
        candidates += [parent / _GOLD_REL for parent in root.parents]
    p = next((c.resolve() for c in candidates if c.exists()), None)
    if p is None:
        return {"kind": "yijing_regress", "verdict": "no_data",
                "note": "gold30 是工作区 QA 产物，不随包分发；未提供 --gold 且各候选路径均未找到。"
                        "这不表示扫描器失败——selftest 的反向哨兵仍可独立运行。",
                "candidates": [str(c) for c in candidates]}
    with open(p, "r", encoding="utf-8") as fh:
        gold = json.load(fh)
    rows = []
    for it in gold["items"]:
        lines = it["句子"]
        risks, _ = _risks_of(lines)
        rows.append({
            "id": it["id"], "篇名": it["篇名"], "作者": it["作者"],
            "risk_count": len(risks),
            "risks": [{
                "code": r["code"], "病名": r["病名"],
                "位置": r["触发位置"], "证据": r["证据"],
                "原文依据": r["原文依据"],
            } for r in risks],
        })
    flagged = [r for r in rows if r["risk_count"] > 0]
    return {
        "kind": "yijing_regress",
        "gold_path": str(p),
        "只读": True,
        "总数": len(rows),
        "被报风险首数": len(flagged),
        "名作误报数": len(flagged),
        "误报率": "0（名作应无风险提示；若非 0 须逐条给出极强证据，否则视为扫描器缺陷）",
        "逐首": rows,
        "结论": "gold30 全部 30 首零风险提示" if not flagged else
                "有 %d 首被报风险，需逐条解释误报或有据" % len(flagged),
    }


def _dump(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="yijing.py", description="意境风险扫描（否定性检查，非鉴赏）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="扫描一首诗；只输出 soft 风险提示")
    s.add_argument("--text", default=None, help="诗句文本；缺省从 stdin 读")
    s.add_argument("--title", default="", help="题目（仅回显，不参与机器判定）")

    sub.add_parser("selftest", help="反向哨兵实测")
    s = sub.add_parser("regress", help="gold30 名作回归（只读）")
    s.add_argument("--gold", default=None, help="gold30.json 路径；缺省用包外相对路径")
    s = sub.add_parser("dianhua", help="列出点化/溯源白名单（每条带书名+行号+原文）")
    s.add_argument("--text", default=None, help="可选：只显示命中该句文本的条目")

    a = ap.parse_args(argv)
    try:
        if a.cmd == "scan":
            text = a.text if a.text is not None else sys.stdin.read()
            _dump(scan(text, a.title))
            return 0
        if a.cmd == "selftest":
            out = selftest()
            _dump(out)
            return 0 if out["all_pass"] else 1
        if a.cmd == "regress":
            out = regress(a.gold)
            _dump(out)
            return 2 if out.get("verdict") == "error" else (1 if out["名作误报数"] else 0)
        if a.cmd == "dianhua":
            table = _load_dianhua()
            entries = table["entries"]
            if a.text:
                entries = [e for e in entries if e["句"] in a.text or a.text in e["完整出处句"]]
            _dump({
                "kind": "yijing_dianhua",
                "条数": len(entries),
                "原则出处": table["原则出处"],
                "能力边界": table["能力边界"],
                "entries": entries,
                "data_source": {"files": {DIANHUA_FILE: _paths.data_digest(DIANHUA_FILE)}},
            })
            return 0
    except (_paths.DataMissing, ValueError) as exc:
        _dump({"kind": a.cmd, "verdict": "error", "ok": False, "error": str(exc)})
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
