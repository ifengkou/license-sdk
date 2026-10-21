# License SDK 中文说明

通用离线软件授权 SDK，独立于业务工程维护，提供 Java 和 Python 实现。当前版本为 `0.4.0`，许可文件协议为 `schema_version = 1`；SDK 发行版本与文件协议版本分别管理。

当前已完成第四阶段的商业许可验签、安装身份观察、时间保护、只读预检及内部控制证明。产品已接入 HTTP 授权检查；后台任务准入、原任务续接及恢复应用仍待后续阶段完成，现阶段不能作为完整客户授权发行。

## 项目职责

- SDK：解析授权文件、验证 Ed25519 签名、检查产品与安装绑定、判断生效及到期状态。
- `license-issuer`：我方独立维护的密钥生成和授权签发工具，不属于 SDK，也不交付客户。
- 产品接入代码：提供可信公钥、本机安装身份和有效时间，将校验结果接入 HTTP 业务准入流程，后台执行边界将在后续阶段接入。

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

Java 暂用坐标为 `io.metor.license:license-core:0.4.0` 和 `io.metor.license:license-spring:0.4.0`。Python 分发名为 `metor-license-core`、`metor-license-fastapi`。正式发布组织及受控仓库地址尚待配置，当前未发布至远程制品仓库。

Java 核心仅依赖 JDK；Jackson/JUnit 仅用于测试。Python 核心依赖 cryptography 和 PyJWT，当前分别锁定为 `46.0.7`、`2.15.1`；Java 框架适配层显式依赖 Nimbus/Jackson，并在产品中与宿主依赖约束对齐。通用核心不依赖业务工程、Spring 或 FastAPI。

## 构建与检查

在 `java/` 目录执行，需要 JDK 21：

```sh
./gradlew check build
./gradlew :license-core:writeRuntimeClasspath
```

核心 JAR 位于 `license-core/build/libs/license-core-0.4.0.jar`。本地发布验证命令为：

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

Python wheel 输出到 `python/dist/`。正式消费者应安装固定版本制品，不通过相邻业务仓库或 issuer 源码导入 SDK。离线构建／安装需要提前准备目标平台所需的 Gradle 缓存及 Python 依赖 wheelhouse；锁文件本身不包含依赖二进制。

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

底层验签接口使用调用方提供的身份与时间，不读取运行目录。第三阶段新增的 `LicenseRuntime` 独立观察身份、当前许可及 HMAC 时间状态，结合 UTC 与单调时钟检测超过 300 秒的回拨；期限判断每次执行，不把容差作为到期宽限。运行时不自动生成缺失身份或时间种子。产品生命周期每 30 秒保存检查点，未来执行准入须先强制持久化。Python 的 `validate_commercial_payload` 仅验证未签名载荷结构，其结果不能作为签名有效的证据。

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

控制证明采用专用 HS256 JWS，密钥从安装主密钥按产品用途域派生，和用户 JWT 密钥分开。声明绑定安装、CONTROL 用途、操作范围、真实 HTTP 方法／路径、原始请求体摘要及随机编号；有效期最多 60 秒，校差 5 秒。严格解析拒绝重复字段、未知字段及非规范编码，不接受令牌指定的外部公钥或其他算法。未来任务证明不能替代控制证明。

Java 使用 `ControlProofCodec`，Python 使用显式 `ControlProofConfig` 和 `ControlProof`；产品配置固定 issuer/audience/domain，通用 SDK 不推断 ICP 专属网络地址。请求操作分类由产品负责，未知操作要求 ACTIVE，已有结果只读不能按 GET/POST 方法自动推断。

构建 Java 制品后，可在已安装核心 SDK 的 Python 环境执行以下独立校验：

```sh
cd java
./gradlew check build :license-spring:writeRuntimeClasspath
cd ..
python tools/verify_phase4_control.py
```

该工具用标准库独立生成合成 CONTROL 证明，验证双语言边界和相互签发兼容性，不包含商业签发能力。产品管理 API、双端协调及业务操作映射的具体说明见各产品 README。
