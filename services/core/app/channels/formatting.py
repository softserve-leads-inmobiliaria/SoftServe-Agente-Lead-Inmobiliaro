"""Markdown del agente → HTML de Telegram (#187, DT-53).

El agente escribe en Markdown (`**negritas**`, viñetas con `-`) y Telegram lo mostraría literal. Se
renderiza al subconjunto que acepta `parse_mode="HTML"` y se escapa todo lo demás, así que un `<`, `&`
o `*` suelto del texto nunca rompe el envío. Solo cambian las marcas: el texto visible es el mismo que
revisaron los guardrails.

Se devuelven las dos formas: el HTML y el texto plano visible, que es lo que se manda si Telegram
rechaza el formato y lo que mide el tope del *caption* (Telegram lo cuenta ya sin las entidades).
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+")
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITALIC_STAR = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])")
_ITALIC_UNDERSCORE = re.compile(r"(?<![\w_])_(?=[^\s_])([^_\n]+?)(?<=[^\s_])_(?![\w_])")
_TAG = re.compile(r"</?(b|i|code)>")


@dataclass(frozen=True)
class Rendered:
    html: str
    plain: str


def render(markdown: str) -> Rendered:
    rendered = "\n".join(_render_line(line) for line in markdown.split("\n"))
    plain = html.unescape(_TAG.sub("", rendered))
    if not _well_nested(rendered):
        # Marcas cruzadas (`**a *b** c*`): Telegram rechazaría el HTML; va el texto sin formato.
        rendered = html.escape(plain, quote=False)
    return Rendered(rendered, plain)


def _render_line(line: str) -> str:
    heading = _HEADING.match(line)
    if heading:
        return f"<b>{_inline(heading.group(1))}</b>"
    return _inline(_BULLET.sub(r"\1• ", line, count=1))


def _inline(text: str) -> str:
    # El código va tal cual: dentro de `...` un `*` no es una marca.
    parts = _CODE.split(_LINK.sub(r"\1 (\2)", text))
    return "".join(f"<code>{html.escape(part, quote=False)}</code>" if index % 2
                    else _emphasis(html.escape(part, quote=False))
                    for index, part in enumerate(parts))


def _emphasis(text: str) -> str:
    text = _BOLD.sub(r"<b>\1</b>", text)
    text = _ITALIC_STAR.sub(r"<i>\1</i>", text)
    return _ITALIC_UNDERSCORE.sub(r"<i>\1</i>", text)


def _well_nested(rendered: str) -> bool:
    stack: list[str] = []
    for tag in _TAG.finditer(rendered):
        if tag.group(0).startswith("</"):
            if not stack or stack.pop() != tag.group(1):
                return False
        else:
            stack.append(tag.group(1))
    return not stack
