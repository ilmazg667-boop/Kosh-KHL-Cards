import ipaddress
import socket
from urllib.parse import urlsplit

import aiohttp

MAX_CARD_IMAGE_BYTES = 8 * 1024 * 1024


def validate_card_image_url(value):
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Некорректный URL изображения") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Для изображения разрешены только HTTP(S)-ссылки")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL изображения не должен содержать учётные данные")
    expected_port = 443 if scheme == "https" else 80
    if port is not None and port != expected_port:
        raise ValueError("Для URL изображения разрешён только стандартный порт")

    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Локальные адреса для изображений запрещены")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("Непубличный IP-адрес для изображения запрещён")
    return raw


class PublicResolver(aiohttp.abc.AbstractResolver):
    """Resolve hostnames only to globally routable addresses."""

    def __init__(self, resolver=None):
        self._resolver = resolver or aiohttp.resolver.DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await self._resolver.resolve(host, port, family)
        if not records:
            raise OSError("DNS не вернул адрес для изображения")
        for record in records:
            try:
                address = ipaddress.ip_address(str(record["host"]).split("%", 1)[0])
            except ValueError as exc:
                raise OSError("DNS вернул некорректный адрес изображения") from exc
            if not address.is_global:
                raise OSError("Загрузка с непубличного IP-адреса запрещена")
        return records

    async def close(self):
        await self._resolver.close()


async def _read_image_response(response, max_bytes=MAX_CARD_IMAGE_BYTES):
    content_type = str(getattr(response, "content_type", "") or "").lower()
    if not content_type.startswith("image/"):
        raise ValueError("Сервер вернул не изображение")

    content_length = getattr(response, "content_length", None)
    if content_length is not None and content_length > max_bytes:
        raise ValueError("Изображение превышает лимит 8 МБ")

    chunks = []
    total = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("Изображение превышает лимит 8 МБ")
        chunks.append(chunk)
    return b"".join(chunks) or None


async def download_card_image(session, url, max_bytes=MAX_CARD_IMAGE_BYTES):
    safe_url = validate_card_image_url(url)
    async with session.get(
        safe_url,
        allow_redirects=False,
        headers={"Accept": "image/*"},
    ) as response:
        if 300 <= response.status < 400:
            raise ValueError("Перенаправления для изображений запрещены")
        response.raise_for_status()
        return await _read_image_response(response, max_bytes=max_bytes)
