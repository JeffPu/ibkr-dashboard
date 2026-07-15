class InMemoryElasticsearchClient:
    def __init__(self) -> None:
        self.storage: dict[tuple[str, str], dict] = {}
        self.indexes: set[str] = set()

    def update(self, *, index: str, id: str, doc: dict, doc_as_upsert: bool) -> None:
        if not doc_as_upsert:
            raise ValueError("doc_as_upsert must be true")
        self.storage[(index, id)] = dict(doc)

    def get(self, *, index: str, id: str) -> dict:
        key = (index, id)
        if key not in self.storage:
            raise KeyError(f"document not found: {index}/{id}")
        return {"_source": dict(self.storage[key])}

    def delete(self, *, index: str, id: str) -> None:
        self.storage.pop((index, id), None)

    def search(
        self,
        *,
        index: str,
        size: int = 10,
        offset: int = 0,
        sort_field: str | None = None,
        descending: bool = True,
        term_filters: dict | None = None,
    ) -> list[dict]:
        docs = [dict(doc) for (idx, _), doc in self.storage.items() if idx == index]
        if term_filters:
            docs = [
                doc
                for doc in docs
                if all(str(doc.get(key)) == str(value) for key, value in term_filters.items())
            ]
        if sort_field is not None:
            docs.sort(key=lambda doc: str(doc.get(sort_field, "")), reverse=descending)
        return docs[offset : offset + size]

    def ping(self) -> None:
        return None

    def ensure_index(self, index: str) -> None:
        self.indexes.add(index)
