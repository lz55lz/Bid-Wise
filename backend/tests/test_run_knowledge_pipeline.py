"""worker.run_knowledge_pipeline ARQ 任务测试

覆盖：
  - 成功：download → ingest → 更新 parse_status=READY
  - 失败：ingest 抛错 → parse_status=FAILED（返回 dict 不抛）
  - download 失败：抛 RuntimeError → 返回 failed
"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def base_kwargs():
    return {
        "ctx": {},
        "document_version_id": "11111111-1111-1111-1111-111111111111",
        "document_id": "22222222-2222-2222-2222-222222222222",
        "knowledge_version_id": "44444444-4444-4444-4444-444444444444",
        "chunk_type": "LEGAL",
        "title": "政府采购法实施条例",
        "authority": "国务院",
        "source_reference": "国务院令第658号",
        "content_summary": "全文摘要",
        "actor_id": "33333333-3333-3333-3333-333333333333",
        "object_key": "knowledge/legal/abc.pdf",
        "file_name": "abc.pdf",
        "mime_type": "application/pdf",
    }


@pytest.mark.asyncio
async def test_run_knowledge_pipeline_success(base_kwargs):
    """成功路径：download → ingest → parse_status=READY。"""
    from app.worker import run_knowledge_pipeline

    mock_result = MagicMock()
    mock_result.chunk_count = 5
    mock_result.knowledge_entry_id = "44444444-4444-4444-4444-444444444444"
    mock_result.knowledge_version_id = "55555555-5555-5555-5555-555555555555"

    with patch(
        "app.services.document_ingest.download_document", return_value="/tmp/test.pdf"
    ), patch(
        "app.worker.ingest_knowledge_document",
        return_value=mock_result,
    ) as mock_ingest, patch(
        "app.worker._update_knowledge_version_status"
    ) as mock_update, patch(
        "app.services.document_ingest.cleanup_temp_file"
    ) as mock_cleanup:
        result = await run_knowledge_pipeline(**base_kwargs)

    mock_ingest.assert_called_once()
    assert result["status"] == "completed"
    assert result["chunk_count"] == 5
    assert result["knowledge_entry_id"] == "44444444-4444-4444-4444-444444444444"
    # ingest 被以正确参数调用
    ingest_kwargs = mock_ingest.call_args.kwargs
    assert ingest_kwargs["chunk_type"] == "LEGAL"
    assert str(ingest_kwargs["knowledge_version_id"]) == base_kwargs["knowledge_version_id"]
    # parse_status 更新为 READY
    mock_update.assert_called_once()
    args = mock_update.call_args.args
    assert str(args[0]) == "11111111-1111-1111-1111-111111111111"
    assert args[1] == "READY"
    # temp 文件清理
    mock_cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_run_knowledge_pipeline_ingest_failure_marks_failed(base_kwargs):
    """ingest 抛错 → 标记失败并继续抛出，使 ARQ 可以重试。"""
    from app.worker import run_knowledge_pipeline

    with patch(
        "app.services.document_ingest.download_document", return_value="/tmp/test.pdf"
    ), patch(
        "app.worker.ingest_knowledge_document",
        side_effect=RuntimeError("ingest failed"),
    ), patch(
        "app.worker._update_knowledge_version_status"
    ) as mock_update, patch(
        "app.services.document_ingest.cleanup_temp_file"
    ):
        with pytest.raises(RuntimeError, match="ingest failed"):
            await run_knowledge_pipeline(**base_kwargs)
    # parse_status 更新为 FAILED
    assert mock_update.call_args.args[1] == "FAILED"


@pytest.mark.asyncio
async def test_run_knowledge_pipeline_download_failure_marks_failed(base_kwargs):
    """download_document 返回 None → 标记失败并抛出，使 ARQ 可以重试。"""
    from app.worker import run_knowledge_pipeline

    with patch(
        "app.services.document_ingest.download_document", return_value=None
    ), patch(
        "app.worker.ingest_knowledge_document"
    ) as mock_ingest, patch(
        "app.worker._update_knowledge_version_status"
    ) as mock_update, patch(
        "app.services.document_ingest.cleanup_temp_file"
    ):
        with pytest.raises(RuntimeError, match="failed to download"):
            await run_knowledge_pipeline(**base_kwargs)
    # ingest 不应被调用（避免在没文件时跑）
    mock_ingest.assert_not_called()
    # parse_status 仍更新为 FAILED
    assert mock_update.call_args.args[1] == "FAILED"


@pytest.mark.asyncio
async def test_run_knowledge_pipeline_cleans_temp_file_on_success(base_kwargs):
    """成功路径必须清理 temp 文件（不泄漏）。"""
    from app.worker import run_knowledge_pipeline

    mock_result = MagicMock()
    mock_result.chunk_count = 1
    mock_result.knowledge_entry_id = "x"
    mock_result.knowledge_version_id = "y"

    with patch(
        "app.services.document_ingest.download_document", return_value="/tmp/abcdef.pdf"
    ), patch(
        "app.worker.ingest_knowledge_document",
        return_value=mock_result,
    ), patch(
        "app.worker._update_knowledge_version_status"
    ), patch(
        "app.services.document_ingest.cleanup_temp_file"
    ) as mock_cleanup:
        await run_knowledge_pipeline(**base_kwargs)

    # 第一个位置参数应是被下载的临时文件路径
    assert mock_cleanup.call_args.args[0] == "/tmp/abcdef.pdf"


@pytest.mark.asyncio
async def test_worker_settings_includes_knowledge_task():
    """WorkerSettings.functions 应包含 run_knowledge_pipeline。"""
    from app.worker import WorkerSettings, run_knowledge_pipeline

    assert run_knowledge_pipeline in WorkerSettings.functions
