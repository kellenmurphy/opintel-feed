from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .models import Article

_EXT = {"html": ".html", "md": ".md", "txt": ".txt"}


def _ordinal(n: int) -> str:
    """Return *n* with its English ordinal suffix, e.g. 3 -> '3rd', 21 -> '21st'."""
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _format_dateline(run_date: datetime) -> str:
    """Build a human "as of" stamp in local time, e.g. 'June 3rd, 2026 @ ~6:40pm'.

    The time is rounded to the nearest 5 minutes; the leading '~' signals that it
    is approximate. *run_date* is converted to the local timezone of the machine
    generating the briefing.
    """
    local = run_date.astimezone()
    # Round to the nearest 5 minutes (carrying into the hour/day as needed).
    remainder = timedelta(minutes=local.minute % 5, seconds=local.second, microseconds=local.microsecond)
    local -= remainder
    if remainder >= timedelta(minutes=2, seconds=30):
        local += timedelta(minutes=5)

    hour12 = local.hour % 12 or 12
    ampm = "am" if local.hour < 12 else "pm"
    return f"{local.strftime('%B')} {_ordinal(local.day)}, {local.year} @ ~{hour12}:{local.minute:02d}{ampm}"


def _group_by_category(articles: list[Article], categories: list[str]) -> dict[str, list[Article]]:
    grouped: dict[str, list[Article]] = {cat: [] for cat in categories}
    for article in articles:
        cat = article.category if article.category in categories else categories[-1]
        grouped[cat].append(article)
    return grouped


def render_html(
    articles: list[Article],
    output_path: Path,
    run_date: datetime,
    lookback_days: int = 7,
    categories: list[str] | None = None,
    templates_dir: Path | None = None,
) -> None:
    from .models import CATEGORIES
    if categories is None:
        categories = list(CATEGORIES)
    if templates_dir is None:
        templates_dir = Path(__file__).parent.parent / "templates"

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=True,
    )
    template = env.get_template("briefing.html.j2")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    html = template.render(
        run_date=run_date.strftime("%Y-%m-%d %H:%M UTC"),
        dateline=_format_dateline(run_date),
        lookback_days=lookback_days,
        categories=categories,
        articles_by_category=_group_by_category(articles, categories),
    )
    output_path.write_text(html, encoding="utf-8")


# Keep the old name as an alias so existing callers don't break.
render_briefing = render_html


def render_markdown(
    articles: list[Article],
    output_path: Path,
    run_date: datetime,
    lookback_days: int = 7,
    categories: list[str] | None = None,
) -> None:
    from .models import CATEGORIES
    if categories is None:
        categories = list(CATEGORIES)
    lines: list[str] = []
    lines.append("# Newsroom (interesting links, security news)")
    lines.append("")
    lines.append("Did anything notable happen this week? Something notable *always* happens\u2026")
    lines.append("")
    lines.append(f"*(As of {_format_dateline(run_date)}.)*")

    for cat, cat_articles in _group_by_category(articles, categories).items():
        if not cat_articles:
            continue
        lines.append("")
        lines.append(f"## {cat}")
        for article in cat_articles:
            lines.append("")
            lines.append(f"**{article.title}**")
            for url in article.all_urls:
                lines.append(f"- {url}")
            for bullet in article.summary_bullets:
                lines.append(f"- {bullet}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_text(
    articles: list[Article],
    output_path: Path,
    run_date: datetime,
    lookback_days: int = 7,
    categories: list[str] | None = None,
) -> None:
    from .models import CATEGORIES
    if categories is None:
        categories = list(CATEGORIES)
    width = 72
    lines: list[str] = []
    lines.append("NEWSROOM (INTERESTING LINKS, SECURITY NEWS)")
    lines.append("Did anything notable happen this week? Something notable always happens\u2026")
    lines.append(f"(As of {_format_dateline(run_date)}.)")

    for cat, cat_articles in _group_by_category(articles, categories).items():
        if not cat_articles:
            continue
        lines.append("")
        lines.append("\u2500" * width)
        lines.append(cat.upper())
        lines.append("\u2500" * width)
        for article in cat_articles:
            lines.append("")
            lines.append(article.title)
            for url in article.all_urls:
                lines.append(f"  {url}")
            for bullet in article.summary_bullets:
                lines.append(f"  \u2022 {bullet}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def output_path_for_format(
    fmt: str,
    base_date: str,
    explicit_output: Path | None,
    single_format: bool,
) -> Path:
    """Return the output path for a given format.

    If *explicit_output* is set and only one format is requested, use it as-is.
    If *explicit_output* is set and multiple formats are requested, replace its
    suffix with the format's canonical extension.
    Otherwise build a default path under output/.
    """
    ext = _EXT[fmt]
    if explicit_output is not None:
        if single_format:
            return explicit_output
        return explicit_output.with_suffix(ext)
    return Path("output") / f"briefing_{base_date}{ext}"


def unique_output_path(base: Path) -> Path:
    if not base.exists():
        return base
    stem = base.stem
    suffix = base.suffix
    parent = base.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
