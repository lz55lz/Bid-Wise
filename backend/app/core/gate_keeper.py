"""GateKeeper — 阶段门控，确保 pipeline 顺序执行

借鉴：BidMaster-Pro core/agent_engine/gate_keeper.py

用法：
    keeper = GateKeeper(projects_root="./data/projects")

    # 检查前置阶段是否完成
    keeper.is_passed(project_id, "extract")  # False

    # 标记阶段完成
    keeper.mark_passed(project_id, "parse", "system", notes="自动通过")

    # 执行前检查
    keeper.require_stages(project_id, ["parse", "clean"])
    # 如果前置未完成，抛出 GateNotPassedError

每个阶段完成后写：
    projects_root/project_id/.stages/parse/.reviewed
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class GateNotPassedError(Exception):
    """阶段门控未通过异常"""

    def __init__(self, project_id: str, stage: str, missing: list[str]):
        self.project_id = project_id
        self.stage = stage
        self.missing = missing
        super().__init__(
            f"Stage '{stage}' requires {missing}, but not all are passed for project {project_id}"
        )


class GateKeeper:
    """阶段门控器

    每个 pipeline 阶段完成后写一个 .reviewed 文件：
        projects_root/project_id/.stages/stage_name/.reviewed

    文件内容：
        {
            "stage": "parse",
            "reviewer": "system",
            "timestamp": "2024-01-01T00:00:00Z",
            "notes": "自动通过"
        }
    """

    def __init__(self, projects_root: Path | str | None = None) -> None:
        self.projects_root = Path(projects_root) if projects_root else Path("./data/projects")

    # --------------------------------------------------------------------------
    # 查询
    # --------------------------------------------------------------------------

    def is_passed(self, project_id: str, stage: str) -> bool:
        """检查阶段是否已通过审核"""
        gate_file = self._gate_file(project_id, stage)
        return gate_file.exists()

    def get_gate_info(self, project_id: str, stage: str) -> dict | None:
        """获取阶段审核信息"""
        gate_file = self._gate_file(project_id, stage)
        if not gate_file.exists():
            return None
        try:
            return json.loads(gate_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception):
            return None

    def list_passed_stages(self, project_id: str) -> list[str]:
        """列出已通过的阶段"""
        stages_dir = self.projects_root / project_id / ".stages"
        if not stages_dir.exists():
            return []
        return sorted([
            d.name for d in stages_dir.iterdir()
            if d.is_dir() and (d / ".reviewed").exists()
        ])

    # --------------------------------------------------------------------------
    # 修改
    # --------------------------------------------------------------------------

    def mark_passed(
        self,
        project_id: str,
        stage: str,
        reviewer: str = "system",
        notes: str = "",
    ) -> None:
        """标记阶段已通过"""
        gate_file = self._gate_file(project_id, stage)
        try:
            gate_file.parent.mkdir(parents=True, exist_ok=True)
            gate_data = {
                "stage": stage,
                "reviewer": reviewer,
                "timestamp": datetime.now(UTC).isoformat(),
                "notes": notes,
            }
            gate_file.write_text(json.dumps(gate_data, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"[GateKeeper] {project_id}/{stage} marked passed by {reviewer}")
        except OSError as e:
            raise GateWriteError(f"写入闸门文件失败: {e}") from e

    def reset(self, project_id: str, stage: str) -> None:
        """重置阶段（删除审核标记）"""
        gate_file = self._gate_file(project_id, stage)
        if gate_file.exists():
            gate_file.unlink()
            logger.info(f"[GateKeeper] {project_id}/{stage} reset")

    def reset_all(self, project_id: str) -> None:
        """重置项目所有阶段"""
        stages_dir = self.projects_root / project_id / ".stages"
        if stages_dir.exists():
            for d in stages_dir.iterdir():
                if d.is_dir():
                    gate_file = d / ".reviewed"
                    if gate_file.exists():
                        gate_file.unlink()
            logger.info(f"[GateKeeper] {project_id} all stages reset")

    # --------------------------------------------------------------------------
    # 检查
    # --------------------------------------------------------------------------

    def require_stages(self, project_id: str, required: list[str]) -> None:
        """确保前置阶段都已通过

        Raises:
            GateNotPassedError: 前置阶段未完成
        """
        passed = set(self.list_passed_stages(project_id))
        missing = [s for s in required if s not in passed]
        if missing:
            # 找第一个缺失阶段的触发者
            first_missing = missing[0]
            raise GateNotPassedError(project_id, first_missing, missing)

    def require_stage(self, project_id: str, stage: str) -> None:
        """确保单个阶段已通过"""
        if not self.is_passed(project_id, stage):
            raise GateNotPassedError(project_id, stage, [stage])

    # --------------------------------------------------------------------------
    # 内部
    # --------------------------------------------------------------------------

    def _gate_file(self, project_id: str, stage: str) -> Path:
        """获取阶段闸门文件路径"""
        return self.projects_root / project_id / ".stages" / stage / ".reviewed"


class GateWriteError(Exception):
    """闸门文件写入失败"""
    pass
