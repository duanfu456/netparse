# netparse-cmdb

netparse 模板 DSL（`.np` / `.cmdb`）的 VSCode 语言支持插件——语法高亮、自动补全、实时体检、[__main__] 一键自测试。

配套引擎：[netparse-cmdb (PyPI)](https://pypi.org/project/netparse-cmdb/) · [GitHub 仓库](https://github.com/duanfu456/netparse)

## 功能

- **语法高亮**：集合声明、字段类型、规则正则、`${变量}`、`-> target` 映射、`[section]` 段头全着色
- **自动补全**：字段类型（`type(str/int/bool/list/dict)`）、段头（`[value]` `[rule]` `[python]` `[__main__]`）、option 标记（`option(Key)` 等）
- **实时 lint**：保存即体检——未声明字段、正则错误、目标字段缺失直接标红
- **定义跳转**：`import` / `from x import *` 跳到子模板文件；`${字段}` 跳到声明处
- **CodeLens 自测试**：`[__main__]` 段顶部「▶ 运行自测试」，一键执行并显示 PASS/FAIL
- **测试段 CodeLens**：每个 `[__test__.NN]` 段头「▶ 运行测试 NN」，单独跑该测试段（`parse -t NN`）
- **`[__main__]` snippet**：输入 `__main__` 展开完整冒烟段模板（cfgstr 样例 + expect= 断言 + 全部高级用法注释示例）

## 快速开始

1. 安装本插件
2. 安装引擎：`pip install netparse-cmdb`（或直接使用 [GitHub Release](https://github.com/duanfu456/netparse/releases) 的 `netparse.exe`）
3. 新建 `.np` 文件，输入 `__main__` 触发 snippet，写规则 → 保存 → 看 lint → 点 CodeLens 跑自测试

## 规则匹配语义（匹配即提取）

- **规则带 `$`** → 严格整行匹配（短行类命令：`pim sm`、`bas`、`statistic enable`）
- **规则不带 `$`** → 前缀匹配提取（长行/带残余参数：`qos-profile X inbound identifier none`）

两种写法并存，按命令形态自选。

## 配置

| 配置项 | 默认 | 说明 |
|---|---|---|
| `netparse.engine` | `netparse` | 引擎可执行文件路径（exe 或 python -m 均可） |

## 已知限制

- Nokia SR OS 模板支持尚在建设中
- expect= 断言路径不支持数组切片（仅 `[索引]` 正索引）

## License

MIT
