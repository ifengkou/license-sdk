# Protocol v1

`request-code-v1-vectors.json` 是从已确认设计复制的合成向量原件。Java/Python 核心制品必须分别内嵌完全相同的资源文件，构建及测试校验 SHA-256，不依赖工作区 `.agents`。

IC1、RC1J、RC1A 编码：20 字节摘要后附 CRC16-XMODEM（poly=0x1021，init=0，非反射，无 xorout）；CRC 覆盖用途前缀 ASCII 字节及摘要。22 字节按最高位优先 Crockford Base32 编码，补 4 个零位，36 字符，每 6 字符分组。前缀用途单独校验，不对前缀应用 O/I/L 替换。正文支持 ASCII 大小写、O→0 和 I/L→1；仅忽略 ASCII 空白 `09 0a 0b 0c 0d 20` 及 ASCII 连字符。任何非 ASCII 字符在大小写转换前拒绝。未知用途、长度、补位、字符或 CRC 错误拒绝，不自动改为无绑定。

IC1 摘要由产品配置的 host-fingerprint 和 host-install 域、真实 machine-id 及 UUIDv4 计算；恢复摘要覆盖产品恢复域、安装 UUID、服务、nonce 与冻结状态指纹。通用算法接收显式域配置，ICP 配置不能成为其他产品的默认身份。输入码最长 256 字符，不进行通用 Unicode 规范化。machine-id 只移除一个末尾 LF，不进行通用 trim；CRLF、额外换行和空格拒绝，必须是非零的 32 位小写十六进制。

Phase 2 implements strict commercial-v1 verification in both SDK cores. The envelope contains
only payload/signature. Limits are 64 KiB for the envelope, 32 KiB for payload, and depth8.
Duplicates, unknown fields, wrong types, floats/nonstandard numbers, noncanonical unpadded
Base64URL, invalid UTF-8, control characters and lone surrogates are rejected. Signatures
cover original payload bytes. TERM uses not_before <= now < expires_at; PERPETUAL requires
explicit null expires_at. HOST_INSTALL requires matching local binding; UNBOUND requires
explicit null binding fields. Trust comes only from fixed product assembly.

`test-fixtures/` contains synthetic signatures and public SPKI only; temporary generation keys
were discarded. Fixtures are excluded from core runtime JARs/wheels and customer trust.
See Java/Python READMEs and `tools/verify_phase2_cross_language.py` for actual verification.
Recovery codes are implemented, but recovery signing/application, runtime clocks and execution
enforcement remain pending.
## Internal CONTROL proof v1 (SDK 0.4.0)

CONTROL is a dedicated HS256 compact JWS, independent of user JWT authentication.
The product assembly supplies the fixed issuer, audience and key domain. For ICP
Diag, derive the 32-byte key as HMAC-SHA256(installation master key,
UTF-8 `icp-diag/control/v1`); issuer is `icp-diag/metor`, audience is
`icp-diag/ai-internal`. Never accept caller-selected key domains or public-key URLs.

The header contains exactly `alg=HS256` and `typ=JWT`. Claims contain exactly
`iss`, `aud`, `installation_id`, `purpose=CONTROL`, `scope`, `iat`, `exp`, `jti`,
`http_method`, `http_path`, `body_sha256`. Installation and token identifiers are
canonical UUIDv4 values. `iat` and `exp` are nonnegative signed 64-bit UTC epoch
seconds; the lifetime is 1–60 seconds. Five seconds of transport skew are allowed;
the upper bound is exclusive (`now < exp + 5`). Commercial PERPETUAL does not
remove this transport limit.

Scopes `LICENSE_STATUS` and `LICENSE_VALIDATE` bind respectively to empty-body
`GET /internal/license/status` and exact original-file-body
`POST /internal/license/validate`. The request body digest is lowercase SHA-256.
The token is limited to 8192 characters, decoded header to 512 bytes, decoded
claims to 4096 bytes and body to 65536 bytes. Duplicate/unknown fields, algorithm
changes, invalid types and noncanonical unpadded Base64URL are rejected.
Identical short-window transport retries remain allowed; CONTROL cannot stand
in for the future TASK proof or grant background execution admission.

Candidate preflight uses actual UTC and never replaces the active license or
saves clock checkpoints. Candidate acceptability and current clock restrictions
are separate results. Product adapters retain administrator authorization,
whole-file digest concurrency control, dual preflight and publication semantics.
Conformance: `tools/verify_phase4_control.py` uses an independent standard-library
HMAC oracle plus actual Java/Python issuance and verification.
