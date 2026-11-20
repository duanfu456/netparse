"""兼容 shim：静态路由校验已迁入 `netparse.cli.route_forms`。

     python tools/check_route_forms.py --sample 60     # 旧写法
     netparse route-forms --sample 60                   # 新写法
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "src"))

from netparse.cli.route_forms import *   # noqa: F401,F403
from netparse.cli.route_forms import main as _route_main, add_arguments  # noqa: F401


def main():
    from netparse.cli import main as cli_main
    return cli_main(["route-forms"] + sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
