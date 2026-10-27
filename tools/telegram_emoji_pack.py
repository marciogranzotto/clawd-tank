#!/usr/bin/env python3
"""
Create a Telegram custom emoji pack from the .webm files made by
tools/svg2telegram_emoji.py, using the Bot API (createNewStickerSet).

The pack is owned by your Telegram account; the bot only creates and edits it.
Anyone can create custom emoji packs; using custom emoji in messages needs
Telegram Premium.

Setup:
  1. Message @BotFather, send /newbot, and copy the bot token.
  2. Open a chat with your new bot and send it /start (so this script can
     find your user id; or pass --user-id yourself).
  3. Run:
       export TELEGRAM_BOT_TOKEN=123456:ABC...
       python tools/telegram_emoji_pack.py

Usage:
    python tools/telegram_emoji_pack.py [input_dir]
      --name clawd_tank     Pack short name; "_by_<bot_username>" is appended
      --title "Clawd Tank"  Pack title
      --user-id 12345       Pack owner (default: sender of the bot's latest private message)
      --dry-run             Show what would be uploaded
"""

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: 'requests' is required. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

API = "https://api.telegram.org/bot{token}/{method}"
MAX_INITIAL = 50  # createNewStickerSet accepts 1-50 initial stickers

# Pack order (device animations first, as in the README gallery) and the
# emoji each one is suggested for. Files not listed here are appended with 🦀.
EMOJI = {
    "clawd-idle-living": "🦀",
    "clawd-notification": "❗",
    "clawd-happy": "😄",
    "clawd-sleeping": "😴",
    "clawd-disconnected": "🔌",
    "clawd-working-thinking": "🤔",
    "clawd-working-typing": "⌨️",
    "clawd-working-debugger": "🔍",
    "clawd-working-building": "🔨",
    "clawd-working-wizard": "🧙",
    "clawd-working-conducting": "🎼",
    "clawd-working-beacon": "📡",
    "clawd-working-juggling": "🤹",
    "clawd-working-confused": "😕",
    "clawd-dizzy": "😵",
    "clawd-working-sweeping": "🧹",
    "clawd-crab-walking": "🚶",
    "clawd-going-away": "👋",
    "clawd-mini-clawd": "🦀",
    "clawd-coffee-head-flip": "☕",
    "clawd-eureka": "💡",
    "clawd-grooving": "🎧",
    "clawd-hat-mishap": "🎩",
    "clawd-idle-low-battery": "🪫",
    "clawd-wake": "⏰",
    "clawd-working-builder": "🧱",
    "clawd-working-carrying": "📦",
    "clawd-working-overheated": "🥵",
    "clawd-working-pushing": "💪",
    "clawd-working-ultra-thinking": "🧠",
    "mini-crab-typing": "💻",
    "clawd-static-base": "🦀",
}


class TelegramError(RuntimeError):
    pass


def call(token, method, data=None, files=None):
    r = requests.post(API.format(token=token, method=method), data=data, files=files, timeout=120)
    js = r.json()
    if not js.get("ok"):
        raise TelegramError(f"{method}: {js.get('description', js)}")
    return js["result"]


def keywords_for(stem):
    words = [w for w in stem.split("-") if w not in ("clawd", "working")]
    out, total = [], 0
    for w in ["clawd", *words]:
        if total + len(w) > 64:  # Telegram caps total keyword length at 64
            break
        out.append(w)
        total += len(w)
    return out


def find_owner_id(token):
    """Sender of the most recent private message to the bot (send it /start first)."""
    updates = call(token, "getUpdates")
    for u in reversed(updates):
        msg = u.get("message") or {}
        if msg.get("chat", {}).get("type") == "private":
            sender = msg["from"]
            return sender["id"], sender.get("username") or sender.get("first_name")
    raise TelegramError("no private messages to the bot yet: open the bot in Telegram, "
                        "send it /start, then re-run (or pass --user-id)")


def ordered_files(in_dir):
    files = {p.stem: p for p in in_dir.glob("*.webm")}
    order = [s for s in EMOJI if s in files] + sorted(s for s in files if s not in EMOJI)
    return [files[s] for s in order]


def input_sticker(path, attach):
    return {
        "sticker": f"attach://{attach}",
        "format": "video",
        "emoji_list": [EMOJI.get(path.stem, "🦀")],
        "keywords": keywords_for(path.stem),
    }


def main():
    ap = argparse.ArgumentParser(description="Create a Telegram custom emoji pack from .webm files")
    ap.add_argument("input_dir", nargs="?", default="assets/telegram-emojis")
    ap.add_argument("--name", default="clawd_tank", help='Pack short name ("_by_<bot>" is appended)')
    ap.add_argument("--title", default="Clawd Tank")
    ap.add_argument("--user-id", type=int, help="Telegram user id of the pack owner")
    ap.add_argument("--token", default=os.environ.get("TELEGRAM_BOT_TOKEN"),
                    help="Bot token (or env TELEGRAM_BOT_TOKEN)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = ordered_files(Path(args.input_dir))
    if not files:
        print(f"No .webm files in {args.input_dir} (run tools/svg2telegram_emoji.py first)",
              file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        for p in files:
            s = input_sticker(p, "x")
            print(f"  {s['emoji_list'][0]}  {p.name}  keywords={s['keywords']}")
        print(f"{len(files)} emoji")
        return

    if not args.token:
        print("Missing bot token: set TELEGRAM_BOT_TOKEN (see --help)", file=sys.stderr)
        sys.exit(2)

    try:
        bot = call(args.token, "getMe")
        name = f"{args.name}_by_{bot['username']}"
        if args.user_id:
            owner, who = args.user_id, args.user_id
        else:
            owner, who = find_owner_id(args.token)
        print(f"Bot @{bot['username']}, pack owner {who} ({owner}), pack '{name}'")

        try:
            existing = call(args.token, "getStickerSet", {"name": name})
            print(f"Pack already exists with {len(existing['stickers'])} emoji: "
                  f"https://t.me/addemoji/{name}\nPick another --name, or delete it in @Stickers "
                  f"(/delpack) to recreate it.", file=sys.stderr)
            sys.exit(1)
        except TelegramError:
            pass  # does not exist yet

        first, rest = files[:MAX_INITIAL], files[MAX_INITIAL:]
        handles = {f"s{i}": open(p, "rb") for i, p in enumerate(first)}
        try:
            stickers = [input_sticker(p, f"s{i}") for i, p in enumerate(first)]
            call(args.token, "createNewStickerSet",
                 data={"user_id": owner, "name": name, "title": args.title,
                       "sticker_type": "custom_emoji", "stickers": json.dumps(stickers)},
                 files={k: (Path(h.name).name, h, "video/webm") for k, h in handles.items()})
        finally:
            for h in handles.values():
                h.close()
        print(f"  created with {len(first)} emoji")

        for p in rest:
            with open(p, "rb") as h:
                call(args.token, "addStickerToSet",
                     data={"user_id": owner, "name": name,
                           "sticker": json.dumps(input_sticker(p, "s"))},
                     files={"s": (p.name, h, "video/webm")})
            print(f"  added {p.name}")
    except TelegramError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\nDone: https://t.me/addemoji/{name}")


if __name__ == "__main__":
    main()
