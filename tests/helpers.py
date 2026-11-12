import httpx


def make_response(status: int, content: bytes = b"audio") -> httpx.Response:
    return httpx.Response(
        status,
        content=content,
        request=httpx.Request("POST", "https://api.elevenlabs.io/v1/text-to-speech/x"),
    )
