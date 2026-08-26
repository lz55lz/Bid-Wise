from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import httpx

from app.core.config import Settings


class ConnectorExecutionError(Exception):
    """A safe, non-sensitive failure raised by a configured external connector."""


@dataclass(frozen=True, slots=True)
class ConnectorExecutionResult:
    external_reference: str | None
    summary: dict[str, object]


class ConnectorExecutor(Protocol):
    def execute(
        self,
        connector_code: str,
        operation: str,
        integration_run_id: UUID,
        project_id: UUID,
        payload: dict[str, object],
    ) -> ConnectorExecutionResult: ...


class HttpConnectorExecutor:
    """Minimal, explicit connector contract used only from the worker.

    The configured base URL must expose POST /operations/lookup and/or
    POST /operations/export. The adapter stores no request body or raw response
    in PostgreSQL; only a small allow-listed execution summary is retained.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def execute(
        self,
        connector_code: str,
        operation: str,
        integration_run_id: UUID,
        project_id: UUID,
        payload: dict[str, object],
    ) -> ConnectorExecutionResult:
        base_url, api_key = self._configuration_for(connector_code)
        if not base_url:
            raise ConnectorExecutionError("连接器尚未完成部署配置")
        url = f"{base_url.rstrip('/')}/operations/{operation.lower()}"
        headers = {"X-Integration-Run-ID": str(integration_run_id)}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=False
            ) as client:
                response = client.post(
                    url,
                    headers=headers,
                    json={
                        "integration_run_id": str(integration_run_id),
                        "project_id": str(project_id),
                        "operation": operation,
                        "payload": payload,
                    },
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ConnectorExecutionError("外部连接器请求失败") from exc
        return self._response_summary(response)

    def _configuration_for(self, connector_code: str) -> tuple[str | None, str | None]:
        configurations = {
            "ERP": (
                self._settings.erp_integration_base_url,
                self._settings.erp_integration_api_key,
            ),
            "CRM": (
                self._settings.crm_integration_base_url,
                self._settings.crm_integration_api_key,
            ),
            "PUBLIC_RESOURCE": (
                self._settings.public_resource_integration_base_url,
                self._settings.public_resource_integration_api_key,
            ),
        }
        base_url, api_key = configurations.get(connector_code, (None, None))
        return base_url, api_key.get_secret_value() if api_key else None

    @staticmethod
    def _response_summary(response: httpx.Response) -> ConnectorExecutionResult:
        reference = response.headers.get("x-request-id") or response.headers.get("x-correlation-id")
        response_kind = "empty"
        result_keys: list[str] = []
        try:
            response_body: Any = response.json()
        except ValueError:
            response_body = None
            response_kind = "non_json"
        if isinstance(response_body, dict):
            response_kind = "object"
            result_keys = sorted(str(key) for key in response_body.keys())[:20]
            candidate = response_body.get("external_reference") or response_body.get("reference")
            if isinstance(candidate, str) and candidate.strip():
                reference = candidate.strip()[:256]
        elif isinstance(response_body, list):
            response_kind = "list"
        return ConnectorExecutionResult(
            external_reference=reference[:256] if reference else None,
            summary={
                "accepted": True,
                "http_status": response.status_code,
                "response_kind": response_kind,
                "result_keys": result_keys,
            },
        )
