# ZStack Machinery for CAPEv2

ZStack Cloud 5.5.6 虚拟化平台集成模块，用于 CAPEv2 沙盒系统。

## 基于官方文档的实现

本模块基于 **ZStack Cloud V5.5.6 开发手册** 实现，完全遵循 ZStack 5.x API 规范：

### ZStack 5.x API 关键特性

1. **API 端点格式**: `http://<host>:8080/zstack/v1/<resource>`
2. **认证方式**: 
   - `PUT /zstack/v1/accounts/login` 获取 session UUID
   - 密码使用 SHA512 哈希
   - 支持双因子认证（systemTags: ["twofatoken::<code>"]）
3. **HTTP 方法**:
   - `PUT` - 执行操作（启动/停止 VM）
   - `GET` - 查询资源
   - `POST` - 创建资源
   - `DELETE` - 删除资源
4. **异步任务**: 返回 `location` 头包含 job UUID，需轮询 `/zstack/v1/api-jobs/{uuid}`

## 目录结构

```
zstack/
├── __init__.py           # 模块初始化
├── zstack_api.py         # ZStack REST API 封装（基于 5.5.6）
├── zstack.py             # ZStack Machinery 主模块
├── zstack.conf.example   # 配置文件示例
├── test_zstack.py        # 独立测试脚本
└── README.md             # 本文档
```

## 主要功能

- ✅ VM 生命周期管理（启动、停止、重启）
- ✅ 快照管理（创建、查询、恢复、删除）
- ✅ 内存转储支持
- ✅ **双因子认证支持**
- ✅ 会话管理和自动认证
- ✅ 异步任务等待机制
- ✅ 完善的错误处理和日志记录
- ✅ 符合 CAPEv2 machinery 接口规范

## 与旧版本的区别

### 4.x vs 5.x API 差异

| 功能 | 4.x API | 5.x API | 说明 |
|------|---------|---------|------|
| 创建快照 | `POST /volume-snapshots/group` | `POST /volume-snapshots` | 5.x 简化了路径 |
| 恢复快照 | `revertVmFromSnapshotGroup` | `revertVmFromSnapshot` | 5.x 去掉了 Group |
| 认证参数 | 基础认证 | 支持 systemTags | 5.x 支持双因子认证 |
| 查询语法 | 简单查询 | ZQL 语法 | 5.x 支持复杂查询 |

## 安装依赖

```bash
# 确保已安装 requests 库
pip install requests
```

## 配置说明

### 1. 复制配置文件

```bash
cp zstack.conf.example zstack.conf
```

### 2. 编辑配置

编辑 `zstack.conf` 文件：

```ini
[zstack]
# ZStack API 地址（必填）
zstack_api = http://192.168.1.100:8080

# ZStack 账户凭证（必填）
zstack_name = admin
zstack_pwd = your_password

# 双因子认证码（可选）
# 如果启用了 2FA，填入 TOTP 应用生成的 6 位代码
two_fa_code = 123456

# SSL 验证（可选，默认：no）
verify_ssl = no

# API 请求超时（可选，默认：30 秒）
timeout = 30

# 虚拟机列表
machines = cuckoo1, cuckoo2

# 虚拟机配置示例
[cuckoo1]
label = cuckoo-sandbox-01
ip = 192.168.100.10
platform = windows
arch = x64
snapshot = clean-state
tags = win10,office
```

### 3. 配置参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `zstack_api` | 是 | ZStack API 服务端点 URL |
| `zstack_name` | 是 | ZStack 账户名 |
| `zstack_pwd` | 是 | ZStack 账户密码（会自动 SHA512 哈希） |
| `two_fa_code` | 否 | 双因子认证代码（如果启用了 2FA） |
| `verify_ssl` | 否 | 是否验证 SSL 证书（默认：no） |
| `timeout` | 否 | API 请求超时时间（默认：30 秒） |
| `machines` | 是 | 虚拟机列表 |

## 双因子认证配置

### 启用 2FA 的步骤

1. 在 ZStack Cloud 控制台启用双因子认证
2. 使用 TOTP 应用（如 Google Authenticator）绑定
3. 获取当前 6 位验证码
4. 在配置文件中填入 `two_fa_code`

### 自动获取 2FA 代码（推荐）

可以编写脚本自动获取 2FA 代码：

```python
# 示例：从 TOTP 密钥生成 2FA 代码
import pyotp

totp_secret = "YOUR_TOTP_SECRET"
totp = pyotp.TOTP(totp_secret)
two_fa_code = totp.now()
```

## 独立测试

在集成到 CAPEv2 之前，可以先独立测试 ZStack 模块：

```bash
# 基础测试（仅认证和 VM 列表）
python test_zstack.py \
  --api-url http://192.168.1.100:8080 \
  --username admin \
  --password password

# 完整测试（包含 VM 生命周期）
python test_zstack.py \
  --api-url http://192.168.1.100:8080 \
  --username admin \
  --password password \
  --test-vm cuckoo-sandbox-01 \
  --verbose

# 使用双因子认证测试
python test_zstack.py \
  --api-url http://192.168.1.100:8080 \
  --username admin \
  --password password \
  --two-fa-code 123456
```

### 测试参数

| 参数 | 说明 |
|------|------|
| `--api-url` | ZStack API 地址（必填） |
| `--username` | ZStack 用户名（必填） |
| `--password` | ZStack 密码（必填） |
| `--two-fa-code` | 双因子认证代码（可选） |
| `--test-vm` | 用于生命周期测试的 VM 名称 |
| `--verbose` | 详细输出模式 |

## 集成到 CAPEv2

### 1. 复制模块到 machinery 目录

```bash
# 在 Linux 服务器上
cp -r zstack/ /opt/CAPEv2/modules/machinery/
cp zstack.conf /opt/CAPEv2/conf/
```

### 2. 修改 CAPE 配置

编辑 `/opt/CAPEv2/conf/cuckoo.conf`：

```ini
[cuckoo]
machinery = zstack
```

### 3. 重启 CAPE 服务

```bash
systemctl restart cape
```

## CAPEv2 集成流程

### Machinery 调用流程

```
CAPE Core (startup.py)
  ↓ import_plugin("modules.machinery.zstack")
  ↓
MachineryManager.create_machinery()
  ↓ plugin() -> ZStack()
  ↓
MachineryManager.initialize_machinery()
  ↓ machinery.initialize()
  ↓   1. _initialize() - 读取配置
  ↓   2. _initialize_check() - 验证配置和连接
  ↓
分析任务开始
  ↓
MachineryManager.find_machine_to_service_task(task)
  ↓ 查找合适的 VM
  ↓
MachineryManager.start_machine(machine)
  ↓ machinery.start(label)
  ↓   1. 获取当前状态
  ↓   2. 如果运行中则先停止
  ↓   3. 恢复到快照
  ↓   4. 启动 VM
  ↓   5. 等待运行状态
  ↓
分析任务进行中
  ↓
MachineryManager.stop_machine(machine)
  ↓ machinery.stop(label)
  ↓   1. 发送停止命令
  ↓   2. 等待停止状态
  ↓
内存转储（如需要）
  ↓
AnalysisManager.dump_memory()
  ↓ machinery.dump_memory(label, path)
  ↓   1. 创建快照
  ↓   2. 下载内存卷
  ↓   3. 删除快照
```

## ZStack API 兼容性

本模块基于 ZStack Cloud V5.5.6 API 开发，已测试的 API 端点：

### 认证相关
- `PUT /zstack/v1/accounts/login` - 用户登录
- `DELETE /zstack/v1/accounts/sessions/{uuid}` - 退出登录

### VM 管理
- `GET /zstack/v1/vm-instances` - 获取 VM 列表
- `GET /zstack/v1/vm-instances/{uuid}` - 获取 VM 详情
- `PUT /zstack/v1/vm-instances/{uuid}/actions` - VM 操作
  - `startVmInstance` - 启动 VM
  - `stopVmInstance` - 停止 VM

### 快照管理
- `POST /zstack/v1/volume-snapshots` - 创建快照
- `GET /zstack/v1/volume-snapshots` - 查询快照
- `PUT /zstack/v1/volume-snapshots/{uuid}/actions` - 快照操作
  - `revertVmFromSnapshot` - 恢复 VM
- `DELETE /zstack/v1/volume-snapshots/{uuid}` - 删除快照

### 异步任务
- `GET /zstack/v1/api-jobs/{uuid}` - 查询任务状态

## 故障排查

### 常见问题

#### 1. 认证失败

```
CuckooCriticalError: ZStack authentication failed
```

**解决方案**：
- 检查 API URL 是否正确（包含端口）
- 验证用户名密码是否正确
- 如果启用了 2FA，确保填入了正确的 `two_fa_code`
- 确认 ZStack 服务正常运行

#### 2. VM 未找到

```
CuckooCriticalError: Machine <name> not found on ZStack host
```

**解决方案**：
- 检查 VM 名称是否与 ZStack 中一致
- 确认 VM 在正确的区域/集群中
- 使用测试脚本验证连接

#### 3. 快照恢复失败

```
CuckooMachineError: Failed to revert VM
```

**解决方案**：
- 确保快照名称配置正确
- 检查 VM 状态是否为停止状态
- 查看 ZStack 任务日志获取详细错误

#### 4. 双因子认证失败

```
CuckooMachineError: Invalid authentication credentials
```

**解决方案**：
- 确认 2FA 代码未过期（TOTP 代码 30 秒过期）
- 检查系统时间是否同步
- 验证 TOTP 密钥是否正确

### 日志位置

CAPEv2 日志：
```
/opt/CAPEv2/log/cuckoo.log
```

测试脚本日志：
```
控制台输出（使用 --verbose 获取详细信息）
```

### 启用详细日志

在 `zstack.conf` 中：

```ini
[zstack]
# 其他配置...

# 在测试时可以在代码中启用 debug 日志
# 修改 zstack.py 中的 log.setLevel(logging.DEBUG)
```

## 开发注意事项

### Windows 开发环境

在 Windows 上开发时注意：

1. 路径分隔符使用 `\\` 或 `pathlib`
2. 确保 Python 3.8+ 环境
3. 使用 poetry 管理依赖
4. 测试时使用独立测试脚本

### Linux 部署环境

部署到 Linux 时：

1. 确保文件权限正确
   ```bash
   chown -R cape:cape /opt/CAPEv2/modules/machinery/zstack/
   chmod -R 755 /opt/CAPEv2/modules/machinery/zstack/
   ```
2. 使用 cape 用户运行
3. 配置 systemd 服务

### 代码规范

遵循 CAPEv2 代码规范：

1. 所有函数必须有 docstring
2. 使用 logging 模块记录日志
3. 异常必须捕获并转换为 CuckooMachineError
4. 遵循 PEP 8 风格指南

## 与官方代码合并的注意事项

### 1. 文件位置

合并时需要将文件移动到：
```
modules/machinery/zstack.py  (主模块)
conf/zstack.conf             (配置文件)
```

### 2. 依赖检查

确保目标环境安装了 requests：
```bash
pip install requests
```

### 3. 配置迁移

将测试环境的配置迁移到生产环境时：
- 更新 API URL
- 更新认证信息
- 配置生产 VM 列表

### 4. 版本兼容

代码已考虑向后兼容，但建议：
- 在 ZStack 5.x 环境测试
- 检查 4.x API 差异
- 必要时添加版本检测

## 待实现功能

- [ ] 截图功能（需要 QEMU Guest Agent 支持）
- [ ] 内存转储完整实现（依赖 ZStack 存储配置）
- [ ] VM 池自动扩展
- [ ] 双因子认证自动获取（集成 TOTP）
- [ ] 支持 ZStack 高可用特性

## 参考资料

- [ZStack Cloud V5.5.6 开发手册](./PD3001%20ZStack%20Cloud%20V5.5.6%20开发手册.pdf)
- [ZStack API 参考文档](https://www.zstack.io/documentation/)
- [CAPEv2 文档](https://capev2.readthedocs.io/)
- [Cuckoo Sandbox 文档](http://www.cuckoosandbox.org/)

## 版本历史

### v0.2.0 (2026-03)
- 基于 ZStack Cloud V5.5.6 开发手册重新实现
- 添加双因子认证支持
- 更新 API 端点以适配 5.x 版本
- 完善错误处理和日志记录
- 添加独立测试脚本

### v0.1.0 (2026-03)
- 初始版本
- 基础 VM 生命周期管理
- 快照管理
- 独立测试框架

## 贡献者

- 基于原始实现重构
- 遵循 CAPEv2 和 ZStack 官方文档
