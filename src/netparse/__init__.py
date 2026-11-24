"""netparse —— 网络设备配置解析器。

把华为 / 中兴 / Nokia 等设备的**配置文件**按模板 DSL 解析成结构化 JSON，
用于 CMDB 与接口台账。

设计要点
--------
* **嵌套状态栈**：模板用缩进表达层级，每层声明 `start` / `end` / `[value]` / `[rule]`。
* **块分隔符**：裸 `#` / `!` 是块分隔符（不是注释），参与 end 出栈。
* **凭据脱敏**：模板 `type(secret)` 精确遮蔽（L1）+ 密文形态兜底（L2），
  遮蔽发生在写入前，记录对象里不存在明文。

快速开始
--------
::

    from netparse import parse_template, run

    colls = parse_template("templates/huawei-vrp.cmdb")
    result = run(colls, open("device.cfg", encoding="utf-8").readlines())
    print(result["Interfaces"][0]["Name"])

命令行::

    netparse parse <模板> <配置文件> --json
    netparse lint  <模板>
    netparse diff  --all --jobs 8
"""

from . import masking
from .engine import (
    CONFIG,
    CREDENTIAL_KEYWORDS,
    TYPE_CONFLICT_KEY,
    Coll,
    CompiledRule,
    Field,
    Frame,
    Rule,
    apply_defaults,
    apply_writes,
    check_required,
    compile_rule,
    convert,
    get_path,
    is_coll_decl,
    lint,
    merge_by_key,
    parse_literal,
    parse_modifiers,
    parse_target,
    parse_template,
    run,
    run_str,
    run_file,
    run_files,
    BatchResult,
    set_path,
    split_tokens,
)
from .masking import (
    MASK_DEFAULT,
    SECRET_SHAPE_PATTERNS,
    STATS,
    MaskingStats,
    is_secret_shape,
    mask_value,
    reset_stats,
    should_mask_by_shape,
)
from .paths import (
    baseline_template,
    change_dir,
    default_template,
    repo_root,
    templates_dir,
)

__version__ = "0.9.0"

__all__ = [
    # 引擎主流程
    "parse_template", "run", "run_str", "run_file", "run_files",
    "BatchResult", "lint", "compile_rule",
    # 模型
    "Field", "Coll", "Rule", "CompiledRule", "Frame",
    # 文法工具
    "split_tokens", "parse_modifiers", "is_coll_decl", "parse_literal",
    "parse_target", "convert", "get_path", "set_path", "apply_writes",
    "check_required", "merge_by_key", "apply_defaults",
    # 常量
    "TYPE_CONFLICT_KEY", "CREDENTIAL_KEYWORDS",
    # 脱敏
    "masking", "CONFIG", "MASK_DEFAULT", "STATS", "MaskingStats",
    "SECRET_SHAPE_PATTERNS", "is_secret_shape", "should_mask_by_shape",
    "mask_value", "reset_stats",
    # 路径
    "repo_root", "templates_dir", "change_dir", "default_template", "baseline_template",
    # 版本
    "__version__",
]
