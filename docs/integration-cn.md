# SDK 0.7.1：五步接入与升级

只需要本语言 SDK、从 Issuer 下载的一份公开配置，以及客户授权目录。SDK 自动初始化安装资料、导出申请、安全导入许可和维护 checkpoint；无需手工制作安装 JSON、随机密钥、clock 或文件发布脚本。

每个服务只创建一个 Runtime。下面以另一个产品的 `gateway` 服务为例；请使用你下载配置中的真实服务 ID。单服务可省略选择，多服务必须明确指定。Python 和 Java 可以独立使用，Java 用户只需 JDK。

## 第一步：准备依赖，下载并固化配置

SDK 从 [GitHub Releases](https://github.com/ifengkou/license-sdk/releases) 下载。Java CLI 选择核心 JAR，Maven／Gradle 构建选择完整 Maven 仓库包；Python 离线安装选择与系统、CPU 和 CPython 3.12 匹配的完整包。具体下载、摘要校验、虚拟环境安装和维护者发行流程见 [发行与离线安装](releasing.md)。
开发者在对应产品的 Issuer 实例解锁，进入 **SDK 接入配置**，下载 `license-product.json`。它包含产品 ID、服务映射、三个算法域、申请前缀和 key_id → 公开 Ed25519 公钥，无需逐字段复制。

这是**产品信任资源**：随产品构建固化，不是客户可上传的运行配置。应用从自己的包资源读取，不允许业务 HTTP、环境变量或客户可写授权目录覆盖信任。Issuer 私钥、口令及记录留在签发端，不能加入客户产品。

### Python 3.12

在产品虚拟环境中安装已交付的制品：

```bash
python -m pip install --no-index --find-links ./wheelhouse metor-license-core==0.7.1
```

`wheelhouse` 必须是发行方准备的目标平台完整依赖目录，包含 core、cryptography、PyJWT 及传递依赖；单个 SDK wheel 不包含这些依赖。FastAPI 示例另需产品已安装 FastAPI，显式依赖适配器可用 `metor-license-fastapi==0.7.1`。日常启动不运行 pip/uv，不连接 Issuer 或制品仓库。

把同一份配置复制到你的 Python 包，例如 `my_product/license-product.json`，并在构建规则中包含该 JSON。CLI 安装操作先使用工作目录中的原始下载文件。

### Java 21

将已交付的 `license-core-0.7.1.jar` 放在工作目录，就可直接执行后面的 CLI。核心只依赖 JDK，不需要 Spring、Python 或其他产品源码。应用构建可使用受控制品仓库的依赖：

```groovy
dependencies {
    implementation 'io.metor.license:license-core:0.7.1'
}
```

从 Release 下载 `license-sdk-java-maven-0.7.1.tar.gz`，校验并解压到产品受控的本地 Maven 仓库，将该目录配置为 repositories 中的 Maven URL，再使用上面的坐标；完整命令见 [发行文档](releasing.md#java使用-mavengradle-坐标)。无需手写 POM，也不依赖 Maven Local 或 SDK 源码。SDK 不发布到 Maven Central／PyPI。将公开配置放在 `src/main/resources/license-product.json`，确保构建后位于 classpath 根目录。`license-spring` 仅在需要独立 CONTROL 证明能力时引入，普通 guard 不依赖它。

**本步预期：** SDK 可用，工作目录有 `license-product.json`，产品构建内也包含同一份公开资源。

## 第二步：在客户首次安装时初始化

执行位置是客户安装／隔离测试目录。先校正宿主 UTC，以下普通快速开始使用当前账号和 `./license-data`，无需 sudo。

Python：

```bash
license-sdk init --config license-product.json --root ./license-data
license-sdk status --config license-product.json --root ./license-data --service gateway
```

Java：

```bash
java -jar license-core-0.7.1.jar init --config license-product.json --root ./license-data
java -jar license-core-0.7.1.jar status --config license-product.json --root ./license-data --service gateway
```

首次返回 `INITIALIZED`，查询得到 `UNLICENSED`，说明资料已准备好、尚无商业许可。SDK 自动初始化**全部配置服务**；重复 init 返回 `ALREADY_INITIALIZED`，核对原资料，不更新身份、密钥或时间高水位。

完成安装后缺失／损坏身份、主密钥或任一服务 clock 会拒绝并保留现场，不自动补造。初始化不承担已有安装新增服务的增量迁移。不要删除授权目录重新 init 处理时间异常或失效许可。

机器绑定使用真实 Linux `/etc/machine-id`，由宿主操作系统提供。容器要挂载真实宿主文件；CLI 可加 `--machine-id /run/your-host/machine-id`。macOS 隔离测试也须显式提供受控测试标识。SDK 不生成替代 machine-id。无绑定许可的核验不会强制读取它。

## 第三步：从产品资源创建同一个 Runtime

授权目录和服务 ID 是部署选择，公开信任来自产品资源。工厂返回原 `LicenseRuntime`，旧状态、guard、preflight 和恢复 API 继续使用。

### Python

```python
from importlib.resources import files
from pathlib import Path
from license_sdk.runtime import LicenseRuntime


def create_runtime(root: Path) -> LicenseRuntime:
    raw = files("my_product").joinpath("license-product.json").read_bytes()
    return LicenseRuntime.from_config(raw, root=root, service="gateway")
```

`my_product` 换成你的实际包名。需要真实宿主挂载时传 `machine_id_file=Path("/run/your-host/machine-id")`，不能从业务请求接受路径。

在现有 FastAPI lifespan 中启动和关闭：

```python
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI


def application(runtime):
    @asynccontextmanager
    async def lifespan(app):
        runtime.start()
        try:
            yield
        finally:
            await asyncio.to_thread(runtime.close)

    return FastAPI(lifespan=lifespan)
```

产品启动装配使用 `runtime = create_runtime(Path("./license-data"))`，将它传给 `application` 和已有业务／管理组件。非 Web 应用在启动调用 `start()`，在 finally 调用 `close()`。

### Java

```java
import io.metor.license.core.LicenseRuntime;
import java.io.IOException;
import java.nio.file.Path;

public final class ProductLicense {
    public static LicenseRuntime create(Path root) throws IOException {
        try (var input = ProductLicense.class.getResourceAsStream("/license-product.json")) {
            if (input == null) throw new IOException("missing product license config");
            return LicenseRuntime.fromConfig(input.readAllBytes(), root, "gateway");
        }
    }
}
```

需要宿主挂载时使用第四参数 `Path.of("/run/your-host/machine-id")`。现有产品生命周期在启动调用 `ProductLicense.create(root).start()`，注入同一个实例，在关闭钩子调用 `close()`。最小命令程序可用 try-with-resources：

```java
try (var runtime = ProductLicense.create(Path.of("./license-data")).start()) {
    System.out.println(runtime.inspect().state());
}
```

SDK 内部每 30 秒 checkpoint，无请求也运行；重复 start 不增加任务，close 停止并等待退出。旧产品若自行调度 checkpoint，选择保留旧调度或换成 SDK start，不能同时启用。状态查询与 guard 不逐请求强制 checkpoint；恢复挑战仍由 Runtime 按需维护。

**本步预期：** 应用能查询 UNLICENSED，正常关闭，没有自建定时循环。

## 第四步：申请 → Issuer 签发 → SDK 安全导入

在同一个客户安装目录执行。若应用选择了受控自定义机器标识路径，request／import 也要使用同一个 `--machine-id`。

Python：

```bash
license-sdk request --config license-product.json --root ./license-data --service gateway --output request.json
# 将 request.json 交给签发人员，在 Issuer 商业签发页上传、选择期限并下载。
license-sdk import --config license-product.json --root ./license-data --service gateway --file customer.license.json
license-sdk status --config license-product.json --root ./license-data --service gateway
```

Java：

```bash
java -jar license-core-0.7.1.jar request --config license-product.json --root ./license-data --service gateway --output request.json
# 把签发下载文件保存为当前目录的 customer.license.json。
java -jar license-core-0.7.1.jar import --config license-product.json --root ./license-data --service gateway --file customer.license.json
java -jar license-core-0.7.1.jar status --config license-product.json --root ./license-data --service gateway
```

申请由真实安装和机器标识生成，不含机器 ID 原文／安装主密钥。选择无绑定商业许可可跳过申请，直接让签发人员选择 UNBOUND。`TERM/PERPETUAL × HOST_INSTALL/UNBOUND` 四种组合均保留。

**预期：** 导入 APPLIED，查询 ACTIVE；第三步已运行实例的下一次状态查询和 guard 也能观察新许可。SDK 会完成验签、产品／绑定／期限预检、原始文件摘要比较和原子发布，不能自行用 `write_bytes`／复制替换活动文件。

错误文件不会替换旧许可。OUTCOME_UNKNOWN 表示可能已发布但持久性待确认：保留现场、核对原始摘要并重试同一文件，不换身份或删除状态。同字节重试可确认已发布数据，返回真实当前状态，不延长许可。

## 第五步：账号鉴权后、关键业务副作用前调用 guard

Python：

```python
from license_sdk.guard import LicenseGuard


def critical_operation(license: LicenseGuard, operation):
    license.require_valid()
    return operation()
```

Java：

```java
public void execute(Runnable business) {
    runtime.requireValid();
    business.run();
}
```

将第三步同一 Runtime 注入关键服务；固定方法首行核验，直接调用和其他入口也经过该方法。账号权限检查仍由产品完成。缺许可／到期时必须在写数据、发消息或调用外部系统之前拒绝，不能捕获后继续执行。

Python `LicenseDeniedError.state/reasons`，Java `LicenseDeniedException.state()/reasons()` 来自本次判断；HTTP 通常映射 403，不再二次查询覆盖原因。状态／申请／导入／恢复维护入口保留原管理员认证，不能被业务 guard 拦住。

## 按需加入管理接口

首次接入可完全使用 CLI；有产品管理页面时，控制器调用下面原 Runtime 方法，不再自行实现文件事务。

| 动作 | Python | Java |
| --- | --- | --- |
| 只读状态 | `status(read_only=True)` | `inspect()` |
| 读取活动文件原始摘要，缺失为 null | `license_file_digest()` | `licenseFileDigest()` |
| 商业申请对象 | `commercial_request()` | `commercialRequest()` |
| 候选预检，不发布 | `preflight(raw)` | `preflight(raw)` |
| 安全导入 | `import_license(raw, expected_file_digest)` | `importLicense(raw, expectedRawDigest)` |
| 取得时间恢复申请 | `get_or_create_recovery_request()` | `getOrCreateRecoveryRequest()` |
| 应用原始恢复文件 | `apply_recovery(raw)` | `applyRecovery(raw)` |

状态接口应返回原始文件摘要。导入 API 先让客户端查询，再随上传提交所看到的旧摘要；不存在用 None/null，HTTP 的 NONE 哨兵在产品控制器转换。摘要冲突返回 409，不能自动改成最新摘要后覆盖他人的更新。CLI 自动读取摘要并在事务锁内比较。

控制器仍需真实登录／管理员权限；Cookie 登录校验 CSRF，所有管理响应 `Cache-Control: no-store`。原始商业／恢复文件限 1–65536 字节，流式读取时限制，不把任意长度请求完整读进内存后才判断。未知异常返回有界存储错误，不泄漏堆栈或秘密。

导入结果包含 `action_status/file_digest/status`（Java 对应 camelCase accessors）。APPLIED／ALREADY_APPLIED 表示持久性已确认；OUTCOME_UNKNOWN 映射 202，状态可能暂不可读，不能误报“旧文件未变”。Python `OnboardingError.code`／Java `OnboardingException.code()` 的状态冲突映射 409、候选错误映射 400、存储失败映射 503。完整可复制路由示例见 Issuer 解锁后的 `/docs` 对应语言页签。

## 时间异常的处理

1. 先校正宿主 UTC，在同一个 Runtime 取得恢复申请；没有适用异常时不会生成恢复文件。
2. Python 申请用 `to_dict()/to_bytes()`，Java 用 `toMap()/toJson()` 导出；交给 Issuer 的是申请对象，不是产品 API 整份成功信封。
3. Issuer 签发后，原始文件交同一个 Runtime 应用。首次 APPLIED，重复 ALREADY_APPLIED，OUTCOME_UNKNOWN 保留文件重试。

SDK 自动维护稳定挑战、目标、原子提交和实例刷新。恢复只改变本服务 clock，不修改商业文件、另一服务或期限；商业许可已过期时恢复后仍 EXPIRED。窗口最长七天，按校正后的 UTC 严格判断；一天回拨容差不扩展商业期限或恢复窗口。恢复异常提供 Python `code/http_status` 或 Java `code()/httpStatus()`，按分类映射；目标改变则重新取得申请。

## 安装资料与高级部署

| 资料 | 来源和生成时机 |
| --- | --- |
| SDK 和公开配置 | 制作方提供固定制品，开发者下载配置并纳入产品资源 |
| root、真实机器标识路径、运行账号／权限 | 产品部署约定，不从 HTTP 传入 |
| `identity/installation.json` | SDK init 生成产品标识、随机 UUID 和完成标记 |
| `identity/service.key` | SDK init 从安全随机源生成 32 字节安装主密钥；不能外发，不能签商业许可 |
| `state/<service>/clock.json` | SDK init 生成所有服务初值，SDK checkpoint／恢复维护 |
| `shared/active.license.json` | Issuer 签发，由 SDK import 发布；init 不生成 |
| 本地锁和 `recovery-challenge.json` | SDK 运行时按需生成 |

本机 POSIX（Linux／macOS）且无符号链接路径；普通目录由当前账号持有，默认 0700／0600。每服务一个进程、一个 Runtime，独立 state。服务可共享产品身份和商业文件；新增安装／导入锁双语言互通不代表可以双进程维护同一服务 clock。Windows、网络盘和多副本状态共享不在本版支持范围。

多账号容器由部署层准备 UID/GID、组权限及挂载，SDK 不递归 chown。不要直接把普通单用户初始化权限套到多 UID 容器。ICP Diag 的 Metor／AI 生产装配须另行迁移验收，SDK 升级不会自动修改产品或既有部署。

### 两个服务账号共享商业许可

默认 import 以 0600 发布。读写服务使用不同 UID 时，部署层准备共享 GID、目录可访问性及补充组；仅发布者可写 shared，读者只读。导入时显式传共享 GID（非负十进制数，最大 2147483647）：

```bash
license-sdk import --config license-product.json --root ./license-data --service gateway --file customer.license.json --shared-group 21000
java -jar license-core-0.7.1.jar import --config license-product.json --root ./license-data --service gateway --file customer.license.json --shared-group 21000
```

API 对应 Python `runtime.import_license(raw, expected, shared_group=21000)`，Java `runtime.importLicense(raw, expected, "21000")`。GID 是部署选择，不加入 Issuer 公开配置或签名内容。

SDK 在原导入锁内完成临时文件写入、设置并核验目标组／0640、同步和原子替换。组或权限设置失败不替换旧文件；替换后持久性未确认返回 OUTCOME_UNKNOWN。同字节重试也确认权限，需要时在锁内原子重发相同字节，成功返回 ALREADY_APPLIED；许可到期仍返回真实 EXPIRED。所有写入口使用同一共享参数，不能在调用返回后再 chmod，也不能用默认私有模式覆盖共享许可。身份、主密钥、clock 和恢复文件不受该参数影响。

### 备份／升级前只读核验安装资料

先停止写入或取得一致快照，再执行：

```bash
license-sdk check --config license-product.json --root ./license-data
java -jar license-core-0.7.1.jar check --config license-product.json --root ./license-data
```

成功返回 `installationId`。API 对应 Python `validate_installation(product_config, root)`（来自 `license_sdk.installation`），Java `LicenseInstallation.validate(productConfig, root)`；Java 也接受配置字节。

check 核验完成身份、32 字节主密钥和全部已配置服务的 clock／HMAC，不需要选择服务、读取 machine-id 或判断当前商业许可。缺少或到期的商业许可不妨碍完整安装备份；坏 clock 仍拒绝。它不创建 Runtime、锁或目录，不初始化、修复、checkpoint 或恢复，可对只读挂载使用。它也不为活动目录创建一致快照；归档白名单、UID/GID、同宿主恢复和停止写入由产品负责。

备份和恢复保留身份、主密钥、商业文件及对应时间记录；已完成安装不能重新生成 UUID／key／clock。当前 init 不为已安装产品增补新服务，须另行受控迁移。

## 从 SDK 0.6.0 升级

旧构造器、guard、状态和恢复 API 保留。SDK 版本与商业／clock／恢复 schema 独立，原 v1 许可与状态字节可继续验证，无需因升级重签。0.6.0 原发行件保留，不覆盖重发。

- 新接入优先使用配置工厂、init、request／import 和 start／close；无需旧版手动安装器和发布事务。
- 已有产品可先保持原接入。采用新工厂时把**完全相同**的产品／服务／域／前缀／可信公钥固化到新资源，保留原授权目录。
- 采用 SDK start／close 时移除原 checkpoint 调度；管理接口 URL、账号权限、业务 guard 和产品清单策略可保持。
- 产品 ID、三个域、既有服务和前缀在交付后不可随意重定义。密钥轮换要协调 SDK 信任；不能只改我方 issuer.toml。显式字段仍在完整 TOML，未引入插值或默认推导。

## 验收

陌生产品不改 SDK 源码、不引用 ICP 工程即可按五步完成。至少核对：未授权业务拒绝且无副作用、管理员管理可达；签发导入后同实例 ACTIVE；错误许可不覆盖旧文件；无请求也 checkpoint、关闭正常；同实例恢复／重复应用及另一服务不变。离线安装必须从完整平台依赖和实际发行件测试，不能用 PYTHONPATH 或相邻源码冒充安装。
