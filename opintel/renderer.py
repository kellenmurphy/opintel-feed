from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .models import CATEGORIES, Article


def render_briefing(
    articles: list[Article],
    output_path: Path,
    run_date: datetime,
    lookback_days: int = 7,
    templates_dir: Path | None = None,
) -> None:
    if templates_dir is None:
        templates_dir = Path(__file__).parent.parent / "templates"

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=True,
    )
    template = env.get_template("briefing.html.j2")

    articles_by_category: dict[str, list[Article]] = {cat: [] for cat in CATEGORIES}
    for article in articles:
        cat = article.category if article.category in CATEGORIES else "Other News"
        articles_by_category[cat].append(article)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    html = template.render(
        run_date=run_date.strftime("%Y-%m-%d %H:%M UTC"),
        lookback_days=lookback_days,
        categories=CATEGORIES,
        articles_by_category=articles_by_category,
    )

    output_path.write_text(html, encoding="utf-8")


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
