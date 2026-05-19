from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .models import Article

_EXT = {"html": ".html", "md": ".md", "txt": ".txt"}


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
    lines.append("# Operational Intelligence Briefing")
    lines.append("")
    lines.append(f"*Generated: {run_date.strftime('%Y-%m-%d %H:%M UTC')} \u2014 Articles from the past {lookback_days} days*")

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
    lines.append("OPERATIONAL INTELLIGENCE BRIEFING")
    lines.append(f"Generated: {run_date.strftime('%Y-%m-%d %H:%M UTC')} \u2014 Articles from the past {lookback_days} days")

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
