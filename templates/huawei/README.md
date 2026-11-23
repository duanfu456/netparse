# 华为模板（模块化目录版）

入口：`main.np`（9 个 `from ... import *` 组合，`.cmdb` 后缀可省略）。

| 文件 | 组内容 | 字段规模 |
|---|---|---|
| `00-device.np` | Device 设备级信息（sysname/版本/RouterId/型号） | 4 |
| `01-interfaces.np` | Interfaces 接口全家桶（含 IpAddress / Bas 子集合） | 92 |
| `02-routes.np` | RouteStatics 静态路由（含 NULL0 黑洞、vpn-instance 变体） | 7 |
| `03-vpn.np` | VpnInstances（IPv4/IPv6 地址族、RD/vpn-target） | 2+子 |
| `04-bgp.np` | Bgp（邻居组/邻居子集合） | 13 |
| `05-acl.np` | Acls（规则子集合） | 3+规则 |
| `06-nqa.np` | Nqas（会话子集合） | 8 |
| `07-static-user.np` | StaticUsers BAS 静态用户绑定 | 7 |
| `08-qos.np` | QosProfiles（car/user-queue，In/Out 视图，限速单位 M/MB） | 20 |

`transforms_huawei.py`：接口类型派生等 transform（被 00-device 之外多组引用，放目录根部）。

## 使用

```bash
# 全量
netparse parse main.np device.cfg --json
# 单组调试
netparse parse 02-routes.np device.cfg --json
# 字段裁剪（解析全量、输出按需；Key 字段自动保留）
netparse parse main.np device.cfg --json --fields "Interfaces=Name,Type,IpAddress"
```

## 维护约定

- **父子捆绑**：子集合只能写在父集合所在的组文件里（数据挂载点在父记录）
- **跨文件顶层集合同名 = 错误**（引擎 parse 期报错）
- include 路径相对当前文件；`.cmdb` 后缀可省略
- 改完跑：`netparse lint main.np --strict` → 抽样解析 → 与单文件版输出对比
- `[__main__]` 自测试段写法范本：SELFTEST-TEMPLATE.md；完整说明：docs/__MAIN__-说明.md
