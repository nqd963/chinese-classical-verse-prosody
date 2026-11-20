"""近体诗格律判定：句式允许集合生成、粘对矩阵、逐字比对、孤平/三仄尾/三平尾、对仗 L1。

裁决书 §1.2.1 的硬要求：句式约束**不得硬编码「第 N 字」**。本模块把约束写成三条规则：

  violates_R1a_sanping(cells)         三平尾：句末三字全平，无条件 hard（§13.1）
  violates_R1b_sanze(cells)           三仄尾：末三字全仄时配对位同仄才 hard（下有三仄上必二平，§13.1）
  violates_R2_guping(cells, skeleton) 孤平配对约束（**仅** `平平仄仄平`、`仄仄平平仄仄平` 两式适用）
  R3 二四六位 = 骨架值                —— 由 `range(0, n, 2)` 的枚举范围直接保证，**不写函数**

再由「一三五位全枚举 + 上述过滤」生成逐位允许集合。「不论位」是算出来的，不是写死的。

符号消歧（裁决书 §1.2.1 明文规定）：本文件里 `R2` 只出现在 `violates_R2_guping` 里，
指**句式生成规则的孤平配对约束**；对仗判定另有名字（见 `check_duizhang` 的 `DZ-*` 键）。
风险项一律写 `RISK-Rn`。禁止裸写 `R2`。

为什么是配对而不是计数（裁决书 §1.2.1 的修正说明）：
  反例 `平仄仄平仄仄平`——除韵脚外平声有 2 个（第 1、4 字），按「平声字 ≥ 2」的计数法会放行；
  但它第 3 字拗（仄）、第 5 字未救（仍仄），实为失律。配对约束「第 3 字与第 5 字不得同时为仄」
  要求「要么不拗（3 平），要么拗而有救（3 仄则 5 必平）」，正是拗救本义。

允许式数（裁决书 §13.1 更新后）：五言 4/2/3/3、七言 6/6/8/4，合计 36
（旧「末三字不得同声」口径为 33；三仄尾部分合法后 +3）。
由 §1.2「正格 + 不论位」推出的单比特集合合计 30，差集 6 式，均可自证
（3 式拗救 + 3 式合法三仄尾）。**不设「具名拗救白名单」**——白名单会漂移、会漏配。
"""
from __future__ import annotations

from itertools import product

import _paths
from schema import Reading, Result, Violation

FORMS = {"wujue": (4, 5), "qijue": (4, 7), "wulv": (8, 5), "qilv": (8, 7)}
FORM_NAME = {"wujue": "五绝", "qijue": "七绝", "wulv": "五律", "qilv": "七律"}

# 八个正格骨架（纯数据）。键 = (字数, 第 2 字平仄「起」, 句尾平仄「收」)
BASE = {
    (5, "仄", "仄"): "ZZPPZ",
    (5, "仄", "平"): "ZZZPP",
    (5, "平", "仄"): "PPPZZ",
    (5, "平", "平"): "PPZZP",
    (7, "仄", "仄"): "ZZPPPZZ",
    (7, "仄", "平"): "ZZPPZZP",
    (7, "平", "仄"): "PPZZPPZ",
    (7, "平", "平"): "PPZZZPP",
}
# 孤平适用域：绑定**骨架身份**，不是「是否平收」。
# `仄仄仄平平`（ZZZPP）虽平收，但不属孤平句式，不得误判（裁决书 §1.3）。
GUPING_SKELETONS = {"PPZZP", "ZZPPZZP"}
# 孤平配对约束的两个位置（0 基下标），只对上述骨架生效
GUPING_PAIR = {"PPZZP": (0, 2), "ZZPPZZP": (2, 4)}

# ── 特拗与对句相救（裁决书 §12，王力原文核定；已收口，无选项）─────
# §12.1：特拗用**绝对字面模式**匹配，禁止「第 3、4 字互换」相对特征。
#   王力原文「在这种情况下，五言第一字、七言第三字必须用平声，不再是可平可仄」。
#   故只认完整模式 `平平仄平仄`(PPZPZ) / `仄仄平平仄平仄`(ZZPPZPZ)，**每个字写死**；
#   相对特征会把 `仄平仄平仄` 归一成 `仄平平仄仄` 而放行王力明言不合格的句子。
#   注意：必须是**绝对相等**（sym == want），不是「跳过自由位后相等」——跳过即退化成相对特征。
# §12.4：`仄仄平仄平`(ZZPZP) 不存在（王力全文仅作「不合律句」引例），禁止准入。
TEAO_PATTERNS = {"PPPZZ": "PPZPZ",      # 五言 平平平仄仄 → 平平仄平仄（3 拗 4 救，本句自救）
                 "ZZPPPZZ": "ZZPPZPZ"}  # 七言 仄仄平平平仄仄 → 仄仄平平仄平仄（5 拗 6 救）
# 互换位置（1 基）：五言第 3、4 字；七言第 5、6 字。
# 「特拗归一」与「V-TONE 去重」共用这一个定位来源，避免两处各写一套导致重复报错。
TEAO_POS = {"PPPZZ": (3, 4), "ZZPPPZZ": (5, 6)}

# §13.7 王力脚注①例外：首句「仄平仄平仄」(ZPZPZ)——即特拗绝对式 PPZPZ 的首字仄变体。
# 注意：主理人消息里写作 `ZPPZP`（仄平平仄平）系笔误，两则王力举例实测均为 ZPZPZ：
#   故人具鸡黍 = 仄平仄平仄；昔闻洞庭水 = 仄平仄平仄（「洞」仄、「庭」平）。
# 七言同类例外王力未举例，故只实现五言。
# 王力未给可推广条件，故不并入普通 V-TEAO（后者要求首字必平），也不把「首句一律放宽」当规则。
TEAO_FIRST_PATTERN = "ZPZPZ"
# 王力 1873 脚注①明举的首字仄特拗例外两句（裁决 §13.7 最终定稿，恢复两例）。
WANGLI_FIRST_EXAMPLES = ("故人具鸡黍", "昔闻洞庭水")
# 王力本人对「昔闻洞庭水」有两处矛盾归类：1873 脚注①举为首字仄特拗例外，
# 2512 行又归入第三类「例外——就平仄说，用古体诗的平仄」。两者均王力原文、非 OCR 错。
# 裁决 §13.7.1：不强行统一、机器不判断哪处更权威，evidence 里并列记录两处（与 §9 口径声明同源）。
WANGLI_GUTI_RECLASSIFIED = ("昔闻洞庭水",)

# §12.2：对句相救（王力 (b) 类）。出句骨架 + 命中模式：
#   在该用 `仄仄平平仄` 的地方，第四字用了仄声（ZZPZZ），
#   或三四两字都用了仄声（ZZZZZ，如李商隐「向晚意不适」）——王力原文明确含此式。
#   七言对应 `平平仄仄平仄仄` / `平平仄仄仄仄仄`。
#   必须**联判**对句救位：五言对句第 3 字、七言对句第 5 字须为平；
#   救成立 soft + pass_with_variant + 双坐标；救位仄 hard；救位 NODATA 整首 unable。
AOJIU_OUT = {"ZZPPZ": ("ZZPZZ", "ZZZZZ"),
             "PPZZPPZ": ("PPZZPZZ", "PPZZZZZ")}
AOJIU_AO_COL = {"ZZPPZ": 4, "PPZZPPZ": 6}    # 拗位（1 基）
AOJIU_SAVE_COL = {"ZZPPZ": 3, "PPZZPPZ": 5}  # 救位在对句（1 基）


def is_aojiu_out(sym: str) -> bool:
    """§13.2/R4 出句结构判据（不靠具体串清单）。

    王力四变体（五言）仄仄平仄仄 / 平仄平仄仄 / 仄仄仄仄仄 / 平仄仄仄仄，
    共有结构 = **第 2 字仄 + 末二字仄仄**；七言 = 第 4 字仄 + 末二字仄仄。
    这样扩展后 4 式统一由结构判定，新增变体不必改串表。
    """
    if len(sym) == 5:
        return sym[1] == "Z" and sym[-2:] == "ZZ"
    if len(sym) == 7:
        return sym[3] == "Z" and sym[-2:] == "ZZ"
    return False
# _assign_line 尝试的「变体模式」——**仅用于分配读法**，与违规归类分离。
# 为什么不能只放 §12 认可的两式：多读字（如「不/为/重」）在定平位需要借「3、4 字互换」
# 这类模式才能选到贴合上下文的读法；若不给候选，_assign_line 落到 partial，
# 会把大量真作品的该位误报 V-TONE（实测 45→73 首 fail）。违规是否成立另由
# check_poem 按 §12 的 TEAO_PATTERNS（绝对模式）与 AOJIU_OUT（联判）严格判定，
# ZZPZP 等「不存在」的模式**只在此处辅助择优，绝不会被登记为合法变体**。
VARIANT_PATTERNS = {
    "PPPZZ": ("PPZPZ",),       # 特拗 3 拗 4 救
    "ZZZPP": ("ZZPZP",),       # 仅读法择优（"仄仄平仄平"不作合法变体登记）
    "ZZPPZ": ("ZZPZZ",),       # 对句相救出句（读法择优；ZZZZZ 由联判层按可用读法识别）
    "ZZPPPZZ": ("ZZPPZPZ",),   # 七言特拗
    "PPZZZPP": ("PPZZPZP",),   # 仅读法择优
    "PPZZPPZ": ("PPZZPZZ",),   # 七言对句相救出句
}

# 裁决书 §1.2 权威句式表（**仅用于 selftest 交叉核对，不参与判定**）。
# 结构：(可不论位, 单点翻转会触发三忌的位, 硬约束说明)
# 裁决书 §13.1 新口径：三仄尾由「末三字不得同声」改为配对约束（第1/3、第3/5 不得同仄）。
# 单比特翻转第 3（五）或第 5（七）字只造成「合法三仄尾」，不再是硬错，故这两式的 hard 集清空。
AUTHORITATIVE = {
    "ZZPPZ": ({"1", "3"}, set(), "尾字必仄"),
    "ZZZPP": ({"1"}, {"3"}, "第 3 字必仄（反则三平尾）"),
    "PPPZZ": ({"1"}, set(), "三仄尾合法：第 1、3 字不得同仄（下有三仄上必二平）"),
    "PPZZP": ({"3"}, {"1"}, "第 1 字必平（反则孤平）"),
    "ZZPPPZZ": ({"1", "3"}, set(), "三仄尾合法：第 3、5 字不得同仄（下有三仄上必二平）"),
    "ZZPPZZP": ({"1", "5"}, {"3"}, "第 3 字必平（反则孤平）"),
    "PPZZPPZ": ({"1", "3", "5"}, set(), "尾字必仄"),
    "PPZZZPP": ({"1", "3"}, {"5"}, "第 5 字必仄（反则三平尾）"),
}
# 允许式数（裁决书 §13.1 更新）：三仄尾部分合法后 33 → 36。
# 五·平起仄收 PPPZZ 2→3（新增 PPZZZ）；七·仄起仄收 ZZPPPZZ 4→6（新增 PZPPZZZ/ZZPPZZZ）。
EXPECT_LEGAL = {"ZZPPZ": 4, "ZZZPP": 2, "PPPZZ": 3, "PPZZP": 3,
                "ZZPPPZZ": 6, "ZZPPZZP": 6, "PPZZPPZ": 8, "PPZZZPP": 4}


# ── R1 拆两条（裁决书 §13.1，王力三书核定；旧「末三字不得同声」作废）──
# R1-a 三平尾：无条件 hard（王力 586/3383、《诗词例话》3113 三处单向负面，无例外）。
# R1-b 三仄尾：按「下有三仄，上必二平」判，不是一律禁：
#   五言 `平平仄仄仄`（第1平、第3仄）合法；`仄平仄仄仄`（第1、3同为仄）落调 hard；
#   七言 `仄仄平平仄仄仄`（第3平、第5仄）合法；`仄仄仄平仄仄仄`（第3、5同为仄）落调 hard。
# 统一判据：末三字全仄时，配对位 (第1,第3)[五] / (第3,第5)[七] 不得同为仄。
# 措辞用配对表述，禁止简化成「第3字必平」（那会误杀五言合法式）。
SANZE_PAIR = {5: (0, 2), 7: (2, 4)}   # 0 基：(第1,第3) / (第3,第5)


def violates_R1a_sanping(cells: list[str]) -> bool:
    """R1-a：三平尾——句末三字全平，无条件硬错。"""
    return len(cells) >= 3 and all(c == "P" for c in cells[-3:])


def violates_R1b_sanze(cells: list[str]) -> bool:
    """R1-b：三仄尾落调——末三字全仄且配对位同仄。合法三仄尾返回 False。"""
    if len(cells) < 3 or not all(c == "Z" for c in cells[-3:]):
        return False
    pair = SANZE_PAIR.get(len(cells))
    if pair is None:
        return False
    a, b = pair
    return cells[a] == "Z" and cells[b] == "Z"


def is_legal_sanze(cells: list[str]) -> bool:
    """末三字全仄、但不落调——「下有三仄，上必二平」的合法三仄尾。"""
    return len(cells) >= 3 and all(c == "Z" for c in cells[-3:]) \
        and not violates_R1b_sanze(cells)


def violates_R1_hard(cells: list[str]) -> bool:
    """生成/翻转层用的合并硬约束 = R1-a ∪ R1-b（两规则本身仍分别定义、分别定性）。"""
    return violates_R1a_sanping(cells) or violates_R1b_sanze(cells)


# 兼容旧引用名；判定口径一律走 violates_R1_hard
violates_R1_end3 = violates_R1_hard


def violates_R2_guping(cells: list[str], skeleton: str) -> bool:
    """孤平配对约束（**必须收 skeleton**）。

    只看 cells 会把 `仄仄仄平平` 误判成孤平——那是平收但非孤平句式。
    五言 `平平仄仄平`：第 1 字与第 3 字不得同时为仄。
    七言 `仄仄平平仄仄平`：第 3 字与第 5 字不得同时为仄。
    """
    if skeleton not in GUPING_PAIR:
        return False
    a, b = GUPING_PAIR[skeleton]
    if b >= len(cells):
        return False
    return cells[a] == "Z" and cells[b] == "Z"


def guping_evidence(cells: list[str], skeleton: str) -> dict | None:
    """孤平触发时的定位说明（A1 要的「违哪条规则」靠这个字段落地）。

    返回 None 表示未触发。字段措辞与裁决书 §1.2.1 一致，不自行发挥。
    """
    if not violates_R2_guping(cells, skeleton):
        return None
    a, b = GUPING_PAIR[skeleton]
    return {
        "骨架": skeleton,
        "触发条件": "第 %d 字与第 %d 字不得同时为仄（实为 %d=%s、%d=%s）"
                    % (a + 1, b + 1, a + 1, cells[a], b + 1, cells[b]),
        "等价修法": "第 %d 字改平，或第 %d 字改平" % (a + 1, b + 1),
    }


def explain_slack(cells: list[str], skeleton: str) -> dict | None:
    """对差集式给出「哪一字拗、哪一字救」的定位说明；给不出说明的式即为非法多余式。

    只有「拗位为仄、救位为平」的组合才构成可自证的拗救；其余一律 None。
    """
    # §13.1 合法三仄尾（PPPZZ/ZZPPPZZ 的新式）不在 GUPING_PAIR 里，须先判。
    if is_legal_sanze(cells):
        pair = SANZE_PAIR.get(len(cells))
        if pair is None:
            return None
        up, _ = pair                       # 配对上位：五言第1字 / 七言第3字（须平）
        ao = len(cells) - 3                # 0 基：末三字第 1 字（五言第3字 / 七言第5字）
        if cells[up] == "P" and ao < len(cells):
            return {"拗位": ao + 1, "救位": up + 1,
                    "类型": "合法三仄尾（下有三仄，上必二平）",
                    "说明": "第 %d 字该平而仄（三仄尾），第 %d 字仍平（上必二平，故合法）"
                            % (ao + 1, up + 1)}
    if skeleton not in GUPING_PAIR:
        return None
    a, b = GUPING_PAIR[skeleton]
    if b >= len(cells):
        return None
    if cells[a] == "Z" and cells[b] == "P":
        if a == 0:
            return {"拗位": a + 1, "救位": b + 1,
                    "说明": "第 1 字该平而仄（一拗），第 3 字该仄而平（三救）"}
        return {"拗位": a + 1, "救位": b + 1,
                "说明": "第 %d 字该平而仄（三拗），第 %d 字该仄而平（五救）" % (a + 1, b + 1)}
    # §13.1：合法三仄尾（末三字全仄但配对位不同仄）也进生成集合，
    # 它同样可自证：拗位=末三字第 1 字（由平转仄），救位=配对上位（保持平，即「上必二平」）。
    # 五言 PPZZZ：第 3 字拗、第 1 字平；七言 PZPPZZZ/ZZPPZZZ：第 5 字拗、第 3 字平。
    if is_legal_sanze(cells):
        pair = SANZE_PAIR.get(len(cells))
        if pair is None:
            return None
        up, _ = pair                       # 配对上位：五言第1字 / 七言第3字（须平）
        ao = len(cells) - 3                # 0 基：末三字第 1 字（五言第3字 / 七言第5字）
        if cells[up] == "P" and ao < len(cells):
            return {"拗位": ao + 1, "救位": up + 1,
                    "类型": "合法三仄尾（下有三仄，上必二平）",
                    "说明": "第 %d 字该平而仄（三仄尾），第 %d 字仍平（上必二平，故合法）"
                            % (ao + 1, up + 1)}
    return None


def allowed_sets(n: int, start: str, tail: str) -> tuple[list[set[str]], list[list[str]]]:
    """返回 (逐位允许集合, 全部合法组合)。

    枚举范围 = 一三五位（`range(0, n, 2)`，去掉句尾），二四六位由骨架钉死。
    """
    skeleton = BASE[(n, start, tail)]
    free = [i for i in range(0, n, 2) if i != n - 1]
    legal = []
    for combo in product("PZ", repeat=len(free)):
        cells = list(skeleton)
        for i, c in zip(free, combo):
            cells[i] = c
        if violates_R1_hard(cells) or violates_R2_guping(cells, skeleton):
            continue
        legal.append(cells)
    legal.sort()
    sets = [{cells[i] for cells in legal} for i in range(n)]
    return sets, legal


def _tone_name(sym: str) -> str:
    return {"P": "平", "Z": "仄"}[sym]


def set_to_name(s) -> str:
    """把允许集合渲染成人话。同时接受 P/Z 符号与「平/仄」汉字（两处调用来源不同）。"""
    name = {"P": "平", "Z": "仄", "平": "平", "仄": "仄"}
    return "、".join(name[x] for x in sorted(s))


def skeleton_of(n: int, start: str, tail: str) -> str:
    return BASE[(n, start, tail)]


def build_matrix(seed: tuple[str, str], n_lines: int, n: int) -> list[dict]:
    """粘对迭代。

    「起」（第 2 字平仄）按交替推：
      对 (1,2)(3,4)(5,6)(7,8) —— 起相反；
      粘 (2,3)(4,5)(6,7)     —— 起相同。
    0 基下标 i 是「新句序号减一」，故 i 为奇数 → 该句是偶数句 → 与上句「对」。

    「收」（句尾平仄）**不能按交替推**：韵句一律平收、非韵句一律仄收。
    偶数句必押韵，故 2/4/6/8 收平；第 1 句收不收由种子决定（首句入韵 = 收平）。
    这条曾经写成「逐句交替」，于是「首句入韵」的诗（第 1、2 句都平收，如杜甫《登高》
    「风急天高猿啸哀 / 渚清沙白鸟飞回」）从第 2 句起整片错位，把好诗判成一片失对 +
    出韵 + 平仄错位。收的判据是「是否韵句」，不是「与上句相反」。
    """
    rhyme_lines = {i for i in range(2, n_lines + 1, 2)}
    if seed[1] == "P":
        rhyme_lines.add(1)
    out = []
    cur_start = seed[0]
    for i in range(n_lines):
        ln = i + 1
        if i > 0:
            cur_start = ("P" if cur_start == "Z" else "Z") if i % 2 == 1 else cur_start
        tail = "P" if ln in rhyme_lines else "Z"
        start_s, tail_s = _tone_name(cur_start), _tone_name(tail)
        sets, legal = allowed_sets(n, start_s, tail_s)
        out.append({"line": ln, "seed": (cur_start, tail),
                    "skeleton": BASE[(n, start_s, tail_s)],
                    "allowed": sets, "legal": legal,
                    "seed_name": "%s起%s收" % (start_s, tail_s)})
    return out


def detect_seeds(line1: str, book) -> list[tuple[str, str]]:
    """由首句反推种子；返回全部可行 (起, 收)，取值为 P/Z 符号（与 BASE 键一致）。"""
    if len(line1) < 2:
        return []
    starts = book.tones(line1[1]) or {"平", "仄"}
    tails = book.tones(line1[-1]) or {"平", "仄"}
    sym = {"平": "P", "仄": "Z"}
    return [(sym[s], sym[t]) for s in ("平", "仄") for t in ("平", "仄")
            if s in starts and t in tails]


def split_lines(text: str, n_lines: int, cpl: int) -> list[str]:
    """把用户文本切成句：优先按标点/换行，其次按字数均分。"""
    punct = "。！？；\n，、,"
    pieces, cur = [], ""
    for ch in text:
        if ch in punct:
            if cur.strip():
                pieces.append(cur)
            cur = ""
        elif "\u4e00" <= ch <= "\u9fff" or ch.isalnum():
            cur += ch
    if cur.strip():
        pieces.append(cur)
    pieces = ["".join(c for c in p if "\u4e00" <= c <= "\u9fff") for p in pieces]
    pieces = [p for p in pieces if p]
    if len(pieces) == n_lines and all(len(p) == cpl for p in pieces):
        return pieces
    joined = "".join(pieces)
    if len(joined) == n_lines * cpl:
        return [joined[i * cpl:(i + 1) * cpl] for i in range(n_lines)]
    return pieces


def _free_positions(allowed_list) -> set[int]:
    """不论位（0 基）：允许集合含两种平仄的位置。"""
    return {j for j, st in enumerate(allowed_list) if len(st) == 2}


def _assign_line(book, line: str, skeleton: str,
                 allowed_list=None) -> tuple[dict[int, Reading], str]:
    """整句统一分配读法，返回 ({字序号(1-based): Reading}, 模式)。

    模式：
      normal —— 全句每个字都有读法可满足骨架（正格或骨架内已含的拗救式）；
      teao   —— 骨架不成立，但按「特拗」形态（第 3、4 字互换）全句可成立；
      partial—— 两种都不成立，逐字取最优，欠配的位留给 V-TONE 报。
    之所以按句而非逐字：多读字（如「怨」平/仄两读）逐字贪心会为了凑骨架而取错读法，
    把真正的拗句误判成平仄错位。
    """

    free = _free_positions(allowed_list) if allowed_list else set()

    def try_pattern(pat: str, *, variant: bool = False):
        """pattern 匹配。

        variant=True 时启用 §11.5「任一读成立即通过」的多读择优 tie-break：
        变体模式（特拗/对句相救）里的每个 P/Z 都是「能让该句成立的读法」，
        只要在不论位上该字**另有**满足模式的读，就优先取它，而不是取 src_rank 最小者。
        否则多读字（如「正是江南好风景」的「正」平仄两读）会被贪心取平，
        使实际 sym 不匹配特拗模式，把合律句误报 V-TONE（gold30 G17）。
        """
        out: dict[int, Reading] = {}
        for j, ch in enumerate(line):
            if j >= len(pat):
                return None
            want = {"P": "平", "Z": "仄"}.get(pat[j])
            if want is None:
                allowed = {"平", "仄"}
            elif j in free:
                # 不论位：正格下通配；变体模式下若该字有契合变体的读则优先取之
                allowed = {"平", "仄"}
            else:
                allowed = {want}
            rs = book.readings(ch, "pingshui")
            if not rs:
                return None
            cand = [r for r in rs if r.tone in allowed]
            if not cand:
                return None
            cand.sort(key=lambda r: (r.src_rank, r.rhyme))
            if variant and want is not None and j in free:
                fit = [r for r in cand if r.tone == want]
                if fit:
                    out[j + 1] = fit[0]           # 取契合变体的读（tie-break）
                    continue
            out[j + 1] = cand[0]
        return out

    got = try_pattern(skeleton)
    if got is not None:
        return got, "normal"
    tps = VARIANT_PATTERNS.get(skeleton, ())
    if isinstance(tps, str):
        tps = (tps,)
    for tp in tps:
        got = try_pattern(tp, variant=True)        # §11.5 多读择优：优先取能让变体成立的读
        if got is not None:
            return got, "variant"
    out: dict[int, Reading] = {}
    for j, ch in enumerate(line):
        allowed = {"平"} if skeleton[j] == "P" else (
            {"仄"} if skeleton[j] == "Z" else {"平", "仄"})
        rs = book.readings(ch, "pingshui")
        if not rs:
            continue
        cand = [r for r in rs if r.tone in allowed] or rs
        cand.sort(key=lambda r: (r.src_rank, r.rhyme))
        out[j + 1] = cand[0]
    return out, "partial"


def _pick_reading(book, ch: str, allowed: set[str]) -> Reading | None:
    rs = book.readings(ch, "pingshui")
    if not rs:
        return None
    for r in rs:
        if r.tone in allowed:
            return r
    return rs[0]


def check_poem(text: str, form: str, *, mode: str = "strict", book,
               forced_group: str | None = None,
               first_line: str | None = None,
               allow_teao: bool = True,
               allow_sanze: bool = True,
               forced_seed: tuple[str, str] | None = None,
               _counterfactual_enabled: bool = True) -> Result:
    """forced_seed 仅供夹具使用：把粘对种子钉死，使「一处篡改」的结论可确定复现。
    它不改变任何判定规则，只是跳过「多解取硬错最少」这一步的择优。

    allow_teao / allow_sanze：变体口径开关。关掉即回到「裁决书 §1.2.1 的 33 式」严格口径，
    特拗与三仄尾都会变成 hard。
    """
    if form not in FORMS:
        return Result(kind="poem", verdict="error", ok=False,
                      confidence={"level": "none", "basis": "error",
                                  "detail": "体式参数非法：%s" % form},
                      notes=["体式须为 %s 之一" % "/".join(FORMS)])
    n_lines, cpl = FORMS[form]
    lines = split_lines(text, n_lines, cpl)
    violations: list[Violation] = []
    if len(lines) != n_lines:
        violations.append(Violation(
            code="V-CHARS", severity="hard", line=0, col=None,
            rule="句数不符", rule_ref="A13 / 近体诗句数",
            actual="%d 句" % len(lines), expected="%d 句" % n_lines,
            evidence={"解析得到": lines}))
        return Result(kind="poem", verdict="violations", ok=False,
                      confidence={"level": "first", "basis": "not_applicable",
                                  "detail": "判据为随包平水韵表"},
                      violations=violations,
                      meta={"form": form, "chars_per_line": cpl, "lines": len(lines)})
    bad_len = [(i + 1, len(l)) for i, l in enumerate(lines) if len(l) != cpl]
    if bad_len:
        for ln, got in bad_len:
            violations.append(Violation(
                code="V-CHARS", severity="hard", line=ln, col=None,
                rule="句内字数不符", rule_ref="近体诗定字",
                actual="%d 字" % got, expected="%d 字" % cpl,
                evidence={"句": lines[ln - 1]}))
        return Result(kind="poem", verdict="violations", ok=False,
                      confidence={"level": "first", "basis": "not_applicable",
                                  "detail": "判据为随包平水韵表"},
                      violations=violations,
                      meta={"form": form, "chars_per_line": cpl, "lines": n_lines})

    seeds = detect_seeds(lines[0], book)
    if forced_group:
        seeds = list(seeds) or seeds
    if first_line == "rhymed":
        seeds = [s for s in seeds if s[1] == "平"] or seeds
    elif first_line == "unrhymed":
        seeds = [s for s in seeds if s[1] == "仄"] or seeds
    if not seeds:
        violations.append(Violation(
            code="V-SEED", severity="hard", line=1, col=None,
            rule="首句不合任何标准句式", rule_ref="裁决书 §1.2 八式",
            actual="/".join(sorted(book.tones(lines[0][1]))), expected="平或仄",
            evidence={"首句": lines[0]}))
        return Result(kind="poem", verdict="violations", ok=False,
                      confidence={"level": "first", "basis": "not_applicable",
                                  "detail": "判据为随包平水韵表"},
                      violations=violations, meta={"form": form})

    # 多解时取「硬错最少」的种子（确定性：并列取 seed 排序首位）
    if forced_seed is not None:
        seeds = [tuple(forced_seed)]
    # 选种子：句尾（韵位）权重最高——「收」逐句交替，句尾对不上说明整篇种子选错了。
    # 权重：句尾 10、其余定字位 3、不论位 1。缺字不计入（算不出就不该拿它当证据）。
    best = None
    for seed in sorted(seeds):
        matrix = build_matrix(seed, n_lines, cpl)
        cost = 0
        for i, line in enumerate(lines):
            for j, ch in enumerate(line):
                ts = book.tones(ch)
                if not ts:
                    continue
                if not (ts & {_tone_name(x) for x in matrix[i]["allowed"][j]}):
                    w = 10 if j == cpl - 1 else (1 if len(matrix[i]["allowed"][j]) == 2 else 3)
                    cost += w
        if best is None or cost < best[0]:
            best = (cost, seed, matrix)
    cost, seed, matrix = best

    readings: list[dict] = []
    chosen: dict[tuple[int, int], Reading] = {}

    # ── 韵脚与韵部 ──────────────────────────────────────────────
    foot_chars = {m["line"]: lines[m["line"] - 1][-1] for m in matrix if m["seed"][1] == "P"}
    foot_missing = sorted(ln for ln, ch in foot_chars.items() if not book.rhyme_sets(ch))
    rhyme_decidable = not foot_missing
    group = forced_group
    if group is None and rhyme_decidable:
        cand_sets = {ln: book.rhyme_sets(ch) for ln, ch in foot_chars.items()}
        counts: dict[str, int] = {}
        for ln, s in cand_sets.items():
            for r in s:
                counts[r] = counts.get(r, 0) + 1
        if counts:
            group = sorted(counts.items(),
                           key=lambda kv: (-kv[1],
                                           book.ps_rhyme_order.index(kv[0])
                                           if kv[0] in book.ps_rhyme_order else 999))[0][0]
    if not rhyme_decidable:
        # 韵脚字判不了 → 整首韵判不了。此时若照常比对，会拿「缺字」当「出韵」，
        # 把数据覆盖问题算成作者的错（登高曾因此被判 3 处出韵）。
        violations.append(Violation(
            code="V-UNDECIDABLE", severity="soft", line=0, col=None,
            rule="韵脚含随包韵表未收录的字，整首韵无法判定",
            rule_ref="A7 / 数据覆盖 / 裁决书 §9.1",
            actual="无法判定", expected=None,
            evidence={"缺字韵脚": [{"句": ln, "字": foot_chars[ln]} for ln in foot_missing],
                      "处置": "不出出韵/重韵结论；整首判数据不足（退出码 3）"}))
    else:
        for ln, ch in sorted(foot_chars.items()):
            rs = book.rhyme_sets(ch)
            if group and group in rs:
                continue
            neighbors = book.neighbors(group, ch, mode) if group else set()
            if rs & neighbors:
                if ln == 1 and mode == "compat":
                    violations.append(Violation(
                        code="V-NEIGHBOR", severity="soft", line=ln, col=cpl,
                        rule="首句借邻韵（孤雁出群格）", rule_ref="A17 / 裁决书 D5",
                        actual="/".join(sorted(rs)), expected=group,
                        evidence={"模式": mode, "邻韵": sorted(rs & neighbors)}))
                    continue
                violations.append(Violation(
                    code="V-RHYME", severity="hard", line=ln, col=cpl,
                    rule="出韵（邻韵不通押）" if rs & neighbors else "出韵",
                    rule_ref="A17 / 裁决书 D5", actual="/".join(sorted(rs)[:3]),
                    expected=group, evidence={"模式": mode,
                                              "邻韵命中": sorted(rs & neighbors)}))
            else:
                violations.append(Violation(
                    code="V-RHYME", severity="hard", line=ln, col=cpl,
                    rule="出韵", rule_ref="A7 / 平水韵部",
                    actual="/".join(sorted(rs)[:3]), expected=group,
                    candidates=book.rhyme_candidates(group, 12) if group else None,
                    evidence={"字": ch}))

        dup = {}
        for ln, ch in foot_chars.items():
            dup.setdefault(ch, []).append(ln)
        for ch, lns in sorted(dup.items()):
            if len(lns) > 1:
                violations.append(Violation(
                    code="V-RHYME-DUP", severity="hard", line=lns[1], col=cpl,
                    rule="重韵（同一字复用为韵脚）", rule_ref="A16",
                    actual=ch, expected="同部他字",
                    candidates=book.rhyme_candidates(group, 12, exclude=ch) if group else None,
                    evidence={"出现句": lns}))

    # ── 逐字平仄：整句统一取读法 ─────────────────────────────────
    # 多读字必须**按句**分配，不能逐字贪心：如「怨」兼平/仄两读，逐字贪心会取平
    # 以「凑合骨架」，反而让真正的拗句（特拗）失配。故按 骨架 → 特拗 → 逐字 三段降级。
    line_modes: dict[int, str] = {}
    pending: list[tuple[int, int, str, Reading, set[str], dict]] = []
    line_missing: dict[int, list[tuple[int, str]]] = {}
    for i, line in enumerate(lines):
        m = matrix[i]
        sk = m["skeleton"]
        assign, mode = _assign_line(book, line, sk, m["allowed"])
        # §13.7：五言首句若各字可用读法能拼出「仄平仄平仄」(ZPZPZ)，按 §11.5 的
        # 「任一读成立即通过」tie-break 取该组读法——否则 PPPZZ 骨架的第 4 位定仄
        # 会逼「庭」取仄，把王力举例「昔闻洞庭水」掩盖成 ZPZZZ 落调（假硬错）。
        # 仅首句、仅五言、仅当全部 5 字都有契合读法时才覆盖；不推广到其余句。
        if i == 0 and len(line) == 5:
            forced: dict[int, Reading] = {}
            for j, ch in enumerate(line):
                want = "平" if TEAO_FIRST_PATTERN[j] == "P" else "仄"
                fit = sorted((r for r in book.readings(ch, "pingshui") if r.tone == want),
                             key=lambda r: (r.src_rank, r.rhyme))
                if not fit:
                    forced = {}
                    break
                forced[j + 1] = fit[0]
            if forced:
                assign, mode = forced, "teao_first"
        line_modes[i + 1] = mode
        for j, ch in enumerate(line):
            r = assign.get(j + 1)
            if r is None:
                line_missing.setdefault(i + 1, []).append((j + 1, ch))
                continue
            chosen[(i + 1, j + 1)] = r
            alternatives = [{"tone": x.tone, "rhyme": x.rhyme}
                            for x in book.readings(ch) if x.tone != r.tone or x.rhyme != r.rhyme]
            if alternatives or len(book.readings(ch)) > 1:
                readings.append({"line": i + 1, "col": j + 1, "char": ch,
                                 "chosen": {"tone": r.tone, "tone_class": r.tone_class,
                                            "rhyme": r.rhyme},
                                 "alternatives": alternatives})
            allowed = {_tone_name(x) for x in m["allowed"][j]}
            if r.tone not in allowed:
                pending.append((i + 1, j + 1, ch, r, allowed, m))

    # 级联抑制（第 1 层）：句内含未收录字 → 该句平仄判不了，只报一条 V-UNDECIDABLE。
    # 之前逐字报 V-NODATA 之后仍继续判平仄/出韵/重韵，一个字判不了就级联出一整首假错。
    for ln, items in sorted(line_missing.items()):
        violations.append(Violation(
            code="V-UNDECIDABLE", severity="soft", line=ln, col=None,
            rule="句内含随包韵表未收录的字，该句平仄无法判定",
            rule_ref="A7 / 数据覆盖 / 裁决书 §9.1",
            actual="无法判定", expected=None,
            evidence={"句": lines[ln - 1],
                      "缺字": [{"col": c, "char": ch} for c, ch in items],
                      "缺字清单": [ch for _, ch in items],
                      "处置": "该句不输出 平仄错位/三仄尾/三平尾/失对/失粘 结论"}))

    # 孤平优先：触发配对的两处不再重复报 V-TONE（否则「孤平」会被淹没在平仄错位里）
    guping_lines: dict[int, dict] = {}
    for i, line in enumerate(lines):
        cells = [chosen[(i + 1, j + 1)].tone if (i + 1, j + 1) in chosen else "?"
                 for j in range(len(line))]
        if "?" in cells:
            continue
        sym = "".join("P" if c == "平" else "Z" for c in cells)
        ev = guping_evidence(sym, matrix[i]["skeleton"])
        if ev:
            ev["实际句式"] = sym
            guping_lines[i + 1] = ev

    for ln, col, ch, r, allowed, m in pending:
        if ln in line_missing:
            continue                      # 级联抑制：该句本身判不了，不输出平仄错位
        ev = guping_lines.get(ln)
        if ev:
            a, b = GUPING_PAIR[m["skeleton"]]
            if col in (a + 1, b + 1):
                continue
        violations.append(Violation(
            code="V-TONE", severity="hard", line=ln, col=col,
            rule="平仄错位", rule_ref="裁决书 §1.2 句式表",
            actual=r.tone, expected=set_to_name(allowed),
            expected_set=sorted(allowed),
            evidence={"字": ch, "句式": m["skeleton"], "标注": m["seed_name"],
                      "该字平水韵读法": [[x.tone, x.rhyme] for x in book.readings(ch)]}))

    # ── 三仄尾 / 三平尾 / 孤平（基于实读平仄，规则同生成层） ────────
    sanze_at: set[tuple[int, int]] = set()
    for i, line in enumerate(lines):
        if i + 1 in line_missing:
            continue
        actual = [chosen[(i + 1, j + 1)].tone if (i + 1, j + 1) in chosen else "?"
                  for j in range(len(line))]
        if len(actual) != cpl or "?" in actual:
            continue
        sym = "".join("P" if c == "平" else "Z" for c in actual)
        sk = matrix[i]["skeleton"]
        tri = list(sym[-3:]) if len(sym) >= 3 else []
        pair = SANZE_PAIR.get(cpl)
        pair_desc = ("第 %d、%d 字不得同仄" % (pair[0] + 1, pair[1] + 1)) if pair else "配对位不得同仄"
        if violates_R1a_sanping(list(sym)):
            # R1-a：三平尾，无条件 hard
            code, sev, exp = "V-SANPING", "hard", "仄"
            rule = "三平尾（近体大忌）"
            rule_ref = "裁决书 §13.1 R1-a / 王力《诗词格律》586、3383"
            ev = {"句末三字": "".join(actual[-3:]), "句式": sk, "实际句式": sym}
        elif violates_R1b_sanze(list(sym)):
            # R1-b 落调：末三字全仄且配对位同仄 → hard
            # 王力《诗词格律》2456/2458「若仄平仄仄仄，则为落调矣。盖下有三仄，上必二平也」。
            code, sev, exp = "V-SANZE", "hard", "平"
            rule = "三仄尾落调：%s（下有三仄，上必二平）" % pair_desc
            rule_ref = "裁决书 §13.1 R1-b / 王力《诗词格律》2456/2458、《汉语诗律学》3433 註十一"
            ev = {"句末三字": "".join(actual[-3:]), "句式": sk, "实际句式": sym,
                  "配对位": [pair[0] + 1, pair[1] + 1] if pair else [],
                  "配对位实为": [sym[pair[0]], sym[pair[1]]] if pair else [],
                  "判据": "末三字全仄时，%s" % pair_desc}
        elif is_legal_sanze(list(sym)):
            # R1-b 合法：三仄尾但配对位不同仄 → soft 变体提示（可忽略），不判错
            code, sev, exp = "V-SANZE-SOFT", "soft" if allow_sanze else "soft", "平"
            rule = "三仄尾合法（下有三仄、上必二平：%s）" % pair_desc
            rule_ref = "裁决书 §13.1 R1-b / 王力《诗词格律》2456/2458"
            ev = {"句末三字": "".join(actual[-3:]), "句式": sk, "实际句式": sym,
                  "配对位": [pair[0] + 1, pair[1] + 1] if pair else [],
                  "配对位实为": [sym[pair[0]], sym[pair[1]]] if pair else [],
                  "判据": "配对位不同仄，故为合法三仄尾，不是出律"}
        else:
            tri = []                      # 句末三字不同声，无三忌
        if tri:
            sanze_at.add((i + 1, cpl - 2))
            violations.append(Violation(
                code=code, severity=sev,
                line=i + 1, col=cpl - 2,
                rule=rule, rule_ref=rule_ref,
                actual=actual[-1], expected=exp,
                evidence=ev))
        if i + 1 in guping_lines:
            ev = guping_lines[i + 1]
            a, b = GUPING_PAIR[sk]
            violations.append(Violation(
                code="V-GUPING", severity="hard", line=i + 1, col=a + 1,
                rule="孤平", rule_ref="裁决书 §1.2.1 / §1.3",
                actual="仄", expected="平",
                evidence=ev))

    # 三仄尾/三平尾位置上的 V-TONE 与句末三字诊断是**同一件事**，只报一次：
    # 否则「潮平两岸阔」（平平仄仄仄）会同时报三仄尾与平仄错位，读者以为是两处错。
    if sanze_at:
        violations = [v for v in violations
                      if not (v.code == "V-TONE" and (v.line, v.col or 0) in sanze_at)]

    # ── 特拗识别（§12.1：绝对模式匹配）──────────────────────────
    # 只认完整字面 `平平仄平仄` / `仄仄平平仄平仄`，逐字相等（sym == want），
    # 不跳过任何自由位——跳过即退化成「3、4 字互换」相对特征，会放行 `仄平仄平仄`。
    # 第 1 字（七言第 3 字）为仄时不命中，照普通句式判（王力「必须用平声」）。
    act_sym: dict[int, str] = {}
    for i, line in enumerate(lines):
        cells = [chosen[(i + 1, j + 1)].tone if (i + 1, j + 1) in chosen else "?"
                 for j in range(len(line))]
        if "?" not in cells:
            act_sym[i + 1] = "".join("P" if c == "平" else "Z" for c in cells)

    teao_lines: dict[int, dict] = {}
    for i, line in enumerate(lines):
        sym = act_sym.get(i + 1)
        if sym is None:
            continue
        sk = matrix[i]["skeleton"]
        want = TEAO_PATTERNS.get(sk)
        if want is None or sym != want:       # 绝对相等，含首字必须为平
            continue
        teao_lines[i + 1] = {
            "骨架": sk, "实际": sym, "位置": list(TEAO_POS.get(sk, (3, 4))),
            "性质": "特拗：第 3、4 字平仄互换，属通行拗救变体，不是失律",
            "说明": "本句自救（五言 3 拗 4 救 / 七言 5 拗 6 救）；首字用平为绝对前提",
        }

    for ln, ev in sorted(teao_lines.items()):
        violations.append(Violation(
            code="V-TEAO", severity="soft" if allow_teao else "hard",
            line=ln, col=None,
            rule=("特拗（第 3、4 字互换），通行拗救变体，不作失律"
                  if allow_teao else "特拗（严格口径下视为平仄错位）"),
            rule_ref="王力「特定的一种平仄格式」/ 裁决书 §12.1",
            actual=ev["实际"], expected=ev["骨架"], evidence=ev))

    # ── 首句特拗首字仄（§13.7 王力脚注举例例外，独立于 V-TEAO）──
    # §12.1 的 V-TEAO 要求首字必平（绝对模式 PPZPZ）；脚注①另给首句「仄平仄平仄」两例，
    # 但王力未给可推广条件——故只认「首句 + 五言 ZPZPZ」，绝不并入 V-TEAO，
    # 否则「首句任意仄起」都会被当成特拗放行。evidence.王力举例 标注是否王力原句。
    teao_first_lines: dict[int, dict] = {}
    teao_first_unknown = False
    if lines and cpl == 5:                       # 只做五言（七言例外王力未举例）
        first_raw = "".join(c for c in lines[0] if "\u4e00" <= c <= "\u9fff")
        first_sym = act_sym.get(1)
        if first_sym is None or (1 in line_missing):
            # 首句有未收录字：仅当**已知位的读法都与 ZPZPZ 相容**时，才是
            # 「首字仄例外」的真候选但因缺字无法判定 → unable（不猜）。
            # 否则该句本就不像例外，交给常规 NODATA 路径（insufficient_data），
            # 避免把任意首句缺字的诗都升级成 unable。
            partial = ""
            for j, ch in enumerate(lines[0][:5]):
                ts = book.tones(ch)
                partial += ("?" if not ts else
                            "P" if ts == {"平"} else
                            "Z" if ts == {"仄"} else "X")
            if len(partial) == 5 and all(
                    a == "?" or a == b for a, b in zip(partial, TEAO_FIRST_PATTERN)):
                teao_first_unknown = True
        elif first_sym == TEAO_FIRST_PATTERN:
            # §13.7.1：王力对「昔闻洞庭水」两处归类矛盾（1873 脚注=特拗首字例外 /
            # 2512 行=用古体诗平仄的例外），两处均原文，并列记录、不静默择一。
            _is_example = first_raw in WANGLI_FIRST_EXAMPLES
            _also_guti = first_raw in WANGLI_GUTI_RECLASSIFIED
            evf = {
                "实际": first_sym, "原句": first_raw,
                "王力举例": _is_example,               # 1873 脚注①：首字仄特拗例外
                "王力另归古体例外": _also_guti,        # 2512 行：用古体诗的平仄（并列，不择一）
                "说明": (
                    "王力原文两处并存：1873 脚注①列为此特拗首字例外，2512 行又归入"
                    "「例外——用古体诗的平仄」。两处均为王力原文，机器不判哪处更权威，"
                    "并列记录（裁决 §13.7.1）"
                    if _also_guti else
                    "王力《诗词格律概要》脚注①原句（首字仄的特拗例外）"
                    if _is_example else
                    "首句符合同形模式，但王力脚注仅举两句、未给推广条件；"
                    "仅 soft 提示，不作规则放宽"),
                "位置": [1, 3],
            }
            teao_first_lines[1] = evf
            violations.append(Violation(
                code="V-TEAO-FIRST", severity="soft", line=1, col=None,
                rule="首句特拗首字仄（王力脚注举例的例外，非可推广规则）",
                rule_ref="王力《诗词格律概要》1873 脚注① / 裁决书 §13.7",
                actual=first_sym, expected="PPZPZ", evidence=evf))
    # 首句被强制取 ZPZPZ 读法后，相对骨架 PPPZZ 的差异位是 1/3/4（第1字不论、
    # 第3字该平、第4字该仄），三处都是同一次「首字仄引发的互换」的组成部分，
    # 不能只压 1/3 而留 4 报硬错。这一段对**所有**取到 ZPZPZ 读法的首句生效，
    # 包括「昔闻洞庭水」这类古体例外——它虽不报 V-TEAO-FIRST，但其读法已由
    # §11.5 择优确定，不能再把第 4 字当平仄错位报硬错（否则与 V-SANZE 误报同源）。
    if allow_teao and (teao_first_lines or line_modes.get(1) == "teao_first"):
        _teao_first_cols = {1, 3, 4}
        violations = [v for v in violations
                      if not (v.code == "V-TONE" and v.line == 1
                              and v.col in _teao_first_cols)]

    # ── 对句相救联判（§12.2：归一到骨架 + 强制联判，不登记豁免）──
    # 出句第 4 字（七言第 6 字）拗 → 必须取对句验证救位（五 3 / 七 5）。
    # 与特拗不同：这是**跨句配对**，必须同时报拗位与救位两个坐标。
    aojiu_lines: dict[int, dict] = {}
    aojiu_rescue_unknown: set[int] = set()
    aojiu_unrescued: dict[int, dict] = {}
    for i in range(0, n_lines - 1):           # 出句 1,3,5,7（含末句无对句者跳过）
        if (i + 1) % 2 == 0:                  # 只看出句（奇数句）
            continue
        sym = act_sym.get(i + 1)
        if sym is None or (i + 2) not in act_sym:
            continue
        sk = matrix[i]["skeleton"]
        if sk not in AOJIU_AO_COL:                 # 非仄起仄收出句骨架，不入联判通道
            continue
        ao_col = AOJIU_AO_COL[sk]             # 拗位（1 基）
        sv_col = AOJIU_SAVE_COL[sk]           # 救位（1 基）
        # 王力「向晚意不适」的「不」在平水韵兼平/仄两读，多读择优会为凑骨架取平声，
        # 把拗句掩盖成 ZZZPZ。故拗位取**可用读法**判定：若把拗位改取仄声后整句
        # 满足 R4 结构判据，即认定拗句（《汉语诗律学》「三四两字都用仄」例一致）。
        ao_char = lines[i][ao_col - 1]
        ao_tones = book.tones(ao_char)
        chosen_is_ao = is_aojiu_out(sym)           # 实际读法本身就满足出句结构
        matched_sym = sym if chosen_is_ao else None
        alt_sym = None
        if matched_sym is None and ao_tones and "仄" in ao_tones:
            alt = sym[:ao_col - 1] + "Z" + sym[ao_col:]
            if is_aojiu_out(alt):
                alt_sym, matched_sym = alt, alt
        if matched_sym is None:
            continue
        # 救位在对句；对句该字 NODATA → 整首 unable（不得放行也不得判错）
        sav_r = chosen.get((i + 2, sv_col))
        if sav_r is None:
            aojiu_rescue_unknown.add(i + 1)
            continue
        rescued = sav_r.tone == "平"
        # 反事实（借多读字的仄读）只用于 soft 报导：救位为平时给出带双坐标的变体说明，
        # 满足 §12.2 反向哨兵（李商隐「向晚意不适」= ZZPZZ/ZZZZZ 而「不」兼平仄）。
        # 但它**不得**制造硬错：拗位实际取平、仅因「另有仄读」就判 V-AOJIU_UNRESCUED，
        # 会把「此地一为别」等大量正格误判（结构同形、只靠语义区分，数据无法裁决）——
        # 按 §9.1「不能确定时必须说不能确定」，这种情形宁可不报硬错。
        if not rescued and not chosen_is_ao:
            continue
        ev = {
            "骨架": sk, "出句实际": matched_sym, "对句实际": act_sym[i + 2],
            "判定来源": "实际读法" if chosen_is_ao else "多读字取仄（反事实，仅 soft）",
            "拗位": {"句": i + 1, "字": ao_col, "字名": ao_char,
                     "读法": sorted(ao_tones)},
            "救位": {"句": i + 2, "字": sv_col, "字名": lines[i + 1][sv_col - 1],
                     "实为": sav_r.tone},
            "要求": "对句第 %d 字须为平（王力「此字必平，救上句」）" % sv_col,
            "性质": "对句相救：出句第 %d 字拗、对句第 %d 字救" % (ao_col, sv_col),
        }
        if rescued:
            aojiu_lines[i + 1] = ev
        else:
            aojiu_unrescued[i + 1] = ev

    for ln, ev in sorted(aojiu_lines.items()):
        violations.append(Violation(
            code="V-AOJIU", severity="soft",
            line=ln, col=ev["拗位"]["字"],
            rule=("对句相救（出句第 %d 字拗、对句第 %d 字救，通行变体）"
                  % (ev["拗位"]["字"], ev["救位"]["字"])),
            rule_ref="王力《诗词格律》「六、拗救」(b) / 裁决书 §12.2",
            actual=ev["出句实际"], expected=ev["骨架"], evidence=ev))
    for ln, ev in sorted(aojiu_unrescued.items()):
        violations.append(Violation(
            code="V-AOJIU_UNRESCUED", severity="hard",
            line=ln, col=ev["拗位"]["字"],
            rule=("拗而未救：出句第 %d 字拗，但对句第 %d 字为%s（王力「必须」为平）"
                  % (ev["拗位"]["字"], ev["救位"]["字"], ev["救位"]["实为"])),
            rule_ref="王力《诗词格律》「六、拗救」(b) / 裁决书 §12.2 条 3",
            actual=ev["救位"]["实为"], expected="平", evidence=ev))
    for ln in sorted(aojiu_rescue_unknown):
        violations.append(Violation(
            code="V-UNDECIDABLE", severity="soft", line=ln, col=None,
            rule="对句救位含随包韵表未收录的字，无法验证是否已救",
            rule_ref="裁决书 §12.2 条 4 / §9.1",
            actual="无法判定", expected=None,
            evidence={"出句": ln, "救位句": ln + 1,
                      "处置": "整首降 unable；不按已救放行，也不按未救判错"}))

    # 特拗 / 对句相救位置上的 V-TONE 已由对应变体码说明，去掉重复报告。
    drop: set[tuple[int, int]] = set()
    for ln, ev in teao_lines.items():
        drop |= {(ln, col) for col in ev["位置"]}
    for ln, ev in list(aojiu_lines.items()) + list(aojiu_unrescued.items()):
        drop.add((ln, ev["拗位"]["字"]))
    if drop:
        violations = [v for v in violations
                      if not (v.code == "V-TONE" and (v.line, v.col) in drop)]

    # ── 失对 / 失粘（结构性诊断） ──────────────────────────────
    # 口径（统一为一种，不留中间态）：**本句实际 二四六 位 vs 本句「期望骨架」**。
    # 期望骨架 = 粘对矩阵给出的本句骨架；若本句已判定为特拗，取其**复原后的骨架字**
    # （第 3、4 字 / 七言第 5、6 字再换回来）。特拗归一必须发生在这一段**之前**——
    # 否则特拗句的第 4 字（七言第 6 字）由仄变平后会被算成「偏离骨架」，产生假失对/假失粘。
    # 矩阵正确时期望骨架天然满足「与上句相对 / 相粘」，故比本句期望 ≡ 比与上句的粘对关系；
    # 证据里同时给出上句实际二四六，便于人工核对是哪一侧错。
    expect_sym: dict[int, str] = {}
    for i in range(n_lines):
        sk = matrix[i]["skeleton"]
        ln = i + 1
        if ln in teao_lines:
            # 特拗：换回正格骨架（3、4 字再换一次）
            expect_sym[ln] = TEAO_PATTERNS.get(sk, sk)
        elif ln in aojiu_lines or ln in aojiu_unrescued:
            # 对句相救出句：拗位（第 4/6 字）变仄，若仍用正格骨架会被算成偏离 →
            # 用命中到的变体模式作期望，与特拗归一同一口径，避免假失对/假失粘。
            pats = AOJIU_OUT.get(sk)
            expect_sym[ln] = act_sym.get(ln, pats[0] if pats else sk)
        else:
            expect_sym[ln] = sk

    structural: dict[tuple[int, int], str] = {}
    struct_rows: dict[int, dict] = {}
    for i in range(1, n_lines):
        cur = i + 1
        if cur in line_missing or i in line_missing:
            continue
        kind = "对" if cur % 2 == 0 else "粘"
        bones = [j for j in range(1, cpl, 2)]
        act, exp = [], []
        for j in bones:
            r = chosen.get((cur, j + 1))
            act.append(None if r is None else r.tone)
            exp.append(_tone_name(expect_sym[cur][j]))
        if any(a is None for a in act):
            continue
        mism = [j for j, a, s in zip(bones, act, exp) if a != s]
        if len(mism) < 2:
            continue
        prev = [chosen.get((i, j + 1)) for j in bones]
        prev_t = [None if r is None else r.tone for r in prev]
        for j in mism:
            structural[(cur, j + 1)] = kind
        struct_rows[cur] = {"kind": kind, "act": act, "exp": exp, "mism": mism,
                            "prev": prev_t, "skeleton": matrix[i]["skeleton"],
                            "expect": expect_sym[cur]}

    # 折腰体：**只有第三句失粘**，其余全部合骨架 → 绝句通行变体，不是失律。
    # 只报「是折腰体」等于发免罪符，必须同时报出失粘位置与上句实际二四六。
    # 折腰体按 §11.3 是「绝句通行变体」——只在 4 句体（五绝/七绝）成立。
    # 8 句律诗若仅第 3 句失粘，是刻意构造的单点失粘（夹具 F08），应报 V-NIAN，
    # 不得套用绝句的折腰免罪符。
    zheyao = None
    if n_lines == 4 and list(sorted(struct_rows)) == [3] and struct_rows[3]["kind"] == "粘":
        zheyao = struct_rows[3]
        violations.append(Violation(
            code="V-ZHEYAO", severity="soft", line=3, col=None,
            rule="折腰体：第三句不与第二句相粘，绝句通行变体（其余各句均合骨架）",
            rule_ref="王力《汉语诗律学》折腰体 / 裁决书 §7.6",
            actual="".join(zheyao["act"]), expected="".join(zheyao["exp"]),
            evidence={"失粘位置": [j + 1 for j in zheyao["mism"]],
                      "上句": 2, "上句二四六": "".join(t or "?" for t in zheyao["prev"]),
                      "本句二四六": "".join(zheyao["act"]),
                      "期望二四六": "".join(zheyao["exp"]),
                      "骨架": zheyao["skeleton"],
                      "判定依据": "仅第 3 句二四六位与期望骨架有 ≥2 位差异，"
                                  "第 4～8 句（若有）均合骨架；故判为折腰而非整首失粘"}))

    for cur, row in sorted(struct_rows.items()):
        if zheyao is not None and cur == 3:
            continue
        kind = row["kind"]
        violations.append(Violation(
            code="V-NIE" if kind == "对" else "V-NIAN", severity="hard",
            line=cur, col=None,
            rule=("失对（本句二四六位应与其「期望骨架」相反）" if kind == "对"
                  else "失粘（本句二四六位应与其「期望骨架」相同）"),
            rule_ref="裁决书 D6 / 粘对",
            actual="".join(row["act"]), expected="".join(row["exp"]),
            evidence={"上句": cur - 1, "上句二四六": "".join(t or "?" for t in row["prev"]),
                      "本句二四六": "".join(row["act"]),
                      "期望二四六": "".join(row["exp"]),
                      "骨架": row["skeleton"], "期望骨架": row["expect"],
                      "偏离位置": [j + 1 for j in row["mism"]],
                      "判定依据": "≥2 位偏离本句期望骨架，判为整句粘对关系错而非单字错位"}))

    # 抑制结构性失对/失粘位置上的重复 V-TONE
    if structural:
        violations = [v for v in violations
                      if not (v.code == "V-TONE" and (v.line, v.col or 0) in structural)]

    # ── 同字复用（软提示，A16） ────────────────────────────────────
    seen: dict[str, list] = {}
    for i, line in enumerate(lines):
        for j, ch in enumerate(line):
            seen.setdefault(ch, []).append([i + 1, j + 1])
    for ch, pos in sorted(seen.items()):
        if len(pos) > 1 and ch not in foot_chars.values():
            if any((ln, col) in structural or ln in line_missing for ln, col in pos):
                continue                  # 级联抑制：结构错/缺字句不重复报软提示
            violations.append(Violation(
                code="V-REPEAT", severity="soft", line=pos[1][0], col=pos[1][1],
                rule="同字复用，宜斟酌", rule_ref="A16 软提示",
                actual=ch, expected=None, evidence={"出现位置": pos}))

    duizhang = None
    if form in ("wulv", "qilv"):
        duizhang = check_duizhang(lines, matrix, book, chosen)

    violations.sort(key=lambda v: (v.line, v.col or 0, v.code))

    # ── 反事实重判（第 4 层）：不得用「我们不知道的读音」断言作者错了 ─────
    # 若硬错涉及「缺读嫌疑清单」里的字（词林收了某声，平水表未收，且审查未放行），
    # 假设它取另一声调重算一次；若全诗随之无硬错 → 降为 unable，不输出 fail。
    hard = [v for v in violations if v.severity == "hard"]
    counterfactual = None
    if hard and _counterfactual_enabled and _suspect_of is not None:
        cands = sorted({v.evidence.get("字") for v in hard
                        if v.code in ("V-TONE", "V-RHYME", "V-SANZE-SOFT", "V-GUPING",
                                      "V-RHYME-DUP")
                        and v.evidence.get("字")} & _suspect_of(book))
        for ch in cands[:4]:
            alt = "仄" if book.tones(ch) == {"平"} else "平"
            trial = check_poem(text, form, mode=mode, book=_tone_overlay(book, {ch: alt}),
                               forced_group=forced_group, first_line=first_line,
                               allow_teao=allow_teao, forced_seed=forced_seed,
                               allow_sanze=allow_sanze, _counterfactual_enabled=False)
            if not trial.hard_count:
                violations = [v for v in violations if v.severity != "hard"]
                counterfactual = {"字": ch, "假设读法": alt,
                                  "反事实结果": "全诗无硬错",
                                  "处置": "verdict 降为 unable：疑似缺读，需人工确认",
                                  "说明": "该字在词林正韵有对岸声调、平水韵表未收；"
                                          "不能用未知读音断言作者出律"}
                break
        if counterfactual:
            violations.append(Violation(
                code="V-UNDECIDABLE", severity="soft", line=0, col=None,
                rule="疑似缺读：假设该字另有一读即全篇合律，故不出硬判",
                rule_ref="裁决书 §9.1 / reading_suspect_queue.json",
                actual=counterfactual["字"], expected=None, evidence=counterfactual))
            hard = []

    undecidable = [v for v in violations if v.code == "V-UNDECIDABLE"]
    variants = [v for v in violations if v.code in ("V-TEAO", "V-TEAO-FIRST", "V-ZHEYAO",
                                                     "V-NEIGHBOR", "V-AOJIU")
                and v.severity == "soft"]
    # §12.2 条 4：对句救位 NODATA → 整首 unable（既不按已救放行，也不按未救判错）。
    # §13.7：首句例外的证据（原句/符号串）因缺字无法确定 → 同样 unable，不猜。
    aojiu_unknown = bool(aojiu_rescue_unknown)
    if hard:
        verdict = "violations"
    elif counterfactual or aojiu_unknown or teao_first_unknown:
        verdict = "unable"
    elif undecidable:
        verdict = "insufficient_data"      # 缺字 ≠ 出律：判不了就说判不了
    elif variants:
        verdict = "pass_with_variant"
    else:
        verdict = "pass"
    result = Result(
        kind="poem", verdict=verdict,
        ok=not hard and not undecidable and not counterfactual and not aojiu_unknown
           and not teao_first_unknown,
        confidence={"level": "first", "basis": "not_applicable",
                    "detail": "近体诗校验不依赖词谱，判据为随包平水韵表 + 词林正韵补全"},
        violations=violations,
        readings=sorted(readings, key=lambda r: (r["line"], r["col"])),
        duizhang=duizhang,
        meta={"form": form, "form_name": FORM_NAME[form], "chars_per_line": cpl,
              "lines": n_lines, "rhyme_mode": mode, "rhyme_book": "pingshui",
              "rhyme_group_used": group, "rhyme_decidable": rhyme_decidable,
              "first_line": "rhymed" if seed[1] == "P" else "unrhymed",
              "seed": [seed[0], seed[1]],
              "seed_name": "%s起%s收" % (_tone_name(seed[0]), _tone_name(seed[1])),
              "hard_count": len(hard),
              "soft_count": len(violations) - len(hard),
              "nodata_count": len(undecidable),
              "undecidable_lines": sorted(line_missing),
              "nodata_chars": sorted({x["char"] for v in undecidable
                                      for x in v.evidence.get("缺字", [])}
                                     | {x["字"] for v in undecidable
                                        for x in v.evidence.get("缺字韵脚", [])}),
              "allow_teao": allow_teao, "allow_sanze": allow_sanze,
              "teao_lines": sorted(teao_lines),
              "teao_first_lines": sorted(teao_first_lines),
              "teao_first_unknown": teao_first_unknown,
              "zheyao": bool(zheyao),
              "aojiu_lines": sorted(aojiu_lines),
              "aojiu_unrescued": sorted(aojiu_unrescued),
              "aojiu_rescue_unknown": sorted(aojiu_rescue_unknown),
              "structural_lines": sorted(struct_rows),
              "variants": sorted({v.code for v in variants}),
              "counterfactual": counterfactual,
              "supplement": {"original_chars": book.sup.get("original_chars"),
                             "added_chars": book.sup.get("added_chars"),
                             "total_after": book.sup.get("total_after")}},
        data_source={"files": {"pingshui.json": _paths.data_digest("pingshui.json"),
                               "supplement.json": _paths.data_digest("supplement.json"),
                               "linvun.json": _paths.data_digest("linvun.json")}},
    )
    if verdict == "pass":
        result.notes.append("全部可判定维度通过；软提示不参与「合律」判定")
    elif verdict == "pass_with_variant":
        result.notes.append("识别出通行变体（%s），不计为出律：%s"
                            % ("、".join(result.meta["variants"]),
                               "；".join(v.rule for v in variants)))
    elif verdict == "unable":
        if counterfactual:
            result.notes.append("疑似缺读：%s 若取%s声则全篇合律（词林正韵有该读，平水韵表未收）。"
                                "已判定的位置无硬错，不出硬判，需人工确认读音。"
                                % (counterfactual["字"], counterfactual["假设读法"]))
        elif teao_first_unknown:
            result.notes.append("首句含随包韵表未收录的字，无法判定是否为王力脚注的首字仄特拗例外；"
                                "不猜、不放行，整首降为 unable，需人工确认。")
        else:
            result.notes.append("对句相救的救位字无法判定（第 %s 句），"
                                "既不能按已救放行也不能按未救判错，整首降为 unable。"
                                % "、".join(str(x + 1) for x in aojiu_rescue_unknown))
    elif verdict == "insufficient_data":
        result.notes.append("存在随包韵表未收录的字（%s），这些位置判不了；"
                            "已判定的位置无硬错。缺字属数据覆盖问题，不计为用户出律。"
                            % ("、".join(result.meta["nodata_chars"])))
    return result


# ── 反事实重判所需的两个钩子 ─────────────────────────────────────
def _suspect_of(book):
    """缺读嫌疑清单：词林收了、平水表未收、审查未放行的字。"""
    return set(getattr(book, "_suspect_chars", set()))


class _ToneOverlay:
    """只改「平仄」不改「韵部」的只读视图：反事实重算用。

    韵部不动是刻意的——反事实要问的是「若这个字是另一声调，平仄是否就合」，
    不是「若它属于另一韵部」；改韵部会把出韵也一起洗掉，等于自证清白。
    """

    def __init__(self, base, extra: dict) -> None:
        self._base = base
        self._extra = extra

    def __getattr__(self, name):
        return getattr(self._base, name)

    def readings(self, ch: str, book: str = "pingshui"):
        rs = list(self._base.readings(ch, book))
        for tone in self._extra.get(ch, ()):
            if all(r.tone != tone for r in rs):
                rs.append(Reading(char=ch, tone=tone, tone_class=tone,
                                  rhyme="（反事实假设）", book="counterfactual",
                                  src_rank=999999))
        return rs

    def tones(self, ch: str) -> set[str]:
        return {r.tone for r in self.readings(ch, "pingshui")}


def _tone_overlay(book, extra: dict):
    return _ToneOverlay(book, extra)


def check_duizhang(lines, matrix, book, chosen) -> dict:
    """对仗三层：L1 硬判（字数/二四六/句末），L2 not_supported，L3 证据软判。

    L3 在原有「同字复现」信号之外，新增基于内置出处词典的义类/工拙证据：
    工对/邻对/宽对/借对/合掌/unable，全部软判、给证据、覆盖不到标 unable。
    键名用 DZ-* 前缀，与句式生成规则的孤平配对约束区分（裁决书 §1.2.1 符号消歧）。
    """
    pairs = [(3, 4), (5, 6)]
    items = []
    status = "pass"
    for a, b in pairs:
        if a > len(lines) or b > len(lines):
            continue
        ca = [chosen.get((a, j + 1)) for j in range(len(lines[a - 1]))]
        cb = [chosen.get((b, j + 1)) for j in range(len(lines[b - 1]))]
        checks = {"字数相等": len(lines[a - 1]) == len(lines[b - 1])}
        cols = [j for j in range(1, len(lines[a - 1]), 2) if j < len(lines[b - 1])]
        rel = True
        fail_at = None
        for j in cols:
            if ca[j] is None or cb[j] is None:
                continue
            if ca[j].tone == cb[j].tone:
                rel = False
                fail_at = {"line": b, "col": j + 1}
                break
        checks["二四六相对"] = rel
        tail_rel = True
        if ca and cb and ca[-1] and cb[-1]:
            tail_rel = ca[-1].tone != cb[-1].tone
            if not tail_rel and fail_at is None:
                fail_at = {"line": b, "col": len(lines[b - 1])}
        checks["句末平仄相对"] = tail_rel
        ok = all(checks.values())
        if not ok:
            status = "fail"
        items.append({"pair": [a, b], "checks": checks, "pass": ok, "fail_at": fail_at})
    sig = []
    seen = {}
    for i, line in enumerate(lines):
        for j, ch in enumerate(line):
            seen.setdefault(ch, []).append([i + 1, j + 1])
    for ch, pos in sorted(seen.items()):
        if len(pos) > 1:
            sig.append({"kind": "同字复现", "char": ch, "positions": pos})
    # L3 证据软判：逐联调 duizhang_pos，词典覆盖不到处标 unable（不猜、不判通过）。
    l3_pairs = []
    try:
        import duizhang_pos
        for a, b in pairs:
            if a > len(lines) or b > len(lines):
                continue
            l3_pairs.append({"pair": [a, b],
                             "judgement": duizhang_pos.judge_couplet(lines[a - 1], lines[b - 1])})
        # 扇对（隔句对）：判据为位置规则但实测不可机械化，返回 unable，不误报
        sandui = duizhang_pos.judge_sandui(lines)
        l3_status = "soft_only"
    except Exception as exc:  # 数据缺失或异常：如实说查不了，不猜测、不影响 L1 硬判
        l3_pairs = []
        sandui = {"status": "unable", "reason": "扇对检查随 L3 证据层不可用"}
        l3_status = "not_supported"
        l3_reason = "对仗 L3 证据层不可用：%s" % exc
    else:
        l3_reason = None

    l3 = {"status": l3_status, "signals": sig,
          "label": "证据提示，非定论：软判，不作『对仗已通过』",
          "pairs": l3_pairs, "扇对": sandui}
    if l3_reason:
        l3["reason"] = l3_reason

    return {
        "DZ-L1": {"lines": [[a, b] for a, b in pairs], "status": status, "items": items,
                  "capability": "字数相等 / 二四六字平仄相对 / 句末平仄相对（可硬判）"},
        "DZ-L2": {"status": "not_supported",
                  "reason": "包内无确定性句法切分器；按裁决书 §7.6 全量降级，切不出即不得猜"},
        "DZ-L3": l3,
        "capability_note": "对仗 L1 做字数与平仄硬判；L2 句法结构未做机器判定；"
                           "L3 词性/义类/工拙仅按内置出处词典给软性证据，未覆盖处标 unable，"
                           "不作通用词性机器判定，也不输出『对仗已通过』",
    }


# ── selftest：两层断言 ────────────────────────────────────────────
def _flip_probe(skeleton: str) -> tuple[set[str], set[str]]:
    """单比特层：逐位翻转，看是否被 R1/R2 拒绝（句尾位不计，属固定韵位）。"""
    n = len(skeleton)
    forbidden, free = set(), set()
    for i in range(n - 1):
        cells = list(skeleton)
        cells[i] = "Z" if cells[i] == "P" else "P"
        if violates_R1_hard(cells) or violates_R2_guping(cells, skeleton):
            forbidden.add(str(i + 1))
        else:
            free.add(str(i + 1))
    return forbidden, free


def selftest_report() -> dict:
    """A 层 8/8 单点翻转 + B 层 生成集合 ⊇ 单比特集合、差集逐式可自证。"""
    rows = []
    a_ok = True
    b_ok = True
    total_legal = 0
    total_derived = 0
    all_slack = []
    for skeleton, (free_declared, hard_declared, note) in AUTHORITATIVE.items():
        n = len(skeleton)
        start, tail = _tone_name(skeleton[1]), _tone_name(skeleton[-1])
        sets, legal = allowed_sets(n, start, tail)
        forbidden, _ = _flip_probe(skeleton)
        match_a = forbidden == hard_declared

        # B1：由 §1.2「正格 + 不论位」全组合推出的单比特集合
        free_idx = sorted(int(x) - 1 for x in free_declared)
        derived = []
        for combo in product("PZ", repeat=len(free_idx)):
            cells = list(skeleton)
            for i, c in zip(free_idx, combo):
                cells[i] = c
            derived.append("".join(cells))
        derived_set = set(derived)
        legal_set = {"".join(c) for c in legal}
        subset = derived_set <= legal_set
        diff = sorted(legal_set - derived_set)
        explainable = []
        for s in diff:
            ex = explain_slack(list(s), skeleton)
            if ex is None:
                b_ok = False
            else:
                ex["式"] = s
                explainable.append(ex)
                all_slack.append(dict(ex, 句式=note))
        b_ok = b_ok and subset
        a_ok = a_ok and match_a
        total_legal += len(legal)
        total_derived += len(derived_set)
        rows.append({
            "skeleton": skeleton, "n": n, "说明": note,
            "允许式数": len(legal), "期望式数": EXPECT_LEGAL[skeleton],
            "式数一致": len(legal) == EXPECT_LEGAL[skeleton],
            "单点翻转禁止位": sorted(forbidden, key=int),
            "§1.2_硬约束位": sorted(hard_declared, key=int),
            "A层一致": match_a,
            "不论位_枚举": sorted({str(i + 1) for i in range(n - 1) if len(sets[i]) == 2}, key=int),
            "§1.2_不论位": sorted(free_declared, key=int),
            "单比特集合式数": len(derived_set),
            "生成集合⊇单比特集合": subset,
            "差集": explainable,
        })
    count_ok = all(r["式数一致"] for r in rows)
    return {
        "A层_单点翻转": {"rows": rows, "all_match": a_ok, "checked": len(rows),
                     "结论": "8/8 与裁决书 §1.2「硬约束」列逐位一致" if a_ok else "存在不一致"},
        "B层_多比特": {
            "生成集合式数合计": total_legal,
            "单比特集合式数合计": total_derived,
            "差集式数": total_legal - total_derived,
            "差集明细": all_slack,
            "subset_ok": b_ok,
            "式数符合仲裁表": count_ok,
            "结论": ("生成集合 ⊇ §1.2 单比特集合，差集 %d 式全部给出「哪一字拗、哪一字救」定位"
                     % (total_legal - total_derived)) if (b_ok and count_ok) else "存在无法自证的式",
        },
        "期望": {"五言": [4, 2, 3, 3], "七言": [6, 6, 8, 4], "合计": 36,
                 "旧口径合计": 33,
                 "变化说明": "裁决书 §13.1：三仄尾由一律禁改为配对约束（下有三仄上必二平），"
                             "PPPZZ 2→3、ZZPPPZZ 4→6"},
        "note": "「硬约束」以单比特翻转法核对（与裁决书 §五 同法）；"
                "孤平用配对约束而非计数；三仄尾用配对约束而非末三字同声（§13.1）",
    }
