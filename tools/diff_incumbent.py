"""兼容 shim：diff 工具已迁入 `netparse.cli.diff`。

保留本文件是为了让文档与历史命令继续可用：

    python tools/diff_incumbent.py --all --jobs 12     # 旧写法
    netparse diff --all --jobs 12                       # 新写法
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "src"))

from netparse.cli.diff import *          # noqa: F401,F403
from netparse.cli.diff import main as _diff_main, add_arguments  # noqa: F401


def main():
    from netparse.cli import main as cli_main
    return cli_main(["diff"] + sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
