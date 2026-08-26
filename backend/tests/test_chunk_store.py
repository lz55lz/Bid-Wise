from app.services.bid_pipeline.chunk_store import fetch_chunks, set_tender_candidates


class _Session:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, _statement: object, params: dict):
        self.calls.append(params)
        return type("Result", (), {"rowcount": len(params.get("indexes", []))})()


def test_replacing_candidate_set_clears_stale_rows_before_selecting_new_ones() -> None:
    session = _Session()

    persisted = set_tender_candidates(session, "version-1", [3, 7])

    assert persisted == 2
    assert len(session.calls) == 2
    assert session.calls[0] == {"version_id": "version-1"}
    assert session.calls[1] == {"version_id": "version-1", "indexes": [3, 7]}


def test_replacing_candidate_set_can_intentionally_leave_no_candidates() -> None:
    session = _Session()

    persisted = set_tender_candidates(session, "version-1", [])

    assert persisted == 0
    assert session.calls == [{"version_id": "version-1"}]


def test_fetch_chunks_excludes_superseded_layout_fragments() -> None:
    class ReadSession:
        def __init__(self) -> None:
            self.statement = ""

        def execute(self, statement: object, _params: dict):
            self.statement = str(statement)
            return type("Result", (), {"fetchall": lambda _self: []})()

    session = ReadSession()
    assert fetch_chunks(session, "version-1") == []
    assert "rechunk_superseded" in session.statement
