from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AgentRecommendation,
    AgentRecommendationEvidence,
    AgentRun,
    AgentRunEvidence,
    AgentRunStep,
    ChallengeDraft,
    CompetitiveAnalysis,
    CompetitiveFinding,
    GraphEdge,
    GraphNode,
    IntegrationConnector,
    IntegrationRun,
    KnowledgeEntry,
    KnowledgeVersion,
    MarketCheck,
    Notification,
    ProjectComment,
    QuoteScenario,
    WorkItem,
)


class AdvancedRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, entity: object) -> None:
        self._session.add(entity)

    def add_all(self, entities: list[object]) -> None:
        self._session.add_all(entities)

    def get_knowledge_entry(
        self, entry_id: UUID, *, for_update: bool = False
    ) -> KnowledgeEntry | None:
        statement = select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def get_knowledge_version(self, version_id: UUID) -> KnowledgeVersion | None:
        return self._session.get(KnowledgeVersion, version_id)

    def get_knowledge_version_by_source_document_version(
        self, document_version_id: UUID
    ) -> KnowledgeVersion | None:
        return self._session.scalar(
            select(KnowledgeVersion).where(
                KnowledgeVersion.source_document_version_id == document_version_id
            )
        )

    def latest_knowledge_version(self, entry_id: UUID) -> KnowledgeVersion | None:
        return self._session.scalar(
            select(KnowledgeVersion)
            .where(KnowledgeVersion.knowledge_entry_id == entry_id)
            .order_by(KnowledgeVersion.version_no.desc())
            .limit(1)
        )

    def next_knowledge_version(self, entry_id: UUID) -> int:
        value = self._session.scalar(
            select(func.max(KnowledgeVersion.version_no)).where(
                KnowledgeVersion.knowledge_entry_id == entry_id
            )
        )
        return int(value or 0) + 1

    def delete_knowledge_entry(self, entry_id: UUID) -> bool:
        entry = self.get_knowledge_entry(entry_id)
        if not entry:
            return False
        self._session.execute(
            KnowledgeEntry.__table__.delete().where(KnowledgeEntry.id == entry_id)
        )
        return True

    def list_knowledge(
        self, *, published_only: bool, query: str | None = None
    ) -> list[tuple[KnowledgeEntry, KnowledgeVersion]]:
        # Subquery: latest version_no per entry
        latest_version_subq = (
            select(
                KnowledgeVersion.knowledge_entry_id,
                func.max(KnowledgeVersion.version_no).label("max_version_no"),
            )
            .group_by(KnowledgeVersion.knowledge_entry_id)
            .subquery()
        )
        # Main query: entry + version, filtered to latest version per entry
        statement = (
            select(KnowledgeEntry, KnowledgeVersion)
            .select_from(KnowledgeEntry)
            .join(
                KnowledgeVersion,
                KnowledgeVersion.knowledge_entry_id == KnowledgeEntry.id,
            )
            .join(
                latest_version_subq,
                latest_version_subq.c.knowledge_entry_id == KnowledgeEntry.id,
            )
            .where(
                KnowledgeEntry.deleted_at.is_(None),
                KnowledgeVersion.version_no == latest_version_subq.c.max_version_no,
            )
        )
        if published_only:
            statement = statement.where(KnowledgeVersion.status == "PUBLISHED")
        if query:
            pattern = f"%{query}%"
            statement = statement.where(
                KnowledgeEntry.title.ilike(pattern) | KnowledgeVersion.content.ilike(pattern)
            )
        statement = statement.order_by(KnowledgeEntry.title)
        return list(self._session.execute(statement).tuples())

    def list_published_manual_knowledge(
        self, *, limit: int
    ) -> list[tuple[KnowledgeEntry, KnowledgeVersion, UUID]]:
        statement = (
            select(
                KnowledgeEntry,
                KnowledgeVersion,
                KnowledgeVersion.source_evidence_id,
            )
            .where(
                KnowledgeEntry.deleted_at.is_(None),
                KnowledgeEntry.knowledge_type.in_(("LEGAL", "CASE")),
                KnowledgeVersion.knowledge_entry_id == KnowledgeEntry.id,
                KnowledgeVersion.status == "PUBLISHED",
                KnowledgeVersion.source_document_version_id.is_(None),
                KnowledgeVersion.source_evidence_id.is_not(None),
            )
            .order_by(KnowledgeVersion.published_at.desc(), KnowledgeVersion.created_at.desc())
            .limit(limit)
        )
        return [
            (entry, version, evidence_id)
            for entry, version, evidence_id in self._session.execute(statement).tuples()
            if evidence_id is not None
        ]

    def list_published_legal_knowledge(
        self, *, limit: int
    ) -> list[tuple[KnowledgeEntry, KnowledgeVersion, UUID]]:
        statement = (
            select(KnowledgeEntry, KnowledgeVersion, KnowledgeVersion.source_evidence_id)
            .where(
                KnowledgeEntry.deleted_at.is_(None),
                KnowledgeEntry.knowledge_type == "LEGAL",
                KnowledgeVersion.knowledge_entry_id == KnowledgeEntry.id,
                KnowledgeVersion.status == "PUBLISHED",
                KnowledgeVersion.source_evidence_id.is_not(None),
            )
            .order_by(KnowledgeVersion.published_at.desc(), KnowledgeVersion.created_at.desc())
            .limit(limit)
        )
        return [
            (entry, version, evidence_id)
            for entry, version, evidence_id in self._session.execute(statement).tuples()
            if evidence_id is not None
        ]

    def get_analysis(self, analysis_id: UUID) -> CompetitiveAnalysis | None:
        return self._session.get(CompetitiveAnalysis, analysis_id)

    def list_analyses(self, project_id: UUID) -> list[CompetitiveAnalysis]:
        return list(
            self._session.scalars(
                select(CompetitiveAnalysis)
                .where(CompetitiveAnalysis.project_id == project_id)
                .order_by(CompetitiveAnalysis.created_at.desc())
            )
        )

    def list_findings(self, analysis_id: UUID) -> list[CompetitiveFinding]:
        return list(
            self._session.scalars(
                select(CompetitiveFinding)
                .where(CompetitiveFinding.analysis_id == analysis_id)
                .order_by(CompetitiveFinding.created_at, CompetitiveFinding.id)
            )
        )

    def get_finding(
        self, finding_id: UUID, *, for_update: bool = False
    ) -> CompetitiveFinding | None:
        statement = select(CompetitiveFinding).where(CompetitiveFinding.id == finding_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def get_challenge(
        self, challenge_id: UUID, *, for_update: bool = False
    ) -> ChallengeDraft | None:
        statement = select(ChallengeDraft).where(ChallengeDraft.id == challenge_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def list_challenges(self, project_id: UUID) -> list[ChallengeDraft]:
        return list(
            self._session.scalars(
                select(ChallengeDraft)
                .where(ChallengeDraft.project_id == project_id)
                .order_by(ChallengeDraft.created_at.desc())
            )
        )

    def get_quote(self, scenario_id: UUID, *, for_update: bool = False) -> QuoteScenario | None:
        statement = select(QuoteScenario).where(QuoteScenario.id == scenario_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def list_quotes(self, project_id: UUID) -> list[QuoteScenario]:
        return list(
            self._session.scalars(
                select(QuoteScenario)
                .where(QuoteScenario.project_id == project_id)
                .order_by(QuoteScenario.name, QuoteScenario.version_no.desc())
            )
        )

    def next_quote_version(self, project_id: UUID, name: str) -> int:
        value = self._session.scalar(
            select(func.max(QuoteScenario.version_no)).where(
                QuoteScenario.project_id == project_id, QuoteScenario.name == name
            )
        )
        return int(value or 0) + 1

    def list_comments(self, project_id: UUID) -> list[ProjectComment]:
        return list(
            self._session.scalars(
                select(ProjectComment)
                .where(ProjectComment.project_id == project_id)
                .order_by(ProjectComment.created_at.desc())
            )
        )

    def get_work_item(self, work_item_id: UUID, *, for_update: bool = False) -> WorkItem | None:
        statement = select(WorkItem).where(WorkItem.id == work_item_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def list_work_items(self, project_id: UUID) -> list[WorkItem]:
        return list(
            self._session.scalars(
                select(WorkItem)
                .where(WorkItem.project_id == project_id)
                .order_by(WorkItem.status, WorkItem.due_at, WorkItem.created_at.desc())
            )
        )

    def list_notifications(self, user_id: UUID) -> list[Notification]:
        return list(
            self._session.scalars(
                select(Notification)
                .where(Notification.user_id == user_id)
                .order_by(Notification.read_at, Notification.created_at.desc())
            )
        )

    def get_notification(self, notification_id: UUID, user_id: UUID) -> Notification | None:
        return self._session.scalar(
            select(Notification).where(
                Notification.id == notification_id, Notification.user_id == user_id
            )
        )

    def list_market_checks(self, project_id: UUID) -> list[MarketCheck]:
        return list(
            self._session.scalars(
                select(MarketCheck)
                .where(MarketCheck.project_id == project_id)
                .order_by(MarketCheck.created_at.desc())
            )
        )

    def list_graph_nodes(self, project_id: UUID) -> list[GraphNode]:
        return list(
            self._session.scalars(
                select(GraphNode)
                .where(GraphNode.project_id == project_id)
                .order_by(GraphNode.entity_type, GraphNode.label)
            )
        )

    def list_graph_edges(self, project_id: UUID) -> list[GraphEdge]:
        return list(
            self._session.scalars(
                select(GraphEdge)
                .where(GraphEdge.project_id == project_id)
                .order_by(GraphEdge.relation_type, GraphEdge.id)
            )
        )

    def get_graph_node(
        self, project_id: UUID, entity_type: str, source_object_id: str
    ) -> GraphNode | None:
        return self._session.scalar(
            select(GraphNode).where(
                GraphNode.project_id == project_id,
                GraphNode.entity_type == entity_type,
                GraphNode.source_object_id == source_object_id,
            )
        )

    def find_graph_edge(
        self, project_id: UUID, from_node_id: UUID, to_node_id: UUID, relation_type: str
    ) -> GraphEdge | None:
        return self._session.scalar(
            select(GraphEdge).where(
                GraphEdge.project_id == project_id,
                GraphEdge.from_node_id == from_node_id,
                GraphEdge.to_node_id == to_node_id,
                GraphEdge.relation_type == relation_type,
            )
        )

    def get_agent_run(self, run_id: UUID, *, for_update: bool = False) -> AgentRun | None:
        statement = select(AgentRun).where(AgentRun.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def list_agent_runs(self, project_id: UUID) -> list[AgentRun]:
        return list(
            self._session.scalars(
                select(AgentRun)
                .where(AgentRun.project_id == project_id)
                .order_by(AgentRun.created_at.desc())
            )
        )

    def latest_bid_readiness_run(self, project_id: UUID) -> AgentRun | None:
        return self._session.scalar(
            select(AgentRun)
            .where(
                AgentRun.project_id == project_id,
                AgentRun.workflow == "BID_READINESS_REVIEW",
                AgentRun.status.in_(("SUCCEEDED", "WAITING_HUMAN")),
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )

    def get_active_bid_readiness_run(self, document_version_id: UUID) -> AgentRun | None:
        return self._session.scalar(
            select(AgentRun)
            .where(
                AgentRun.source_document_version_id == document_version_id,
                AgentRun.workflow == "BID_READINESS_REVIEW",
                AgentRun.status.in_(("QUEUED", "RUNNING", "WAITING_HUMAN")),
            )
            .order_by(AgentRun.created_at.desc())
            .limit(1)
        )

    def list_agent_steps(self, agent_run_id: UUID) -> list[AgentRunStep]:
        return list(
            self._session.scalars(
                select(AgentRunStep)
                .where(AgentRunStep.agent_run_id == agent_run_id)
                .order_by(AgentRunStep.created_at, AgentRunStep.id)
            )
        )

    def list_evidence_ids(self, agent_run_id: UUID) -> list[UUID]:
        return list(
            self._session.scalars(
                select(AgentRunEvidence.evidence_id).where(
                    AgentRunEvidence.agent_run_id == agent_run_id
                )
            )
        )

    def list_agent_recommendations(self, agent_run_id: UUID) -> list[AgentRecommendation]:
        return list(
            self._session.scalars(
                select(AgentRecommendation)
                .where(AgentRecommendation.agent_run_id == agent_run_id)
                .order_by(AgentRecommendation.created_at, AgentRecommendation.id)
            )
        )

    def get_agent_recommendation(
        self, recommendation_id: UUID, *, for_update: bool = False
    ) -> AgentRecommendation | None:
        statement = select(AgentRecommendation).where(AgentRecommendation.id == recommendation_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def list_agent_recommendation_evidence_ids(self, recommendation_id: UUID) -> list[UUID]:
        return list(
            self._session.scalars(
                select(AgentRecommendationEvidence.evidence_id)
                .where(AgentRecommendationEvidence.recommendation_id == recommendation_id)
                .order_by(AgentRecommendationEvidence.evidence_id)
            )
        )

    def supersede_proposed_recommendations(self, agent_run_id: UUID) -> None:
        recommendations = self._session.scalars(
            select(AgentRecommendation)
            .where(
                AgentRecommendation.agent_run_id == agent_run_id,
                AgentRecommendation.status == "PROPOSED",
            )
            .with_for_update()
        )
        for recommendation in recommendations:
            recommendation.status = "SUPERSEDED"

    def list_agent_evidence_ids(self, agent_run_id: UUID) -> list[UUID]:
        return list(
            self._session.scalars(
                select(AgentRunEvidence.evidence_id)
                .where(AgentRunEvidence.agent_run_id == agent_run_id)
                .order_by(AgentRunEvidence.evidence_id)
            )
        )

    def get_agent_step_for_update(self, agent_run_id: UUID, step_name: str) -> AgentRunStep | None:
        return self._session.scalar(
            select(AgentRunStep)
            .where(
                AgentRunStep.agent_run_id == agent_run_id,
                AgentRunStep.step_name == step_name,
            )
            .with_for_update()
        )

    def list_connectors(self) -> list[IntegrationConnector]:
        return list(
            self._session.scalars(select(IntegrationConnector).order_by(IntegrationConnector.code))
        )

    def get_connector(self, code: str, *, for_update: bool = False) -> IntegrationConnector | None:
        statement = select(IntegrationConnector).where(IntegrationConnector.code == code)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def get_integration_run(
        self, run_id: UUID, *, for_update: bool = False
    ) -> IntegrationRun | None:
        statement = select(IntegrationRun).where(IntegrationRun.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def list_integration_runs(self, project_id: UUID) -> list[IntegrationRun]:
        return list(
            self._session.scalars(
                select(IntegrationRun)
                .where(IntegrationRun.project_id == project_id)
                .order_by(IntegrationRun.created_at.desc())
            )
        )
