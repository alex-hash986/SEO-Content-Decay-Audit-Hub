"""Management reports for SEO & Content Decay Audit Hub."""

import csv
import io
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter, quote_sheetname
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __author__
from app.models import PageMetricSnapshot, Project, QuarantineRecord, QueryPageMetric
from app.schemas import ActionPlanItem, CannibalizationCluster, CtrOpportunityItem, PageAuditItem
from app.services.audit_engine import (
    audit_pages,
    build_action_plan,
    detect_cannibalization,
    find_ctr_opportunities,
    summarize_audit,
)


SHEET_NAMES = (
    "Executive Summary", "Content Decay & Action Plan",
    "CTR Opportunities", "Cannibalization & Quarantine",
)
PLAN_HEADERS = (
    "Priority Score", "URL", "Заголовок", "Группа контента", "Статус деградации", "Decay Score",
    "Клики (было)", "Клики (стало)", "Дельта кликов", "Позиция (было → стало)", "Возвращаемые клики",
    "Эквивалент бюджета", "Прогноз выручки", "Диагноз", "Рекомендуемое действие",
)
INTEGER = "#,##0"
DECIMAL = "#,##0.00"
PERCENT = "0.0%"
HEADER_FILL = PatternFill("solid", fgColor="FF1E40AF")
HEADER_FONT = Font(name="Arial", size=10, bold=True, color="FFFFFFFF")
BODY_FONT = Font(name="Arial", size=10, color="FF0F172A")
STRIPE_FILL = PatternFill("solid", fgColor="FFF1F5F9")


@dataclass
class AuditReport:
    pages: dict[str, PageAuditItem]
    opportunities: list[CtrOpportunityItem]
    clusters: list[CannibalizationCluster]
    plan: list[ActionPlanItem]


def _load_report(db: Session, project: Project) -> AuditReport:
    pages = list(db.scalars(select(PageMetricSnapshot).where(PageMetricSnapshot.project_id == project.id)))
    queries = db.scalars(select(QueryPageMetric).where(QueryPageMetric.project_id == project.id))
    audited = audit_pages(pages)
    opportunities = find_ctr_opportunities(pages)
    clusters = detect_cannibalization(queries)
    return AuditReport(
        pages={page.url: page for page in audited},
        opportunities=opportunities,
        clusters=clusters,
        plan=build_action_plan(project, audited, opportunities, clusters),
    )


def _plan_rows(report: AuditReport) -> Iterator[tuple]:
    for action in report.plan:
        page = report.pages[action.url]
        yield (
            action.priority_score, action.url, page.page_title, page.content_group,
            page.decay_status, page.decay_score, page.prev_clicks, page.curr_clicks,
            page.clicks_delta, f"{page.prev_position:g} → {page.curr_position:g}",
            action.recoverable_clicks, action.equivalent_ad_value, action.projected_revenue_impact,
            page.decay_driver, page.recommended_action,
        )


class _Formula(str):
    """An Excel formula authored by this module."""


@dataclass
class _SheetWriter:
    sheet: Worksheet
    row: int = 0

    def append(
        self, values: tuple, *, header: bool = False, formats: dict[int, str] | None = None,
    ) -> None:
        self.row += 1
        for index, value in enumerate(values, start=1):
            if isinstance(value, str):
                cleaned = ILLEGAL_CHARACTERS_RE.sub("", value).replace("\ufffe", "�").replace("\uffff", "�")
            else:
                cleaned = value
            cell = self.sheet.cell(self.row, index, cleaned)
            if isinstance(value, str) and not isinstance(value, _Formula):
                cell.data_type = "s"
            cell.font = HEADER_FONT if header else BODY_FONT
            cell.alignment = Alignment(wrap_text=True, vertical="center" if header else "top")
            if header:
                cell.fill = HEADER_FILL
            elif self.row % 2 == 0:
                cell.fill = STRIPE_FILL
            if formats and index in formats:
                cell.number_format = formats[index]


def _format_sheet(sheet: Worksheet) -> None:
    sheet.freeze_panes = "A2"
    sheet.sheet_view.showGridLines = False
    widths = {}
    for column in sheet.columns:
        letter = get_column_letter(column[0].column)
        lengths = (
            max((len(line) for line in str(cell.value).splitlines()), default=0)
            if isinstance(cell.value, str) and cell.data_type != "f" else 16
            for cell in column if cell.value is not None
        )
        widths[letter] = min(55, max(12, max(lengths, default=0) + 3))
        sheet.column_dimensions[letter].width = widths[letter]
    for row in sheet.rows:
        lines = 1
        for cell in row:
            if cell.data_type != "s" or not cell.value:
                continue
            width = max(1, widths[cell.column_letter] - 4)
            lines = max(lines, sum(max(1, ceil(len(line) / width)) for line in cell.value.splitlines()))
        sheet.row_dimensions[row[0].row].height = min(180, 7 + 14 * lines)


def build_excel_report(db: Session, project: Project) -> bytes:
    report = _load_report(db, project)
    quarantine = list(db.scalars(
        select(QuarantineRecord)
        .where(QuarantineRecord.project_id == project.id)
        .order_by(QuarantineRecord.batch_id.desc(), QuarantineRecord.row_number, QuarantineRecord.id)
    ))
    summary = summarize_audit(
        project, list(report.pages.values()), report.opportunities, report.clusters, report.plan, len(quarantine),
    )
    with io.BytesIO() as output:
        workbook = Workbook()
        try:
            workbook.properties.creator = __author__
            workbook.properties.title = "SEO & Content Decay Audit Hub"
            executive = _SheetWriter(workbook.active)
            executive.sheet.title = SHEET_NAMES[0]
            plan, ctr, cannibalization = [_SheetWriter(workbook.create_sheet(name)) for name in SHEET_NAMES[1:]]
            executive.append(("SEO & Content Decay Audit Hub", project.name), header=True)
            executive.append(("Домен", project.domain))
            executive.append(("Дата генерации (UTC)", datetime.now(timezone.utc).date()), formats={2: "yyyy-mm-dd"})
            executive.append(("Экономика проекта", "Значение"), header=True)
            for name, value, fmt in (
                ("CPC", project.default_cpc, DECIMAL), ("Conversion Rate", project.conversion_rate, PERCENT),
                ("Avg Lead Value", project.avg_lead_value, DECIMAL), ("Валюта", project.currency, "General"),
            ):
                executive.append((name, value), formats={2: fmt})

            action_sheet = quote_sheetname(plan.sheet.title)
            last_row = len(report.plan) + 1
            cash_total = _Formula(f"=ROUND(SUM({action_sheet}!L2:L{last_row}),2)") if report.plan else 0
            revenue_total = _Formula(f"=ROUND(SUM({action_sheet}!M2:M{last_row}),2)") if report.plan else 0
            traffic_total = _Formula(f"=SUM({action_sheet}!K2:K{last_row})") if report.plan else 0
            executive.append(("KPI проекта", "Значение"), header=True)
            for name, value, fmt in (
                ("Клики (было)", summary.prev_clicks, INTEGER), ("Клики (стало)", summary.curr_clicks, INTEGER),
                ("Изменение кликов", _Formula("=IF(B10>0,ROUND((B11-B10)/B10,4),IF(B11>0,1,0))"), PERCENT),
                ("Критическая деградация", summary.pages_by_status["critical_decay"], INTEGER),
                ("Умеренная деградация", summary.pages_by_status["moderate_decay"], INTEGER),
                ("Точки роста CTR", summary.ctr_opportunities_count, INTEGER),
                ("Кластеры каннибализации", summary.cannibalization_clusters_count, INTEGER),
                ("Возвращаемые клики", traffic_total, INTEGER),
                (f"Эквивалент рекламного бюджета ({project.currency})", cash_total, DECIMAL),
                ("Прогноз лидов", summary.projected_leads, DECIMAL),
                (f"Прогноз выручки ({project.currency})", revenue_total, DECIMAL),
            ):
                executive.append((name, value), formats={2: fmt})
            executive.append(("Топ-5 приоритетных URL", "Priority Score", "Возвращаемые клики"), header=True)
            for row in report.plan[:5]:
                executive.append((row.url, row.priority_score, row.recoverable_clicks), formats={2: DECIMAL, 3: INTEGER})

            plan.append(PLAN_HEADERS, header=True)
            for row in _plan_rows(report):
                plan.append(row, formats={
                    1: DECIMAL, 6: DECIMAL, 7: INTEGER, 8: INTEGER, 9: INTEGER,
                    11: INTEGER, 12: DECIMAL, 13: DECIMAL,
                })
            plan.sheet.auto_filter.ref = plan.sheet.dimensions

            ctr.append(("URL", "Текущая позиция", "Показы", "Фактический CTR %", "Ожидаемый CTR %",
                          "Разрыв CTR %", "Потенциал доп. кликов", "Snippet Score"), header=True)
            for row in report.opportunities:
                ctr.append((
                    row.url, row.curr_position, row.curr_impressions, row.curr_ctr, row.expected_ctr,
                    row.ctr_gap, row.potential_extra_clicks, row.snippet_opportunity_score,
                ), formats={2: DECIMAL, 3: INTEGER, 4: PERCENT, 5: PERCENT, 6: PERCENT, 7: INTEGER, 8: DECIMAL})
            ctr.sheet.auto_filter.ref = ctr.sheet.dimensions

            cannibalization.append(("Запрос", "Cannibalization Score", "Основной URL", "Конкурирующие URL",
                                     "Потерянные клики", "Стратегия исправления", "Рекомендация"), header=True)
            for cluster in report.clusters:
                cannibalization.append((
                    cluster.normalized_query, cluster.cannibalization_score, cluster.primary_url,
                    "\n".join(item.url for item in cluster.competing_urls if not item.is_primary),
                    cluster.estimated_lost_clicks, cluster.resolution_strategy, cluster.resolution_recommendation,
                ), formats={2: DECIMAL, 5: INTEGER})
            if quarantine:
                cannibalization.append(("Data Quarantine", "Батч", "Набор", "Строка", "Исходные поля", "Причина"), header=True)
                for row in quarantine:
                    cannibalization.append((
                        f"Запись {row.id}", row.batch_id, row.dataset_type, row.row_number,
                        json.dumps(row.raw_payload, ensure_ascii=False), row.error_reason,
                    ), formats={2: INTEGER, 4: INTEGER})

            for sheet in workbook:
                _format_sheet(sheet)
            workbook.save(output)
        finally:
            workbook.close()
        return output.getvalue()


def _csv_value(value):
    if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))):
        return "'" + value
    if isinstance(value, float):
        return f"{value:.2f}".replace(".", ",")
    return value


def build_csv_action_plan(db: Session, project: Project) -> bytes:
    report = _load_report(db, project)
    with io.StringIO(newline="") as output:
        writer = csv.writer(output, delimiter=";")
        writer.writerow(PLAN_HEADERS)
        writer.writerows(tuple(map(_csv_value, row)) for row in _plan_rows(report))
        return output.getvalue().encode("utf-8-sig")


_CHECKLISTS = {
    "ranking_drop": (
        "Сопоставить интент страницы с выдачей и обновить структуру под приоритетные запросы.",
        "Обновить устаревшие данные, примеры и ссылки. Добавить внутренние ссылки с тематических страниц.",
    ),
    "ctr_erosion": (
        "Переписать Title и Description под основной запрос и преимущество для читателя.",
        "Проверить отображение сниппета и уместность FAQ. Сравнить CTR после переобхода.",
    ),
    "demand_shift": (
        "Сверить изменения спроса по запросам и выявить новые темы с растущими показами.",
        "Добавить релевантные подтемы и обновить примеры под актуальный спрос.",
    ),
    "mixed_decay": (
        "Проверить интент, фактуру и коммерческие блоки. Обновить слабые разделы и внутренние ссылки.",
    ),
    "healthy": (
        "Сохранить релевантное содержание и зафиксировать исходные показатели перед изменениями.",
    ),
}


def _markdown_text(value: str) -> str:
    return re.sub(r"([\\\x60*_{}\[\]()<>#+.!|&~-])", r"\\\1", " ".join(value.split()))


def _money(value: float, currency: str) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + f" {_markdown_text(currency)}"


def build_markdown_brief(db: Session, project: Project) -> str:
    report = _load_report(db, project)
    summary = summarize_audit(project, list(report.pages.values()), report.opportunities, report.clusters, report.plan, 0)
    lost_clicks = sum(max(0, -page.clicks_delta) for page in report.pages.values())
    lines = [
        f"# ТЗ для редакции — {_markdown_text(project.name)}", "",
        f"Домен — {_markdown_text(project.domain)}.", "",
        "## Сводка потерь и денежный потенциал", "",
        f"- Органические клики — {summary.prev_clicks} → {summary.curr_clicks} ({summary.clicks_change_pct:+.1%}).",
        f"- Потеря на просевших страницах — {lost_clicks} кликов. Эквивалент по CPC — {_money(lost_clicks * project.default_cpc, project.currency)}.",
        f"- Возвращаемый трафик — {summary.recoverable_clicks} кликов.",
        f"- Эквивалент рекламного бюджета — {_money(summary.equivalent_ad_value, project.currency)}.",
        f"- Прогноз лидов — {summary.projected_leads:g}. Прогноз выручки — {_money(summary.projected_revenue_impact, project.currency)}.",
        "", "## Топ-10 страниц на рерайт и оптимизацию сниппетов", "",
    ]
    if not report.plan:
        lines.extend(("По текущей выгрузке приоритетных страниц нет.", ""))
    opportunity_urls = {item.url for item in report.opportunities}
    for index, item in enumerate(report.plan[:10], start=1):
        page = report.pages[item.url]
        lines.extend((
            f"### {index}. {_markdown_text(page.page_title or page.url)}", "",
            f"- URL — {_markdown_text(page.url)}.",
            f"- Приоритет — {item.priority_score:.1f}. Дельта кликов — {page.clicks_delta:+d}.",
            f"- Возвращаемые клики — {item.recoverable_clicks}. Прогноз выручки — {_money(item.projected_revenue_impact, project.currency)}.",
            "", f"Диагноз — {page.decay_driver}. {page.recommended_action}.", "",
        ))
        checklist = _CHECKLISTS[page.decay_driver]
        if page.url in opportunity_urls and page.decay_driver != "ctr_erosion":
            checklist += _CHECKLISTS["ctr_erosion"]
        if item.cannibalization_impact:
            checklist += ("Применить стратегию для этого URL из раздела каннибализации.",)
        lines.extend(f"- [ ] {task}" for task in checklist)
        lines.append("")

    lines.extend(("## Устранение каннибализации", ""))
    if not report.clusters:
        lines.append("Кластеры каннибализации по текущим данным отсутствуют.")
    for cluster in report.clusters:
        secondary = ", ".join(_markdown_text(item.url) for item in cluster.competing_urls if not item.is_primary)
        lines.extend((
            f"### {_markdown_text(cluster.normalized_query)}", "",
            f"- Основной URL — {_markdown_text(cluster.primary_url)}.",
            f"- Конкурирующие URL — {secondary}.",
            f"- Оценка потерянных кликов — {cluster.estimated_lost_clicks}.",
            f"- [ ] {cluster.resolution_recommendation}.",
            "- [ ] После обновления проверить показы, клики и долю основного URL по этому запросу.", "",
        ))
    return "\n".join(lines).rstrip() + "\n"
