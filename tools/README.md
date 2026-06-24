# tools/

项目附属工具脚本 (与主程序无运行时依赖)。

## socks2http.py

把仅支持 HTTP 代理的客户端 (例如 Git for Windows 自带的 .NET HTTP 客户端)
通过本地 SOCKS5 代理 (例如 127.0.0.1:10808) 转发到公网。

**典型场景**: 你的代理工具 (Clash / V2Ray / ...) 只暴露 SOCKS5 端口 (10808),
但 Git for Windows 用 `ServicePointManager` 不识别 SOCKS 协议, 推送时
报 `ServicePointManager 不支持具有 socks5 方案的代理`。

**使用**:

```bash
# 1. 安装依赖
pip install PySocks

# 2. 启动桥接 (默认监听 7891, 转发到 10808)
python tools/socks2http.py

# 3. 让 git 走本地 HTTP 代理
git config --global http.proxy http://127.0.0.1:7891
git config --global https.proxy http://127.0.0.1:7891

# 4. 推 / 拉
git push origin main

# 5. 完成后恢复
git config --global --unset http.proxy
git config --global --unset https.proxy
```

**自定义端口**:

```bash
python tools/socks2http.py <listen_port> <socks5_host> <socks5_port>
# 例: python tools/socks2http.py 9000 192.168.1.5 1080
```
