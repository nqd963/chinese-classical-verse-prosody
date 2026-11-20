"""路径解析与环境自检。

铁律：所有路径以本文件位置为基准（__file__ → 技能根），禁止 cwd / chdir 依赖。
数据缺失一律 raise DataMissing（不返回空 dict、不静默降级）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
DATA_DIR = SKILL_DIR / "data"
PY_MIN = (3, 8)


class DataMissing(Exception):
    """数据文件缺失或不可解析。"""


def data_path(name: str) -> Path:
    return DATA_DIR / name


def data_digest(name: str) -> str:
    """sha256 前 12 位，写入证据块。"""
    p = data_path(name)
    if not p.exists():
        raise DataMissing("数据文件缺失：%s（期望路径 %s）" % (name, p))
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def load_json(name: str) -> dict:
    p = data_path(name)
    if not p.exists():
        raise DataMissing("数据文件缺失：%s（期望路径 %s）" % (name, p))
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise DataMissing("数据文件损坏：%s（%s）" % (name, exc))


def load_jsonl(name: str) -> list:
    p = data_path(name)
    if not p.exists():
        raise DataMissing("数据文件缺失：%s（期望路径 %s）" % (name, p))
    rows = []
    with open(p, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def env_check() -> dict:
    """环境自检：Python 版本 + 必需数据文件是否齐备。"""
    need = ["pingshui.json", "supplement.json", "reading_suspect_queue.json",
            "cilin.json", "linvun.json", "cipu_long.json",
            "cipu_wang.json", "cipai_index.json", "trad_simp.json", "corpus.jsonl"]
    files, missing = {}, []
    for n in need:
        p = data_path(n)
        if p.exists():
            files[n] = {"bytes": p.stat().st_size, "sha256_12": data_digest(n)}
        else:
            missing.append(n)
    ok_py = sys.version_info[:2] >= PY_MIN
    return {"python": sys.version.split()[0], "python_ok": ok_py,
            "skill_dir": str(SKILL_DIR), "data_dir": str(DATA_DIR),
            "files": files, "missing": missing,
            "ok": ok_py and not missing}


def main() -> None:
    print(json.dumps(env_check(), ensure_ascii=False, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
