import difflib

from .models import PageDiff


def _line_sections(lines: list[str]) -> list[str | None]:
    current = None
    sections: list[str | None] = []
    for line in lines:
        stripped = line.strip()
        marker_length = len(stripped) - len(stripped.lstrip("#"))
        if 1 <= marker_length <= 6 and stripped[marker_length:].startswith(" "):
            current = stripped[marker_length:].strip()[:1024]
        sections.append(current)
    return sections


def _collect_changes(
    old_lines: list[str],
    new_lines: list[str],
) -> tuple[list[str], list[str], list[str]]:
    removed: list[str] = []
    added: list[str] = []
    changed_sections: list[str] = []
    old_sections = _line_sections(old_lines)
    new_sections = _line_sections(new_lines)
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag in {"delete", "replace"}:
            removed.extend(old_lines[old_start:old_end])
        if tag in {"insert", "replace"}:
            added.extend(new_lines[new_start:new_end])
        if tag == "equal":
            continue
        sections = [
            *old_sections[old_start:old_end],
            *new_sections[new_start:new_end],
        ]
        for section in sections:
            if section and section not in changed_sections:
                changed_sections.append(section)
    return removed, added, changed_sections


def bounded_diff(
    previous: str | None,
    current: str | None,
    max_input_lines: int,
    max_operations: int,
    max_output_lines: int,
) -> PageDiff | None:
    if previous == current:
        return None
    old_lines = (previous or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    new_lines = (current or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    if (
        len(old_lines) > max_input_lines
        or len(new_lines) > max_input_lines
        or len(old_lines) * len(new_lines) > max_operations
    ):
        return PageDiff(
            truncated=True,
            previous_lines=len(old_lines),
            current_lines=len(new_lines),
        )
    removed, added, changed_sections = _collect_changes(old_lines, new_lines)
    truncated = (
        len(removed) > max_output_lines
        or len(added) > max_output_lines
        or len(changed_sections) > max_output_lines
    )
    return PageDiff(
        truncated=truncated,
        previous_lines=len(old_lines),
        current_lines=len(new_lines),
        added_lines=[line[:1024] for line in added[:max_output_lines]],
        removed_lines=[line[:1024] for line in removed[:max_output_lines]],
        changed_sections=changed_sections[:max_output_lines],
    )
