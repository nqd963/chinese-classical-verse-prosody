"""抽取期公用工具（仅 tools/ 使用，不进运行时分发依赖链）。

设计约束（见 data-抽取规格 §0.2）：
- 一源一脚本一输出；
- 幂等：写盘统一 sort_keys + ensure_ascii=False + indent=1 + 固定 \\n；
- 不静默丢弃：被丢弃的片段必须有计数并能落盘；
- 路径由命令行传入，脚本内不出现任何绝对路径。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def is_cjk(ch: str) -> bool:
    return bool(CJK_RE.match(ch))


def cjk_only(text: str) -> str:
    return "".join(c for c in text if is_cjk(c))


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def read_lines(path: str) -> list[str]:
    return read_text(path).split("\n")


def dump_json(obj, path: str) -> None:
    """确定性写盘：键排序、UTF-8、固定缩进、固定换行。"""
    text = json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.write("\n")
    os.replace(tmp, path)


def dump_jsonl(rows, path: str) -> None:
    lines = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))
        fh.write("\n")
    os.replace(tmp, path)


def out_dir(argv, default_rel: str) -> str:
    """解析输出目录：--out <dir>，默认取脚本所在技能包的 data/。"""
    out = default_rel
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]
    os.makedirs(out, exist_ok=True)
    return out


def src_root(argv) -> str:
    """解析语料根目录：--src <dir>（必填，避免任何硬编码路径）。"""
    if "--src" in argv:
        return argv[argv.index("--src") + 1]
    sys.stderr.write("用法错误：必须用 --src <语料根目录> 指定原始语料位置\n")
    raise SystemExit(2)


def skill_data_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, os.pardir, "data"))


def rel(path: str, root: str) -> str:
    """把绝对路径转成相对语料根的 POSIX 路径（落盘不写盘符）。"""
    return os.path.relpath(os.path.abspath(path), os.path.abspath(root)).replace(os.sep, "/")


CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_num(s: str) -> int:
    """中文数字 → 整数，覆盖 1..999（韵目序号 ≤30、词牌字数 ≤240）。"""
    total = 0
    num = 0
    for ch in s:
        if ch in CN_DIGITS:
            num = CN_DIGITS[ch]
        elif ch == "十":
            total += (num or 1) * 10
            num = 0
        elif ch == "百":
            total += (num or 1) * 100
            num = 0
        elif ch == "零":
            num = 0
        else:
            raise ValueError("非中文数字：%r" % s)
    return total + num


def num_cn(n: int) -> str:
    """整数 → 中文数字，与源文件字形一致（21 → 二十一，30 → 三十）。"""
    digits = "零一二三四五六七八九"
    if n < 10:
        return digits[n]
    if n == 10:
        return "十"
    if n < 20:
        return "十" + digits[n % 10]
    tens, ones = divmod(n, 10)
    return digits[tens] + "十" + (digits[ones] if ones else "")
