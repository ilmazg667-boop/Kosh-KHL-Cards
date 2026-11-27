import unittest

from app.image_fetch import (
    PublicResolver,
    _read_image_response,
    validate_card_image_url,
)


class FakeResolver:
    def __init__(self, records):
        self.records = records
        self.closed = False

    async def resolve(self, host, port, family):
        return self.records

    async def close(self):
        self.closed = True


class FakeContent:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, _size):
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, chunks, content_type="image/jpeg", content_length=None):
        self.content = FakeContent(chunks)
        self.content_type = content_type
        self.content_length = content_length


class CardImageFetchTests(unittest.IsolatedAsyncioTestCase):
    def test_accepts_public_http_and_https_urls(self):
        self.assertEqual(
            validate_card_image_url("https://cdn.example/image.jpg"),
            "https://cdn.example/image.jpg",
        )
        self.assertEqual(
            validate_card_image_url("http://cdn.example/image.png"),
            "http://cdn.example/image.png",
        )

    def test_rejects_unsafe_urls(self):
        for url in (
            "ftp://cdn.example/image.jpg",
            "http://localhost/image.jpg",
            "http://127.0.0.1/image.jpg",
            "http://[::1]/image.jpg",
            "https://user:pass@cdn.example/image.jpg",
            "https://cdn.example:8443/image.jpg",
            "https://host.local/image.jpg",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    validate_card_image_url(url)

    async def test_public_resolver_rejects_private_dns_results(self):
        resolver = PublicResolver(FakeResolver([{"host": "10.0.0.5"}]))
        with self.assertRaises(OSError):
            await resolver.resolve("image.example", 443)
        await resolver.close()
        self.assertTrue(resolver._resolver.closed)

    async def test_public_resolver_accepts_public_dns_results(self):
        resolver = PublicResolver(FakeResolver([{"host": "1.1.1.1"}]))
        records = await resolver.resolve("image.example", 443)
        self.assertEqual(records[0]["host"], "1.1.1.1")
        await resolver.close()

    async def test_reads_image_with_streaming_limit(self):
        response = FakeResponse([b"abc", b"def"])
        self.assertEqual(await _read_image_response(response, max_bytes=6), b"abcdef")
        with self.assertRaises(ValueError):
            await _read_image_response(response, max_bytes=5)

    async def test_rejects_wrong_content_type_and_declared_oversize(self):
        with self.assertRaises(ValueError):
            await _read_image_response(FakeResponse([b"data"], "text/html"))
        with self.assertRaises(ValueError):
            await _read_image_response(FakeResponse([], content_length=9), max_bytes=8)


class FakeResponseContext(FakeResponse):
    def __init__(self, status=200, chunks=None, content_type="image/jpeg"):
        super().__init__(chunks or [b"img"], content_type=content_type)
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError("HTTP error")


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class CardImageDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_disables_redirects_and_requests_image(self):
        from app.image_fetch import download_card_image

        session = FakeSession(FakeResponseContext())
        self.assertEqual(
            await download_card_image(session, "https://cdn.example/card.jpg"),
            b"img",
        )
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://cdn.example/card.jpg")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["headers"]["Accept"], "image/*")

    async def test_download_rejects_redirects(self):
        from app.image_fetch import download_card_image

        session = FakeSession(FakeResponseContext(status=302))
        with self.assertRaises(ValueError):
            await download_card_image(session, "https://cdn.example/card.jpg")


if __name__ == "__main__":
    unittest.main()
