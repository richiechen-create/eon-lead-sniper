"""Acceptance criterion #5: reveal_phone_number must be false on every Apollo match call."""
import httpx

from app.apollo.client import ApolloClient


class _Recorder:
    def __init__(self, response_body: dict):
        self.posted: list[tuple[str, dict]] = []
        self._body = response_body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        try:
            payload = request.read()
            import json as _json
            data = _json.loads(payload)
        except Exception:
            data = {}
        self.posted.append((str(request.url), data))
        return httpx.Response(200, json=self._body, request=request)


def test_match_never_reveals_phone(session):
    body = {"person": {"id": "p1", "email": "a@b.com", "email_status": "verified"}}
    recorder = _Recorder(body)
    transport = httpx.MockTransport(recorder)
    http = httpx.Client(transport=transport)

    client = ApolloClient(session, http_client=http)
    client.match_person("p1")

    assert recorder.posted, "no requests were captured"
    url, payload = recorder.posted[-1]
    assert "/people/match" in url
    assert payload["reveal_phone_number"] is False
    # Personal emails also must be false (privacy default).
    assert payload["reveal_personal_emails"] is False


def test_search_uses_header_auth_not_url_param(session):
    body = {"people": [], "pagination": {"total_pages": 1}}
    headers_seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers_seen.append(dict(request.headers))
        return httpx.Response(200, json=body, request=request)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ApolloClient(session, http_client=http)

    from app.apollo.client import SearchQuery

    list(
        client.search_people(
            SearchQuery(
                domain="example.com",
                titles=["CTO"],
                seniorities=[],
                locations=[],
                departments=[],
            )
        )
    )
    assert headers_seen, "search made no http call"
    assert headers_seen[0].get("x-api-key") == "test-key"
