# License SDK

[中文使用说明](README_cn.md)

独立于业务工程的离线许可 SDK。当前为 Phase 4：商业文件验签、安装身份观察、持久化时间保护、内部控制证明和 HTTP 许可规则已实现；后台执行与恢复尚未接入，不能作为完整客户授权发行。

已实现：Java 21 与 Python 3.12 的编码协议、严格解析、Ed25519 原始字节验签、四种许可组合、产品及绑定检查、精确期限边界，以及独立核心与框架适配制品。产品通过薄适配接入 HTTP 许可检查；框架模块不自动替代账号权限或注册业务过滤器。

后续阶段才实现：恢复、后台任务准入及已启动任务续接。签发、密钥生成及商业私钥管理只属于独立 license-issuer 工程。通用核心不导入业务代码、Spring 或 FastAPI。

目录：

- `protocol/`：权威 v1 合成编码向量、版本清单及字节协议说明。
- `java/`：`license-core` 和 `license-spring`，通过 Gradle Wrapper 构建。
- `python/`：独立发布的 `metor-license-core` 与 `metor-license-fastapi`，通过 uv 构建。
- `examples/`：最小宿主验签调用说明；实际 CLI 与双语言回归见 `tools/verify_phase2_cross_language.py`。

暂用版本 `0.4.0`、Maven group `io.metor.license` 及 Python 分发名前缀 `metor-license-`。这是本地验证坐标，发布组织和私有仓库地址尚待确认，未发布到远程仓库。Java/Python SDK 发行号与许可 schema_version 是独立概念；当前文件校验不表示业务产品已启用授权保护。

SDK 会随客户运行制品交付；依赖包不是防反编译边界。测试向量无客户数据或商业私钥。

Phase 4 的 Java `license-spring` 使用 Nimbus/Jackson，Python 核心使用 PyJWT 实现独立 CONTROL 证明；用户 JWT 与内部控制证明不互换。跨语言验证入口为 `tools/verify_phase4_control.py`。
