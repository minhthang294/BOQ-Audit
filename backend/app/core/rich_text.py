from html import escape
from html.parser import HTMLParser


ALLOWED_TAGS = {"p", "div", "br", "strong", "b", "em", "i", "u", "s", "ul", "ol", "li", "blockquote"}
BLOCKED_TAGS = {"script", "style"}
VOID_TAGS = {"br"}


class _RichTextSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.blocked_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in BLOCKED_TAGS:
            self.blocked_depth += 1
        elif not self.blocked_depth and tag in ALLOWED_TAGS:
            self.parts.append(f"<{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self.blocked_depth and tag.lower() in VOID_TAGS:
            self.parts.append("<br>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in BLOCKED_TAGS and self.blocked_depth:
            self.blocked_depth -= 1
        elif not self.blocked_depth and tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.blocked_depth:
            self.parts.append(escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if not self.blocked_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self.blocked_depth:
            self.parts.append(f"&#{name};")

    def html(self) -> str:
        return "".join(self.parts)


def sanitize_rich_text(value: str) -> str:
    sanitizer = _RichTextSanitizer()
    sanitizer.feed(value)
    sanitizer.close()
    return sanitizer.html()
