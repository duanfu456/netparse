"""兼容 shim：只读自检已迁入 `netparse.cli.readonly`。

     python tools/readonly_guard.py --verify     # 旧写法
     netparse readonly --verify                   # 新写法
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "src"))

from netparse.cli.readonly import *      # noqa: F401,F403


def main():
    from netparse.cli import main as cli_main
    return cli_main(["readonly"] + sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
