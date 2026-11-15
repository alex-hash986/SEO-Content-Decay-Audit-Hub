"""Offline B2B examples for SEO & Content Decay Audit Hub."""

from datetime import date, timedelta
from typing import NamedTuple

from sqlalchemy import false, select, text, update
from sqlalchemy.orm import Session

from app.models import ImportBatch, PageMetricSnapshot, Project, QuarantineRecord, QueryPageMetric
from app.schemas import ImportPreset


class PageExample(NamedTuple):
    url: str
    title: str
    previous: tuple[int, int, float]  # Clicks, impressions, position.
    current: tuple[int, int, float]


class QueryExample(NamedTuple):
    url: str
    clicks: int
    impressions: int
    position: float
    intent: str = "informational"


CYBERNEURO_PAGES = (
    PageExample("/blog/ai-agents-b2b-sales-2024", "AI-агенты в B2B-продажах", (2450, 30000, 2.1), (780, 21000, 8.9)),
    PageExample("/guides/rag-architecture-enterprise", "Архитектура Enterprise RAG", (1820, 19000, 3.4), (640, 14000, 11.2)),
    PageExample("/blog/getcourse-n8n-automation", "Автоматизация GetCourse через n8n", (1510, 15600, 4.2), (510, 11000, 12.8)),
    PageExample("/guides/ai-agent-security-checklist", "Чек-лист безопасности AI-агентов", (1040, 12500, 4.8), (380, 9000, 11.4)),
    PageExample("/blog/end-to-end-analytics-guide", "Сквозная аналитика маркетинга", (950, 15500, 5.0), (610, 13800, 10.0)),
    PageExample("/guides/llm-cost-model", "Как оценить стоимость LLM в продакшене", (820, 13000, 6.0), (530, 11900, 10.8)),
    PageExample("/services/rag-integration", "Интеграция корпоративных RAG-систем", (710, 9500, 4.0), (450, 8800, 8.8)),
    PageExample("/blog/lead-scoring-with-llm", "Lead scoring с LLM для B2B", (640, 11500, 5.0), (415, 10600, 10.6)),
    PageExample("/guides/n8n-crm-workflows", "Сценарии n8n для CRM", (520, 9100, 5.5), (450, 9000, 8.3)),
    PageExample("/services/marketing-analytics", "Внедрение маркетинговой аналитики", (430, 7800, 4.0), (370, 8200, 6.6)),
    PageExample("/blog/ai-knowledge-base-playbook", "База знаний для AI-ассистента", (350, 6600, 6.4), (295, 6250, 9.8)),
    PageExample("/services/ai-agents", "Внедрение AI-агентов для бизнеса", (200, 8000, 2.1), (200, 8000, 2.1)),
    PageExample("/guides/rag-evaluation-metrics", "Метрики качества RAG", (56, 4000, 3.4), (56, 4000, 3.4)),
    PageExample("/blog/ai-marketing-stack", "Стек AI-инструментов для маркетинга", (174, 6000, 4.2), (174, 6000, 4.2)),
    PageExample("/cases/crm-roi-reconciliation", "Кейс: сверка ROI маркетинга и CRM", (330, 5400, 5.3), (470, 6700, 4.5)),
    PageExample("/cases/rag-support-desk", "Кейс: RAG-ассистент для службы поддержки", (230, 4600, 7.1), (410, 6500, 5.4)),
    PageExample("/cases/ai-agent-qualification", "Кейс: AI-агент для квалификации лидов", (0, 0, 5.3), (320, 5300, 5.3)),
    PageExample("/cases/n8n-invoice-routing", "Кейс: маршрутизация счетов через n8n", (272, 5500, 7.4), (278, 5600, 7.3)),
)
CYBERNEURO_CLUSTERS = (
    ("внедрение ai агентов в продажи", (
        QueryExample("/services/ai-agents", 98, 620, 3.1, "commercial"),
        QueryExample("/blog/ai-agents-b2b-sales-2024", 61, 510, 7.0),
        QueryExample("/cases/ai-agent-qualification", 20, 220, 9.2),
    )),
    ("сквозная аналитика маркетинг crm", (
        QueryExample("/cases/crm-roi-reconciliation", 130, 960, 4.1),
        QueryExample("/blog/end-to-end-analytics-guide", 81, 790, 7.3),
    )),
    ("rag система для бизнеса", (
        QueryExample("/services/rag-integration", 125, 1050, 4.0, "commercial"),
        QueryExample("/guides/rag-architecture-enterprise", 47, 800, 12.0),
        QueryExample("/cases/rag-support-desk", 21, 440, 14.1),
    )),
    ("n8n интеграция с crm", (
        QueryExample("/guides/n8n-crm-workflows", 68, 790, 6.3),
        QueryExample("/blog/getcourse-n8n-automation", 54, 660, 8.9),
    )),
    ("метрики оценки rag", (
        QueryExample("/guides/rag-evaluation-metrics", 24, 450, 3.6),
        QueryExample("/blog/ai-knowledge-base-playbook", 15, 290, 7.9),
    )),
)
CYBERNEURO_SINGLE_QUERIES = (
    "ai sales playbook 2024", "enterprise rag архитектура", "автоматизация getcourse через n8n",
    "чек лист безопасности ai агента", "сквозная аналитика b2b пример", "калькулятор затрат llm",
    "интеграция rag с sharepoint", "оценка лидов llm", "n8n автоматизация crm",
    "агентство маркетинговой аналитики", "база знаний ai playbook", "ai агенты под ключ",
    "rag evaluation recall", "ai инструменты маркетинга b2b",
)

FLOWSTACK_PAGES = (
    PageExample("/comparisons/flowstack-vs-notion", "FlowStack vs Notion for Ops", (1700, 21000, 2.5), (510, 14300, 11.3)),
    PageExample("/comparisons/flowstack-vs-asana", "FlowStack vs Asana for B2B teams", (1250, 17000, 3.8), (440, 12200, 10.7)),
    PageExample("/comparisons/flowstack-vs-monday", "FlowStack vs Monday for RevOps", (800, 14000, 4.8), (490, 11500, 9.4)),
    PageExample("/blog/operations-kpi-framework", "An operations KPI framework", (640, 10300, 6.1), (430, 9800, 10.0)),
    PageExample("/blog/quarterly-business-review-guide", "How to run a quarterly business review", (510, 7800, 5.5), (435, 7600, 8.3)),
    PageExample("/blog/revops-operating-model", "A scalable RevOps operating model", (380, 6400, 6.1), (323, 6500, 9.0)),
    PageExample("/templates/qbr-review-template", "Quarterly business review template", (160, 8200, 2.1), (166, 8400, 2.1)),
    PageExample("/templates/revops-okr-tracker", "RevOps OKR tracker", (134, 5300, 3.6), (134, 5400, 3.6)),
    PageExample("/templates/approval-workflow", "Approval workflow template", (100, 3500, 4.0), (103, 3600, 4.0)),
    PageExample("/templates/customer-onboarding", "B2B customer onboarding checklist", (200, 3200, 5.2), (215, 3360, 5.0)),
    PageExample("/docs/approvals", "Configure workflow approvals", (250, 4100, 5.7), (384, 5900, 4.7)),
    PageExample("/docs/automation/trigger-rules", "Trigger rules for workflow automation", (160, 2600, 6.3), (315, 4400, 4.5)),
    PageExample("/docs/releases/2025-04", "April release: audit logs and exports", (0, 0, 4.4), (290, 3800, 4.4)),
    PageExample("/docs/releases/2025-05", "May release: approvals and workspaces", (0, 0, 4.1), (312, 4050, 4.1)),
    PageExample("/docs/reports/qbr", "Build a QBR dashboard", (160, 3200, 7.1), (170, 3200, 7.1)),
    PageExample("/blog/customer-onboarding-operations", "Operationalize B2B customer onboarding", (208, 4100, 6.4), (210, 4170, 6.5)),
)
FLOWSTACK_CLUSTERS = (
    ("quarterly business review template", (
        QueryExample("/templates/qbr-review-template", 72, 960, 4.0, "commercial"),
        QueryExample("/blog/quarterly-business-review-guide", 51, 710, 6.5),
        QueryExample("/docs/reports/qbr", 28, 450, 8.6),
    )),
    ("flowstack approval workflow", (
        QueryExample("/docs/approvals", 91, 880, 3.7),
        QueryExample("/templates/approval-workflow", 39, 520, 11.4),
        QueryExample("/docs/automation/trigger-rules", 22, 395, 13.0),
    )),
    ("revops okr tracker", (
        QueryExample("/templates/revops-okr-tracker", 56, 750, 4.6),
        QueryExample("/blog/revops-operating-model", 44, 650, 7.7),
    )),
    ("b2b customer onboarding process", (
        QueryExample("/templates/customer-onboarding", 82, 980, 5.3),
        QueryExample("/blog/customer-onboarding-operations", 71, 890, 6.6),
    )),
)
FLOWSTACK_SINGLE_QUERIES = (
    "flowstack notion comparison", "flowstack vs asana features", "monday alternative for operations",
    "b2b ops kpi examples", "qbr agenda guide", "scale a revops team", "download qbr template",
    "okr spreadsheet revops", "workflow approval template download", "onboarding checklist saas",
    "flowstack configure approval steps", "workflow automation trigger examples",
)


def _make_pages(examples: tuple[PageExample, ...]) -> list[PageMetricSnapshot]:
    pages = []
    for index, example in enumerate(examples):
        prev_clicks, prev_impressions, prev_position = example.previous
        curr_clicks, curr_impressions, curr_position = example.current
        published_at = date(2024, 1, 15) + timedelta(days=index * 31)
        if example.url.startswith("/docs/releases/"):
            published_at = date.fromisoformat(example.url.rsplit("/", 1)[1] + "-01")
        pages.append(PageMetricSnapshot(
            url=example.url, page_title=example.title, content_group=example.url.split("/")[1],
            published_at=published_at, last_updated_at=published_at + timedelta(days=14),
            prev_clicks=prev_clicks, prev_impressions=prev_impressions, prev_position=prev_position,
            prev_ctr=prev_clicks / prev_impressions if prev_impressions else 0.0,
            curr_clicks=curr_clicks, curr_impressions=curr_impressions, curr_position=curr_position,
            curr_ctr=curr_clicks / curr_impressions if curr_impressions else 0.0,
        ))
    return pages


def _make_queries(
    pages: tuple[PageExample, ...],
    clusters: tuple[tuple[str, tuple[QueryExample, ...]], ...],
    single_queries: tuple[str, ...],
) -> list[QueryPageMetric]:
    pages_by_url = {page.url: page for page in pages}
    examples = [(term, hit) for term, hits in clusters for hit in hits]
    for index, term in enumerate(single_queries):
        page = pages[index]
        clicks = min(18 + index * 2, page.current[0] // 8)
        examples.append((term, QueryExample(page.url, clicks, 240 + index * 23, 4.2 + index * 0.4)))

    metrics = []
    for term, hit in examples:
        page = pages_by_url[hit.url]
        prev_impressions = round(hit.impressions * page.previous[1] / page.current[1])
        prev_clicks = min(prev_impressions, round(hit.clicks * page.previous[0] / page.current[0]))
        metrics.append(QueryPageMetric(
            query=term, url=hit.url, intent=hit.intent,
            curr_clicks=hit.clicks, curr_impressions=hit.impressions, curr_position=hit.position,
            curr_ctr=hit.clicks / hit.impressions, prev_clicks=prev_clicks, prev_impressions=prev_impressions,
        ))
    return metrics


def _add_sample_import(
    project: Project, preset: ImportPreset, filename: str, rejected: list[tuple[dict[str, str], str]],
) -> None:
    valid = len(project.page_snapshots)
    batch = ImportBatch(
        dataset_type="pages", preset=preset, filename=filename,
        total_rows=valid + len(rejected), valid_rows=valid, quarantined_rows=len(rejected),
    )
    project.import_batches.append(batch)
    for number, (row, reason) in enumerate(rejected, start=valid + 1):
        project.quarantine_records.append(QuarantineRecord(
            batch=batch, dataset_type="pages", row_number=number, raw_payload=row, error_reason=reason,
        ))


def _make_demo_projects() -> list[Project]:
    cyberneuro = Project(
        name="CyberNeuro Blog & AI Agency", domain="cyberneuro.ru", slug="cyberneuro-blog-ai-agency",
        description="B2B-блог и агентство по внедрению AI-агентов, RAG-систем и сквозной маркетинговой аналитики",
        currency="RUB", default_cpc=240.0, conversion_rate=0.022, avg_lead_value=45000.0, is_demo=True,
        page_snapshots=_make_pages(CYBERNEURO_PAGES),
        query_metrics=_make_queries(CYBERNEURO_PAGES, CYBERNEURO_CLUSTERS, CYBERNEURO_SINGLE_QUERIES),
    )
    _add_sample_import(cyberneuro, "yandex_webmaster", "cyberneuro-yandex-pages.csv", [
        ({"Адрес страницы": "", "Клики (стало)": "12"}, "url is required"),
        ({"Адрес страницы": "/blog/n8n-roundup", "Клики (стало)": "-8"}, "curr_clicks must be a non-negative 32-bit integer"),
        ({"Адрес страницы": "/guides/agent-observability", "CTR (%)": "145%"}, "curr_ctr must be between 0 and 1 after normalization"),
        ({"Адрес страницы": "/services/ai-readiness", "Средняя позиция": "0"}, "curr_position must be between 1 and 200 when supplied"),
    ])
    flowstack = Project(
        name="FlowStack B2B SaaS Knowledge Base", domain="flowstack.io", slug="flowstack-b2b-saas-kb",
        description="База знаний, документация и шаблоны процессов для B2B SaaS-платформы операционного управления",
        currency="USD", default_cpc=4.50, conversion_rate=0.03, avg_lead_value=600.0, is_demo=True,
        page_snapshots=_make_pages(FLOWSTACK_PAGES),
        query_metrics=_make_queries(FLOWSTACK_PAGES, FLOWSTACK_CLUSTERS, FLOWSTACK_SINGLE_QUERIES),
    )
    _add_sample_import(flowstack, "gsc", "flowstack-gsc-pages.csv", [
        ({"Top pages": "", "Clicks": "8", "Impressions": "160"}, "url is required"),
        ({"Top pages": "/docs/audit-logs", "Clicks": "41", "Impressions": "19"}, "curr_clicks cannot exceed curr_impressions"),
        ({"Top pages": "/docs/account-mapping", "Position": "N/A"}, "curr_position must be a number"),
    ])
    return [cyberneuro, flowstack]


class DemoSlugConflict(ValueError):
    """A user project occupies a slug needed by a demo project."""


def seed_demo_projects(db: Session, force_reset: bool = False) -> list[Project]:
    """Stage demo rows in the caller's transaction and return the newly created projects."""
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        db.execute(text("LOCK TABLE projects IN SHARE ROW EXCLUSIVE MODE"))
    elif dialect == "sqlite":
        # Acquire SQLite's write lock before testing emptiness or checking slugs.
        db.execute(update(Project).where(false()).values(is_demo=Project.is_demo))

    if not force_reset and db.scalar(select(Project.id).limit(1)) is not None:
        return []

    projects = _make_demo_projects()
    if force_reset:
        conflicting_slug = db.scalar(select(Project.slug).where(
            Project.is_demo.is_(False), Project.slug.in_(project.slug for project in projects),
        ))
        if conflicting_slug is not None:
            raise DemoSlugConflict(f"Demo slug is already used by a user project: {conflicting_slug}")
        for existing in db.scalars(select(Project).where(Project.is_demo.is_(True))):
            db.delete(existing)
        db.flush()

    db.add_all(projects)
    db.flush()
    return projects
