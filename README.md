# License SDK

独立于业务工程的离线许可 SDK。当前是 Phase 1 骨架，可用于审阅协议及构建边界，不能用于客户授权发行。

已实现：Java 21 与 Python 3.12 的 IC1、RC1J、RC1A 编解码及合成向量回归、分别可打包的核心与框架适配工程。框架模块暂不安装请求过滤器，不会产生授权保护。

后续阶段才实现：Ed25519 验签、严格许可解析、期限与绑定状态、时钟保护、恢复及 HTTP/任务准入。签发、密钥生成及商业私钥管理只属于独立 license-issuer 工程。通用核心不导入业务代码、Spring 或 FastAPI。

目录：

- `protocol/`：权威 v1 合成编码向量、版本清单及字节协议说明。
- `java/`：`license-core` 和 `license-spring`，通过 Gradle Wrapper 构建。
- `python/`：独立发布的 `metor-license-core` 与 `metor-license-fastapi`，通过 uv 构建。
- `examples/`：仅记录后续最小宿主契约；真实双语言许可及第二产品隔离样例在 Phase 2 实现。

暂用版本 `0.1.0`、Maven group `io.metor.license` 及 Python 分发名前缀 `metor-license-`。这是本地验证坐标，发布组织和私有仓库地址尚待确认，未发布到远程仓库。Java/Python SDK 发行号与许可 schema_version 是独立概念；当前只实现编码协议，不表示商业协议已经可用。

SDK 会随客户运行制品交付；依赖包不是防反编译边界。测试向量无客户数据或商业私钥。
