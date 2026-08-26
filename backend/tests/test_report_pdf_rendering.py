from __future__ import annotations

from uuid import uuid4

import app.services.report_service as report_module
from app.services.report_service import ReportService, _DraftSection


class _RecordingCanvas:
    instances: list[_RecordingCanvas] = []

    def __init__(self, output, **_kwargs) -> None:
        self._output = output
        self.pages = 1
        self.draw_y: list[float] = []
        self.__class__.instances.append(self)

    def setTitle(self, _value) -> None:  # noqa: N802
        pass

    def setFont(self, _name, _size) -> None:  # noqa: N802
        pass

    def drawString(self, _x, y, _text) -> None:  # noqa: N802
        self.draw_y.append(y)

    def setStrokeColorRGB(self, *_args) -> None:  # noqa: N802
        pass

    def setLineWidth(self, _value) -> None:  # noqa: N802
        pass

    def line(self, *_args) -> None:
        pass

    def showPage(self) -> None:  # noqa: N802
        self.pages += 1

    def save(self) -> None:
        self._output.write(b"%PDF-test")


def test_pdf_renderer_resets_cursor_after_page_break(monkeypatch) -> None:
    _RecordingCanvas.instances.clear()
    monkeypatch.setattr(report_module.canvas, "Canvas", _RecordingCanvas)
    section = _DraftSection(
        code="PROJECT_OVERVIEW",
        content="\n".join(f"line {index}" for index in range(120)),
        evidence_ids=[uuid4()],
    )

    ReportService._render_pdf("测试项目", 1, [section])

    rendered = _RecordingCanvas.instances[0]
    assert rendered.pages > 1
    assert min(rendered.draw_y) >= 48
