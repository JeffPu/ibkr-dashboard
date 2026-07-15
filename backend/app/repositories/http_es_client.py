from typing import Any

import httpx


class HttpElasticsearchClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 5.0,
        api_key: str | None = None,
        username: str | None = None,
        password: str | None = None,
        headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if client is not None:
            self._client = client
            return

        merged_headers = dict(headers or {})
        if api_key and "Authorization" not in merged_headers:
            merged_headers["Authorization"] = f"ApiKey {api_key}"
        auth: httpx.BasicAuth | None = None
        if not api_key and username and password:
            auth = httpx.BasicAuth(username=username, password=password)
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers=merged_headers or None,
            auth=auth,
            transport=transport,
        )

    def update(self, *, index: str, id: str, doc: dict[str, Any], doc_as_upsert: bool) -> None:
        response = self._client.post(
            f"/{index}/_update/{id}",
            json={"doc": doc, "doc_as_upsert": doc_as_upsert},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"failed to update {index}/{id}: {response.status_code}")

    def get(self, *, index: str, id: str) -> dict[str, Any]:
        response = self._client.get(f"/{index}/_doc/{id}")
        if response.status_code == 404:
            raise KeyError(f"document not found: {index}/{id}")
        if response.status_code >= 400:
            raise RuntimeError(f"failed to fetch {index}/{id}: {response.status_code}")
        return response.json()

    def delete(self, *, index: str, id: str) -> None:
        response = self._client.delete(f"/{index}/_doc/{id}")
        if response.status_code not in (200, 404):
            raise RuntimeError(f"failed to delete {index}/{id}: {response.status_code}")

    def search(
        self,
        *,
        index: str,
        size: int = 10,
        offset: int = 0,
        sort_field: str | None = None,
        descending: bool = True,
        term_filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {
            "from": offset,
            "size": size,
            "query": {"match_all": {}},
        }
        if term_filters:
            filter_clauses = []
            for field, value in term_filters.items():
                # Compatibility for indices where field is mapped as text and keyword subfield exists.
                filter_clauses.append(
                    {
                        "bool": {
                            "should": [
                                {"term": {field: value}},
                                {"term": {f"{field}.keyword": value}},
                            ],
                            "minimum_should_match": 1,
                        }
                    }
                )
            body["query"] = {
                "bool": {
                    "filter": filter_clauses
                }
            }
        if sort_field is not None:
            order = "desc" if descending else "asc"
            body["sort"] = [{sort_field: {"order": order}}]
        response = self._client.post(f"/{index}/_search", json=body)
        if response.status_code >= 400 and sort_field is not None:
            # Fallback 1: some indices map strings as text and only allow sorting on .keyword
            order = "desc" if descending else "asc"
            fallback_body = dict(body)
            fallback_body["sort"] = [{f"{sort_field}.keyword": {"order": order}}]
            response = self._client.post(f"/{index}/_search", json=fallback_body)
        if response.status_code >= 400 and sort_field is not None:
            # Fallback 2: return unsorted data rather than crashing the entire page.
            fallback_body = dict(body)
            fallback_body.pop("sort", None)
            response = self._client.post(f"/{index}/_search", json=fallback_body)
        if response.status_code >= 400:
            raise RuntimeError(f"failed to search {index}: {response.status_code}")
        payload = response.json()
        hits = payload.get("hits", {}).get("hits", [])
        return [hit.get("_source", {}) for hit in hits]

    def ping(self) -> None:
        response = self._client.get("/")
        if response.status_code >= 400:
            raise RuntimeError(f"failed to ping elasticsearch: {response.status_code}")

    def ensure_index(self, index: str) -> None:
        response = self._client.put(f"/{index}")
        if response.status_code in (200, 201):
            return
        if response.status_code == 400:
            payload = response.json()
            error = payload.get("error", {})
            if isinstance(error, dict) and error.get("type") == "resource_already_exists_exception":
                return
        raise RuntimeError(f"failed to create index {index}: {response.status_code}")
