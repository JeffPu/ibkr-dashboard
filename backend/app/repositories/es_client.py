from typing import Any, Protocol


class ElasticsearchLike(Protocol):
    def update(self, *, index: str, id: str, doc: dict[str, Any], doc_as_upsert: bool) -> None: ...

    def get(self, *, index: str, id: str) -> dict[str, Any]: ...

    def delete(self, *, index: str, id: str) -> None: ...

    def search(
        self,
        *,
        index: str,
        size: int = 10,
        offset: int = 0,
        sort_field: str | None = None,
        descending: bool = True,
        term_filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...

    def ping(self) -> None: ...

    def ensure_index(self, index: str) -> None: ...
