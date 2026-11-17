# 部署手册（DEPLOY.md）

WorkBuddy Dashboard 设计目标是「本地自用，双击即用」。本手册覆盖常见部署场景。

## 一、本机桌面使用（默认）

无需部署。直接按平台运行启动脚本：

- macOS：双击 `start-mac.command`
- Windows：双击 `start-windows.bat`
- Linux：`./start-linux.sh`

服务只监听 `127.0.0.1`，浏览器自动打开。数据自动从 `~/.workbuddy` 发现。

## 二、指定数据源

如果 WorkBuddy 装在非标准位置，或想看另一份数据：

```bash
WORKBUDDY_DB="/path/to/workbuddy.db" \
WORKBUDDY_TRACES="/path/to/traces" \
python3 dashboard.py
```

只想体验界面，用演示数据：

```bash
WORKBUDDY_DEMO=1 python3 dashboard.py
```

## 三、局域网访问

默认只监听本机，如需在局域网其他设备查看：

```bash
PANEL_BIND=0.0.0.0 PANEL_TOKEN='换成强口令' python3 dashboard.py
```

然后用 `http://<本机IP>:8790/` 访问，口令为设定的 `PANEL_TOKEN`。

> 注意：`0.0.0.0` 会把数据暴露给同网段设备。请务必设置强口令，
> 且仅在可信网络环境使用。

## 四、固定端口 + 反向代理

配合 Nginx 时固定端口便于反代配置：

```bash
PANEL_BIND=127.0.0.1 PANEL_PORT=8888 PANEL_TOKEN='强口令' \
WORKBUDDY_NO_BROWSER=1 python3 dashboard.py
```

Nginx 示例：

```nginx
server {
    listen 443 ssl;
    server_name panel.example.com;
    ssl_certificate     /path/cert.pem;
    ssl_certificate_key /path/key.pem;
    location / {
        proxy_pass http://127.0.0.1:8888;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

用 `curl` 检查存活（免口令）：

```bash
curl -s http://127.0.0.1:8888/healthz
```

## 五、后台常驻（systemd · Linux）

```ini
[Unit]
Description=WorkBuddy Dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/workbuddy-dashboard
Environment=PANEL_BIND=127.0.0.1
Environment=PANEL_PORT=8888
Environment=PANEL_TOKEN=your-strong-token
ExecStart=/usr/bin/python3 /opt/workbuddy-dashboard/dashboard.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now evanworkbuddy
```

## 六、免口令（仅本机自用）

```bash
WORKBUDDY_NO_AUTH=1 python3 dashboard.py
```

此时 `/` 与 `/api/stats` 无需口令。仅在确认只有本机能访问时使用。

## 常见问题

**Q：页面数据显示为演示数据？**
说明未找到本机 WorkBuddy 数据源。用 `WORKBUDDY_DB` 显式指定，或检查
`~/.workbuddy/workbuddy.db` 是否存在。

**Q：Token 数据为 0，但会话正常？**
Token 来自 traces 的 generation span。确认 `~/.workbuddy/traces/` 下有 `*.jsonl` 文件；
可用 `WORKBUDDY_TRACES` 显式指定。

**Q：数据不更新？**
热数据 5 秒、traces 30 秒缓存。稍等即可，或点击页面右上角刷新按钮（会重新拉取，
仍受缓存约束），也可重启服务立即生效。

**Q：改了端口怎么找地址？**
运行目录下的 `.port` 文件记录了实际地址；终端启动时也会打印。

**Q：会不会修改原始数据？**
不会。数据库全部以只读 URI 打开并开启 `query_only`，traces 只读流式解析，
代码中没有任何写操作作用于源数据。
