# -*- coding: utf-8 -*-
"""PyInstaller 入口：强制 UTF-8 输出 + 绝对导入启动 netparse CLI。

控制台/重定向输出统一 UTF-8（Windows 默认 GBK 会让中文 JSON 变成乱码字节）。
"""
import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from netparse.cli import main

sys.exit(main())
