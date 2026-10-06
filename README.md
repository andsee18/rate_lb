# Rate LB

Telegram Mini App, REST API and Telegram bot for a private district rating.

## Run locally

1. Create a bot with `@BotFather`, add it to the group as an administrator and enable group member updates.
2. Copy `.env.example` to `.env` and set:
   - `BOT_TOKEN` — bot token;
   - `GROUP_CHAT_ID` — numeric ID of the target group;
   - `ADMIN_IDS` — comma-separated Telegram IDs allowed to run `/scan`, `/rate` and `POST /api/rate`.
   - `DATABASE_URL` — PostgreSQL connection URI from Supabase, using the Session pooler.
3. Install dependencies and start the server:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m backend.main
```

The Mini App is published at `https://fancy-queijadas-cf8f5c.netlify.app` and uses the API at `https://rate-lb-backend.onrender.com`. For local development, run the backend on `http://localhost:8000` and temporarily change `API_BASE_URL` in `index.html`.

The Render web service must run exactly one instance and one Uvicorn worker because Telegram long polling allows only one `getUpdates` consumer per bot token. Use:

```bash
uvicorn backend.main:app --host 0.0.0.0 --port $PORT --workers 1
```

Do not run a second Render service, local polling process, or background worker with the same `BOT_TOKEN`.

Admin commands in a private chat:

```text
/gender @username girl
/gender @username boy
/params @username
Симметрия: 5.4
Челюсть: 4.3
Кожа: 5.9
Гармония: 4.8
Взгляд: 5.0
Причёска: 5.8
```

The rating scale is 0–10. Male tiers are Sub-3 (0–2.9), Sub-5 (3.0–3.9), LTN (4.0–4.7), MTN (4.8–5.5), HTN (5.6–6.7), Chad (6.8–8.2), and True Adam (8.3–10). Female tiers use the same thresholds with LTB, MTB, HTB, Stacy, and True Eva replacing the male labels. Existing numeric ratings are preserved; only future status classification follows the new thresholds.

## Bot commands

```text
/scan
/rate @username 3.9 Саня Машина
/rate 123456789 3.9 Саня Машина
/id Имя Фамилия
```

Оценивать можно по username или напрямую по числовому Telegram ID. Если записи с
таким ID ещё нет, бот создаст её без Telegram-профиля; имя и username подтянутся,
когда пользователь напишет в группе или откроет Mini App. Telegram не позволяет
получить ID произвольного пользователя только по username: ID можно взять из
пересланного сообщения/сообщения пользователя, через `@userinfobot` или из
`message.from_user.id` в обработчике бота.
Если username отсутствует, но участник уже отображается в списке «Пока не
оценён», используй `/id Имя Фамилия`: бот найдёт его ID в своей базе. Можно также
сразу выполнить `/rate Имя Фамилия 4.2`; при совпадении имён бот покажет список
ID для выбора.

The bot records users from `new_chat_members` and `chat_member` updates, fetches their latest profile photo through Telegram and stores its `file_id` in PostgreSQL. The frontend receives `/api/avatar/{telegram_id}`, which proxies the image without exposing the bot token.

Telegram Bot API does not expose a method to enumerate every member of a group. Therefore `/scan` synchronizes administrators and already-known users; regular members are added when they join or when Telegram sends a member/message update to the bot.

## API

- `GET /api/health` — health check.
- `GET /api/users` — `{ "rated": [...], "unrated": [...] }`, with rated users sorted from 8 to 0.
- `POST /api/rate` — accepts `{ "telegram_id": 123, "score": 3.9, "nickname": "Саня Машина" }`, creates a placeholder when the ID is not known yet, and requires the `X-Admin-Id` header.
- `GET /api/avatar/{telegram_id}` — cached Telegram profile image proxy.
- `POST /api/users/me` — validates Telegram Mini App `initData` and adds the current viewer to the unrated list.

For a post button or a direct link to the Mini App, configure the bot's Main Mini App in `@BotFather` and use:

```text
https://t.me/rate_lb_bot?startapp
```

# rate_lb
