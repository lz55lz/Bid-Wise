"""进程级 HTTP 连接池；内网 AI 服务复用 keep-alive，避免每批请求重复握手。"""

import asyncio

import httpx

_internal_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


async def get_internal_http_client() -> httpx.AsyncClient:
    """返回当前进程共享的直连客户端；适用于 embedding/reranker 等内网端点。"""
    global _internal_client
    if _internal_client is not None and not _internal_client.is_closed:
        return _internal_client
    async with _client_lock:
        if _internal_client is None or _internal_client.is_closed:
            _internal_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                trust_env=False,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return _internal_client


async def close_http_clients() -> None:
    """应用/Worker 退出时显式关闭进程级连接池。"""
    global _internal_client
    client, _internal_client = _internal_client, None
    if client is not None and not client.is_closed:
        await client.aclose()
