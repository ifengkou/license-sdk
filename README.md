# License SDK

[中文使用说明](README_cn.md)

独立于业务工程的离线许可 SDK。提供商业文件验签、安装身份观察、持久化时间保护和 HTTP 入口使用的本地校验结果。产品只需在配置的请求入口调用 SDK，各服务独立验证，不改动业务流程。

已实现：Java 21 与 Python 3.12 的编码协议、严格解析、Ed25519 原始字节验签、四种许可组合、产品及绑定检查、精确期限边界，以及独立核心与框架适配制品。产品通过薄适配接入 HTTP 许可检查；框架模块不自动替代账号权限或注册业务过滤器。

后台任务准入、执行续接和业务细粒度授权不在本方案范围内。单服务签名时间恢复已实现，客户最终交付验收另行实施。签发、密钥生成及商业私钥管理只属于独立 license-issuer 工程。通用核心不导入业务代码、Spring 或 FastAPI。

目录：

- `protocol/`：权威 v1 合成编码向量、版本清单及字节协议说明。
- `java/`：`license-core` 和 `license-spring`，通过 Gradle Wrapper 构建。
- `python/`：独立发布的 `metor-license-core` 与 `metor-license-fastapi`，通过 uv 构建。
- `examples/`：最小宿主验签调用说明；实际 CLI 与双语言回归见 `tools/verify_phase2_cross_language.py`。

目标版本 `0.6.0`（本地统一构建，未远程发布）、Maven group `io.metor.license` 及 Python 分发名前缀 `metor-license-`。这是本地验证坐标，发布组织和私有仓库地址尚待确认，未发布到远程仓库。Java/Python SDK 发行号与许可 schema_version 是独立概念；当前文件校验不表示业务产品已启用授权保护。

SDK 会随客户运行制品交付；依赖包不是防反编译边界。测试向量无客户数据或商业私钥。

Phase 4 的 Java `license-spring` 使用 Nimbus/Jackson，Python 核心使用 PyJWT 实现独立 CONTROL 证明；用户 JWT 与内部控制证明不互换。跨语言验证入口为 `tools/verify_phase4_control.py`。

2026-10-22 简化决策：仅对产品配置的关键 API 做入口校验，未匹配请求直接放行；不引入 ExecutionGate、TASK 证明或服务间联动验证。Phase 5.5 改用统一 `requireValid` / `require_valid` 入口，移除历史只读/操作分类；回拨容差为一天，商业期限仍精确判断。既有 `0.4.0` 制品不覆盖，实验性 `0.5.0` 已撤回。5.5/6B/6C 完成后统一正式发行 `0.6.0`。

客户部署使用我方预制镜像或完整运行安装包：SDK 与全部运行依赖在我方构建期预装，客户启动不执行 Gradle/Maven/pip/uv 安装、不访问公网。Python 单个 SDK wheel 不包含 cryptography/PyJWT 的全部依赖，独立离线接入须同时提供目标平台完整 wheelhouse。


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
