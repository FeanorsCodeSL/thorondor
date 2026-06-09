"""Source-type-aware pre-cleaning. Extension point: add format-specific
normalization here without touching the chunker."""
import re

_IMAGE_LINE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$", re.MULTILINE)
_MULTI_BLANK = re.compile(r"\n{3,}")


def preclean(text: str, source_type: str) -> str:
    """Light, reversible normalization keyed by source_type."""
    if source_type == "WEB_MARKDOWN":
        text = _IMAGE_LINE.sub("", text)      # drop standalone image embeds
        text = _MULTI_BLANK.sub("\n\n", text)  # collapse runs of blank lines
    return text.strip()
