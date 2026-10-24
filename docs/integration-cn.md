# SDK 0.6.0 接入与升级

面向产品开发者。SDK 负责本地验签、机器绑定、时间保护和签名恢复；产品负责安装初始化、固定信任、公钥与清单打包、HTTP 鉴权及错误响应。Java 与 Python 各使用本服务的一个长生命周期 Runtime，不互相查询授权结果。

## 1. 引入固定制品

Java 21 依赖：

```groovy
repositories {
    maven { url = uri('/absolute/vendor/license-maven') }
}
dependencies {
    implementation 'io.metor.license:license-core:0.6.0'
}
```

`license-core` 仅依赖 JDK。普通请求拦截不需要 `license-spring`；后者提供独立 CONTROL 证明能力，只有需要该能力时才引入。构建者将 SDK 的 `java/build/maven-repository/` 复制到受控制品目录，核对 JAR/POM 及摘要。不要使用 `includeBuild` 或相邻 SDK 源码替代发行件。

Python 3.12 的 uv 消费者可将固定 wheel 放进产品自己的 wheelhouse：

```toml
[project]
dependencies = ["metor-license-core==0.6.0"]

[tool.uv.sources]
metor-license-core = { path = "wheelhouse/metor_license_core-0.6.0-py3-none-any.whl" }
```

这是并入现有 pyproject.toml 的片段。准备制品时生成并提交消费者锁文件，摘要必须匹配实际 wheel。FastAPI 依赖适配器另需 `metor-license-fastapi==0.6.0`，其元数据固定依赖同版本 core。

SDK wheel 不包含 cryptography、PyJWT 及其平台二进制依赖。制作方提前准备匹配目标 Linux 架构和 Python 3.12 的完整 wheelhouse，使用 `--no-index --find-links` 或 uv offline 验证全新环境安装。客户运行镜像必须已含全部依赖，启动时不运行 Maven、Gradle、pip 或 uv。

## 产品定义与 issuer.toml 一致

每个产品使用独立的一份 `issuer.toml` 启动我方 issuer：

```sh
license-issuer-web --config /absolute/vendor/other-product/issuer.toml
```

产品、服务、三个算法域和全部前缀都显式写在该文件中；ICP Diag 也使用完整配置，没有内置默认产品。不同实例使用各自端口及记录／输出目录。issuer 加载一次，Web 解锁后的“SDK 接入配置”页展示当前进程实际使用的公开参数、服务映射和公钥；修改 TOML 后必须重启。CLI 产品命令也只通过 `--config` 指定实例，不能混用旧 `--product`、密钥或记录目录参数；`keygen` 独立使用原参数。

**客户 SDK 不读取我方 issuer.toml、不连接 issuer，也不接受客户环境变量／上传文件覆盖身份参数。** 产品开发者把页面参数内置为代码常量或受控包内资源，通过现有构造接口传入 SDK；固定可信公钥和模式也由产品发行提供。SDK 生产 API 和发行版本仍为 0.6.0。

| issuer.toml／页面字段 | SDK 使用方式 | 变更影响 |
| --- | --- | --- |
| `product_id` | Python ProductProfile；Java Config.productId | 许可、安装身份、服务状态和挑战使用该产品标识 |
| `fingerprint_domain` | Python bytes；Java Config.fingerprintDomain | 改变由机器 ID 派生的指纹及机器绑定 |
| `binding_domain` | Python bytes；Java Config.bindingDomain | 改变 HOST_INSTALL 安装绑定值 |
| `recovery_domain` | Python bytes；Java Config.recoveryDomain | 改变恢复目标；这里的域是 ASCII 算法命名空间，无需 DNS 或联网 |
| `request_prefix` | Python ProductProfile；Java 管理入口的 ManualCodeCodec | 编码／解析商业申请码；Java Config 没有此参数 |
| `services[].id`、`recovery_prefix` | Python 完整 recovery_prefixes；Java 每服务 Config.service/recoveryPrefix | 服务隔离时间状态，前缀用于申请码编码和 CRC 校验 |
| `key_id`、公开 SPKI PEM | 两语言可信公钥映射 | 私钥仅在我方 issuer，客户包只含可信公钥 |

例如，另一个产品有 `gateway`、`worker` 两个服务，其完整身份参数可以固定为：

```python
from license_sdk import ProductProfile

PRODUCT = ProductProfile(
    product_id="other-product",
    fingerprint_domain=b"other-product/host-fingerprint/v1",
    binding_domain=b"other-product/host-install-code/v1",
    recovery_domain=b"other-product/recovery-target/v1",
    request_prefix="IC1",
    recovery_prefixes=(("gateway", "RC101"), ("worker", "RC102")),
)
```

Python Runtime 使用 `profile=PRODUCT` 并显式选择 `service="gateway"` 或 `"worker"`，继续使用下文的路径、可信公钥和 CUSTOMER_REQUIRED 模式。Java 每服务构造自己的 Config，例如 gateway：

```java
var config = new LicenseRuntime.Config(
    "other-product", "gateway",
    "other-product/host-fingerprint/v1",
    "other-product/host-install-code/v1",
    "other-product/recovery-target/v1",
    root.resolve("identity/installation.json"),
    root.resolve("identity/service.key"),
    root.resolve("shared/active.license.json"),
    root.resolve("state/gateway/clock.json"),
    machineId, "RC101");
```

worker 保留相同产品与域，使用 `"worker"`、`state/worker/clock.json` 和 `"RC102"`。管理入口编码商业申请时另用 `new ManualCodeCodec("IC1")`；不要把这个前缀添到不存在的 Config 参数中。上面是配置片段，`root`、`machineId`、可信公钥及 Runtime 生命周期由产品按下文装配，不是完整安装程序。

**SDK 固化交付后，不可随意更改 issuer.toml 的产品 ID、三个域、商业前缀、既有服务 ID 和恢复前缀。** 新增服务只追加新条目，原映射保持不变；改变 `[[services]]` 顺序不应重新编号。备份 TOML 与产品构建中使用的对应定义。显示名、端口和本机路径不参与协议身份，但搬迁记录／输出必须保留原资料。key ID／公钥更新属于客户信任迁移，必须同步客户发行，不能只改我方 TOML；页面警示不会自动检查或锁住已交付 SDK。

前缀不提供授权真实性，也不单独出现在签名恢复 payload；恢复文件由 Ed25519、产品／安装／服务、目标和窗口验证。错误前缀在申请解析时拒绝，错误恢复域造成目标不一致，二者不能相互替代。三个域、产品和服务也不能只在 issuer 改名后期望旧安装继续工作。

## 2. 安装器初始化目录

接入开发者提供的是受控产品定义、公钥信任以及客户授权目录／宿主机器 ID 路径；来源分别为 issuer 的 SDK 接入配置页、制作方固定发行信任和产品部署约定。下述文件由程序生成，不要求开发者或客户手工编写。该目录位于客户运行机器，与签发端 vendor 目录分开。

| 资料 | 生成方和时间 | 信息来源 |
| --- | --- | --- |
| `identity/installation.json` | 安装器首次安装时生成，完成后保留 | 产品 ID 为固化值；schema_version 为协议版本 1；安装 UUID 随机生成；initialized 由安装器完成持久化后设置 |
| `identity/service.key` | 安装器首次安装时生成，后续复用 | 系统安全随机源生成 32 字节安装主密钥，与我方商业签发私钥不同 |
| `state/<service>/clock.json` | 安装器生成初始状态；SDK checkpoint／签名恢复时更新 | 安装身份、产品／服务、主密钥与安装时 UTC；SDK 编码接口计算 HMAC |
| `shared/active.license.json` | Issuer 签发，客户通过产品管理接口导入 | 原始商业签名文件；初始化不生成，没有时为 UNLICENSED，管理能力仍保留 |
| 状态锁文件 | SDK 运行时按需生成 | SDK 本地状态并发控制 |
| `recovery-challenge.json` | 时间异常后取得恢复申请时由 SDK 自动生成／复用 | 当前服务的异常状态与 SDK 随机挑战 |

首次部署顺序：校正宿主 UTC → 执行安装初始化 → 启动服务 → 导出机器绑定申请（如需要）→ Issuer 签发 → 产品管理接口导入许可。各服务读取部署映射的真实宿主机器 ID，通常为 `/etc/machine-id`；该标识由操作系统提供，SDK 不生成替代值。

ICP 已有自动初始化命令。在目标 Linux、包含 deployment 目录的交付工作目录执行：

```bash
sudo python3 deployment/scripts/local-stack.py license-init \
  --license-root /opt/icp-diag/runtime/license
```

该命令仅依赖 Python 标准库，root 用于设置 ICP 的数值所有者／权限。部署的 `LICENSE_ROOT` 指向同一目录；首次返回 INITIALIZED，完整安装重复执行返回 ALREADY_INITIALIZED 并只校验。无需向命令提供 UUID、随机主密钥、clock 内容或商业文件。

**SDK 0.6.0 当前没有跨产品的通用初始化 CLI。** ICP 命令的产品、服务与 UID/GID 固定；其他产品需将自动初始化集成到自身安装包，让客户执行产品安装命令。Python 使用 `uuid.uuid4()`、`secrets.token_bytes(32)` 与 SDK `ClockState`／`encode_clock`；Java 使用 `UUID.randomUUID()`、`SecureRandom` 与 `ClockState`／`ClockStateCodec.encode`。安装器负责持久化、完成标记、幂等核对、并发／中断处理和权限，不能把创建状态移到每次 Runtime 启动或运行文件缺失的自动补造路径。

以下是 ICP Diag 的实际目录约定，`root` 是挂载到服务内的授权根目录：

```text
root/
  identity/installation.json          安装 UUID 与产品标识
  identity/service.key                32 字节安装主密钥，不能外发
  shared/active.license.json          当前商业许可，两服务可共用
  state/metor/clock.json              Metor 的 HMAC 时间记录
  state/metor/clock.json.lock         Metor 状态锁，SDK 创建
  state/metor/recovery-challenge.json Metor 的持久恢复挑战，按需创建
  state/ai/clock.json                 AI 的独立时间记录
  state/ai/clock.lock                 AI 状态锁，SDK 创建
  state/ai/recovery-challenge.json    AI 的持久恢复挑战，按需创建
```

安装器在首次安装时生成身份、主密钥和各服务时间种子。SDK 不自动修复缺失身份、不重建 clock，也不自动签发许可。ICP Diag 使用 deployment 的 `license-init`；它的产品和服务是 ICP 专用，不能直接给其他产品改一个 issuer 配置后复用。新产品安装器必须准备与自身 product_id 一致的身份、32 字节主密钥、每服务 HMAC clock seed、实际目录和权限，并与 Runtime 的产品／服务定义一致。使用 SDK 已有编码接口准备种子，不复制到业务代码另写一套 HMAC 或恢复算法。

身份、主密钥和商业许可通常只读挂载给服务；各自 state 目录必须可写。备份时保留安装身份、主密钥、商业许可和对应时间状态。不要用新 UUID、删 clock 或重新初始化来处理时间异常。每个 state 目录仅供一个服务进程和一个 Runtime 使用，不支持多副本共享该目录。

## 3. Java 构造 Runtime

下面使用 ICP Diag 的固定产品域。`trustedKeys` 从我方发行信任加载，不能取自客户上传文件或可覆盖的运行参数。路径应指向安装器准备的实际绝对路径。

```java
import io.metor.license.core.LicenseGuard;
import io.metor.license.core.LicenseRuntime;
import io.metor.license.core.TimeSource;
import java.nio.file.Path;
import java.security.PublicKey;
import java.util.Map;

public final class LicenseIntegration {
    public static LicenseRuntime create(
            Path root, Path machineId, Map<String, PublicKey> trustedKeys) {
        var config = new LicenseRuntime.Config(
            "icp-diag", "metor",
            "icp-diag/host-fingerprint/v1",
            "icp-diag/host-install-code/v1",
            "icp-diag/recovery-target/v1",
            root.resolve("identity/installation.json"),
            root.resolve("identity/service.key"),
            root.resolve("shared/active.license.json"),
            root.resolve("state/metor/clock.json"),
            machineId, "RC1J");
        return new LicenseRuntime(config, trustedKeys, TimeSource.system());
    }

    public static void protectedOperation(LicenseGuard guard, Runnable operation) {
        guard.requireValid();
        operation.run();
    }
}
```

`Config` 最后一个 `recoveryPrefix` 必须显式传入，ICP 示例的 Metor 为 RC1J。旧兼容构造器仍可验证商业许可，但不能取得恢复申请。SDK 中的 ICP 示例和 Python ICP_DIAG 常量只是已有产品定义；issuer 不依赖该常量。其他产品按前节将 issuer.toml／页面的全部身份参数固定到产品，不能只替换 productId。

应用启动时创建并注入这一个实例。生命周期定时器每 30 秒调用 `runtime.checkpoint()`，无请求时也执行，并记录返回状态及调度失败；关闭应用时停止定时器。请求只调用 `requireValid()`，不要把 checkpoint 放入每次请求。

`requireValid()` 仅在 ACTIVE 时通过，失败抛出 `LicenseDeniedException`，可读取 `state()`、`reasons()`。Java 核心没有开发模式开关；产品若提供开发发行模式，必须在受控构建元数据中固定，客户参数不能切换。

## 4. Python 构造与入口检查

```python
from pathlib import Path

from license_sdk import ICP_DIAG
from license_sdk.guard import LicenseGuard
from license_sdk.runtime import BuildMode, LicenseRuntime, RuntimePaths


def create_runtime(root: Path, machine_id: Path, trusted_keys: dict[str, bytes]) -> LicenseRuntime:
    return LicenseRuntime(
        profile=ICP_DIAG,
        service="ai",
        paths=RuntimePaths(root=root, machine_id_file=machine_id),
        trusted_keys=trusted_keys,
        mode=BuildMode.CUSTOMER_REQUIRED,
    )


def protected_operation(guard: LicenseGuard) -> str:
    guard.require_valid()
    return "继续原业务逻辑"
```

公钥映射为 key ID 到 SPKI PEM/DER 字节；路径必须为绝对路径。模式由构建固定，客户运行不使用 DEVELOPMENT。生命周期每 30 秒调用 `runtime.checkpoint()`；异步服务用 `asyncio.to_thread` 执行文件操作，避免阻塞事件循环。请求入口 `require_valid()` 失败抛出 `LicenseDeniedError`，其 `state`、`reasons` 来自同一次判断。

FastAPI 可使用显式依赖适配器：

```python
from fastapi import APIRouter, Depends
from license_sdk.guard import LicenseGuard
from license_sdk_fastapi import require_license


def protected_router(guard: LicenseGuard, authenticate) -> APIRouter:
    router = APIRouter()
    check_license = require_license(guard)

    @router.post("/run", dependencies=[Depends(authenticate), Depends(check_license)])
    def run():
        return {"accepted": True}

    return router
```

`authenticate` 是产品已有的账号鉴权依赖。适配器默认返回 403；如需原产品信封，传 `on_denied` 返回产品自己的异常。中间件与固定关键方法共用同一产品 guard，捕获授权拒绝后立即返回，不再次读状态，也不继续业务操作。

## 5. 请求清单与固定关键入口

SDK 不包含产品路径或自动注册业务过滤器。产品在原身份鉴权之后匹配启动时加载的受控 API；匹配才调用 guard，未匹配进入原请求链。对极少数必须保护的方法，在第一条业务语句前固定调用同一 guard，构造器必须要求该依赖。

ICP Diag 的产品 guard 还持有启动策略校验结果：构建对最终入包 JSON 原始字节固定 SHA256，启动校验非空清单、严格规则、资源唯一性及摘要，创建不可变规则。策略无效时先拒绝业务及固定关键入口，精确维护例外保留原鉴权。运行中不重载；文件被改后，下次启动拒绝，需恢复正确发行包再重启。外部 YAML、环境变量或清空清单不能关闭检查。

这部分属于产品接入代码，不是通用 SDK 的自动行为。当前固定入口只有 Metor 的 `TicketService.create`、AI 的 `InvocationService.invoke`；不增加后台任务、排队、租约、重试或业务实体中的授权状态。

## 6. 两个恢复管理入口

先校正宿主 UTC，再通过已有管理员鉴权调用同一个运行实例。控制器只负责鉴权、文件大小和结果映射，不创建临时 Runtime、不直接写 clock。

| 动作 | Java | Python |
| --- | --- | --- |
| 取得申请 | `runtime.getOrCreateRecoveryRequest()` | `runtime.get_or_create_recovery_request()` |
| 保存申请对象 | `request.toJson()` 返回 byte[] | `request.to_bytes()` 返回 bytes |
| 返回申请 JSON | `request.toMap()` | `request.to_dict()` |
| 应用恢复 | `runtime.applyRecovery(rawBytes)` | `runtime.apply_recovery(raw_bytes)` |
| 结果 | `actionStatus()`、`status()`、`recoveryId()` | `action_status`、`status`、`recovery_id` |
| 异常映射 | `RecoveryException.code()/httpStatus()` | `RecoveryError.code/http_status` |

申请序列化方法已返回字节，不要再 JSON 序列化这些字节。若原 API 使用成功信封，应把申请对象放进 data；交给 issuer 的文件只含 data 对象，不能是整份 HTTP 响应。申请只在时间异常时适用，重复取得、并发取得及重启复用仍适用的挑战，目标变化才重新申请。

恢复文件以原始字节交给 SDK，产品 API 限制至多 64KiB，不允许客户端覆盖恢复目标、时间或 epoch。签名文件最多七天窗口，按校正后的墙钟严格判断；一天回拨容差不扩展该窗口。

| 返回 | 产品处理 |
| --- | --- |
| APPLIED | 首次持久提交已确认，同一 Runtime 内存已刷新 |
| ALREADY_APPLIED | 此恢复编号已完成，不重复重置；窗口过后仍可确认 |
| OUTCOME_UNKNOWN | 返回待核对/202，保持受限，使用同一文件重试 |
| LICENSE_STORAGE_OUTCOME_UNKNOWN | 取得申请尚未确认，返回 202，重复取得，暂不交我方签发 |
| LICENSE_RECOVERY_TARGET_CHANGED | 409，取得当前申请，重新签发 |
| LICENSE_RECOVERY_NOT_REQUIRED | 409，当前没有适用的时间恢复原因 |
| LICENSE_RECOVERY_INVALID / LICENSE_RECOVERY_WINDOW_INVALID | 400，核对文件、目标服务、UTC 与签名窗口 |
| LICENSE_STORAGE_FAILED | 503，排查本服务 state 权限及存储后重试 |

恢复只改本服务时间状态，商业文件、机器绑定、期限和另一服务时间文件不变。成功后的商业状态仍可能 EXPIRED，需要另外续期。缺失/损坏身份或主密钥不属于自动恢复范围。

ICP Diag 的路由及操作步骤见 [issuer 恢复签发](../../license-issuer/docs/recovery-cn.md)和[客户恢复手册](../../deployment/docs/license-recovery.md)。其他产品可使用相同 SDK 方法定义自己的管理 API。

## 7. 从旧接入升级

| 旧做法 | 0.6.0 接入 |
| --- | --- |
| Java OperationPolicy、beforeExecution | `LicenseGuard.requireValid()`，删除操作枚举和任务上下文 |
| Python 操作分类/check | `LicenseGuard.require_valid()`，适配器显式传 guard |
| 每请求强制保存时钟 | 请求只观察，生命周期独立每 30 秒 checkpoint |
| 只更新业务源码、混用旧 JAR/wheel | SDK、适配器、产品依赖及锁文件统一 0.6.0，核对实际制品摘要 |
| 可被外部空配置覆盖的规则 | 产品包内清单 + 构建摘要 + 启动加载；固定关键方法仍直接调用 guard |
| 商业许可导入当作时间修复 | 正常续期与签名时间恢复分开，分别调用已有导入和恢复 API |

SDK 版本与许可 schema_version 独立，商业许可 v1 继续使用。保留旧 0.4.0 制品供回退核查，实验 0.5.0 不使用。回拨只有超过 86,400 秒才作为异常拒绝，高水位仍不下降；商业期限始终为精确的 `[not_before, expires_at)`，没有提前生效或延迟到期宽限。

完成接入后至少验证：有效/失效入口、未匹配放行、直接方法调用拒绝、清单篡改后重启拒绝、无请求时 checkpoint、同 Runtime 恢复及同文件重试。双语言实际签发验证见根 README 的验证命令。
