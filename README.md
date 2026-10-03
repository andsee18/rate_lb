# Rate LB

Telegram Mini App, REST API and Telegram bot for a private district rating.

## Run locally

1. Create a bot with `@BotFather`, add it to the group as an administrator and enable group member updates.
2. Copy `.env.example` to `.env` and set:
   - `BOT_TOKEN` — bot token;
   - `GROUP_CHAT_ID` — numeric ID of the target group;
   - `ADMIN_IDS` — comma-separated Telegram IDs allowed to run `/scan`, `/rate` and `POST /api/rate`.
3. Install dependencies and start the server:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m backend.main
```

The API and Mini App are available at `http://localhost:8000`. Deploy `index.html` behind the same domain as the API, or set `WEBAPP_ORIGINS` to the frontend origin and change the frontend API base URL.

## Bot commands

```text
/scan
/rate @username 3.90 Саня Машина
```

The bot records users from `new_chat_members` and `chat_member` updates, fetches their latest profile photo through Telegram and stores its `file_id` in SQLite. The frontend receives `/api/avatar/{telegram_id}`, which proxies the image without exposing the bot token.

Telegram Bot API does not expose a method to enumerate every member of a group. Therefore `/scan` synchronizes administrators and already-known users; regular members are added when they join or when Telegram sends a member/message update to the bot.

## API

- `GET /api/health` — health check.
- `GET /api/users` — `{ "rated": [...], "unrated": [...] }`, with rated users sorted from 8.00 to 0.00.
- `POST /api/rate` — accepts `{ "telegram_id": 123, "score": 3.9, "nickname": "Саня Машина" }` and requires the `X-Admin-Id` header.
- `GET /api/avatar/{telegram_id}` — cached Telegram profile image proxy.

# rate_lb
