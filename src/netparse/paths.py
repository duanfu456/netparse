"""仓库内路径解析。

原来 `tools/diff_incumbent.py` 用 `HERE = dirname(__file__)` / `ROOT = dirname(HERE)` 算仓库根，
脚本进入包之后 `__file__` 变成 `src/netparse/paths.py`，那个算法会指到 `src/`。
这里统一从包位置向上找 `pyproject.toml`，脚本、CLI、测试都走同一套。
"""

import os

_MARKER = "pyproject.toml"


def repo_root():
    """仓库根目录（含 pyproject.toml 的那一层）。

    找不到就退化成 `src/` 的上一层 —— 让 `pip install` 后的场景也能跑
    （安装后 `__file__` 在 site-packages 里，没有 pyproject，此时返回 site-packages 的父级，
    仅用于拼默认路径，不是"仓库"语义）。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    while True:
        if os.path.isfile(os.path.join(cur, _MARKER)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return os.path.dirname(here)
        cur = parent


def templates_dir():
    """模板目录（`templates/`）。"""
    return os.path.join(repo_root(), "templates")


def change_dir(name="netparse-v3-diff-and-mask"):
    """变更规格目录（`.devforge/spec/changes/<name>`）。"""
    return os.path.join(repo_root(), ".devforge", "spec", "changes", name)


def default_template():
    """默认模板（华为 VRP 台账口径）。"""
    return os.path.join(templates_dir(), "huawei-vrp.np")


def baseline_template():
    """修前基线模板（能力 B 三栏对比的「修前」侧）。"""
    return os.path.join(change_dir(), "baseline-template.cmdb")
