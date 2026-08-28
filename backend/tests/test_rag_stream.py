import asyncio
import json

from app.services.rag_stream import stream_rag_answer


def test_stream_retries_once_when_no_token_was_emitted() -> None:
    class FlakyLlm:
        calls = 0

        async def astream_answer(self, question, contexts):
            del question, contexts
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("temporary upstream failure")
            yield "恢复后的回答"

    async def collect():
        llm = FlakyLlm()
        events = [
            event
            async for event in stream_rag_answer(
                llm, "问题", [{"evidence_id": "e1", "content": "证据"}]
            )
        ]
        return llm, events

    llm, events = asyncio.run(collect())
    payloads = [json.loads(event.decode().removeprefix("data: ").strip()) for event in events]

    assert llm.calls == 2
    assert payloads[-1]["type"] == "done"
    assert payloads[-1]["answer"] == "恢复后的回答"
