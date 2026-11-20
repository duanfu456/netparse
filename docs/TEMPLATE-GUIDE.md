# netparse 模板编写手册

> 面向对象：写 `.cmdb` 模板的人（网络工程师）
> 配套：`FINAL-plan.md`（总方案：架构 / 语义 / CMDB 建模 / 实测数据）
> 参考实现：`templates/huawei-vrp.cmdb`、`templates/nokia-sros.cmdb`

---

## 0. 五分钟上手

**模板 = 你期望的配置文件长什么样，以及每个参数叫什么名字。**

```text
# 集合声明：产出什么、什么时候进去
Interfaces type(list) start(^interface\s+) desc(接口集合)
[value]
Name        type(str)  reg(\S+) desc(接口名) option(Key)
Description type(str)  reg(.+)  desc(描述)
AdminStatus type(str)  default(up) reg(up|down) desc(管理状态)
[rule]
^interface\s+${Name}
^\s*description\s+${Description}\s*$
^\s*shutdown\s*$                   -> AdminStatus=down
^\s*undo shutdown\s*$              -> AdminStatus=up
```

对上这份配置：

```
interface GigabitEthernet1/0/0
 description TO-[SNANK-PC-CMNET-RT01-NE5KE]-GE4/0/0=1*10GE
 undo shutdown
```

得到：

```json
{
  "Interfaces": [
    {
      "Name": "GigabitEthernet1/0/0",
      "Description": "TO-[SNANK-PC-CMNET-RT01-NE5KE]-GE4/0/0=1*10GE",
      "AdminStatus": "up"
    }
  ]
}
```

**三步走**：① 抄一段真实配置 → ② 看每一行想要什么字段 → ③ 一行一条规则。

---

## 1. 心智模型

| 认知 | 说明 |
|---|---|
| **层级由模板声明** | 缩进写的是**模板自己**的父子关系，与被解析文件的排版无关 |
| **边界由正则触发** | `start` 进、`end` 出；不写 `end` 就靠"下一个 start"自动退出 |
| **每层有 start/end** | 一眼看出**什么时候进去、什么时候退出** |
| **`[value]` / `[rule]` 分区** | 字段段只有字段，规则段一行一条正则（TextFSM 的 `Value` / `Rule`） |
| **`${字段}` = 插入该字段的正则** | 正则只在字段声明里写一次，规则里引用；`${A.B}` 引用嵌套字段 |
| **键与必填写在字段上** | `option(Key)` / `option(Required)`，集合声明里不写 |
| **省略 `->` 时** | 捕获组名即目标列名，落在**当前集合当前记录** |
| **子集合自动归属父元素** | 不需要 `inherit=`，也不需要重复父层的 key |
| **没写规则的命令会丢** | 引擎**不兜底**；用 `python tools/legacy/probe_coverage3.py` 查哪些命令族没被覆盖 |

---

## 2. 文件骨架

```text
# ══ 头部注释：设备型号、验证样本、用法 ══

Device type(dict) desc(设备)
[value]
SysName type(str) reg(\S+) desc(系统名)
[rule]
^sysname\s+${SysName}\s*$

Interfaces type(list) start(^interface\s+) desc(接口集合)
[value]
Name type(str) reg(\S+) desc(接口名) option(Key)
[rule]
^interface\s+${Name}
```

集合之间**空行**分隔。**当前是单文件交付，引擎不支持 `Include` 或多文件拼接**——
厂商多时需人工拆成多个模板文件再合并。

---

## 3. 语法

### 3.1 集合声明

```
<集合名> type(<类型>) [start(<正则>)] [end(<正则>)] [desc(<说明>)]
```

| 选项 | 形式 | 含义 | 省略时 |
|---|---|---|---|
| `type` | `type(list\|dict\|str\|int\|float\|bool)` | 集合类型 | **list** |
| `start` | `start(<正则>)` | **进入**该层的触发器 | 该层为**栈底常驻**，始终生效 |
| `end` | `end(<正则>)` | **离开**该层的触发器 | **不启用 end 匹配** |
| `desc` | `desc(<说明>)` | 中文说明 | 无 |

> ⚠️ **`end` 的实际状态（2026-11 更新）**：`templates/huawei-vrp.cmdb` 的
> `Interfaces` / `VpnInstances` / `Acls` / `AclRules` 已声明 `end(^#$)`
> （见 3.1.1「块分隔符」）。中兴的 `$`、诺基亚的 `exit` 仍需各自实测。

> **`end` 匹配已从「只查栈顶」改为「从栈顶向下找第一个匹配层」**（方案 X）。
> 栈顶是子集合（end 为 None）时，父集合的 end 也能正确关闭。只收最内层，
> 不一路收到底（一路收到底是分隔符 `#` 的专属语义）。

> ⚠️ **`end` 是绝对正则，无法表达「相对缩进」**。诺基亚的 `exit` 是层级退出命令，
> 接口块内子块的 `exit`（缩进 +4）也会匹配 `end(^\s*exit\s*$)`，导致接口被过早关闭
> （实测真地址 94→58，丢 36 条）。所以**诺基亚接口不能用 `exit` 作 end**，
> 需要引擎支持「end 只退出比它缩进更浅的最近一层」后才能接入。

> **集合上不写 `Key` / `Required`**——它们写在字段的 `option(...)` 里（见 3.2）。
> 集合级 `Key(...)` / `Required(...)` 已废弃，lint 直接报错。
> 继承用 `inherit=`，列表用 `type(list)`——**不用** `Filldown`/`Fillup`/`List`。

#### 3.1.1 块分隔符 `#` / `!`（v3 起的重要语义）

**裸 `#` 或 `!`（长度 ≤ 2 的行）是块分隔符，不是注释。**

| 行为 | 说明 |
|---|---|
| 参与 `end` 匹配 | 分隔符会触发栈顶各层的 `end` 出栈，从最深一层开始，连同其上子层一起收起 |
| 不关没有 `end` 的层 | 该层没声明 `end` 时，分隔符**不关闭它**，仍靠下一个同级 `start` 归档（向后兼容） |
| 长 `#` 行仍是注释 | 例如脚本头部的 `# 模板说明…`，长度 > 2，按注释跳过 |

**为什么必须这样**：华为全量配置 **100% 用 `#` 作块分隔符**
（222 万行抽样：`#` 出现 249282 次、`!` 出现 0 次）。

> ⚠️ **这里出过一个真实 bug（已修）**：早期把 `#` 一律当注释跳过，
> 导致 `end(^#$)`) **永远不生效**，层级一路挂着不收。
> 实测后果：`Acls` 从 `acl number 2000` 起一直挂到几千行后的
> `interface NULL0`，中间的 ` description SSH-Acl-Control` 被当成**接口**描述，
> **103 个文件的 NULL0 接口凭空多出 ACL 描述**。
>
> 判据：裸 `#` 行执行时栈应当已收干净；若模板该层没 `end`，则栈保留该层属正常。
> 回归见 `tests/test_engine_scope.py`（9 项）。


### 3.2 `[value]` 段 —— 字段声明

```
<字段名> type(<类型>) [option(<标记> ...)] [default(<值>)] [reg(<正则>)]
             [inherit=<集合>.<字段>] [desc(<说明>)]
```

| 选项 | 含义 |
|---|---|
| `option(Key)` | 该字段参与**聚合键**：同一父元素下，键相同的子记录**合并成一个对象** |
| `option(Required)` | 该字段必填，缺失则本条记录**无效（丢弃）** |
| `default(<值>)` | 未命中时的默认值 |
| `reg(<正则>)` | 该字段的匹配正则，供 `${字段}` 引用 |
| `inherit=<集合>.<字段>` | 继承该集合该字段**上次**匹配到的值 ⚠️ **不是"所属父"** |
| `desc(<说明>)` | 中文说明 |

> ⚠️ `inherit=` 取的是"最近一次见到的值"，**不是"包含它的那一层"**。
> 实测：`interface IF1` 块**之后**出现的 `port 9/9/9` 会被归到 `IF1`。
> 需要真正的"所属"关系时，用**嵌套子集合**（作用域由状态栈保证）。

`option(...)` 里可写多个标记，空格或逗号分隔：`option(Required Key)`。

**键的构成**：集合内所有 `option(Key)` 字段值的**元组**。

```text
Prefix      type(str) reg(\S+) desc(前缀) option(Key)
Mask        type(str) reg(\S+) desc(掩码) option(Key)
VpnInstance type(str) reg(\S+) desc(VPN 实例) option(Key)
```

> 复合键的必要性：华为有两条路由前缀相同、掩码不同
> （`10.108.8.0/22` 与 `10.108.8.0/24`），只按前缀聚合会把它俩并成一条。

**缺键的处理**：全部键字段都为空 → 记录无法识别 → **丢弃**；
部分为空（可选键）→ 空值参与键计算，不合并也不丢弃。

> Key 聚合只作用于**同一父元素下的子记录**。顶层集合不聚合——
> Nokia 不同 router 下有大量同名接口，合并会直接吞数据。

**缩进表示字段的嵌套**（一对一子结构）：

```text
Statistic   type(dict) desc(流量统计)
  Enable type(bool) desc(统计开关)
  Mode   type(str)  reg(\S+) desc(统计模式)
```

`reg` 注意贪婪：`\S+` 会吃掉 `/` 这类分隔符，`[^/\s]+` 才安全。

### 3.3 `[rule]` 段 —— 规则声明

```
<正则> [-> <目标路径>] [=常量] [{字段1,字段2}]
```

规则段里**一行就是一条正则**（对应 TextFSM 的 `Start` 段）。
长规则可换行续写（续行缩进大于段基准）。

### 3.4 `${字段}` 占位符（TextFSM 风格）

```
集合：  Name type(str) reg(\S+)
规则：  ^interface\s+${Name}
编译：  ^interface\s+(?P<Name>\S+)
```

- **命名组名 = 字段名 → 自动对应目标列**，`-> Description` 可省略
- 重复引用编译成**回溯引用** `(?P=Name)`，语义"两处必须相同"：

```
^\s*ip address\s+${Address}\s+${Address}$
→ ^\s*ip address\s+(?P<Address>\S+)\s+(?P=Address)$
  'ip address 1.1.1.1 1.1.1.1' → 匹配
  'ip address 1.1.1.1 2.2.2.2' → 不匹配
```

- **嵌套字段用点号引用**：`${Statistic.Mode}` → 捕获组名 `Statistic__Mode`，
  写入时还原成 `{"Statistic": {"Mode": ...}}`

### 3.4.1 两种写法：`${字段}` 与裸命名组

| 写法 | 例子 | 落到 | 支持嵌套字段 |
|---|---|---|---|
| `${字段}` | `^interface\s+${Name}` | 顶层字段 `Name` | ✅ `${Statistic.Mode}` |
| 裸命名组 | `^\s*interface\s+"(?P<Name>[^"]+)"` | 顶层字段 `Name` | ❌ 见下 |

**三条硬约束**：

1. **同一条规则里不能混用** `${X}` 与 `(?P<X>...)` —— 会抛 `redefinition of group name` 崩溃
2. **裸命名组不还原点号路径** —— `(?P<Stat__Mode>\S+)` 写成平铺列 `"Stat__Mode"`，
   不是 `{"Stat": {"Mode": ...}}`。写嵌套字段**必须用** `${Stat.Mode}`
3. 裸命名组的组名**不在字段表里也不会报错**（会静默产生一个未声明的列）——
   在 lint 补上这条检查之前，务必人工核对组名

> 什么时候用哪种：需要**引用字段声明里的 `reg()`** 时用 `${字段}`；
> 需要**写正则里没法用 reg 表达的片段**（如 `"[^"]+"`）时用裸命名组。
> 两者服务于同一条规则时的常见错误就是混用。

### 3.5 目标路径：`->` 落到哪

| 写法 | 落在哪 | 写什么 |
|---|---|---|
| 省略 `->` | 当前集合·当前记录 | 全部捕获组 |
| `-> 字段` | 当前集合·当前记录 | 全部捕获组 |
| `-> 字段=常量` | 当前集合·当前记录 | 全部捕获组 **+** 该字段 |
| `-> 路径.字段` | 当前记录内的嵌套 dict | **只写**该路径 |
| `-> 路径.字段=常量` | 当前记录内的嵌套 dict | **只写**该路径 |
| `-> 集合名{字段1,字段2}` | 指定集合（跨集合时用） | 只写花括号里列出的字段 |

**当前集合 = 这条规则所在的那个 `[rule]` 段的集合。**
规则必须写在它要写的那一层里，否则会**静默写错对象**（当前 lint 不会报这件事，见 §5）。

```text
  IpAddress type(list) start(^\s*ip(v6)? address)
  [value]
  Address type(str) reg([^/\s]+) desc(地址)
  Mask    type(str) reg(\S+) desc(掩码)
  Family  type(str) reg(ipv4|ipv6) desc(地址族)
  [rule]
  ^\s*ip address\s+${Address}\s+${Mask}\s*$   -> Family="ipv4"
```

= 写进 IpAddress 当前记录：`Address` + `Mask`（捕获组）+ `Family="ipv4"`（常量）。

实测对比（同一行）：

```text
-> Family="ipv4"                        → {Address, Mask, Family:"ipv4"}
-> IpAddress{Address,Mask,Family="ipv4"} → {Address, Mask, Family:"ipv4"}   ← 等价
省略 ->                                  → {Address, Mask}                  ← 常量丢了
```

**跨集合才需要写集合名**：

```text
^\s*group\s+${Groups.Name}\s+internal\s*$  -> Groups{Name,Type="internal"}
```

`Groups` 是 Bgp 的嵌套 list 字段 → 追加一个元素 `{"Name":..., "Type":"internal"}`。

### 3.6 规则顺序与命令变体

按声明顺序，**第一条命中即生效**。更精确的规则要写前面，
或给宽松的规则加 `$` 锚点，避免抢先把长命令吃掉：

```
^\s*ipv6 address\s+${Address}\s*$          ← 必须有 \s*$
^\s*ipv6 address\s+auto\s+${Mode}\s*$
```

同一命令的多种写法要各写一条。lint 会把"命中了但值不满足 reg"的行报出来：

```
^\s*ip route-static\s+${Prefix}\s+${Mask}\s+${NextHop}...
^\s*ip route-static\s+vpn-instance\s+${VpnInstance}\s+${Prefix}\s+${Mask}\s+${NextHop}...
```

### 3.7 字段类型

`str` / `int` / `float` / `bool` / `dict`（单个对象）/ `list`（数组，默认）/ `secret`（凭据）。

`secret` 与其他类型互斥。捕获值在引擎写入时立即替换为固定掩码 `******`：
- L1：字段显式声明 `type(secret)`；不得与 `option(Key)` 同用，也不得被 `inherit=` 引用。
- L2：即使模板漏标，华为 `%\^%#...%\^%#` 等密文形态也会被兜底遮蔽。
- CLI 可用 `--mask-value <字符串>` 修改掩码，或 `--no-mask-heuristic` 关闭 L2（生产不建议）。
- `PasswordType type(str)` 只保存 `cipher` 等类型，不是密码；`Password type(secret)` 才是凭据。

### 3.8 集合类型

`dict` = 单个对象（如设备级元信息）；
`list` = 数组（默认，如接口列表）。

---

## 4. 场景手册

每个场景三段：**配置原文 → 模板 → 输出**。

### 场景 1 · 抓一个简单字段

```
interface GigabitEthernet1/0/0
 description TO-[SNANK-PC-CMNET-RT01-NE5KE]-GE4/0/0=1*10GE
```
```text
Interfaces type(list) start(^interface\s+)
[value]
Name        type(str) reg(\S+) option(Key)
Description type(str) reg(.+)
[rule]
^interface\s+${Name}
^\s*description\s+${Description}\s*$
```
```json
{ "Name": "GigabitEthernet1/0/0",
  "Description": "TO-[SNANK-PC-CMNET-RT01-NE5KE]-GE4/0/0=1*10GE" }
```

### 场景 2 · 抓多个参数

```
 ip address 111.19.27.122 255.255.255.252
```
```text
  IpAddress type(list) start(^\s*ip(v6)? address)
  [value]
  Address type(str) reg([^/\s]+)
  Mask    type(str) reg(\S+)
  [rule]
  ^\s*ip address\s+${Address}\s+${Mask}\s*$
```
```json
{ "IpAddress": [ { "Address": "111.19.27.122", "Mask": "255.255.255.252" } ] }
```

> 规则写在 IpAddress 自己的 `[rule]` 里 → 省略 `->` 就是写进 IpAddress 当前记录。
> `Address` 的 reg 必须是 `[^/\s]+` 而不是 `\S+`——`\S+` 贪婪会把 `/127` 一起吃掉。

### 场景 3 · 开关型命令（正向 / 反向）

```
 shutdown
 undo shutdown
```
```text
  AdminStatus type(str) default(up) reg(up|down)

^\s*undo shutdown\s*$ -> AdminStatus=up
^\s*shutdown\s*$      -> AdminStatus=down
```
```json
{ "AdminStatus": "down" }
```

> 同一条命令的正反两个写法写两条规则。华为接口默认 up，
> `default(up)` 不能省，否则**没配过 shutdown 的接口状态会为空**。
> 实测样本（华为 ME60-X16A，1044 个接口）里 1044 个都显式配了状态，
> 但别的样本（如 BRAS22）有 **672/802 没配**——那种情况下只能靠默认值兜底。

### 场景 4 · 重复命令自动成数组（注意锚点）

```
 ipv6 address 2409:8070:9A:1::702F/127
 ipv6 address auto link-local
```
```text
  Ipv6Addresses type(list) start(^\s*ipv6 address)
  [value]
  Address type(str) reg([^/\s]+)
  Mode    type(str) reg(\S+)
  [rule]
  ^\s*ipv6 address\s+${Address}\s*$          -> Ipv6Addresses{Address}
  ^\s*ipv6 address\s+auto\s+${Mode}\s*$      -> Ipv6Addresses{Address="auto",Mode}
```
```json
{ "Ipv6Addresses": [
    { "Address": "2409:8070:9A:1::702F/127" },
    { "Address": "auto", "Mode": "link-local" } ] }
```

> **第一条规则末尾的 `\s*$` 不能省**。少了它，`ipv6 address auto link-local`
> 会先被第一条命中（`${Address}` 匹配到 "auto"），Mode 就丢了。

### 场景 5 · 命令族合并成嵌套对象

```
 statistic enable
 statistic mode forward
```
```text
  Statistic type(dict)
    Enable type(bool)
    Mode   type(str) reg(\S+)

^\s*statistic enable\s*$                   -> Statistic.Enable=true
^\s*statistic mode\s+${Statistic.Mode}\s*$  -> Statistic.Mode
```
```json
{ "Statistic": { "Enable": true, "Mode": "forward" } }
```

> 点号路径自动建嵌套对象；字段声明用缩进表达嵌套，规则目标用点号引用。

### 场景 6 · 隐含对象聚合（最难的一种）

同一对象的属性散落在多行：

```
 peer 218.200.1.10 as-number 64650
 peer 218.200.1.10 group RR-L2
 peer 218.200.1.10 description SNANK-PC-CMNET-RT01-NE5KE
```
```text
  BgpPeers type(list) start(^\s*peer)
  [value]
  Peer        type(str) reg(\S+) option(Key)
  AsNumber    type(int) reg(\d+)
  Group       type(str) reg(\S+)
  Description type(str) reg(.+)
  [rule]
  ^\s*peer\s+${Peer}\s+as-number\s+${AsNumber}\s*$
  ^\s*peer\s+${Peer}\s+group\s+${Group}\s*$
  ^\s*peer\s+${Peer}\s+description\s+${Description}\s*$
```
```json
{ "Peer": "218.200.1.10", "AsNumber": 64650, "Group": "RR-L2",
  "Description": "SNANK-PC-CMNET-RT01-NE5KE" }
```

> **`option(Key)` 是关键**：同一 `Peer` 的多条命令合并成一个对象，
> 后到的补空缺、不覆盖已有。实测（华为 ME60-X16A）：41 行 peer 命令 → 6 个 peer 对象。

### 场景 7 · 可选段（一条规则覆盖多种写法）

```
 ip route-static 103.85.74.96 255.255.255.224 NULL0 description huwang-fengdu
 ip route-static 111.18.27.196 255.255.255.252 NULL0 preference 220 description JiaKuanVIP1
 ip route-static vpn-instance ChinaMobile_CMNET_SN_IMS 10.59.104.0 255.255.248.0 NULL0
```
```text
RouteStatics type(list) start(^ip route-static)
[value]
Prefix      type(str) reg(\S+) option(Key)
Mask        type(str) reg(\S+) option(Key)
Mask        type(str) reg(\S+)
NextHop     type(str) reg(\S+)
Preference  type(int) reg(\d+)
Description type(str) reg(.+)
VpnInstance type(str) reg(\S+)
[rule]
^\s*ip route-static\s+${Prefix}\s+${Mask}\s+${NextHop}
      (?:\s+preference\s+(?P<Preference>\d+))?
      (?:\s+description\s+(?P<Description>.+))?\s*$
      -> RouteStatics{Prefix,Mask,NextHop,Preference,Description}
^\s*ip route-static\s+vpn-instance\s+${VpnInstance}\s+${Prefix}\s+${Mask}\s+${NextHop}
      (?:\s+preference\s+(?P<Preference>\d+))?
      (?:\s+description\s+(?P<Description>.+))?\s*$
      -> RouteStatics{Prefix,Mask,NextHop,Preference,Description,VpnInstance}
```
```json
[
  { "Prefix": "103.85.74.96", "Mask": "255.255.255.224", "NextHop": "NULL0",
    "Description": "huwang-fengdu" },
  { "Prefix": "111.18.27.196", "Mask": "255.255.255.252", "NextHop": "NULL0",
    "Preference": 220, "Description": "JiaKuanVIP1" },
  { "Prefix": "10.59.104.0", "Mask": "255.255.248.0", "NextHop": "NULL0",
    "VpnInstance": "ChinaMobile_CMNET_SN_IMS" }
]
```

> 可选段用 `(?:...)?`。里面用 `(?P<X>...)` 而不是 `${X}`，
> 因为可选组的字段在字段声明里也要声明，两边名字要对得上。
>
> **命令变体要各写一条**：实测（华为 ME60-X16A）28 条 route-static 里 18 条带 `vpn-instance` 前缀。

### 场景 8 · 关联关系（子集合自动归属）

```
interface GigabitEthernet1/0/2
 eth-trunk 42
```
```text
Interfaces type(list) start(^interface\s+)
[value]
Name type(str) reg(\S+) option(Key)
[rule]
^interface\s+${Name}

  EthTrunkMember type(list) start(^\s*eth-trunk)
  [value]
  Trunk type(str) reg(\S+) desc(所属聚合组)
  [rule]
  ^\s*eth-trunk\s+${Trunk}\s*$
```
```json
{ "Name": "GigabitEthernet1/0/2",
  "EthTrunkMember": [ { "Trunk": "42" } ] }
```

> **子集合的元素自动嵌在父元素里**，不用写父层的 key 字段，也不用 `inherit=`。
>
> 注意华为的 `eth-trunk 42` 是**聚合组编号**（不是 `Eth-Trunk42`），
> 要转成接口名得靠 transform。

### 场景 9 · 中间层不成对象

```
ip vpn-instance ChinaMobile_CMNET_SN_IMS
 ipv4-family
  route-distinguisher 64650:10009150
```
```text
VpnInstances type(list) start(^ip vpn-instance)
[value]
Name               type(str) reg(\S+) option(Key)
RouteDistinguisher type(str) reg(\S+)
IpFamilies         type(list)
  Family type(str) reg(\S+)
[rule]
^ip vpn-instance\s+${Name}
^\s*route-distinguisher\s+${RouteDistinguisher}\s*$
^\s*ipv4-family\s*$               -> IpFamilies{Family="ipv4"}
```
```json
{
  "Name": "ChinaMobile_CMNET_SN_IMS",
  "RouteDistinguisher": "64650:10009150",
  "IpFamilies": [ { "Family": "ipv4" } ]
}
```

> `ipv4-family` 那条规则**没有独立的 start** → 它不成为独立层，
> 它的子命令归到当前层（VpnInstance）。

### 场景 10 · 自由文本（含空格）

```
 description TO-SNANK RT01 NE5000 ETH-Trunk23
```
```text
  Description type(str) reg(.+)

^\s*description\s+${Description}\s*$
```
```json
{ "Description": "TO-SNANK RT01 NE5000 ETH-Trunk23" }
```

> 字段的 `reg` 写成 `(.+)` 就能吃到含空格的整段。
> 值里本来就没空格时用 `\S+` 更严。

### 场景 11 · 元信息（栈底常驻）

```
!Software Version V800R023C00SPC100
sysname SNANK-MC-CMNET-BRAS22-NHXL-X16A
```
```text
Device type(dict) desc(设备)
[value]
Version type(str) reg(\S+) desc(版本)
SysName type(str) reg(\S+) desc(系统名)
[rule]
^!Software Version\s+${Version}\s*$
^sysname\s+${SysName}\s*$
```
```json
{ "Device": { "Version": "V800R023C00SPC100",
               "SysName": "SNANK-MC-CMNET-BRAS22-NHXL-X16A" } }
```

> **无 `start` = 栈底常驻层**，规则始终生效。

### 场景 12 · 嵌套 还是 独立顶层集合？

**判据：该命令是不是只出现在父层上下文里。**

| 命令 | 出现位置 | 该用哪种 |
|---|---|---|
| 华为 `ip address` | 258 条**全在** interface 块内 | **嵌套** |
| Nokia `port` | 598 条里**只有 43 条**在 interface 块内 | 独立顶层 + `inherit=` ⚠️ 见下方警告 |

实测代价：Nokia 的 `port` 做成嵌套只抓到 43，做成顶层常开抓到 598（**但归属会错**，见场景 12 的警告）。

```text
# 独立顶层集合（跨上下文）+ inherit 回填父 key
NokiaPorts type(list) desc(绑定物理口)
[value]
Interface type(str) reg(\S+) inherit=NokiaInterfaces.Name desc(所属接口)
Port      type(str) reg(\S+) desc(物理口)
[rule]
^\s*port\s+(?P<Port>\S+)\s*$   -> NokiaPorts{Interface,Port}
```

> ⚠️ **这个模式有两个坑，照抄会丢数据**：
>
> 1. **`inherit=` 是"上次见到"不是"所属"** —— 实测 `interface IF1` 块**之后**出现的
>    `port 9/9/9` 会被归到 `IF1`。
> 2. **独立顶层集合没写 `start(...)` → 整个文件只产出一条记录**（后写覆盖先写）。
>
> Nokia 的 `port` 有 598 条、其中只有 43 条在 interface 块内，
> 归属关系需要**作用域模型增强**才能做对——**当前没有可用的写法**，单独排期。

### 场景 13 · 作用域隔离（**最核心的价值**）

同一个命令出现在多个模块：

```
aaa
 password cipher %^%#xxx%^%#
bgp 64650
 peer RR-L2 password cipher %^%#yyy%^%#
```

不做作用域限定时，两条密码会被抓进同一个字段。

```text
  BgpPeers type(list) start(^\s*peer)
  [value]
  Peer     type(str) reg(\S+) option(Key)
  Password type(str) reg(.+)
  [rule]
  ^\s*peer\s+${Peer}\s+password\s+${Password}
```

> 子集合的规则只在**父层激活期间**匹配，
> aaa 块的密码天然被隔离。

实测数据：华为 BRAS 配置里 `password` 出现在 **5 个模块**
（interface 88、local-aaa-server 31、bgp 9、aaa 7、user-interface 2），
`description` 出现在 **4 个模块**（interface 638、acl 30、ip 10、bgp 6）。

### 场景 14 · 命令变体要各写一条（真实形态清单）

**同一条命令在不同设备上有多种写法，每种都要一条规则。**
下面是华为真实配置里实测到的形态，漏掉任何一种，那条命令就整类抓不到：

```text
# vlan-type：299 条全是 3 token（dot1q + 编号）
VlanType   type(str) reg(dot1q|trunk) desc(VLAN类型)
VlanId     type(int) reg(\d+)      desc(VLAN编号)
[rule]
^\s*vlan-type\s+${VlanType}\s+${VlanId}\s*$
```

```text
# user-vlan：三种形态（2 token / 5 token / 6 token）
#   user-vlan 100
#   user-vlan 1000 1999 qinq
#   user-vlan 1000 1999 qinq 1888 1890
UserVlan   type(int) reg(\d+) desc(用户VLAN)
QinqOuter  type(int) reg(\d+) desc(QinQ外层)
[rule]
^\s*user-vlan\s+${UserVlan}\s*$
^\s*user-vlan\s+${UserVlan}\s+${QinqOuter}\s+qinq\s*$
^\s*user-vlan\s+${UserVlan}\s+${QinqOuter}\s+qinq\s+${QinqInner}\s+${QinqInner2}\s*$
```

```text
# vrrp：两种形态（virtual-ip / priority）
Vrrp.Vrid    type(int) reg(\d+) desc(VRID)
Vrrp.VirtualIp type(str) reg(\S+) desc(虚IP)
Vrrp.Priority type(int) reg(\d+) desc(优先级)
[rule]
^\s*vrrp\s+vrid\s+${Vrrp.Vrid}\s+virtual-ip\s+${Vrrp.VirtualIp}\s*$
^\s*vrrp\s+vrid\s+${Vrrp.Vrid}\s+priority\s+${Vrrp.Priority}\s*$
```

```text
# nqa：两种形态，test-instance 是 4 token
#   nqa twamp-light
#   nqa test-instance ls.dev 1648461635486
Type       type(str) reg(\S+) desc(类型)
Name       type(str) reg(\S+) desc(名称)
InstanceId type(str) reg(\S+) desc(实例ID) option(Key)
[rule]
^nqa\s+(?P<Type>\S+)(?:\s+(?P<Name>\S+))?(?:\s+(?P<InstanceId>\S+))?\s*$
```

```text
# track：真实是 4 token，且第一个是名字不是数字
#   track monitor-group JianKong trigger-down-weight 100
TrackGroup  type(str) reg(\S+) desc(联动组)
TrackWeight type(int) reg(\d+) desc(权重)
[rule]
^\s*track\s+monitor-group\s+${TrackGroup}\s+trigger-down-weight\s+${TrackWeight}\s*$
```

> **写规则前先统计形态**：`python tools/legacy/probe_acl_rules.py <配置>` 这类探针可以先看命令长什么样，
> 再决定字段怎么拆。**凭印象写规则是模板 bug 的主要来源**——
> 上面 5 条里，现网模板有 4 条写错了（`vlan-type` / `user-vlan` / `vrrp` / `track` 全都没命中过）。

---

## 5. Lint —— 模板写完必跑

```bash
netparse lint <模板>
```

> 旧写法 `python tools/probe_stack.py <模板> <配置> --lint` 仍可用（`tools/` 留了兼容 shim），
> 但 `netparse lint` 是现在的正式入口。

### 5.1 已经实现的检查（会真的报错）

| 检查 | 级别 | 怎么修 |
|---|---|---|
| `${字段}` 引用了未声明的字段 | **错误** | 补字段声明 |
| 集合级还写着 `Key(...)` / `Required(...)` | **错误** | 改到字段的 `option(Key)` / `option(Required)` |
| `option(Key)` 标在了嵌套字段上 | **错误** | Key 必须是本集合的顶层字段 |
| 目标里的字段 / 路径没声明 | **错误** | 补字段声明 |

### 5.2 计划中、**尚未实现**的检查（别指望它们会报错）

| 检查 | 计划级别 | 现状 |
|---|---|---|
| `inherit=` 的来源集合或字段不存在 | 错误 | ❌ 未实现，拼错不报错 |
| 规则正则语法错误 / 组名重定义 | 错误 | ❌ 未实现，运行时抛裸 `PatternError`（不知哪条规则） |
| 裸命名组名不在字段表 | 错误 | ❌ 未实现，静默产生一个未声明的列 |
| 集合名重复 / 同层字段名重复 | 错误 | ❌ 未实现，静默覆盖（同名集合还会产生空记录） |
| 子集合没有 `start` | 错误 | ❌ 未实现，**静默 0 条**，输出里整个键消失 |
| 未知段头（如 `[ignore]`） | 错误 | ❌ 未实现，静默跳过且**不重置 mode** → 幽灵字段 / 幽灵集合 |
| 规则命中但值不满足 `reg` | 警告 | ❌ 未实现 |
| 实际配置里的命令未被任何集合覆盖 | 警告 | ⚠️ **有工具但未接入 lint**，见 5.3 |
| 同一命令被多条规则命中 | 警告 | ❌ 未实现 |
| `Required` 字段在记录里缺失 | 错误 | ⚠️ 引擎**运行时**会丢弃该记录，但 lint 不报 |
| 集合声明了但配置里从未出现 | 提示 | ❌ 未实现 |

### 5.3 「命令未被覆盖」——单独跑（这条最重要）

它保证一个接口不漏。当前用探针跑：

```bash
python tools/legacy/probe_coverage3.py templates/huawei-vrp.cmdb <配置文件>
```

输出按**命令族**给覆盖率与缺口样例。实测（华为 ME60-X16A）：

| 命令族 | 行数 | 覆盖 | 影响 |
|---|---|---|---|
| `user-vlan` | 1533 | 163（11%） | PVLAN / CVLAN |
| `rule` | 686 | 129（19%） | ACL 明细 |
| `bas` | 514 | 0 | 域名名称 |
| `vlan-type dot1q` | 299 | 0 | VLAN 封装类型 |
| `ipv6 enable` | 226 | 0 | IPv6 使能 |
| `pim sm` | 141 | 0 | 组播类型 |
| `vrrp vrid` | 110 | 0 | VRRP 虚 IP |
| `qos car` | 94 | 0 | 入/出流限速 |

> ⚠️ **统计口径**：必须是**引擎内埋点**的真实匹配结果。
> 事后用正则重测会把作用域外的同名命令（比如 aaa 块里的 `description`）误判成已覆盖，
> 两种口径的数字差着一个量级。

### 5.4 全量台账口径的实测缺口（与 5.3 不可混算）

5.3 是**单台设备、命令族行数**口径；下面这张是**跨月语料、19 列台账值条数**口径。
两者分母不同，**不能相互印证或换算**。

复现：`netparse diff --all --jobs 12`（20 文件抽样结果与全量方向一致）

| 列 | incumbent | netparse 修前 | netparse 修后 | 证据化根因 |
|---|---|---|---|---|
| 接口入流限速(M) | 16378 | 0 | 0 | 模板未覆盖该命令 |
| 接口出流限速(M) | 16279 | 0 | 0 | 模板未覆盖该命令 |
| 端口IPv6 | 3872 | 18770 | 18770 | **语义不同**（netparse 多，见下） |
| IPv4静态路由明细 | 210 | 0 | **210** | 修前整条记录丢失，已修复 |
| IPv6静态路由明细 | 364 | 0 | 0 | 模板未覆盖该命令 |
| VPN实例 | 37806 | 16583 | 16583 | 模板未覆盖该命令（第二种形态） |
| 域名名称 | 63796 | 0 | 0 | 模板未覆盖该命令 |
| 组播类型 | 18284 | 0 | 0 | 模板未覆盖该命令 |
| 入/出方向流量策略 | 19880 / 15199 | 0 | 0 | 模板未覆盖该命令 |
| PVLAN | 33910 | 0 | 0 | 模板未覆盖该命令 |
| CVLAN / VLAN封装类型 | 116142 | 0 | 0 | 模板未覆盖该命令 |

**三条容易写错的实证结论：**

1. **端口IPv6 是「netparse 多」，不是缺口。** 2039 vs 493（20 文件口径：18770 vs 3872），
   方向是 netparse 多出 —— 属捕获范围/入表口径差异，**不计入模板补齐人日**。
   只标红不诊断方向，会把它错算成一列要补的工作量。
2. **VPN实例两种形态，只覆盖一种。** 真机 4622 = `ip binding vpn-instance` 2016
   + BAS 块内 `vpn-instance` 其余；模板只声明了前者。所以不能写"VPN 已支持"，
   也不能只写"VPN 未覆盖"。
3. **PVLAN 在真机上没有独立 `qinq` 命令。** 外层与内层同行：
   `user-vlan 1000 1999 qinq 2000`。按"独立 qinq 行"写规则会一条都匹配不到。
   另：`vlan-type dot1q` 模板无对应规则，这是 CVLAN/VLAN封装类型为 0 的直接原因。

---

## 6. 常见问题速查

| 现象 | 原因 | 修法 |
|---|---|---|
| 输出里字段名不对 | 捕获组名和目标列名不一致 | 在目标里显式写列名（引擎**不会**自动驼峰化） |
| 参数没抓到 | 正则和命令对不上 | 核对 token 数与分隔符 |
| 值被截断（只取一半） | `reg` 写的是 `\S+` 但值含空格或 `/` | 改成 `(.+)` 或 `[^/\s]+` |
| 整条规则不匹配 | `reg` 贪婪吃掉了后面的分隔符 | 用字符类排除分隔符 |
| 数组里混进了不该有的元素 | 规则缺 `\s*$` 锚点，先命中了长的那条 | 加锚点或调整规则顺序 |
| 数组只有一个元素 | 规则没覆盖另一种写法 | 补一条规则（见场景 3、7） |
| 字段没出现在对象里 | 目标列不在字段声明里 | lint 会报（目标字段未声明已实现）；补声明 |
| 子集合元素跑到顶层了 | 子集合缩进不对 | 核对缩进层级 |
| 重复命令覆盖了 | 目标列不是 list | 字段声明里把类型改成 `list` |
| 聚合没合并 | 字段没标 `option(Key)` | 给该字段加 `option(Key)` |
| 输出只有一条记录 | 集合没写 `start(...)` | 无 `start` 的集合整个文件只产出一条（后写覆盖先写） |
| 某个子集合整个消失 | 子集合没写 `start(...)` | 子集合无 `start` → 永不激活 → 静默 0 条 |
| 子集合列有时是对象有时是数组 | 父集合是 `type(dict)` 且只有 1 个子元素 | 消费方按 list-or-dict 兼容；或把父改成 `type(list)` |
| 嵌套字段写成了平铺列 | 用了裸命名组 `(?P<A__B>...)` | 裸命名组不还原点号路径，改用 `${A.B}` |
| 抓到别的模块的字段 | 集合的 start/end 范围写错 | 核对区间正则 |
| 字段值类型不对 | 字段声明的是 `str` | 改成 `int` / `float` |
| 记录莫名消失 | 键字段没抓到值（全空） | 检查该集合的 `option(Key)` 字段是不是本该有值 |
| 记录莫名消失 | `option(Required)` 字段缺失 | 该记录本来就无效 |

---

## 7. 完整模板与工具在哪

| 文件 | 覆盖 |
|---|---|
| `templates/huawei-vrp.cmdb` | 华为 VRP，12 个集合（含 5 个子集合） |
| `templates/nokia-sros.cmdb` | Nokia SR OS，1 集合 + 2 子集合 |

| 命令 | 作用 |
|---|---|
| `netparse parse <模板> <配置>` | 解析并打印集合树 + 结果统计 |
| `netparse parse <模板> <配置> --json` | 输出完整 JSON（stdout 是纯 JSON，可直接 `json.load`） |
| `netparse lint <模板>` | 只跑模板体检 |
| `netparse diff --all --jobs 12` | 与 incumbent 的 19 列三栏对比（需生产只读语料） |
| `python tools/legacy/probe_coverage3.py <模板> <配置>` | **命令族覆盖率 + 缺口样例**（写完模板必跑） |
| `python tools/legacy/probe_coverage2.py <模板> <配置>` | 全量覆盖率 + 零覆盖命令族清单 |
| `python tools/legacy/probe_blind.py` | 引擎边界行为用例（end / inherit / 重名 / 正则 / 命名组） |
| `python tests/run_regression.py` | 回归基线比对（`--update` 覆盖基线） |

> `tools/` 下保留的是**兼容 shim**（旧命令仍可跑）+ `legacy/` 历史探针。
> 正式用法是 `netparse` 子命令；库用法见 `README.md`。

回归基线：`tests/fixtures/sample-vrp.txt` + `sample-vrp.expected.json`
（**嵌套形状**：子集合的元素嵌在父元素里）。
