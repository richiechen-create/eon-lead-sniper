import logging
import time
from dataclasses import dataclass
from typing import Any, Iterator, Optional

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ApiCallLog

logger = logging.getLogger(__name__)


class ApolloError(Exception):
    pass


@dataclass
class SearchQuery:
    domain: str
    titles: list[str]
    seniorities: list[str]
    locations: list[str]
    departments: list[str]
    per_page: int = 25


@dataclass
class MatchResult:
    apollo_person_id: str
    email: Optional[str]
    email_status: Optional[str]  # verified | likely | unverified
    credits_used: int
    raw: dict


class ApolloClient:
    """Thin Apollo client.

    - Uses X-Api-Key header (never URL params).
    - Logs every call to api_call_log.
    - Retries 429 with exponential backoff up to 5 times.
    - Retries 5xx once.
    - reveal_phone_number is ALWAYS false on /people/match.
    """

    def __init__(self, session: Session, *, http_client: Optional[httpx.Client] = None):
        settings = get_settings()
        self.api_key = settings.APOLLO_API_KEY
        self.base_url = settings.APOLLO_BASE_URL.rstrip("/")
        self.per_page = settings.APOLLO_PER_PAGE
        self.max_pages = settings.APOLLO_MAX_PAGES
        self.session = session
        self._http = http_client or httpx.Client(timeout=30.0)
        self._owns_http = http_client is None

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "ApolloClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- public API -------------------------------------------------------

    def search_people(self, query: SearchQuery) -> Iterator[dict]:
        """Yield candidate person records across pages.

        Stops when no results, when page hits self.max_pages, or
        when the upstream signals last page (people array empty).
        """
        page = 1
        while page <= self.max_pages:
            payload = {
                "q_organization_domains_list": [query.domain],
                "per_page": query.per_page or self.per_page,
                "page": page,
            }
            if query.titles:
                payload["person_titles"] = query.titles
            if query.seniorities:
                payload["person_seniorities"] = query.seniorities
            if query.locations:
                payload["person_locations"] = query.locations
            if query.departments:
                payload["person_departments"] = query.departments

            response = self._post("/mixed_people/search", payload, credits=0)
            people = response.get("people") or []
            if not people:
                return
            for person in people:
                yield person
            pagination = response.get("pagination") or {}
            total_pages = pagination.get("total_pages")
            if total_pages is not None and page >= total_pages:
                return
            page += 1

    def match_person(self, apollo_person_id: str) -> MatchResult:
        """Enrich one person. reveal_phone_number is forced to False."""
        payload = {
            "id": apollo_person_id,
            "reveal_personal_emails": False,
            "reveal_phone_number": False,
        }
        # Pre-call invariant: defense in depth against accidental mutation.
        assert payload["reveal_phone_number"] is False, "phone reveal must never be enabled"

        response = self._post("/people/match", payload, credits=0)
        person = response.get("person") or {}
        email = person.get("email")
        email_status = self._derive_email_status(person)
        credits_used = 1 if email else 0

        # Backfill the call log with the actual credit cost now that we know.
        if credits_used:
            self._backfill_last_call_credits(credits_used)

        return MatchResult(
            apollo_person_id=apollo_person_id,
            email=email,
            email_status=email_status,
            credits_used=credits_used,
            raw=person,
        )

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _derive_email_status(person: dict) -> Optional[str]:
        if not person.get("email"):
            return "unverified"
        status = (person.get("email_status") or "").lower()
        if status in {"verified", "valid"}:
            return "verified"
        if status in {"likely", "guessed"}:
            return "likely"
        return "unverified"

    def _post(self, endpoint: str, payload: dict, *, credits: int) -> dict:
        url = f"{self.base_url}{endpoint}"
        headers = {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

        attempt = 0
        max_429_retries = 5
        retried_5xx = False
        while True:
            attempt += 1
            try:
                resp = self._http.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                self._log_call(endpoint, None, credits, payload, {"error": str(exc)})
                raise ApolloError(f"network error calling {endpoint}: {exc}") from exc

            status = resp.status_code
            if status == 429 and attempt <= max_429_retries:
                wait = min(2 ** (attempt - 1), 30)
                logger.warning("Apollo 429 on %s, retry %d after %ds", endpoint, attempt, wait)
                time.sleep(wait)
                continue
            if 500 <= status < 600 and not retried_5xx:
                retried_5xx = True
                logger.warning("Apollo %d on %s, retrying once", status, endpoint)
                time.sleep(1)
                continue

            try:
                body = resp.json()
            except Exception:
                body = {"raw_text": resp.text}

            summary = self._summarize_response(body, status)
            self._log_call(endpoint, status, credits, payload, summary)

            if status >= 400:
                raise ApolloError(f"Apollo {endpoint} returned {status}: {body}")
            return body

    def _summarize_response(self, body: dict, status: int) -> dict:
        summary = {"http_status": status}
        if isinstance(body, dict):
            if "pagination" in body:
                summary["pagination"] = body["pagination"]
            if "people" in body and isinstance(body["people"], list):
                summary["people_count"] = len(body["people"])
            if "person" in body and isinstance(body["person"], dict):
                pid = body["person"].get("id")
                summary["person_id"] = pid
                summary["email_present"] = bool(body["person"].get("email"))
        return summary

    def _log_call(
        self,
        endpoint: str,
        http_status: Optional[int],
        credits: int,
        payload: dict,
        summary: dict,
    ) -> None:
        # Strip api_key never present in payload, but redact any sensitive-looking keys.
        safe_payload = {k: v for k, v in payload.items() if k.lower() not in {"api_key"}}
        entry = ApiCallLog(
            endpoint=endpoint,
            http_status=http_status,
            credits_used=credits,
            request_payload=safe_payload,
            response_summary=summary,
        )
        self.session.add(entry)
        self.session.flush()
        self._last_log_id = entry.id

    def _backfill_last_call_credits(self, credits: int) -> None:
        last_id = getattr(self, "_last_log_id", None)
        if last_id is None:
            return
        row = self.session.get(ApiCallLog, last_id)
        if row is not None:
            row.credits_used = credits
            self.session.flush()
