"""数据结构与 JSON 序列化。

幂等要求：to_json 使用 sort_keys、ensure_ascii=False、无时间戳、无随机序，
同一输入两次执行输出逐字节一致。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

SCHEMA_VERSION = "1.0"

# 违规代码表（模型只读，不得改写为自造码）
CODES = {
    "V-TONE": "平仄错位",
    "V-GUPING": "孤平",
    "V-SANZE": "三仄尾",
    "V-SANPING": "三平尾",
    "V-RHYME": "出韵",
    "V-RHYME-DUP": "重韵",
    "V-RHYME-POS": "非韵位押韵/韵位失韵",
    "V-NIE": "失对",
    "V-NIAN": "失粘",
    "V-CHARS": "字数不符",
    # 推定谱（例词反推，非一等谱）专用码：韵脚平仄由表头韵式独立判定（hard），
    # 非韵脚平仄仅软提示（原谱该位可能本为「可平可仄」，不因错谱误伤正确词作）。
    "V-RHYME-TONE": "推定谱：韵脚字声调与词牌韵式不符（hard）",
    "V-PAT-SOFT": "推定谱：非韵脚平仄与例词反推谱不同（soft，仅供参考）",
    "V-DUIZ": "对仗 L1 不成立",
    "V-SEED": "首句不合任何标准句式",
    "V-NEIGHBOR": "邻韵（模式相关）",
    "V-RHYME-SHIFT": "换韵位置与谱不符",
    "V-REPEAT": "叠句/叠字要求未满足",
    "V-OPT": "衬字位字数越界",
    # 数据侧缺口：字未收录属「判不了」，不属「判错了」。
    # 用 soft 表达，并在无硬错时把整篇判为 insufficient_data（退出码 3），
    # 避免把好诗判成出律（韵表覆盖不足是数据问题，不是用户的问题）。
    "V-NODATA": "字未收录（数据不足，不判错）",
    # 特拗：平平仄平仄 / 仄仄平仄仄（第 3、4 字互换）。
    # 唐诗中极常见（本包 98 首韵表全覆盖的近体诗里过半出现），属通行拗救变体，不是错误。
    # 单列为 soft 码，便于用户按自己的谱系口径取舍：严格口径下它会变成 hard。
    "V-TEAO": "特拗（第 3、4 字互换，通行拗救变体）",
    # 首字例外的特拗（裁决书 §13.7）：首句「仄平仄平仄」——王力《诗词格律概要》脚注①
    # 为「五言第 1 字必平」明确列了两例（孟浩然「故人具鸡黍」、杜甫「昔闻洞庭水」），
    # 但未给可推广条件，故只按首句 + 王力原句判定，不并入普通 V-TEAO（后者要求首字必平）。
    # evidence.王力举例 = true 仅当原句正是那两句之一；soft + pass_with_variant。
    "V-TEAO-FIRST": "首句特拗首字仄（王力脚注举例例外，非规则放宽）",
    # 三仄尾：唐人多用，属拗句而非出律（王力《汉语诗律学》论「三仄尾」可不论）。
    # 与三平尾分开计数：三平尾是大忌（hard），三仄尾按 soft 记，可 `--strict-variants` 转 hard。
    "V-SANZE-SOFT": "三仄尾（唐人多用，属拗句；严格口径下视为出律）",
    # 句内出现未收录字 → 该句平仄/押韵**判不了**。级联抑制：不再输出该句的
    # V-TONE / V-RHYME / V-REPEAT / V-SANZE / V-SANPING / 失对失粘，避免一个字缺失
    # 级联成一整首假错（裁决书 §9.1：不能靠「做更少的事」掩盖，也不能靠缺字冤枉作者）。
    "V-UNDECIDABLE": "含未收录字，该句/该首无法判定（数据不足，不判错）",
    # 折腰体：仅第三句不与第二句相粘，其余合骨架。绝句通行变体（王力《汉语诗律学》）。
    "V-ZHEYAO": "折腰体：第三句不与第二句相粘（绝句通行变体）",
    # 对句相救（王力 (b) 类，裁决书 §12.2）：出句第 4 字（七言第 6 字）拗，
    # 对句第 3 字（七言第 5 字）须为平。救位成立 → soft + pass_with_variant，
    # evidence 必须同时给拗位与救位两个坐标（反向哨兵：李商隐《登乐游原》）。
    "V-AOJIU": "对句相救：出句第 4 字拗、对句第 3 字救（通行变体）",
    # 拗而未救：王力原文用「必须」二字，故救位为仄是真错，不是变体（hard）。
    "V-AOJIU_UNRESCUED": "拗而未救：出句第 4 字拗但对句第 3 字未用平声相救",
}


class SchemaError(Exception):
    pass


@dataclass(frozen=True)
class Reading:
    char: str
    tone: str          # 平 / 仄
    tone_class: str    # 平 / 上 / 去 / 入
    rhyme: str         # 韵目全称，如 上平一东
    book: str          # pingshui / cilin
    note: str | None = None
    src_rank: int = 0

    def as_dict(self) -> dict:
        return {"char": self.char, "tone": self.tone, "tone_class": self.tone_class,
                "rhyme": self.rhyme, "book": self.book, "note": self.note,
                "src_rank": self.src_rank}


@dataclass
class Violation:
    code: str
    severity: str            # hard / soft
    line: int                # 第 N 句（1-based）
    col: int | None          # 第 M 字（1-based）；句级给 None
    rule: str
    rule_ref: str
    actual: str | None
    expected: str | None
    expected_set: list | None = None
    evidence: dict = field(default_factory=dict)
    candidates: list | None = None

    def __post_init__(self) -> None:
        if self.code not in CODES:
            raise SchemaError("未登记的违规代码：%s" % self.code)
        if self.severity not in ("hard", "soft"):
            raise SchemaError("severity 必须为 hard/soft：%s" % self.severity)
        # A1：五个字段恒存（可为 None，但键必须在）
        for k in ("line", "col", "rule", "actual", "expected"):
            if not hasattr(self, k):
                raise SchemaError("违规项缺字段 %s" % k)
        if not self.rule:
            raise SchemaError("违规项缺 rule 文案")

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "line": self.line,
                "col": self.col, "rule": self.rule, "rule_ref": self.rule_ref,
                "actual": self.actual, "expected": self.expected,
                "expected_set": self.expected_set, "evidence": self.evidence,
                "candidates": self.candidates}


@dataclass
class Result:
    kind: str                      # poem / ci / char / cipai / quote / selftest
    verdict: str                   # pass / violations / error / no_data / conflict / insufficient_data
    ok: bool
    confidence: dict
    violations: list = field(default_factory=list)
    readings: list = field(default_factory=list)
    duizhang: dict | None = None
    disclosure: dict | None = None
    meta: dict = field(default_factory=dict)
    data_source: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    payload: dict | None = None    # 查询类结果（char / cipai / quote）

    @property
    def hard_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "hard")

    @property
    def soft_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "soft")

    def exit_code(self) -> int:
        """0 通过 ｜ 1 硬错 ｜ 2 错误 ｜ 3 数据不足/不支持/冲突/判不了。

        `pass_with_variant`（识别出折腰体/特拗等通行变体，无硬错）与 `pass` 同为 0：
        变体不是错，但必须在 `meta.variants` 里点名，不能悄悄放行。
        `unable`（疑似缺读，反事实重判可解）与 `insufficient_data` 同为 3：
        判不了就说判不了，不用未知读音断言作者出律。
        """
        if self.verdict in ("pass", "pass_with_variant"):
            return 0
        if self.verdict == "violations":
            return 1
        if self.verdict == "error":
            return 2
        return 3

    def to_dict(self) -> dict:
        d = {
            "schema_version": SCHEMA_VERSION,
            "kind": self.kind,
            "ok": self.ok,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "meta": self.meta,
            "violations": [v.as_dict() for v in self.violations],
            "readings": self.readings,
            "duizhang": self.duizhang,
            "disclosure": self.disclosure,
            "data_source": self.data_source,
            "notes": self.notes,
        }
        if self.payload is not None:
            d["payload"] = self.payload
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=1, sort_keys=True)


def info(kind: str, **kw) -> Result:
    """构造 pass 结果。"""
    return Result(kind=kind, verdict="pass", ok=True,
                  confidence=kw.pop("confidence", {"level": "first", "basis": "not_applicable",
                                                   "detail": "依据随包结构化数据"}),
                  **kw)


def error(kind: str, reason: str, **kw) -> Result:
    return Result(kind=kind, verdict="error", ok=False,
                  confidence={"level": "none", "basis": "error", "detail": reason},
                  notes=[reason], **kw)
