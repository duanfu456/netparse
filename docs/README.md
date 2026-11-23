# 文档地图

| 文档 | 内容 | 什么时候看 |
|---|---|---|
| [README.md](../README.md) | 项目总览：功能状态表 / 安装 / 快速开始 | 第一次接触 |
| [TEMPLATE-GUIDE.md](TEMPLATE-GUIDE.md) | 模板编写手册：**19 个场景**全语法（段头/字段/规则/target/模块化/自测试段…） | 写或改模板 |
| [TEMPLATE-CHEATSHEET.md](TEMPLATE-CHEATSHEET.md) | 速查：铁律 7 条 + 高频写法 | 日常写模板随手翻 |
| [__MAIN__-说明.md](__MAIN__-说明.md) | `[__main__]` 自测试段专篇：三种写法 / 注入对象 / 行为规则 / 错误对照 | 给模板加冒烟测试 |
| [CLI-GUIDE.md](CLI-GUIDE.md) | 命令行：parse/lint/diff/readonly 全参数、`--selftest`、`--fields`、模块化运行 | 跑命令 |
| [PYTHON-GUIDE.md](PYTHON-GUIDE.md) | Python API：两步模型、fields 白名单、模块化加载、脱敏控制 | 嵌入自己的脚本 |
| [FINAL-plan.md](FINAL-plan.md) | 总方案：架构 / 语义 / CMDB 建模 / 版本演进记录 | 了解设计背景 |

## 目录结构（整理后）

| 目录 | 内容 |
|---|---|
| `src/netparse/` | 引擎本体（engine / cli / masking） |
| `templates/` | `.np` 模板（华为模块化 9 组 + main 入口 + 单文件版 / 中兴 / 诺基亚） |
| `tools/` | 活跃工具：`batch_real.py`（真机批量回归）、`_probe_conserve.py`（守恒探针）、`diff_incumbent.py`、`readonly_guard.py`、`masking.py`、`probe_stack.py`（原型 CLI） |
| `tools/probes-archive/` | 评估期一次性探针（14 个，已归档不再维护） |
| `tools/legacy/` | 早期实现存档 |
| `editor/vscode-netparse/` | VSCode 插件源码（vsce package 打 vsix） |
| `skills/netparse/` | skill 包（SKILL.md + docs + exe） |
| `tests/` | 回归 + 单测（fixtures 双基线） |
| `docs/` | 本文档目录 |
| `packaging/` | PyInstaller 打包（entry.py + netparse.spec） |

## 模板资产

| 位置 | 内容 |
|---|---|
| `templates/huawei-vrp.np` | 华为 VRP **单文件版**（18 集合，与模块化版输出逐字节一致） |
| `templates/huawei/main.np` | 华为**模块化入口**：9 个功能组 + `SELFTEST-TEMPLATE.md`（自测试写法范本） |
| `templates/nokia-sros.np` | Nokia SR OS |
| `editor/vscode-netparse/` | VSCode 插件（高亮/补全/lint 诊断/CodeLens 自测试，`vsce package` 打 vsix） |

## 30 秒上手

```bash
netparse parse templates/huawei/main.np device.cfg --json          # 全量解析
netparse parse templates/huawei/main.np --selftest                 # 模板自测试
netparse parse templates/huawei/main.np device.cfg --json --fields "Interfaces=Name,Type"
```
