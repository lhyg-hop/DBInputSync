# DBInputSync Secure Remote

一个偏自用的 Windows 输入同步工具。

它保留了“手机浏览器 + 手机输入法/语音输入 -> 电脑当前光标位置实时上屏”的体验，同时提供两类连接方式：

1. 同一局域网下优先走局域网直连
2. 不同网络下走可切换的跨网隧道

当前跨网层支持：

- Cloudflare Quick Tunnel
- OpenFrp / FRP

如果你在中国大陆网络环境下使用，建议优先配置 OpenFrp / FRP，再把 Cloudflare 当作备用通道。

## 特性

- 手机继续使用浏览器和系统输入法，不需要原生 App
- 同时提供局域网二维码和跨网二维码
- 同网优先 LAN，异网支持多种隧道
- 启动后生成一次性二维码和配对码
- 所有写操作都必须先配对，再拿会话令牌访问
- `undo` 按会话隔离，不会串到别的设备
- 支持文本发送、回车、退格、方向键、符号包裹、撤销
- 本地控制面板支持重新生成二维码、刷新局域网地址、暂停远程输入、紧急断开和重连隧道

## 直接使用发布版

如果你只是想在另一台 Windows 电脑上用，不想自己装 Python：

1. 到 GitHub Releases 下载 `DBInputSync-Windows-Portable.zip`
2. 解压到任意目录
3. 双击 `start.bat`
4. 稍等几秒后打开本机控制面板：

```text
http://127.0.0.1:5000/control
```

发布版已经打包了 Python 运行环境，并会优先使用随包附带的隧道客户端。

## 从源码运行

### 运行要求

- Windows
- Python 3.9+
- 至少准备一种跨网客户端：
  - `cloudflared`
  - 或 `openfrpc.exe` / `frpc.exe`

### 安装

```bash
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirement.txt
```

### 启动

不要直接双击 `main.py`，请运行：

```text
start.bat
```

本地控制面板地址：

```text
http://127.0.0.1:5000/control
```

## 中国大陆网络建议

如果你发现 Cloudflare Quick Tunnel 在当前网络下不可用，推荐配置 OpenFrp / FRP。

程序支持两种配置方式：

1. 环境变量
2. 程序目录下的 `dbsync.ini`

建议直接复制一份示例文件：

```text
dbsync.example.ini -> dbsync.ini
```

然后填写：

```ini
[tunnel]
provider_priority = openfrp,cloudflare

[openfrp]
token = 你的 OpenFrp token
proxy_ids = 你的隧道 ID，多个可用逗号分隔
public_url = https://你的公网访问地址
executable = openfrpc.exe
```

说明：

- `provider_priority` 决定跨网优先级
- 如果你把它设为 `openfrp,cloudflare`，程序会优先显示 OpenFrp 的跨网二维码
- `public_url` 是手机真正访问的公网地址，程序会在这个地址后自动拼接 `pair_code`
- `executable` 可以是程序目录里的 `openfrpc.exe` / `frpc.exe`，也可以填绝对路径

## 发布版打包

如果你要自己生成一个可分发的 release 包：

1. 先准备好 `.venv`
2. 确认本机已经安装 `cloudflared`
3. 运行：

```text
build_release.bat
```

打包完成后会生成两个结果：

- `dist\DBInputSync\`
- `release\DBInputSync-Windows-Portable.zip`

其中 zip 包就可以直接上传到 GitHub Releases。

## 使用方式

1. 在电脑上启动程序
2. 打开本地控制面板
3. 如果手机和电脑在同一 Wi-Fi / 同一局域网，优先扫“局域网直连”二维码
4. 如果手机和电脑不在同一网络，扫“跨网连接”二维码
5. 在手机页面输入文字或使用语音转文字
6. 内容会同步到电脑当前光标位置

## 安全说明

- 二维码中的配对码默认 120 秒过期
- 配对码只能使用一次
- 同一轮二维码中的局域网入口和跨网入口共用同一个配对码，任一入口兑换成功后另一入口立即失效
- 手机页面会换取签名会话令牌，之后所有写操作都必须带令牌
- 控制面板只允许从本机 `localhost/127.0.0.1` 打开
- 远程输入可一键暂停，必要时可紧急断开跨网隧道

## 热替换规则

依然支持 `hot-rule.txt`：

```txt
男主 = 张无忌
\s+ =
```

规则会在发送文本前应用。
