# DBInputSync v0.0.8

本次更新重点增强了中国大陆网络环境下的跨网可用性。

## Highlights

- 新增可切换的跨网通道架构
- 保留 Cloudflare Quick Tunnel
- 新增 OpenFrp / FRP 作为国内网络优先备用通道
- 控制面板会显示当前实际使用的跨网通道
- 新增 `dbsync.example.ini` 示例配置文件
- 发布包会自动附带示例配置文件，方便用户直接复制成 `dbsync.ini`

## 使用建议

### 同一局域网

- 优先使用 LAN 二维码

### 不同网络

- 海外网络或可正常访问 Cloudflare 的环境：可继续使用 Cloudflare
- 中国大陆网络环境：建议优先配置 OpenFrp / FRP，并把优先级设为 `openfrp,cloudflare`

## 配置示例

```ini
[tunnel]
provider_priority = openfrp,cloudflare

[openfrp]
token = 你的 OpenFrp token
proxy_ids = 你的隧道 ID
public_url = https://你的公网访问地址
executable = openfrpc.exe
```

## Notes

- 该版本提升了“国内跨网可用性”，但仍不等于“任何网络下绝对可用”
- 如果你没有配置 OpenFrp / FRP，程序仍会回退到 Cloudflare
- 敏感信息如密码、验证码、私钥、助记词仍然不建议通过此类输入同步工具传输
