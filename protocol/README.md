# Protocol v1 scaffold

`request-code-v1-vectors.json` 是从已确认设计复制的合成向量原件。Java/Python 核心制品必须分别内嵌完全相同的资源文件，构建及测试校验 SHA-256，不依赖工作区 `.agents`。

IC1、RC1J、RC1A 编码：20 字节摘要后附 CRC16-XMODEM（poly=0x1021，init=0，非反射，无 xorout）；CRC 覆盖用途前缀 ASCII 字节及摘要。22 字节按最高位优先 Crockford Base32 编码，补 4 个零位，36 字符，每 6 字符分组。前缀用途单独校验，不对前缀应用 O/I/L 替换。正文支持 ASCII 大小写、O→0 和 I/L→1；仅忽略 ASCII 空白 `09 0a 0b 0c 0d 20` 及 ASCII 连字符。任何非 ASCII 字符在大小写转换前拒绝。未知用途、长度、补位、字符或 CRC 错误拒绝，不自动改为无绑定。

IC1 摘要由产品配置的 host-fingerprint 和 host-install 域、真实 machine-id 及 UUIDv4 计算；恢复摘要覆盖产品恢复域、安装 UUID、服务、nonce 与冻结状态指纹。通用算法接收显式域配置，ICP 配置不能成为其他产品的默认身份。输入码最长 256 字符，不进行通用 Unicode 规范化。machine-id 只移除一个末尾 LF，不进行通用 trim；CRLF、额外换行和空格拒绝，必须是非零的 32 位小写十六进制。

Phase 1 不包含 SDK 商业许可签名/验签实现或生产信任材料。`test-fixtures/` 提供四种组合的纯测试签名样例及 SPKI 公钥，生成用临时私钥已丢弃；它不打入运行 JAR/wheel，也不进入客户信任。JDK 独立验证样例签名，完整 SDK 验签及第二产品签名隔离在 Phase 2 验证；本目录的未签名字段组合不等于合法许可。
