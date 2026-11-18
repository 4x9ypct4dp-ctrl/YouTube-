"""Telegram-бот: принимает ссылку на YouTube-видео и присылает пересказ.

Бот работает на сервере 24/7, поэтому пока вы оффлайн, он обрабатывает
ссылки, а готовый пересказ ждёт вас в чате.
"""
import asyncio
import logging
import os
import re

from anthropic import Anthropic
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from youtube_transcript_api import YouTubeTranscriptApi

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("videobot")

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
# Языки субтитров по приоритету
LANGS = os.environ.get("TRANSCRIPT_LANGS", "ru,en").split(",")
MAX_CHARS = 300_000  # ограничение длины транскрипта

claude = Anthropic()  # ключ берётся из ANTHROPIC_API_KEY

YT_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|shorts/|live/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)

PROMPT = (
    "Ниже транскрипт видео. Сделай пересказ на русском языке:\n"
    "1) О чём видео — 2-3 предложения.\n"
    "2) Главные мысли — списком.\n"
    "3) Ключевые моменты с таймкодами (если они есть в тексте).\n"
    "Если пользователь задал вопрос, ответь и на него.\n\n"
    "Вопрос пользователя: {question}\n\n"
    "Транскрипт:\n{transcript}"
)


def fetch_transcript(video_id: str) -> str:
    """Возвращает текст субтитров с таймкодами. Работает и со старой, и с новой версией библиотеки."""
    try:
        data = YouTubeTranscriptApi().fetch(video_id, languages=LANGS)
        items = [(s.start, s.text) for s in data]
    except AttributeError:
        raw = YouTubeTranscriptApi.get_transcript(video_id, languages=LANGS)
        items = [(s["start"], s["text"]) for s in raw]
    lines = []
    for start, text in items:
        m, s = divmod(int(start), 60)
        lines.append(f"[{m:02d}:{s:02d}] {text}")
    return "\n".join(lines)[:MAX_CHARS]


def summarize(transcript: str, question: str) -> str:
    resp = claude.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[
            {
                "role": "user",
                "content": PROMPT.format(
                    question=question or "(нет)", transcript=transcript
                ),
            }
        ],
    )
    return resp.content[0].text


async def send_long(update: Update, text: str):
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i : i + 4000])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Пришли ссылку на YouTube-видео, и я пришлю пересказ.\n"
        "Можно добавить вопрос после ссылки, например:\n"
        "https://youtu.be/XXXXXXXXXXX что он говорит про цены?"
    )


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    m = YT_RE.search(text)
    if not m:
        await update.message.reply_text("Пришли ссылку на YouTube-видео.")
        return
    video_id = m.group(1)
    question = YT_RE.sub("", text).strip()
    await update.message.reply_text("Смотрю видео, это займёт до минуты…")
    try:
        transcript = await asyncio.to_thread(fetch_transcript, video_id)
    except Exception as e:
        log.warning("transcript error: %s", e)
        await update.message.reply_text(
            "Не удалось получить субтитры: у видео их нет или доступ закрыт."
        )
        return
    try:
        summary = await asyncio.to_thread(summarize, transcript, question)
    except Exception as e:
        log.exception("claude error")
        await update.message.reply_text(f"Ошибка при пересказе: {e}")
        return
    await send_long(update, summary)


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.run_polling()


if __name__ == "__main__":
    main()
