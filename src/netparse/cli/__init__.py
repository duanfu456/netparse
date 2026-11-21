"""netparse 命令行入口。

单一入口 + 子命令::

    netparse parse  <模板> <配置文件> [--json] [--mask-value M] [--no-mask-heuristic]
    netparse lint   <模板> [--json] [--strict]
    netparse diff   (--all | --sample N) [--jobs N] [...]
    netparse route-forms [--sample N] [--file F]
    netparse readonly (--capture | --verify)

子命令的实现分散在 `netparse.cli.*`，这样每个子命令可以独立 import，
不装可选依赖（如 diff 需要生产 build_excel）时其余子命令照常可用。
"""

import argparse
import sys

from .. import __version__

SUBCOMMANDS = ("parse", "lint", "diff", "route-forms", "readonly")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="netparse",
        description="网络设备配置解析器（模板 DSL + 嵌套状态栈）",
    )
    parser.add_argument("--version", action="version", version="netparse " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="{%s}" % ",".join(SUBCOMMANDS))

    # ── parse ──
    p = sub.add_parser("parse", help="按模板解析配置文件或配置文件夹")
    p.add_argument("template", help="模板路径（.cmdb）")
    p.add_argument("config", help="配置文件或配置文件夹（文件夹=递归解析全部文件）")
    p.add_argument("output", nargs="?", default=None,
                   help="输出 JSON 路径（可选；不指定则打印到 stdout）")
    p.add_argument("--json", action="store_true", help="输出完整 JSON（stdout 保持纯 JSON）")
    p.add_argument("--mask-value", default=None, help="掩码串（默认 ******）")
    p.add_argument("--no-mask-heuristic", action="store_true",
                   help="关闭 L2 密文形态兜底，只保留模板 type(secret) 的精确遮蔽")

    # ── lint ──
    p = sub.add_parser("lint", help="模板体检（不解析配置）")
    p.add_argument("template", help="模板路径（.cmdb）")
    p.add_argument("--json", action="store_true", help="以 JSON 输出问题列表")
    p.add_argument("--strict", action="store_true", help="有「警告」也返回非零退出码")

    # 其余子命令的解析器在各自模块里，惰性注册（避免 import 不到的依赖直接炸主入口）
    for name, module, func in (
        ("diff", "diff", "add_arguments"),
        ("route-forms", "route_forms", "add_arguments"),
        ("readonly", "readonly", "add_arguments"),
    ):
        sp = sub.add_parser(name, help="见 `netparse %s --help`" % name)
        sp.set_defaults(_lazy=(module, func))

    return parser


def _inject_lazy_args(parser, argv):
    """把子命令专属参数的定义推迟到真正要用时才 import。

    diff / route-forms / readonly 依赖生产只读目录或外部脚本，环境不满足时
    仍应能 `netparse parse`。所以先由主解析器识别子命令，再交给子模块补参数。
    """
    if not argv:
        return None
    name = argv[0]
    if name not in ("diff", "route-forms", "readonly"):
        return None

    import importlib
    module = importlib.import_module(
        "." + {"diff": "diff", "route-forms": "route_forms",
               "readonly": "readonly"}[name],
        package=__name__)

    sp = argparse.ArgumentParser(prog="netparse " + name, description=module.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp.add_argument("--version", action="version", version="netparse " + __version__)
    module.add_arguments(sp)
    return name, module, sp


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    lazy = _inject_lazy_args(None, argv)
    if lazy is not None:
        name, module, sp = lazy
        args = sp.parse_args(argv[1:])
        return module.main(args)

    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1

    if args.command == "parse":
        from . import parse
        return parse.main(args)
    if args.command == "lint":
        from . import parse as parse_mod
        return parse_mod.main_lint(args)

    parser.error("未知子命令：%s" % args.command)
    return 2


if __name__ == "__main__":
    sys.exit(main())
