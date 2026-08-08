"""Generic HTML-to-Markdown extraction before semantic chunking."""
from collections import Counter
from collections.abc import Callable
from dataclasses import replace

from .types import CleanedPage, Page

Extractor = Callable[..., str | None]


def _removed_line_count(original: str, cleaned: str) -> int:
    remaining = Counter(line.strip() for line in cleaned.splitlines() if line.strip())
    removed = 0
    for line in (line.strip() for line in original.splitlines() if line.strip()):
        if remaining[line]:
            remaining[line] -= 1
        else:
            removed += 1
    return removed


class MarkdownCleanerImpl:
    def __init__(
        self,
        extractor: Extractor,
        extractor_name: str,
        extractor_version: str,
        favor_recall: bool,
        include_comments: bool,
        include_tables: bool,
        deduplicate: bool,
    ):
        self._extractor = extractor
        self._extractor_name = extractor_name
        self._extractor_version = extractor_version
        self._favor_recall = favor_recall
        self._include_comments = include_comments
        self._include_tables = include_tables
        self._deduplicate = deduplicate

    @property
    def cleaner_version(self) -> str:
        return f"{self._extractor_name}@{self._extractor_version}"

    def clean(self, page: Page) -> CleanedPage:
        original = page.original_markdown or page.markdown
        source = page.html
        if source:
            try:
                extracted = self._extractor(
                    source,
                    url=page.url,
                    output_format="markdown",
                    favor_recall=self._favor_recall,
                    include_comments=self._include_comments,
                    include_tables=self._include_tables,
                    deduplicate=self._deduplicate,
                )
            except Exception:
                extracted = None
            cleaned = extracted.strip() if extracted else page.markdown.strip()
        else:
            cleaned = page.markdown.strip()

        cleaned_page = replace(page, markdown=cleaned, original_markdown=original)
        return CleanedPage(
            page=cleaned_page,
            chars_before=len(page.markdown),
            chars_after=len(cleaned),
            blocks_dropped=_removed_line_count(original, cleaned),
            cleaner_version=self.cleaner_version,
        )
