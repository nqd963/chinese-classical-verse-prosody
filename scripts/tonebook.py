"""韵表加载、繁简归一、多读查询与多读择优。

裁决书 §8：平水韵表本体为简体，不得掺入繁体；查询层必须做繁→简归一。
裁决书 §7.7：半部韵字级划分仅用于邻韵兼容模式，严格模式默认关闭邻韵；
改 `linvun.json` 的 config.half_resolution 即可切「整部宽松」口径，无需改代码。

多读择优（蓝图 §4.4）：确定性全序 tie-break——硬错数 → 软提示数 → src_rank → 读法字典序。
"""
from __future__ import annotations

from pathlib import Path

import _paths
from schema import Reading

PING_PARTS = ("上平", "下平")


class ToneBook:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else _paths.DATA_DIR
        self.ps = self._load("pingshui.json")
        self.sup = self._load("supplement.json")
        self.cl = self._load("cilin.json")
        self.lv = self._load("linvun.json")
        self.ts = self._load("trad_simp.json")
        self.t2s = self.ts.get("t2s", {})
        self.alias = self.ts.get("alias", {})
        self.ps_chars_raw = self.ps["chars"]
        self.ps_chars = self._merge_supplement()
        self.cl_chars = self.cl["char_index"]
        self.ps_rhyme_chars = {r["id"]: r["chars"] for r in self.ps["rhymes"]}
        self.ps_rhyme_order = [r["id"] for r in self.ps["rhymes"]]
        self._rhyme_part = {r["id"]: r["part"] for r in self.ps["rhymes"]}
        self.config = self.lv.get("config", {})
        self.suspect = self._load("reading_suspect_queue.json")
        self._suspect_chars = {x["字"] for x in self.suspect.get("rejected", [])}
        self._build_neighbors()
        self._append_supplement_to_rhymes()

    # ---------- 补全合并 ----------
    def _merge_supplement(self) -> dict:
        """把 `supplement.json` 并入查询层字表。

        原表**不改一字**：`pingshui.json` 仍是 4281 字，补入条目在此处合并，
        第 5 位标 `supplement` 以便输出里区分「据平水韵原表」与「补自词林正韵」。
        """
        merged = {c: [list(t) for t in v] for c, v in self.ps_chars_raw.items()}
        for k, e in enumerate(self.sup.get("entries", [])):
            merged.setdefault(e["字"], []).append(
                [e["平仄"], e["韵部"], e["声调"], 900000 + k, "supplement"])
        return merged

    def _append_supplement_to_rhymes(self) -> None:
        extra: dict[str, list] = {}
        for e in self.sup.get("entries", []):
            extra.setdefault(e["韵部"], [])
            if e["字"] not in extra[e["韵部"]]:
                extra[e["韵部"]].append(e["字"])
        for rid, chars in extra.items():
            if rid in self.ps_rhyme_chars:
                have = set(self.ps_rhyme_chars[rid])
                self.ps_rhyme_chars[rid] += "".join(c for c in chars if c not in have)


    def _load(self, name: str) -> dict:
        import json
        p = self.data_dir / name
        if not p.exists():
            raise _paths.DataMissing("数据文件缺失：%s（期望路径 %s）" % (name, p))
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)

    # ---------- 繁简归一 ----------
    def normalize(self, ch: str) -> tuple[str | None, str]:
        """返回 (韵表字形, 归一途径)。途径 ∈ asis / t2s / alias / miss。"""
        if ch in self.ps_chars:
            return ch, "asis"
        if ch in self.t2s and self.t2s[ch] in self.ps_chars:
            return self.t2s[ch], "t2s"
        if ch in self.alias and self.alias[ch] in self.ps_chars:
            return self.alias[ch], "alias"
        cand = self.t2s.get(ch) or self.alias.get(ch)
        if cand and cand in self.ps_chars:
            return cand, "t2s"
        return None, "miss"

    # ---------- 查询 ----------
    def readings(self, ch: str, book: str = "pingshui") -> list[Reading]:
        if book == "cilin":
            # 词林正韵的字集与平水韵不完全重合（词林多收、也各有个别缺字），
            # 故先按词林本色查，查不到再走繁简归一。
            direct = ch if ch in self.cl_chars else None
            if direct is None:
                direct = {v for v in self.t2s.values()} and None
            if direct is not None:
                return self._cilin_readings(direct)
            norm, _ = self.normalize(ch)
            if norm is not None and norm in self.cl_chars:
                return self._cilin_readings(norm)
            for alt in (self.t2s.get(ch), self.alias.get(ch)):
                if alt and alt in self.cl_chars:
                    return self._cilin_readings(alt)
            return []
        norm, _ = self.normalize(ch)
        if norm is None:
            return []
        out = []
        if book == "pingshui":
            for rec in self.ps_chars[norm]:
                tag = rec[4] if len(rec) > 4 else "pingshui"
                out.append(Reading(char=norm, tone=rec[0], tone_class=rec[2],
                                   rhyme=rec[1], book=tag, src_rank=rec[3],
                                   note=None if tag == "pingshui" else "补自词林正韵"))
        out.sort(key=lambda r: r.src_rank)
        return out

    def _cilin_readings(self, ch: str) -> list[Reading]:
        out = []
        for rec in self.cl_chars.get(ch, []):
            tone = "平" if rec["tone"] == "平" else "仄"
            out.append(Reading(char=ch, tone=tone, tone_class=rec["tone"],
                               rhyme="词林第%d部" % rec["part"], book="cilin",
                               note=rec.get("note"), src_rank=rec["src_rank"]))
        out.sort(key=lambda r: r.src_rank)
        return out

    def tones(self, ch: str) -> set[str]:
        """{平} / {仄} / {平,仄} / 空=缺字。"""
        return {r.tone for r in self.readings(ch, "pingshui")}

    def tone_classes(self, ch: str) -> set[str]:
        return {r.tone_class for r in self.readings(ch, "pingshui")}

    def rhyme_of(self, ch: str, book: str = "pingshui") -> list[str]:
        return [r.rhyme for r in self.readings(ch, book)]

    def rhyme_sets(self, ch: str) -> set[str]:
        """平水韵部集合（用于韵脚交集合成）；缺字返回空集。"""
        return set(self.rhyme_of(ch, "pingshui"))

    def is_rusheng(self, ch: str) -> bool:
        return "入" in self.tone_classes(ch)

    def is_known(self, ch: str) -> bool:
        return self.normalize(ch)[0] is not None

    # ---------- 邻韵（D5 / A17）----------
    def _build_neighbors(self) -> None:
        self.class_of: dict[str, set[int]] = {}
        self.class_members: dict[int, list[str]] = {}
        idx = 0
        for c in self.lv["classes"]:
            idx += 1
            members = []
            for key in ("ping", "shang", "qu"):
                for r in c.get(key, []):
                    half = r.endswith("<half>")
                    rid = r[:-6] if half else r
                    members.append(rid)
                    self.class_of.setdefault(rid, set()).add(idx)
            self.class_members[idx] = members
        for c in self.lv["rusheng_classes"]:
            idx += 1
            members = []
            for r in c.get("members", []):
                half = r.endswith("<half>")
                rid = r[:-6] if half else r
                members.append(rid)
                self.class_of.setdefault(rid, set()).add(idx)
            self.class_members[idx] = members
        # 半部韵的组归属（fine 口径）
        self.half_group_class: dict[str, dict[str, int]] = {}
        for full, info in self.lv.get("half_chars", {}).items():
            self.half_group_class[full] = {
                "fine": info["resolution"] == "fine",
                "classes": sorted(self.class_of.get(full, set())),
                "groups": info["groups"],
            }

    def rhyme_class(self, rhyme: str) -> list[int]:
        return sorted(self.class_of.get(rhyme, []))

    def neighbors(self, rhyme: str, char: str | None = None,
                  mode: str = "strict") -> set[str]:
        """邻韵集合。strict 模式一律返回空集（裁决书 D5：严格模式默认关闭邻韵）。"""
        if mode != "compat":
            return set()
        classes = set(self.class_of.get(rhyme, set()))
        half = self.half_group_class.get(rhyme)
        if half and half["fine"] and char is not None:
            norm, _ = self.normalize(char)
            owner = None
            for part_no, chars in half["groups"].items():
                if norm and norm in chars:
                    owner = int(part_no)
                    break
            if owner is not None:
                own = self.cl["xiaoyun"]
                classes_for_owner = set()
                for g in own:
                    if g["part"] == owner and g.get("rhyme_full") == rhyme:
                        classes_for_owner |= set(self.class_of.get(rhyme, set()))
                if classes_for_owner:
                    classes = classes_for_owner
        out = set()
        for c in classes:
            out |= set(self.class_members.get(c, []))
        out.discard(rhyme)
        return out

    def rhyme_candidates(self, rhyme: str, limit: int = 40, exclude: str | None = None) -> list[str]:
        """同韵部可选字集。按韵表原序返回，**不按常用度排序**（包内无词频源）。"""
        chars = self.ps_rhyme_chars.get(rhyme, "")
        out = [c for c in chars if c != exclude]
        return out[:limit]

    # ---------- 多读择优 ----------
    def solve(self, cells: list[dict], lines: list[list[str]] | None = None
              ) -> tuple[dict, int]:
        """在给定约束下为每个字位选一个读法。

        cells: [{"line": i, "col": j, "char": c, "allowed": set({"平","仄"}),
                 "rhyme_target": set() | None}]
        返回 ({(line,col): Reading}, cost)；cost = 无法满足的字位数。
        tie-break 全序：allowed 命中优先 → src_rank 小 → rhyme 字典序。
        """
        chosen: dict[tuple[int, int], Reading] = {}
        cost = 0
        for cell in sorted(cells, key=lambda c: (c["line"], c["col"])):
            rs = self.readings(cell["char"], "pingshui")
            if not rs:
                cost += 1
                continue
            target = cell.get("rhyme_target")
            ok = [r for r in rs if r.tone in cell["allowed"]]
            if target:
                ok = [r for r in ok if r.rhyme in target] or ok
            if not ok:
                cost += 1
                ok = rs
            ok.sort(key=lambda r: (r.src_rank, r.rhyme, r.tone))
            chosen[(cell["line"], cell["col"])] = ok[0]
        return chosen, cost


def load_default() -> ToneBook:
    return ToneBook()
