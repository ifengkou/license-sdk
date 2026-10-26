# SDK 发行与下载

SDK 统一从 [GitHub Releases](https://github.com/ifengkou/license-sdk/releases) 发行。维护者推送 `v版本号` tag，GitHub Actions 测试并构建 Java／Python 制品，准备平台离线依赖包，生成摘要，最后发布 Release。普通分支、PR 和手动运行只验证、上传 Actions artifacts。

这里发行的是通用 SDK。`license-product.json` 由各产品自己的 Issuer 导出，随产品固化；它不属于公共 SDK 下载包。Issuer 私钥、口令、客户安装资料和签发记录不能进入 SDK 发行件。

## 下载哪个文件

以 `v0.7.1` 为例，工作流成功后 Release 包含以下文件。若对应版本尚未完成发行，下载地址将不可用，请以该 tag 的 Releases 页面为准。

| 使用者 | 下载文件 | 用途 |
| --- | --- | --- |
| Java 产品／CLI | `license-core-0.7.1.jar` | JDK 21 可直接运行；核心没有额外 Java 运行依赖 |
| Java CONTROL 集成 | `license-spring-0.7.1.jar` | 可选；还需 core 和其 Nimbus／Jackson 依赖，见 Java 工程构建文件 |
| Java 开发者 | 两模块的 `-sources.jar`、`-javadoc.jar` | 源码与 API 文档 |
| Maven／Gradle 产品构建 | `license-sdk-java-maven-0.7.1.tar.gz` | 两模块完整本地 Maven 仓库，含 POM、Gradle 元数据、上述 JAR 及内部摘要 |
| Python 已有依赖环境 | `metor_license_core-0.7.1-py3-none-any.whl` | 安装核心，单个 wheel 不包含传递依赖 |
| Python FastAPI 集成 | `metor_license_fastapi-0.7.1-py3-none-any.whl` | 可选适配器，需要同版本 core 与 FastAPI |
| Python 源码发行 | 两模块的 `.tar.gz` | sdist，供开发者构建；客户离线安装优先用下述完整包 |
| Linux x64／glibc | `license-sdk-python-0.7.1-cp312-linux-x86_64.tar.gz` | CPython 3.12 完整 wheelhouse |
| Linux arm64／glibc | `license-sdk-python-0.7.1-cp312-linux-aarch64.tar.gz` | CPython 3.12 完整 wheelhouse |
| macOS Apple Silicon | `license-sdk-python-0.7.1-cp312-macos-aarch64.tar.gz` | CPython 3.12 完整 wheelhouse |
| 所有人 | `SHA256SUMS` | 核对下载文件完整性 |

Python SDK wheel 自身通用，cryptography、cffi、pydantic-core 等依赖带平台／Python ABI 限制。离线包不能跨系统、CPU 或 Python 小版本混用。当前 CI 使用 Ubuntu 22.04 x64／arm64 和 macOS 14 arm64 原生构建并安装验证；glibc 兼容范围还受包内第三方 wheel 标签限制。Alpine／musl、Windows、macOS Intel 和其他 Python 小版本不在当前离线包矩阵中。

离线包包含 core、adapter、全部运行依赖 wheel、带版本与哈希的 `requirements-core.txt`／`requirements-fastapi.txt`、`BUILD-INFO.json`、内部 `SHA256SUMS`、接入指南和 Python 示例。core 安装仅选择核心依赖；FastAPI 安装再选择适配器依赖。Python／JDK 本身由产品运行环境预装，下载包不附带解释器。

## Java：使用 Maven／Gradle 坐标

制作方下载仓库包、校验后解压。它只包含 SDK 发布物，不包含产品其他依赖或 Gradle／JDK；客户运行已构建的产品，不执行下面的构建步骤。

```bash
curl -fLO https://github.com/ifengkou/license-sdk/releases/download/v0.7.1/license-sdk-java-maven-0.7.1.tar.gz
curl -fLO https://github.com/ifengkou/license-sdk/releases/download/v0.7.1/SHA256SUMS
grep '  license-sdk-java-maven-0.7.1.tar.gz$' SHA256SUMS | sha256sum -c -
tar -xzf license-sdk-java-maven-0.7.1.tar.gz
(cd license-sdk-java-maven-0.7.1 && sha256sum -c SHA256SUMS)
```

将解压目录放到产品的受控构建输入，例如 `vendor/license-maven`，保留现有依赖坐标：

```groovy
repositories {
    maven { url = uri('vendor/license-maven') }
    // 产品使用其他依赖时，仍按原有构建规则准备相应仓库／离线缓存。
}
dependencies {
    implementation 'io.metor.license:license-core:0.7.1'
}
```

SDK 不发布到 Maven Central，GitHub Releases 页面也不是 Maven 仓库地址。无需手写 POM 或改成文件依赖。可选的 `license-spring` 坐标会精确引用同版 core；其 Nimbus／Jackson 等第三方依赖仍须在产品制作期准备。离线构建前还须缓存 Gradle 分发包和产品插件。

## Java：独立 JAR／CLI

在开发机或发行制作机下载，校验后纳入产品构建；客户日常启动不下载依赖。

```bash
curl -fLO https://github.com/ifengkou/license-sdk/releases/download/v0.7.1/license-core-0.7.1.jar
curl -fLO https://github.com/ifengkou/license-sdk/releases/download/v0.7.1/SHA256SUMS
# Linux：只校验当前下载文件；macOS 用 shasum -a 256 -c。
grep '  license-core-0.7.1.jar$' SHA256SUMS | sha256sum -c -
java -jar license-core-0.7.1.jar --help
```

普通 Java 产品可把 JAR 放进 `libs/`，Gradle 使用：

```groovy
dependencies {
    implementation files('libs/license-core-0.7.1.jar')
}
```

需要独立 CLI 或简单 classpath 接入时可使用单个 JAR。采用 Maven／Gradle 坐标的产品优先使用上面的完整仓库包；`license-spring` 不是包含全部依赖的 fat JAR，核心 CLI 不需要它。

下一步从对应 Issuer 下载公开配置，按 [接入与交付指南](integration-cn.md) 先完成 Runtime、状态和业务 guard 集成，再构建交付，在目标安装执行 init，最后申请、签发并导入许可。不要下载别的产品的公钥配置。

## Python：完整依赖离线安装

示例目标为 Linux x64、CPython 3.12。在能联网的发行制作机下载对应包及摘要，校验后拷贝到目标环境；客户环境无需联网。

```bash
curl -fLO https://github.com/ifengkou/license-sdk/releases/download/v0.7.1/license-sdk-python-0.7.1-cp312-linux-x86_64.tar.gz
curl -fLO https://github.com/ifengkou/license-sdk/releases/download/v0.7.1/SHA256SUMS
grep '  license-sdk-python-0.7.1-cp312-linux-x86_64.tar.gz$' SHA256SUMS | sha256sum -c -
tar -xzf license-sdk-python-0.7.1-cp312-linux-x86_64.tar.gz
cd license-sdk-python-0.7.1-cp312-linux-x86_64
sha256sum -c SHA256SUMS

# 系统已安装 CPython 3.12 的 venv／pip 支持；普通账号执行，无需 sudo。
python3.12 -m venv .venv
.venv/bin/python -m pip install --no-index --find-links=wheelhouse \
  --require-hashes -r requirements-core.txt
.venv/bin/python -m pip check
.venv/bin/license-sdk --help
```

如果产品已经有虚拟环境，用该虚拟环境的 Python 执行上述 `pip install`，无须新建 `.venv`。FastAPI 产品将 `requirements-core.txt` 换成 `requirements-fastapi.txt`。已有产品依赖与发行锁冲突时，在产品构建期解决并验证，不能用 `--no-deps` 隐藏冲突。macOS 校验命令为 `shasum -a 256 -c`。

安装完成后按 [Python 接入说明](../python/README.md) 使用。发行制作方可以把此环境预装到镜像或完整运行包中；客户每次启动只运行产品，不再次执行 pip／uv。离线依赖包中不含客户产品本身的其他业务依赖。

## 维护者：发行一个版本

工作流为 [release.yml](../.github/workflows/release.yml)，辅助脚本为 [release.py](../tools/release.py)。需要仓库已开启 Actions，允许工作流使用 `GITHUB_TOKEN` 创建 Releases；不需要 PyPI token、Maven 密钥或商业签发私钥。只有发行 job 有 `contents: write`，PR／构建 job 为只读。

1. **更新版本并提交。** 同时修改 Python core／adapter 版本、adapter 精确 core 依赖、Java 子工程版本和 `protocol/compatibility.json` 的 `sdk_version`，同步接入文档、工具默认 JAR 路径及 Python lock：

   ```bash
   cd python
   uv lock
   cd ..
   python3.12 tools/release.py check-version
   ```

   商业／clock／恢复 schema 不随 SDK 发行号自动升级。版本检查会拒绝坐标不一致或错误 tag；当前正式发行 tag 只接受 `vMAJOR.MINOR.PATCH`。

2. **推送代码，等待 CI 通过。** PR／分支运行 Java 全量测试、独立 Maven 消费者验证、Python 三平台测试、静态检查、构建及全新 venv 的无网络安装检查。Linux 额外以不同 UID／共享 GID 验证两语言发布、rename 前后中断和真实只读挂载 check。Actions 页可下载试制 artifacts；`workflow_dispatch` 也只试构建，不发布。公共 SDK CI 不依赖相邻私有 Issuer 或业务仓库。

3. **在已确认的提交创建并推送 tag。** 下列操作会实际触发发行，只在全部验证通过、准备完整后执行：

   ```bash
   git status --short                 # 确认没有遗漏的未提交发行内容
   git tag -a v0.7.1 -m 'License SDK 0.7.1'
   git push origin v0.7.1
   ```

4. **检查自动发行结果。** tag 必须等于源码版本。所有平台及 Linux 权限验证成功后，CI 收集 14 个制品并生成 `SHA256SUMS`，先建带齐附件的 draft，再自动公开到 [Releases](https://github.com/ifengkou/license-sdk/releases)。检查 tag 对应提交、Java 6 个 JAR、1 个 Maven 仓库包、Python 4 个原始发行件、3 个离线包和摘要。平台缺件、重复文件不同字节、Maven 包内部摘要／坐标或独立 JAR 不一致会阻止发布。

5. **试装后交付产品。** 在目标平台按上述离线命令验证，再把固定版本 SDK、完整运行依赖及该产品自己的公开配置纳入镜像／安装包。Release 的构建信息记录源提交、工作区是否有未提交修改、Python、平台及 uv.lock 摘要；下载包摘要用于完整性校验。

版本一经正式发行就不覆盖。修复代码或更新依赖时升补丁版本、更新 lock，重新测试并推送新 tag；不能在同一版本的未来重跑中悄悄替换第三方依赖。CI 不使用 `--clobber`，已有 Release 会让创建操作失败。

若构建 job 失败，修复后在尚未发行的版本上重新走发行准备。若只在上传／公开时失败，可能留下 draft；核对同一 workflow run 的 artifacts 和摘要后，维护者在 GitHub 完成该 draft 或删除失败 draft 再重试。不要删除已公开版本或移动它的 tag。GitHub 已启用 immutable releases 时，先 draft、附件齐全后公开的流程也适用。

## 本地预构建与增加平台

制作机需要 Python 3.12、uv、pip、JDK 21，下载依赖时可联网。以下仅构建本机平台离线包，不会向 GitHub 发布：

```bash
# SDK 仓库根目录；使用全新的输出目录，不能复用已发行包目录。
python3.12 tools/release.py check-version
uv build --project python --all-packages --out-dir ./dist/python
python3.12 tools/release.py wheelhouse --dist ./dist/python --output ./dist/bundles
cd java
./gradlew check build publishAllPublicationsToVerificationRepository
cd ..
python3.12 tools/release.py maven --repository java/build/maven-repository --dist java --output dist/java
python3.12 -m unittest discover -s tools -p test_release.py
```

Java 仓库包解压后，可运行 `python3.12 tools/verify_maven_consumer.py --repository /absolute/extracted/license-sdk-java-maven-0.7.1 --offline`，以独立临时项目从该仓库解析 core 并调用初始化、只读核验及共享导入 API。不会使用 Maven Local、SDK 源码或相邻业务仓库。离线模式需要预先缓存 Gradle。

离线包使用 uv.lock 导出带哈希的精确运行依赖，并用 pip `--only-binary=:all:` 下载当前原生平台 wheel。随后新建与源码隔离的 venv，以 `--no-index --require-hashes` 实际安装核心和适配器、执行 CLI help／import／pip check，成功才打包。只有提前缓存了完整 wheelhouse 时才使用脚本的 `--offline`，并通过 `PIP_FIND_LINKS` 指向缓存；缺 wheel 或哈希不匹配必须失败。

增加 Python 小版本／CPU／系统支持时，修改 workflow 的原生 runner 矩阵与 release.py 的平台检查／附件清单，在新目标上实际验证。不要把其他系统下载的 wheel 改名冒充目标平台；Windows 当前还需要 SDK 文件锁和路径语义的专门适配，不能仅添加 runner。

参考：[GitHub runner 列表](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)、[GitHub CLI release create](https://cli.github.com/manual/gh_release_create)、[pip download](https://pip.pypa.io/en/stable/cli/pip_download/)、[uv GitHub Actions 集成](https://docs.astral.sh/uv/guides/integration/github/)。
