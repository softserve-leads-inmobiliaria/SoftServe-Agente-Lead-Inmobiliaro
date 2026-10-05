"""#187: el Markdown del agente llega a Telegram como formato, no como asteriscos literales."""
from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from typing import Any

import pytest
from telegram.constants import ParseMode
from telegram.error import BadRequest

from app.channels.formatting import render
from app.channels.telegram import CAPTION_MAX_CHARS, ChannelReply, send_reply

PHOTO = "https://images.unsplash.com/photo-1?w=1024"
LISTING = ("Encontré estas opciones:\n"
           "- **Casa 3 rec.** - $4,800,000 | 180 m² | Zapopan\n"
           "- **Depa 2 rec.** - $2,100,000 | 85 m² | Providencia")


class _TelegramHtml(HTMLParser):
    """Lo que Telegram exige del HTML: solo sus etiquetas y bien anidadas."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        assert tag in {"b", "i", "code"}, tag
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        assert self.stack and self.stack.pop() == tag, tag

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def visible(html: str) -> str:
    parser = _TelegramHtml()
    parser.feed(html)
    parser.close()
    assert not parser.stack
    return "".join(parser.text)


# --- el renderizado ---------------------------------------------------------------------------------------------

def test_bold_and_bullets_become_telegram_html():
    rendered = render(LISTING)
    assert "**" not in rendered.html
    assert "• <b>Casa 3 rec.</b> - $4,800,000 | 180 m² | Zapopan" in rendered.html
    assert rendered.plain == ("Encontré estas opciones:\n"
                              "• Casa 3 rec. - $4,800,000 | 180 m² | Zapopan\n"
                              "• Depa 2 rec. - $2,100,000 | 85 m² | Providencia")


@pytest.mark.parametrize("text", [
    "a < b & c > d",
    "prop_00231 y snake_case_id",
    "2 * 3 = 6 y un * suelto",
    "un _ suelto y un ** doble",
    "<b>no soy una etiqueta</b>",
    "**a *b** c*",  # marcas cruzadas
    "***todo***",
    "`**literal**` dentro de código",
    "&amp; ya escapado",
])
def test_special_characters_always_produce_valid_html_with_the_same_visible_text(text):
    rendered = render(text)
    assert visible(rendered.html) == rendered.plain


def test_loose_characters_are_left_as_written():
    for text in ["a < b & c > d", "prop_00231 y snake_case_id", "2 * 3 = 6 y un * suelto"]:
        assert render(text).plain == text


def test_italics_headings_and_code():
    assert render("*aprox* y _ojo_").html == "<i>aprox</i> y <i>ojo</i>"
    assert render("### Opciones").html == "<b>Opciones</b>"
    assert render("`**x**`").html == "<code>**x**</code>"


def test_a_markdown_link_keeps_its_url_visible():
    assert render("[ver ficha](https://x.mx/p?a=1&b=2)").plain == "ver ficha (https://x.mx/p?a=1&b=2)"


# --- cómo se manda ----------------------------------------------------------------------------------------------

class FakeMessage:
    def __init__(self, text_errors: list[Exception] | None = None):
        self.text_errors = list(text_errors or [])
        self.sent: list[tuple[str, str, Any]] = []

    async def reply_text(self, value, reply_markup=None, parse_mode=None):
        if self.text_errors:
            raise self.text_errors.pop(0)
        self.sent.append(("text", value, parse_mode))

    async def reply_photo(self, photo, caption=None, reply_markup=None, parse_mode=None):
        self.sent.append(("photo", caption, parse_mode))


def test_the_text_goes_as_html():
    message = FakeMessage()
    asyncio.run(send_reply(message, ChannelReply(LISTING)))
    ((kind, value, parse_mode),) = message.sent
    assert kind == "text" and parse_mode == ParseMode.HTML
    assert "<b>Casa 3 rec.</b>" in value and "**" not in value


def test_the_caption_goes_as_html():
    message = FakeMessage()
    asyncio.run(send_reply(message, ChannelReply("**Depa 2 rec.** en Providencia", PHOTO)))
    assert message.sent == [("photo", "<b>Depa 2 rec.</b> en Providencia", ParseMode.HTML)]


def test_special_characters_are_escaped_before_sending():
    message = FakeMessage()
    asyncio.run(send_reply(message, ChannelReply("Precio < $5M & más_info *")))
    assert message.sent == [("text", "Precio &lt; $5M &amp; más_info *", ParseMode.HTML)]


def test_if_telegram_rejects_the_html_the_plain_text_goes(caplog):
    message = FakeMessage([BadRequest("Can't parse entities: unsupported start tag")])
    asyncio.run(send_reply(message, ChannelReply(LISTING)))
    ((kind, value, parse_mode),) = message.sent
    assert kind == "text" and parse_mode is None
    assert value == render(LISTING).plain
    assert "texto plano" in caplog.text


def test_the_caption_limit_is_measured_without_the_markup():
    """Telegram cuenta el *caption* ya sin entidades: las `**` no deben sacar la foto."""
    message = FakeMessage()
    text = "**" + "a" * CAPTION_MAX_CHARS + "**"
    asyncio.run(send_reply(message, ChannelReply(text, PHOTO)))
    assert [kind for kind, _, _ in message.sent] == ["photo"]


def test_a_caption_over_the_limit_once_rendered_goes_as_text():
    message = FakeMessage()
    text = "**" + "a" * (CAPTION_MAX_CHARS + 1) + "**"
    asyncio.run(send_reply(message, ChannelReply(text, PHOTO)))
    assert [kind for kind, _, _ in message.sent] == ["text"]
