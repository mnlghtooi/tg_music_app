"""Local, single-request @fmusbot protocol probe. No web server or downloads."""
import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path

BOT = "fmusbot"
ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".private"


def summarize(message):
    document = getattr(message, "document", None)
    file = getattr(message, "file", None)
    return {
        "id": message.id,
        "text": (getattr(message, "raw_text", "") or "")[:2000],
        "media_type": type(message.media).__name__ if message.media else None,
        "file": {
            "mime_type": getattr(document, "mime_type", None),
            "size": getattr(document, "size", None),
            "duration": getattr(file, "duration", None),
            "title": getattr(file, "title", None),
            "performer": getattr(file, "performer", None),
        } if document else None,
        # Keep labels and types; never export callback payloads or URLs.
        "buttons": [
            [{"text": b.text, "type": type(b.button).__name__} for b in row]
            for row in (message.buttons or [])
        ],
    }


async def probe(args):
    from telethon import TelegramClient, events
    from telethon.errors import FloodWaitError

    api_id = os.getenv("TG_API_ID") or input("API ID (my.telegram.org): ").strip()
    api_hash = os.getenv("TG_API_HASH") or getpass.getpass("API hash: ").strip()
    if not api_id.isdecimal() or not api_hash:
        raise ValueError("API credentials missing")

    STATE.mkdir(exist_ok=True)
    # No automatic retries or automatic waiting through Telegram rate limits.
    client = TelegramClient(
        str(STATE / "fmus_probe"), int(api_id), api_hash,
        flood_sleep_threshold=0, request_retries=0
    )
    report = {"bot": BOT, "mode": args.mode, "messages": []}
    try:
        await client.start(
            phone=lambda: input("Telegram phone (+...): ").strip(),
            code_callback=lambda: getpass.getpass("Telegram login code: "),
            password=lambda: getpass.getpass("Telegram 2FA password: ")
        )
        entity = await client.get_entity(BOT)
        if not getattr(entity, "bot", False):
            raise ValueError("Target is not a bot")
        report["inline_available"] = bool(
            getattr(entity, "bot_inline_placeholder", None)
        )
        if not args.query:
            report["status"] = "metadata_only"
        elif args.mode == "inline":
            if not report["inline_available"]:
                report["status"] = "inline_not_advertised"
            else:
                results = await asyncio.wait_for(
                    client.inline_query(entity, args.query), timeout=30
                )
                report["inline_results"] = [
                    {"type": r.type, "title": r.title,
                     "description": r.description}
                    for r in results[:10]
                ]
                report["status"] = "inline_received"
        else:
            captured = {}
            sent_id = None
            pending = []

            async def receive(event):
                message = event.message
                if getattr(message, "out", False):
                    return
                if sent_id is None:
                    pending.append(message)
                elif message.id > sent_id:
                    captured[message.id] = summarize(message)

            new = events.NewMessage(chats=entity, incoming=True)
            edited = events.MessageEdited(chats=entity, incoming=True)
            client.add_event_handler(receive, new)
            client.add_event_handler(receive, edited)
            try:
                # Exactly one outgoing message. No /start, clicks or downloads.
                sent = await client.send_message(entity, args.query)
                sent_id = sent.id
                for message in pending:
                    if message.id > sent_id:
                        captured[message.id] = summarize(message)
                await asyncio.sleep(args.wait)
                report["messages"] = list(captured.values())[:20]
                report["status"] = (
                    "responses_received" if captured else "no_response_in_window"
                )
                report["note"] = (
                    "Replies are candidates, not proven correlation: "
                    "do not use this bot chat concurrently during the probe."
                )
            finally:
                client.remove_event_handler(receive, new)
                client.remove_event_handler(receive, edited)
    except FloodWaitError as exc:
        report["status"] = "rate_limited"
        report["retry_after_seconds"] = exc.seconds
    finally:
        await client.disconnect()

    output = STATE / "probe-result.json"
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("Report:", output)
    print("Never share the .session file or login credentials.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", help="One query sent to @fmusbot")
    parser.add_argument("--mode", choices=("chat", "inline"), default="chat")
    parser.add_argument("--wait", type=int, default=15)
    args = parser.parse_args()
    if args.query and not 1 <= len(args.query.strip()) <= 200:
        parser.error("Query must contain 1..200 characters")
    if not 5 <= args.wait <= 45:
        parser.error("--wait must be 5..45 seconds")
    try:
        asyncio.run(probe(args))
    except KeyboardInterrupt:
        print("Cancelled.")
    except Exception as exc:
        # Avoid traceback/repr exposing credentials or session information.
        print("Probe failed:", type(exc).__name__)
        print("Check credentials, Telegram access, bot availability and dependency installation.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
