"""
VScan Admin Bot — Telegram-интерфейс для управления лицензиями.
Запуск: python bot.py
"""

import asyncio
import datetime
import io
import logging
import os
from functools import wraps
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from config import BOT_TOKEN, ADMIN_IDS
from core.datastore import DataStore
from core.license import create_key

# ── Логирование ───────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Глобальное хранилище ──────────────────────────────────────

store = DataStore()

# ── Состояния ConversationHandler ────────────────────────────

(
    ISSUE_ID,
    ISSUE_NAME,
    ISSUE_MODEL,
    ISSUE_OS,
    ISSUE_DAYS,
    ISSUE_CONFIRM,
) = range(6)

(
    EDIT_FIELD,
    EDIT_VALUE,
) = range(10, 12)


# ── Декоратор авторизации ─────────────────────────────────────

def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if ADMIN_IDS and uid not in ADMIN_IDS:
            await update.effective_message.reply_text("⛔ Нет доступа.")
            return ConversationHandler.END
        return await func(update, ctx)
    return wrapper


# ── Форматирование ────────────────────────────────────────────

def fmt_user(u: dict, compact: bool = False) -> str:
    icon = "🟢" if u.get("status") == "ACTIVE" else "🔴"
    if compact:
        return (
            f"{icon} <code>{u['device_id']}</code>\n"
            f"    {u.get('name', '—')}  ·  {u.get('expires_at', '—')}"
        )
    return (
        f"{icon} <b>Device ID:</b> <code>{u['device_id']}</code>\n"
        f"👤 <b>Имя:</b> {u.get('name', '—')}\n"
        f"📱 <b>Модель:</b> {u.get('model', '—')}\n"
        f"🤖 <b>Android:</b> {u.get('os', '—')}\n"
        f"📊 <b>Статус:</b> {'🟢 ACTIVE' if u.get('status') == 'ACTIVE' else '🔴 REVOKED'}\n"
        f"📅 <b>Истекает:</b> {u.get('expires_at', '—')}\n"
        f"🔑 <b>Ключ:</b> <code>{u.get('license_key', '—')}</code>"
    )


def days_left(expires_at: str) -> str:
    try:
        delta = datetime.date.fromisoformat(expires_at) - datetime.date.today()
        d = delta.days
        if d < 0:
            return f"просрочено {abs(d)} дн."
        if d == 0:
            return "истекает сегодня"
        return f"осталось {d} дн."
    except Exception:
        return ""


# ── /start, /help ─────────────────────────────────────────────

@admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔐 <b>VScan Admin Bot</b>\n\n"
        "<b>Лицензии:</b>\n"
        "  /list [поиск] — список всех лицензий\n"
        "  /info <code>&lt;device_id&gt;</code> — подробная карточка\n"
        "  /issue — выдать новую лицензию\n"
        "  /edit <code>&lt;device_id&gt;</code> — редактировать\n\n"
        "<b>Действия:</b>\n"
        "  /revoke <code>&lt;device_id&gt;</code> — заблокировать\n"
        "  /restore <code>&lt;device_id&gt;</code> — разблокировать\n"
        "  /delete <code>&lt;device_id&gt;</code> — удалить запись\n\n"
        "<b>Данные:</b>\n"
        "  /export — скачать CSV с лицензиями\n"
        "  /reload — перезагрузить из Gist\n"
        "  /stats — краткая статистика",
        parse_mode="HTML",
    )


# ── /reload ───────────────────────────────────────────────────

@admin_only
async def cmd_reload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загрузка из Gist…")
    ok, host = store.load()
    if ok:
        total = len(store.users())
        await msg.edit_text(
            f"✅ Загружено с <code>{host}</code>\n"
            f"📦 Лицензий в базе: <b>{total}</b>",
            parse_mode="HTML",
        )
    else:
        await msg.edit_text("❌ Все источники недоступны. Проверьте GIST_TOKEN и GIST_ID.")


# ── /stats ────────────────────────────────────────────────────

@admin_only
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    users = store.users()
    total   = len(users)
    active  = sum(1 for u in users if u.get("status") == "ACTIVE")
    revoked = total - active
    expired = sum(
        1 for u in users
        if u.get("status") == "ACTIVE"
        and u.get("expires_at", "9999") < datetime.date.today().isoformat()
    )
    await update.message.reply_text(
        f"📊 <b>Статистика</b>\n\n"
        f"Всего:       <b>{total}</b>\n"
        f"🟢 Активных: <b>{active}</b>\n"
        f"🔴 Отозваных: <b>{revoked}</b>\n"
        f"⏰ Просрочено (но ACTIVE): <b>{expired}</b>",
        parse_mode="HTML",
    )


# ── /list ─────────────────────────────────────────────────────

@admin_only
async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query_str = " ".join(ctx.args).lower().strip() if ctx.args else ""
    users = store.users()

    if query_str:
        users = [
            u for u in users
            if query_str in (u.get("device_id", "") + u.get("name", "") + u.get("model", "")).lower()
        ]

    if not users:
        tip = f' по запросу "<i>{query_str}</i>"' if query_str else ""
        await update.message.reply_text(f"📭 Лицензий не найдено{tip}.", parse_mode="HTML")
        return

    # Отправляем по 15 записей на сообщение
    chunk = 15
    for i in range(0, len(users), chunk):
        part = users[i : i + chunk]
        header = (
            f"📋 <b>Лицензии</b> {i+1}–{i+len(part)} из {len(users)}"
            + (f'  🔍 <i>"{query_str}"</i>' if query_str else "")
            + "\n\n"
        )
        lines = []
        for u in part:
            icon = "🟢" if u.get("status") == "ACTIVE" else "🔴"
            dl   = days_left(u.get("expires_at", ""))
            lines.append(
                f"{icon} <code>{u['device_id']}</code>\n"
                f"    👤 {u.get('name','—')}  |  {dl}"
            )
        await update.message.reply_text(header + "\n".join(lines), parse_mode="HTML")


# ── /info ─────────────────────────────────────────────────────

@admin_only
async def cmd_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Использование: /info <code>&lt;device_id&gt;</code>", parse_mode="HTML")
        return
    dev_id = ctx.args[0]
    user = store.find(dev_id)
    if not user:
        await update.message.reply_text(f"❌ Device ID не найден: <code>{dev_id}</code>", parse_mode="HTML")
        return

    dl = days_left(user.get("expires_at", ""))
    text = fmt_user(user) + (f"\n⏰ <i>{dl}</i>" if dl else "")

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✏️ Изменить",    callback_data=f"info_edit:{dev_id}"),
        InlineKeyboardButton("🔴 Отозвать",    callback_data=f"info_revoke:{dev_id}"),
        InlineKeyboardButton("🗑 Удалить",     callback_data=f"info_delete:{dev_id}"),
    ]])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# ── /revoke ───────────────────────────────────────────────────

@admin_only
async def cmd_revoke(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Использование: /revoke <code>&lt;device_id&gt;</code>", parse_mode="HTML")
        return
    dev_id = ctx.args[0]
    user = store.find(dev_id)
    if not user:
        await update.message.reply_text(f"❌ Не найден: <code>{dev_id}</code>", parse_mode="HTML")
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔴 Да, заблокировать", callback_data=f"revoke_yes:{dev_id}"),
        InlineKeyboardButton("❌ Отмена",             callback_data="noop"),
    ]])
    await update.message.reply_text(
        f"Заблокировать <b>{user.get('name','—')}</b>?\n<code>{dev_id}</code>",
        parse_mode="HTML", reply_markup=keyboard,
    )


# ── /restore ──────────────────────────────────────────────────

@admin_only
async def cmd_restore(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Использование: /restore <code>&lt;device_id&gt;</code>", parse_mode="HTML")
        return
    dev_id = ctx.args[0]
    user = store.find(dev_id)
    if not user:
        await update.message.reply_text(f"❌ Не найден: <code>{dev_id}</code>", parse_mode="HTML")
        return
    store.restore({dev_id})
    msg = await update.message.reply_text("⏳ Сохранение…")
    ok = store.save()
    await msg.edit_text(
        f"✅ Разблокирован: <code>{dev_id}</code>" if ok else "❌ Ошибка сохранения",
        parse_mode="HTML",
    )


# ── /delete ───────────────────────────────────────────────────

@admin_only
async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Использование: /delete <code>&lt;device_id&gt;</code>", parse_mode="HTML")
        return
    dev_id = ctx.args[0]
    user = store.find(dev_id)
    if not user:
        await update.message.reply_text(f"❌ Не найден: <code>{dev_id}</code>", parse_mode="HTML")
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🗑 Да, удалить", callback_data=f"delete_yes:{dev_id}"),
        InlineKeyboardButton("❌ Отмена",       callback_data="noop"),
    ]])
    await update.message.reply_text(
        f"⚠️ Удалить запись <b>{user.get('name','—')}</b>?\n"
        f"<code>{dev_id}</code>\n<i>Это действие необратимо.</i>",
        parse_mode="HTML", reply_markup=keyboard,
    )


# ── /checkkey ─────────────────────────────────────────────────

@admin_only
async def cmd_checkkey(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Отладка: /checkkey <device_id> <expires_at>
    Показывает какой ключ сгенерирует бот — сравните с настольным приложением.
    Пример: /checkkey abc123 2026-12-31
    """
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "Использование:\n"
            "/checkkey <code>&lt;device_id&gt; &lt;expires_at&gt;</code>\n\n"
            "Пример:\n"
            "<code>/checkkey abc123 2026-12-31</code>\n\n"
            "Введите те же данные что и в настольной программе — ключи должны совпасть.",
            parse_mode="HTML",
        )
        return

    import hashlib
    from config import SECRET_KEY

    dev_id     = ctx.args[0]
    expires_at = ctx.args[1]
    key        = create_key(dev_id, expires_at)

    # Показываем fingerprint ключа чтобы убедиться что SECRET_KEY совпадает
    key_fingerprint = hashlib.sha256(SECRET_KEY).hexdigest()[:16]

    await update.message.reply_text(
        f"🔑 <b>Результат генерации ключа:</b>\n\n"
        f"Device ID:   <code>{dev_id}</code>\n"
        f"Expires at:  <code>{expires_at}</code>\n"
        f"Ключ:        <code>{key}</code>\n\n"
        f"🔐 SECRET_KEY fingerprint: <code>{key_fingerprint}</code>\n\n"
        f"<i>Сгенерируйте ключ для тех же данных в настольной программе "
        f"и сравните. Если ключи разные — SECRET_KEY не совпадает.</i>",
        parse_mode="HTML",
    )


# ── /export ───────────────────────────────────────────────────

@admin_only
async def cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    content = store.export_csv()
    if not content:
        await update.message.reply_text("📭 Нет данных для экспорта.")
        return
    buf = io.BytesIO(content.encode("utf-8"))
    ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    await update.message.reply_document(
        buf,
        filename=f"vscan_licenses_{ts}.csv",
        caption=f"📊 Экспорт лицензий · {len(store.users())} записей",
    )


# ── /issue — пошаговый диалог ─────────────────────────────────

@admin_only
async def issue_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text(
        "📱 <b>Шаг 1/5</b> — Введите <b>Device ID</b>:\n"
        "<i>(или /cancel для отмены)</i>",
        parse_mode="HTML",
    )
    return ISSUE_ID


async def issue_get_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    dev_id = update.message.text.strip()
    if not dev_id:
        await update.message.reply_text("⚠️ Device ID не может быть пустым. Попробуйте снова:")
        return ISSUE_ID

    ctx.user_data["device_id"] = dev_id
    existing = store.find(dev_id)
    if existing:
        ctx.user_data["existing"] = existing
        await update.message.reply_text(
            f"⚠️ Этот Device ID уже зарегистрирован:\n{fmt_user(existing)}\n\n"
            f"Продолжить — перезапишет запись.\n\n"
            f"👤 <b>Шаг 2/5</b> — Имя пользователя\n"
            f"<i>Текущее: {existing.get('name','—')} (нажмите Enter или введите новое)</i>",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            "👤 <b>Шаг 2/5</b> — Введите <b>имя пользователя</b>:",
            parse_mode="HTML",
        )
    return ISSUE_NAME


async def issue_get_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    existing = ctx.user_data.get("existing", {})
    name = update.message.text.strip() or existing.get("name", "User")
    ctx.user_data["name"] = name
    await update.message.reply_text(
        f"📱 <b>Шаг 3/5</b> — Введите <b>модель устройства</b>:\n"
        f"<i>Пример: Samsung Galaxy S21  (или «-» для пропуска)</i>",
        parse_mode="HTML",
    )
    return ISSUE_MODEL


async def issue_get_model(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    existing = ctx.user_data.get("existing", {})
    val = update.message.text.strip()
    ctx.user_data["model"] = val if val != "-" else existing.get("model", "Unknown")
    await update.message.reply_text(
        "🤖 <b>Шаг 4/5</b> — Введите <b>версию Android</b>:\n"
        "<i>Пример: 13  (или «-» для пропуска)</i>",
        parse_mode="HTML",
    )
    return ISSUE_OS


async def issue_get_os(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    existing = ctx.user_data.get("existing", {})
    val = update.message.text.strip()
    ctx.user_data["os"] = val if val != "-" else existing.get("os", "—")
    await update.message.reply_text(
        "📅 <b>Шаг 5/5</b> — Введите <b>срок действия в днях</b>:\n"
        "<i>По умолчанию: 30</i>",
        parse_mode="HTML",
    )
    return ISSUE_DAYS


async def issue_get_days(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        days = max(1, int(update.message.text.strip()))
    except ValueError:
        days = 30

    dev_id     = ctx.user_data["device_id"]
    expires_at = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
    key        = create_key(dev_id, expires_at)

    user = {
        "device_id":   dev_id,
        "name":        ctx.user_data["name"],
        "model":       ctx.user_data["model"],
        "os":          ctx.user_data["os"],
        "status":      "ACTIVE",
        "expires_at":  expires_at,
        "license_key": key,
    }
    ctx.user_data["pending_user"] = user

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Создать и сохранить", callback_data="issue_confirm"),
        InlineKeyboardButton("❌ Отмена",               callback_data="issue_cancel"),
    ]])
    await update.message.reply_text(
        f"📋 <b>Предпросмотр лицензии:</b>\n\n{fmt_user(user)}\n\nСохранить?",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    return ISSUE_CONFIRM


async def issue_confirm_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "issue_cancel":
        await query.edit_message_text("❌ Отменено.")
        return ConversationHandler.END

    user = ctx.user_data.get("pending_user")
    if not user:
        await query.edit_message_text("❌ Данные потеряны. Начните заново /issue")
        return ConversationHandler.END

    await query.edit_message_text("⏳ Сохранение в Gist…")
    store.upsert_user(user)
    ok = store.save()

    if ok:
        await query.edit_message_text(
            f"✅ Лицензия создана!\n\n{fmt_user(user)}",
            parse_mode="HTML",
        )
    else:
        await query.edit_message_text("❌ Ошибка сохранения. Запись добавлена локально, но не синхронизирована.")

    return ConversationHandler.END


async def issue_cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено.")
    return ConversationHandler.END


# ── /edit — пошаговый диалог ──────────────────────────────────

EDIT_FIELD_LABELS = {
    "name":       "👤 Имя пользователя",
    "model":      "📱 Модель",
    "os":         "🤖 Android",
    "expires_at": "📅 Дата истечения",
    "status":     "📊 Статус",
}


@admin_only
async def edit_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Использование: /edit <code>&lt;device_id&gt;</code>", parse_mode="HTML")
        return ConversationHandler.END

    dev_id = ctx.args[0]
    user = store.find(dev_id)
    if not user:
        await update.message.reply_text(f"❌ Не найден: <code>{dev_id}</code>", parse_mode="HTML")
        return ConversationHandler.END

    ctx.user_data["edit_user"]  = user
    ctx.user_data["edit_field"] = None

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(
                f"{label}  ·  {user.get(key, '—')}",
                callback_data=f"editfield:{key}",
            )]
            for key, label in EDIT_FIELD_LABELS.items()
        ]
        + [[InlineKeyboardButton("❌ Отмена", callback_data="edit_cancel")]]
    )
    await update.message.reply_text(
        f"✏️ Редактирование <code>{dev_id}</code>\n\nВыберите поле:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    return EDIT_FIELD


async def edit_choose_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "edit_cancel":
        await query.edit_message_text("❌ Отменено.")
        return ConversationHandler.END

    field = query.data.split(":", 1)[1]
    ctx.user_data["edit_field"] = field
    user  = ctx.user_data["edit_user"]
    label = EDIT_FIELD_LABELS.get(field, field)

    hint = ""
    if field == "expires_at":
        hint = "\n<i>Формат: YYYY-MM-DD, например 2026-12-31</i>"
    elif field == "status":
        hint = "\n<i>Введите ACTIVE или REVOKED</i>"

    await query.edit_message_text(
        f"✏️ <b>{label}</b>\n"
        f"Текущее: <code>{user.get(field, '—')}</code>\n\n"
        f"Введите новое значение:{hint}",
        parse_mode="HTML",
    )
    return EDIT_VALUE


async def edit_set_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    field = ctx.user_data.get("edit_field")
    user  = ctx.user_data.get("edit_user")
    if not field or not user:
        await update.message.reply_text("❌ Сессия устарела. Начните заново /edit")
        return ConversationHandler.END

    value = update.message.text.strip()

    # Валидация
    if field == "expires_at":
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            await update.message.reply_text("⚠️ Неверный формат даты. Нужно YYYY-MM-DD:")
            return EDIT_VALUE

    if field == "status" and value not in ("ACTIVE", "REVOKED"):
        await update.message.reply_text("⚠️ Статус может быть только ACTIVE или REVOKED:")
        return EDIT_VALUE

    # Обновление
    user[field] = value

    # Перегенерация ключа при изменении expires_at
    if field == "expires_at":
        user["license_key"] = create_key(user["device_id"], value)

    msg = await update.message.reply_text("⏳ Сохранение…")
    store.update_user(user)
    ok = store.save()

    await msg.edit_text(
        f"✅ Сохранено!\n\n{fmt_user(user)}" if ok else "❌ Ошибка сохранения.",
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def edit_cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено.")
    return ConversationHandler.END


# ── Callback-обработчик для всех inline-кнопок ───────────────

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data

    if data == "noop":
        await query.edit_message_reply_markup(reply_markup=None)
        return

    # Кнопки из /info
    if data.startswith("info_edit:"):
        dev_id = data.split(":", 1)[1]
        ctx.args = [dev_id]
        # Симулируем /edit через фейк-update
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"Используйте команду:\n/edit <code>{dev_id}</code>",
            parse_mode="HTML",
        )
        return

    if data.startswith("info_revoke:"):
        dev_id = data.split(":", 1)[1]
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔴 Да, заблокировать", callback_data=f"revoke_yes:{dev_id}"),
            InlineKeyboardButton("❌ Отмена",             callback_data="noop"),
        ]])
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return

    if data.startswith("info_delete:"):
        dev_id = data.split(":", 1)[1]
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Да, удалить", callback_data=f"delete_yes:{dev_id}"),
            InlineKeyboardButton("❌ Отмена",       callback_data="noop"),
        ]])
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return

    # Подтверждения revoke / delete
    if data.startswith("revoke_yes:"):
        dev_id = data.split(":", 1)[1]
        store.revoke({dev_id})
        await query.edit_message_text("⏳ Сохранение…")
        ok = store.save()
        await query.edit_message_text(
            f"{'🔴 Заблокирован' if ok else '❌ Ошибка'}: <code>{dev_id}</code>",
            parse_mode="HTML",
        )
        return

    if data.startswith("delete_yes:"):
        dev_id = data.split(":", 1)[1]
        store.delete({dev_id})
        await query.edit_message_text("⏳ Сохранение…")
        ok = store.save()
        await query.edit_message_text(
            f"{'🗑 Удалён' if ok else '❌ Ошибка'}: <code>{dev_id}</code>",
            parse_mode="HTML",
        )
        return


# ── Запуск ────────────────────────────────────────────────────

async def post_init(app: Application) -> None:
    """Устанавливает меню команд и загружает данные при старте."""
    import hashlib
    from config import SECRET_KEY
    key_hash = hashlib.sha256(SECRET_KEY).hexdigest()[:12]
    source = "env VSCAN_SECRET_KEY" if os.environ.get("VSCAN_SECRET_KEY") else "ВСТРОЕННЫЙ (из оригинального config.py)"
    log.info("SECRET_KEY источник: %s | sha256[:12]=%s", source, key_hash)

    await app.bot.set_my_commands([
        BotCommand("list",    "Список лицензий"),
        BotCommand("issue",   "Выдать лицензию"),
        BotCommand("info",    "Карточка пользователя"),
        BotCommand("edit",    "Редактировать запись"),
        BotCommand("revoke",  "Заблокировать"),
        BotCommand("restore", "Разблокировать"),
        BotCommand("delete",  "Удалить запись"),
        BotCommand("export",  "Скачать CSV"),
        BotCommand("stats",    "Статистика"),
        BotCommand("reload",   "Перезагрузить из Gist"),
        BotCommand("checkkey", "Отладка генерации ключа"),
        BotCommand("start",    "Помощь"),
    ])
    log.info("Начальная загрузка данных…")
    ok, host = store.load()
    if ok:
        log.info("Загружено %d лицензий с %s", len(store.users()), host)
    else:
        log.warning("Не удалось загрузить данные при старте")


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан. Добавьте его в GitHub Secrets.")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # ── /issue ConversationHandler ────────────────────────────
    issue_conv = ConversationHandler(
        entry_points=[CommandHandler("issue", issue_start)],
        states={
            ISSUE_ID:      [MessageHandler(filters.TEXT & ~filters.COMMAND, issue_get_id)],
            ISSUE_NAME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, issue_get_name)],
            ISSUE_MODEL:   [MessageHandler(filters.TEXT & ~filters.COMMAND, issue_get_model)],
            ISSUE_OS:      [MessageHandler(filters.TEXT & ~filters.COMMAND, issue_get_os)],
            ISSUE_DAYS:    [MessageHandler(filters.TEXT & ~filters.COMMAND, issue_get_days)],
            ISSUE_CONFIRM: [CallbackQueryHandler(issue_confirm_cb, pattern="^issue_(confirm|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", issue_cancel_cmd)],
        per_chat=True,
        per_user=True,
        per_message=False,
    )

    # ── /edit ConversationHandler ─────────────────────────────
    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("edit", edit_start)],
        states={
            EDIT_FIELD: [CallbackQueryHandler(edit_choose_field, pattern="^(editfield:|edit_cancel)")],
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_set_value)],
        },
        fallbacks=[CommandHandler("cancel", edit_cancel_cmd)],
        per_chat=True,
        per_user=True,
        per_message=False,
    )

    # ── Простые команды ───────────────────────────────────────
    for cmd, handler in [
        ("start",   cmd_start),
        ("help",    cmd_start),
        ("reload",  cmd_reload),
        ("stats",   cmd_stats),
        ("list",    cmd_list),
        ("info",    cmd_info),
        ("revoke",  cmd_revoke),
        ("restore", cmd_restore),
        ("delete",  cmd_delete),
        ("export",  cmd_export),
        ("checkkey", cmd_checkkey),
    ]:
        app.add_handler(CommandHandler(cmd, handler))

    app.add_handler(issue_conv)
    app.add_handler(edit_conv)
    app.add_handler(CallbackQueryHandler(on_callback))

    log.info("Бот запущен (polling)…")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
