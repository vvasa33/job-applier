from jobhunter.ingestion.ports import JobSource


class SourceRegistry:
    """In-memory registry of job sources. Real boards are registered here later."""

    def __init__(self) -> None:
        self._sources: dict[str, JobSource] = {}

    def register(self, source: JobSource) -> None:
        identity = source.identify()
        if not identity.key.strip():
            raise ValueError("source key is required")
        if identity.key in self._sources:
            raise ValueError(f"source already registered: {identity.key}")
        self._sources[identity.key] = source

    def get(self, key: str) -> JobSource:
        try:
            return self._sources[key]
        except KeyError:
            raise KeyError(f"unknown source: {key}") from None

    def all(self) -> tuple[JobSource, ...]:
        return tuple(self._sources.values())
