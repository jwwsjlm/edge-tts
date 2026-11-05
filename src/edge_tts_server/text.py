"""Lossless text splitting shared by the CLI example and background jobs."""

SPLIT_MARKS = ("\n", "。", "！", "？", ". ", "! ", "? ", "；", "; ", "，", ", ")


def split_text(text: str, max_chars: int) -> list[str]:
    """Prefer sentence boundaries while preserving every source character."""
    if not text.strip():
        raise ValueError("Text must not be blank")
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]
    segments: list[str] = []
    start = 0
    while len(text) - start > max_chars:
        end_limit = start + max_chars
        minimum = start + max(1, max_chars // 2)
        candidates = [
            candidate + len(mark)
            for mark in SPLIT_MARKS
            if (candidate := text.rfind(mark, start, end_limit)) >= start
            and candidate + len(mark) >= minimum
        ]
        end = max(candidates, default=end_limit)
        segments.append(text[start:end])
        start = end
    if start < len(text):
        segments.append(text[start:])
    return segments
