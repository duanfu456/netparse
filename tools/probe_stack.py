"""兼容 shim：引擎实现已迁入 `netparse.engine`（见 pyproject.toml / src/netparse/）。

保留本文件是为了让文档与历史探针里的 `python tools/probe_stack.py ...` 继续可用。
新代码请直接用库接口或 `netparse parse` 子命令：

    from netparse import parse_template, run
    netparse parse <模板> <配置文件> --json
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "src"))

from netparse import masking  # noqa: E402
from netparse.engine import *          # noqa: F401,F403  (re-export 全部引擎符号)
from netparse.engine import (          # noqa: F401  显式列出，避免 lint 误报
    CONFIG, CREDENTIAL_KEYWORDS, Coll, CompiledRule, Field, Frame, Rule,
    TYPE_CONFLICT_KEY, apply_defaults, apply_writes, check_required, compile_rule,
    convert, get_path, is_coll_decl, lint, merge_by_key, parse_literal,
    parse_modifiers, parse_target, parse_template, run, set_path, split_tokens,
)
from netparse.masking import MASK_DEFAULT, STATS, mask_value  # noqa: F401

#: 兼容旧全局名（旧代码会 `probe_stack.MASK_HEURISTIC = False`）
#: 这里必须代理到 masking.CONFIG，否则赋值落在 shim 自己的命名空间上、引擎读不到。
__all__ = ["parse_template", "run", "lint", "compile_rule", "Field", "Coll", "Rule",
           "CompiledRule", "Frame", "split_tokens", "parse_modifiers", "is_coll_decl",
           "parse_literal", "parse_target", "convert", "get_path", "set_path",
           "apply_writes", "check_required", "merge_by_key", "apply_defaults",
           "TYPE_CONFLICT_KEY", "CREDENTIAL_KEYWORDS", "MASK_DEFAULT", "STATS",
           "mask_value", "masking", "CONFIG"]


def __getattr__(name):
    if name == "MASK_VALUE":
        return masking.CONFIG.value
    if name == "MASK_HEURISTIC":
        return masking.CONFIG.heuristic
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


class _ShimModule(type(sys)):
    def __setattr__(self, name, value):
        if name == "MASK_VALUE":
            masking.CONFIG.value = value
            return
        if name == "MASK_HEURISTIC":
            masking.CONFIG.heuristic = value
            return
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _ShimModule


def main():
    """转发到 `netparse parse` 子命令。

    旧写法 `probe_stack.py <模板> <配置> --lint` 里，`--lint` 是「只体检、不解析」的开关。
    新 CLI 把体检拆成了独立子命令，所以这里要做一次参数翻译，
    否则旧命令会直接报 `unrecognized arguments: --lint`。
    """
    from netparse.cli import main as cli_main

    argv = sys.argv[1:]
    if "--lint" in argv:
        argv = [a for a in argv if a != "--lint"]
        if not argv:
            print("用法: python tools/probe_stack.py <模板> <配置文件> --lint", file=sys.stderr)
            return 1
        return cli_main(["lint"] + argv[:1])
    return cli_main(["parse"] + argv)


if __name__ == "__main__":
    sys.exit(main())
