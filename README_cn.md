# License SDK 中文说明

通用离线软件授权 SDK，独立于业务工程维护，提供 Java 和 Python 实现。当前优化目标版本为 `0.6.0`（本地统一构建，未远程发布），许可文件协议为 `schema_version = 1`；SDK 发行版本与文件协议版本分别管理。

提供商业许可验签、安装身份观察、时间保护与预检。2026-10-22 起产品接入收敛为可配置的 HTTP 请求入口校验；各服务独立验证。后台任务准入、原任务续接及业务流程授权不在范围内；单服务签名时间恢复已实现，客户最终交付验收另行实施。

## 项目职责

- SDK：解析授权文件、验证 Ed25519 签名、检查产品与安装绑定、判断生效及到期状态。
- `license-issuer`：我方独立维护的密钥生成和授权签发工具，不属于 SDK，也不交付客户。
- 产品接入代码：提供可信公钥、本机安装身份和有效时间，在受控 HTTP 入口及少数固定关键方法首行检查本地校验结果；未匹配的请求直接放行，不深入业务流程。

SDK 会随客户运行制品交付，依赖包不能保证抵抗反编译或程序修改。商业私钥及签发逻辑只保留在我方签发环境。

## 已实现能力

- Java 21、Python 3.12 的统一编码协议及公开合成测试向量。
- 机器申请码和恢复码编解码、身份摘要推导；申请码本身不代表已获授权。
- 对原始载荷字节进行 Ed25519 验签，不重新序列化后验签。
- 严格、有界的 UTF-8 JSON 和无填充 Base64URL 解析，拒绝重复字段、未知字段、非法类型及非法日期等输入。
- 产品隔离、绑定一致性检查、精确的生效／到期边界判断。
- 不可变校验结果，包含载荷、载荷与文件 SHA-256 摘要及许可状态。

授权组合由两个独立字段组成：

| 绑定方式 | 期限方式 | 含义 |
| --- | --- | --- |
| `HOST_INSTALL` | `TERM` | 绑定指定安装，限时使用 |
| `HOST_INSTALL` | `PERPETUAL` | 绑定指定安装，无到期时间 |
| `UNBOUND` | `TERM` | 不绑定机器，限时使用 |
| `UNBOUND` | `PERPETUAL` | 不绑定机器，无到期时间 |

无限期或无绑定许可仍须经过签名、产品和生效时间校验，不等于关闭授权校验。

## 目录与制品

| 路径 | 内容 |
| --- | --- |
| [protocol/](protocol/README.md) | v1 编码协议、权威公开向量及兼容性清单 |
| [java/](java/README.md) | `license-core` 与 `license-spring`，Gradle 独立构建 |
| [python/](python/README.md) | `metor-license-core` 与 `metor-license-fastapi`，uv 独立构建 |
| [examples/](examples/README.md) | 最小宿主调用说明 |
| `tools/` | 编码及真实签发文件的跨语言验证工具 |

Java 暂用坐标为 `io.metor.license:license-core:0.6.0` 和 `io.metor.license:license-spring:0.6.0`。Python 分发名为 `metor-license-core`、`metor-license-fastapi`。正式发布组织及受控仓库地址尚待配置，当前未发布至远程制品仓库。

Java 核心仅依赖 JDK；Jackson/JUnit 仅用于测试。Python 核心依赖 cryptography 和 PyJWT，当前分别锁定为 `46.0.7`、`2.15.1`；Java 框架适配层显式依赖 Nimbus/Jackson，并在产品中与宿主依赖约束对齐。通用核心不依赖业务工程、Spring 或 FastAPI。

## 构建与检查

在 `java/` 目录执行，需要 JDK 21：

```sh
./gradlew check build
./gradlew :license-core:writeRuntimeClasspath
```

核心 JAR 位于 `license-core/build/libs/license-core-0.6.0.jar`。本地发布验证命令为：

```sh
./gradlew publishAllPublicationsToVerificationRepository
```

该命令只写入本项目的 `build/maven-repository`，不向远程仓库发布。

在 `python/` 目录执行，需要 Python 3.12 和 uv：

```sh
uv sync --locked --all-packages
uv lock --check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy src fastapi/src
uv run --locked pytest
uv build --all-packages
```

Python wheel 输出到 `python/dist/`。正式消费者应安装固定版本制品，不通过相邻业务仓库或 issuer 源码导入 SDK。我方制作离线构建／接入包时需提前准备目标平台所需的 Gradle 缓存及完整 Python 依赖 wheelhouse；锁文件本身不包含依赖二进制。客户正常部署加载预制镜像，Java SDK 随 BootJar、Python SDK 和 cryptography/PyJWT 等依赖随运行环境预装；客户启动不执行 Gradle/Maven/pip/uv 安装或远程拉取。单个 Python SDK wheel 不自带全部依赖，独立离线接入安装必须使用 no-index/offline 与目标平台完整 wheelhouse。

## 校验接口

Java 调用形式：

```java
var verifier = new CommercialLicenseVerifier(productId, trustedPublicKeys);
var result = verifier.verify(licenseBytes, localBindingId, effectiveNow);
```

类型位于 `io.metor.license.core`。`trustedPublicKeys` 为密钥 ID 到 `PublicKey` 的可信映射，`effectiveNow` 为 `Instant`。也可使用 `verifyWithBindingProvider(bytes, supplier, now)` 延迟取得本机绑定；无绑定许可不会调用该提供接口。非法文件抛出带有界错误码的 `LicenseException`。

Python 调用形式：

```python
from license_sdk import verify_license

result = verify_license(
    license_bytes,
    product_id=product_id,
    trusted_keys=trusted_keys,
    binding_id=local_binding_id,
    effective_now=effective_now,
)
```

`trusted_keys` 为密钥 ID 到 SPKI PEM／DER 公钥字节的可信映射；`effective_now` 必须为带时区的 `datetime`。非法文件抛出带 `reason` 类别的 `LicenseValidationError`。

两端返回 `ACTIVE`、`NOT_YET_VALID` 或 `EXPIRED` 状态：生效边界包含，到期边界不包含。验签成功不等于当前可使用，产品必须检查状态。本机绑定应根据真实宿主及安装身份重新计算，不能直接信任申请文件中的摘要。可信公钥应由产品受控装配。

底层验签接口使用调用方提供的身份与时间，不读取运行目录。第三阶段新增的 `LicenseRuntime` 独立观察身份、当前许可及 HMAC 时间状态，结合 UTC 与单调时钟检测超过 24 小时（86,400 秒）的回拨；期限判断每次执行，不把容差作为到期宽限。运行时不自动生成缺失身份或时间种子。产品生命周期每 30 秒调用 checkpoint 保存检查点；受控请求使用只观察的 requireValid / require_valid，不逐请求写时间文件。等于一天不因回拨本身拒绝，有效时间高水位仍不下降；商业许可仍严格按 [not_before, expires_at) 判断。Python 的 `validate_commercial_payload` 仅验证未签名载荷结构，其结果不能作为签名有效的证据。

### 产品请求入口

Java 使用 `LicenseGuard.requireValid()`；`LicenseRuntime` 实现这一接口，失败抛出 `LicenseDeniedException`，提供 `state()` 和不可变 `reasons()`。Python 使用 `LicenseGuard` Protocol 的 `require_valid()`，失败抛出 `LicenseDeniedError`，提供 `state` 字符串及 `reasons` 元组。产品用同一次拒绝结果映射错误，不再次查询状态。

Python DEVELOPMENT 模式或 ACTIVE 状态允许，其余状态均拒绝；Java 产品由固定构建模式处理 DEVELOPMENT，再委托 runtime。状态展示只有 NORMAL/RESTRICTED/DEVELOPMENT；不存在 SDK 只读放行策略，健康与管理例外由产品定义。

FastAPI 适配使用 `require_license(guard, on_denied=...)`，无操作枚举、无默认放行 guard。适配器在原账号鉴权之后执行。

## 框架接入与验证范围

`license-spring` 提供独立 CONTROL 证明能力；请求分类和过滤器由产品装配，不自动替代现有账号权限。`metor-license-fastapi` 提供需显式注入 guard 的薄适配层，没有默认放行的运行时。产品仍需保留自身账号鉴权和业务操作语义。

真实 CLI 签发及双语言回归工具为 `tools/verify_phase2_cross_language.py`。在包含两个项目的工作区根目录、完成 Java 构建及 issuer 依赖安装后执行：

```sh
license-issuer/.venv/bin/python license-sdk/tools/verify_phase2_cross_language.py
```

工具使用临时合成密钥，覆盖异常载荷、期限边界、四种许可组合、文件／手抄申请、重试及跨产品隔离。测试信任与签发入口不进入 SDK 运行制品。当前验证不能替代 Linux AMD64 整栈离线交付验收。

时间种子协议及正负向量见 `protocol/clock-state-v1-vectors.json`。独立核对命令为 `python tools/verify_phase3_cross_language.py`（在安装核心 SDK 的环境中，从本 SDK 根目录执行，先构建 Java 制品）。Java/Python 对标准库生成的种子进行解析与规范输出，并验证到期、回拨、损坏和重启。


## 第四阶段：候选预检、控制证明与 HTTP 接入

预检使用真实 UTC 判断候选文件，不发布当前许可、不写入时间记录。首次无当前许可、或当前时间状态异常时，仍能判断合法候选是否可导入；导入普通许可不会清除时间异常。Java 的 `inspect()` 与 Python 的 `status(read_only=True)` 提供不保存检查点的诊断观察。

控制证明采用专用 HS256 JWS，密钥从安装主密钥按产品用途域派生，和用户 JWT 密钥分开。声明绑定安装、CONTROL 用途、操作范围、真实 HTTP 方法／路径、原始请求体摘要及随机编号；有效期最多 60 秒，校差 5 秒。严格解析拒绝重复字段、未知字段及非规范编码，不接受令牌指定的外部公钥或其他算法。控制证明属于独立协议能力，当前产品请求授权不依赖远程服务调用，也不引入任务证明。

Java 使用 `ControlProofCodec`，Python 使用显式 `ControlProofConfig` 和 `ControlProof`；产品配置固定 issuer/audience/domain，通用 SDK 不推断 ICP 专属网络地址。当前产品通过方法和路径配置决定是否调用 SDK；命中规则必须通过正常校验，未匹配请求直接放行。

构建 Java 制品后，可在已安装核心 SDK 的 Python 环境执行以下独立校验：

```sh
cd java
./gradlew check build :license-spring:writeRuntimeClasspath
cd ..
python tools/verify_phase4_control.py
```

该工具用标准库独立生成合成 CONTROL 证明，验证双语言边界和相互签发兼容性，不包含商业签发能力。产品的独立管理 API 与请求配置说明见各产品 README。

## 简化接入边界

产品只配置需要保护的 HTTP 方法和路径。过滤器命中规则才调用本地 SDK；通过即继续，失败直接返回授权错误。未命中规则不检查授权，账号及数据权限保持原行为。Java 与 Python 各自验证自己的许可，不做联合预检或任务续接。固定关键方法首行可调用同一 guard；不改后台队列、重试、租约、业务实体或数据库迁移。Phase 6 统一使用 SDK `0.6.0` 与 issuer `0.3.0` 的固定本地制品，消费者锁定实际摘要；保留既有 `0.4.0`，不使用实验性的 `0.5.0`。


## Phase 6：单服务签名时间恢复

同一个 `LicenseRuntime` 提供 Java `getOrCreateRecoveryRequest()` / `applyRecovery(bytes)`、Python `get_or_create_recovery_request()` / `apply_recovery(bytes)`。产品只负责已有管理员权限或安装 ControlProof、收文件和返回结果，不直接写 clock。每服务独立操作，不调用另一服务；当前发行保持每个服务单进程、单 runtime、独立 state 目录。

先校正宿主 UTC，再取得申请。只允许恢复回拨或时间记录异常；普通过期、未来生效或缺少商业许可不是恢复理由。适用挑战持久化在 `state/<service>/recovery-challenge.json`，反复取得和进程重启复用同一 nonce、target 和 RC1J/RC1A 码；申请文件可出网时交文件，不能出网时抄产品、安装 UUID、服务和对应恢复码。IC1 机器申请码不能用于时间恢复。

我方 issuer 0.3.0 的 `sign-recovery` 签发独立 Ed25519 恢复文件；窗口最多七天，按已校正墙钟精确判断，不使用一天回拨容差。SDK 在 runtime 锁和本服务文件锁内验证目标、原子提交并刷新当前实例；商业许可原始字节、机器绑定及到期时间不变。恢复后返回当前商业状态，许可过期仍为 EXPIRED。

Python `RecoveryRequest.to_dict()/to_bytes()` 返回可保存的申请；`RecoveryResult` 提供 `action_status/status/recovery_id`。`RecoveryError` 提供 `code/http_status`。首次成功为 APPLIED；相同 recovery_id 再次应用为 ALREADY_APPLIED，不降低高水位、不增加 epoch，允许窗口过后确认。OUTCOME_UNKNOWN 表示文件可能已发布但尚未确认持久性，保持受限并用同文件重试；取得申请时对应的 `LICENSE_STORAGE_OUTCOME_UNKNOWN` 返回 202，重试确认后才返回可用申请。

公开合成恢复向量位于 `protocol/recovery-v1-vectors.json`，不进入运行制品信任。完成本地 SDK 与 issuer 构建后，从工作区根目录运行：

```sh
license-issuer/.venv/bin/python license-sdk/tools/verify_phase6_recovery.py
```

该工具真实调用 issuer CLI，以临时密钥和目录验证两服务、两语言、文件与手抄输入、三种异常、窗口/签名拒绝和幂等恢复。客户不接收 issuer、商业私钥或签发记录；没有 sign-policy 命令，包内 API 清单由产品构建摘要保护。
