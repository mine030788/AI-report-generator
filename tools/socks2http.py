"""socks2http.py - SOCKS5 -> HTTP 代理桥接器

用途: 把只支持 HTTP 代理的客户端 (例如 Git for Windows 的 ServicePointManager)
通过本地 SOCKS5 代理 (例如 127.0.0.1:10808) 转发到公网。

启动:
    python tools/socks2http.py                    # 监听 127.0.0.1:7891 -> 10808
    python tools/socks2http.py 7891 127.0.0.1 10808

依赖: pip install PySocks
"""
from __future__ import annotations

import socket
import socketserver
import threading
from urllib.parse import urlparse

import socks  # PySocks

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 7891
SOCKS5_HOST = "127.0.0.1"
SOCKS5_PORT = 10808


def _relay(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(8192)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


def _connect_via_socks(host: str, port: int) -> socket.socket:
    s = socks.socksocket()
    s.set_proxy(socks.SOCKS5, SOCKS5_HOST, SOCKS5_PORT)
    s.settimeout(30)
    s.connect((host, port))
    return s


def _read_request_head(client: socket.socket) -> bytes:
    buf = b""
    client.settimeout(15)
    while b"\r\n\r\n" not in buf:
        chunk = client.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 65536:
            break
    return buf


class ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client = self.request
        try:
            head = _read_request_head(client)
            if not head:
                return
            first = head.split(b"\r\n", 1)[0]
            try:
                method, target, _ver = first.split(b" ", 2)
            except ValueError:
                client.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return
            method = method.decode("ascii", "ignore").upper()

            if method == "CONNECT":
                host, _, port_s = target.decode("ascii", "ignore").partition(":")
                port = int(port_s or "443")
            else:
                parsed = urlparse(target.decode("ascii", "ignore"))
                host = parsed.hostname
                port = parsed.port or 80
                if not host:
                    client.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                    return

            upstream = _connect_via_socks(host, port)

            if method == "CONNECT":
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                t1 = threading.Thread(target=_relay, args=(client, upstream), daemon=True)
                t2 = threading.Thread(target=_relay, args=(upstream, client), daemon=True)
                t1.start()
                t2.start()
                t1.join()
            else:
                upstream.sendall(head)
                _relay(upstream, client)
        except Exception as exc:  # noqa: BLE001
            try:
                client.sendall(
                    f"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n".encode()
                )
            except OSError:
                pass
            print(f"[bridge] {exc}", flush=True)
        finally:
            try:
                client.close()
            except OSError:
                pass


def main() -> None:
    import sys

    global LISTEN_PORT, SOCKS5_HOST, SOCKS5_PORT
    if len(sys.argv) >= 2:
        LISTEN_PORT = int(sys.argv[1])
    if len(sys.argv) >= 3:
        SOCKS5_HOST = sys.argv[2]
    if len(sys.argv) >= 4:
        SOCKS5_PORT = int(sys.argv[3])

    server = socketserver.ThreadingTCPServer((LISTEN_HOST, LISTEN_PORT), ProxyHandler)
    server.daemon_threads = True
    print(
        f"[bridge] HTTP proxy listening on http://{LISTEN_HOST}:{LISTEN_PORT} "
        f"-> SOCKS5 {SOCKS5_HOST}:{SOCKS5_PORT}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
