"""
VScan Admin Bot — полностью кнопочный интерфейс.
Запуск: python bot.py
"""

import asyncio
import datetime
import hashlib
import io
import logging
import os
from functools import wraps
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
    ReplyKeyboardRemove,
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

from config import BOT_TOKEN, ADMIN_IDS, SECRET_KEY
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

SEARCH_QUERY = 20

# ── Тексты кнопок главного меню ───────────────────────────────

BTN_LIST     = "📋 Все лицензии"
BTN_SEARCH   = "🔍 Поиск"
BTN_ISSUE    = "➕ Выдать лицензию"
BTN_STATS    = "📊 Статистика"
BTN_EXPORT   = "📤 Экспорт CSV"
BTN_RELOAD   = "🔄 Обновить данные"

# ── Главная клавиатура (нижняя панель) ───────────────────────

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BTN_LIST,   BTN_SEARCH],
        [BTN_ISSUE,  BTN_STATS],
        [BTN_EXPORT, BTN_RELOAD],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


# ── Декоратор авторизации ─────────────────────────────────────

def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user:
            return
            
        uid = update.effective_user.id
        
        # Если списка нет или пользователя нет в списке — бот просто молчит
        if not ADMIN_IDS or uid not in ADMIN_IDS:
            return 
            
        return await func(update, ctx)
    return wrapper


# ── Утилиты ───────────────────────────────────────────────────

def days_left(expires_at: str) -> str:
    try:
        delta = datetime.date.fromisoformat(expires_at) - datetime.date.today()
        d = delta.days
        if d < 0:   return f"⏰ Просрочено ({abs(d)} дн.)"
        if d == 0:  return "⚠️ Истекает сегодня"
        if d <= 7:  return f"⚠️ Осталось {d} дн."
        return f"✅ Осталось {d} дн."
    except Exception:
        return ""


def status_icon(u: dict) -> str:
    return "🟢" if u.get("status") == "ACTIVE" else "🔴"


def fmt_card(u: dict) -> str:
    dl = days_left(u.get("expires_at", ""))
    return (
        f"{status_icon(u)} <b>{u.get('name', '—')}</b>\n"
        f"🆔 <code>{u['device_id']}</code>\n"
        f"📱 {u.get('model', '—')}  |  Android {u.get('os', '—')}\n"
        f"📊 {'🟢 ACTIVE' if u.get('status') == 'ACTIVE' else '🔴 REVOKED'}"
        + (f"  ·  {dl}" if dl else "") + "\n"
        f"📅 Истекает: <b>{u.get('expires_at', '—')}</b>\n"
        f"🔑 <code>{u.get('license_key', '—')}</code>"
    )


def user_action_keyboard(dev_id: str, status: str) -> InlineKeyboardMarkup:
    """Кнопки действий под карточкой пользователя."""
    toggle = (
        InlineKeyboardButton("🟢 Разблокировать", callback_data=f"restore:{dev_id}")
        if status == "REVOKED"
        else InlineKeyboardButton("🔴 Заблокировать",  callback_data=f"revoke:{dev_id}")
    )
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Изменить срок",  callback_data=f"extend:{dev_id}"),
            InlineKeyboardButton("📝 Редактировать",  callback_data=f"edit:{dev_id}"),
        ],
        [
            toggle,
            InlineKeyboardButton("🗑 Удалить",        callback_data=f"delete:{dev_id}"),
        ],
        [InlineKeyboardButton("« Назад к списку",    callback_data="back_to_list")],
    ])


def back_keyboard(callback: str = "back_to_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("« Назад", callback_data=callback)
    ]])


# ── Главное меню ─────────────────────────────────────────────

@admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    users  = store.users()
    total  = len(users)
    active = sum(1 for u in users if u.get("status") == "ACTIVE")
    await update.message.reply_text(
        f"🔐 <b>VScan Admin</b>\n\n"
        f"📦 Лицензий в базе: <b>{total}</b>  |  🟢 Активных: <b>{active}</b>\n\n"
        f"Выберите действие:",
        parse_mode="HTML",
        reply_markup=MAIN_KEYBOARD,
    )


# ── Список лицензий ──────────────────────────────────────────

PAGE_SIZE = 8


def list_keyboard(users: list[dict], page: int, total: int, query: str = "") -> InlineKeyboardMarkup:
    """Инлайн-кнопки: страница карточек + пагинация."""
    rows = []
    start = page * PAGE_SIZE
    chunk = users[start : start + PAGE_SIZE]

    for u in chunk:
        icon = status_icon(u)
        label = f"{icon} {u.get('name','—')}  ·  {u.get('device_id','')[:8]}…"
        rows.append([InlineKeyboardButton(label, callback_data=f"view:{u['device_id']}")])

    # Пагинация
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"page:{page-1}:{query}"))
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="noop"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"page:{page+1}:{query}"))
    if nav:
        rows.append(nav)

    return InlineKeyboardMarkup(rows)


async def show_list(update_or_query, ctx: ContextTypes.DEFAULT_TYPE,
                    page: int = 0, query: str = "", edit: bool = False):
    users = store.users()
    if query:
        q = query.lower()
        users = [
            u for u in users
            if q in (u.get("device_id","") + u.get("name","") + u.get("model","")).lower()
        ]

    total = len(users)
    if not total:
        tip = f' по запросу "<i>{query}</i>"' if query else ""
        text = f"📭 Лицензий не найдено{tip}."
        kb   = back_keyboard()
        if edit:
            await update_or_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await update_or_query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return

    header = (
        f"📋 <b>Лицензии</b>  ({total})"
        + (f'  🔍 <i>"{query}"</i>' if query else "")
        + "\nВыберите запись:"
    )
    kb = list_keyboard(users, page, total, query)

    if edit:
        await update_or_query.edit_message_text(header, parse_mode="HTML", reply_markup=kb)
    else:
        await update_or_query.message.reply_text(header, parse_mode="HTML", reply_markup=kb)


@admin_only
async def on_btn_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["list_query"] = ""
    await show_list(update, ctx)


# ── Поиск ─────────────────────────────────────────────────────

@admin_only
async def on_btn_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["await_search"] = True
    await update.message.reply_text(
        "🔍 Введите имя, Device ID или модель устройства:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="cancel_search")
        ]]),
    )
    return SEARCH_QUERY


async def search_handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    ctx.user_data["list_query"] = query
    await show_list(update, ctx, query=query)
    return ConversationHandler.END


async def search_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("❌ Поиск отменён.")
    return ConversationHandler.END


# ── Просмотр карточки ─────────────────────────────────────────

async def show_user_card(query, dev_id: str):
    user = store.find(dev_id)
    if not user:
        await query.edit_message_text(f"❌ Не найден: <code>{dev_id}</code>", parse_mode="HTML")
        return
    await query.edit_message_text(
        fmt_card(user),
        parse_mode="HTML",
        reply_markup=user_action_keyboard(dev_id, user.get("status", "")),
    )


# ── Быстрое продление срока ───────────────────────────────────

def extend_keyboard(dev_id: str) -> InlineKeyboardMarkup:
    options = [7, 14, 30, 60, 90, 180, 365]
    rows = []
    row = []
    for d in options:
        row.append(InlineKeyboardButton(f"+{d} дн.", callback_data=f"extend_do:{dev_id}:{d}"))
        if len(row) == 4:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("« Назад", callback_data=f"view:{dev_id}")])
    return InlineKeyboardMarkup(rows)


# ── Статистика ────────────────────────────────────────────────

@admin_only
async def on_btn_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    users   = store.users()
    total   = len(users)
    active  = sum(1 for u in users if u.get("status") == "ACTIVE")
    revoked = total - active
    today   = datetime.date.today().isoformat()
    expired = sum(
        1 for u in users
        if u.get("status") == "ACTIVE" and u.get("expires_at", "9999") < today
    )
    soon = sum(
        1 for u in users
        if u.get("status") == "ACTIVE"
        and 0 <= (
            (datetime.date.fromisoformat(u["expires_at"]) - datetime.date.today()).days
            if u.get("expires_at") else -1
        ) <= 7
    )
    key_fp = hashlib.sha256(SECRET_KEY).hexdigest()[:12]
    await update.message.reply_text(
        f"📊 <b>Статистика</b>\n\n"
        f"📦 Всего лицензий:    <b>{total}</b>\n"
        f"🟢 Активных:          <b>{active}</b>\n"
        f"🔴 Заблокированных:   <b>{revoked}</b>\n"
        f"⏰ Просрочено:        <b>{expired}</b>\n"
        f"⚠️ Истекает ≤7 дней: <b>{soon}</b>\n\n"
        f"🔐 SECRET_KEY: <code>{key_fp}…</code>",
        parse_mode="HTML",
    )


# ── Экспорт ───────────────────────────────────────────────────

@admin_only
async def on_btn_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    content = store.export_csv()
    if not content:
        await update.message.reply_text("📭 Нет данных для экспорта.")
        return
    buf = io.BytesIO(content.encode("utf-8"))
    ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    await update.message.reply_document(
        buf,
        filename=f"vscan_licenses_{ts}.csv",
        caption=f"📊 Экспорт · {len(store.users())} записей",
    )


# ── Обновить данные ───────────────────────────────────────────

@admin_only
async def on_btn_reload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Загрузка из Gist…")
    ok, host = store.load()
    if ok:
        await msg.edit_text(
            f"✅ Обновлено с <code>{host}</code>\n"
            f"📦 Лицензий: <b>{len(store.users())}</b>",
            parse_mode="HTML",
        )
    else:
        await msg.edit_text("❌ Все источники недоступны.")


# ── /issue — пошаговый диалог ─────────────────────────────────

@admin_only
async def issue_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    # Может быть вызван и как команда, и из callback
    msg = update.message or update.callback_query.message
    send = msg.reply_text if update.message else update.callback_query.edit_message_text

    await send(
        "➕ <b>Новая лицензия</b>\n\n"
        "<b>Шаг 1 из 5</b> — Введите <b>Device ID</b>:\n"
        "<i>(или нажмите Отмена)</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="issue_cancel_cb")
        ]]),
    )
    return ISSUE_ID

@admin_only
async def issue_get_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    dev_id = update.message.text.strip()
    if not dev_id:
        await update.message.reply_text("⚠️ Device ID не может быть пустым. Введите снова:")
        return ISSUE_ID

    ctx.user_data["device_id"] = dev_id
    existing = store.find(dev_id)
    prefix = ""
    if existing:
        ctx.user_data["existing"] = existing
        prefix = f"⚠️ Этот Device ID уже существует — запись будет обновлена.\n\n"

    await update.message.reply_text(
        prefix +
        "<b>Шаг 2 из 5</b> — Введите <b>имя пользователя</b>:"
        + (f"\n<i>Текущее: {existing.get('name','—')}</i>" if existing else ""),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="issue_cancel_cb")
        ]]),
    )
    return ISSUE_NAME


async def issue_get_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    existing = ctx.user_data.get("existing", {})
    ctx.user_data["name"] = update.message.text.strip() or existing.get("name", "User")
    await update.message.reply_text(
        "<b>Шаг 3 из 5</b> — Введите <b>модель устройства</b>:\n"
        "<i>Пример: Samsung Galaxy S21  (или «-» пропустить)</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭ Пропустить", callback_data="issue_skip_model"),
            InlineKeyboardButton("❌ Отмена",     callback_data="issue_cancel_cb"),
        ]]),
    )
    return ISSUE_MODEL


async def issue_get_model(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    existing = ctx.user_data.get("existing", {})
    val = update.message.text.strip()
    ctx.user_data["model"] = existing.get("model", "Unknown") if val == "-" else val
    await _ask_os(update.message.reply_text, ctx)
    return ISSUE_OS


async def _ask_os(reply_fn, ctx):
    existing = ctx.user_data.get("existing", {})
    await reply_fn(
        "<b>Шаг 4 из 5</b> — Версия <b>Android</b>:\n"
        "<i>Пример: 13  (или «-» пропустить)</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭ Пропустить", callback_data="issue_skip_os"),
            InlineKeyboardButton("❌ Отмена",     callback_data="issue_cancel_cb"),
        ]]),
    )


async def issue_get_os(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    existing = ctx.user_data.get("existing", {})
    val = update.message.text.strip()
    ctx.user_data["os"] = existing.get("os", "—") if val == "-" else val
    await _ask_days(update.message.reply_text)
    return ISSUE_DAYS


async def _ask_days(reply_fn):
    await reply_fn(
        "<b>Шаг 5 из 5</b> — Срок действия:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("7 дней",  callback_data="days:7"),
                InlineKeyboardButton("14 дней", callback_data="days:14"),
                InlineKeyboardButton("30 дней", callback_data="days:30"),
            ],
            [
                InlineKeyboardButton("60 дней",  callback_data="days:60"),
                InlineKeyboardButton("90 дней",  callback_data="days:90"),
                InlineKeyboardButton("180 дней", callback_data="days:180"),
                InlineKeyboardButton("365 дней", callback_data="days:365"),
            ],
            [InlineKeyboardButton("✏️ Ввести вручную", callback_data="days:manual")],
            [InlineKeyboardButton("❌ Отмена",          callback_data="issue_cancel_cb")],
        ]),
    )


async def issue_get_days(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Принимает число дней введённое вручную."""
    try:
        days = max(1, int(update.message.text.strip()))
    except ValueError:
        await update.message.reply_text("⚠️ Введите число дней (например: 30):")
        return ISSUE_DAYS
    await _build_preview(update.message.reply_text, ctx, days)
    return ISSUE_CONFIRM


async def _build_preview(reply_fn, ctx, days: int):
    dev_id     = ctx.user_data["device_id"]
    expires_at = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
    key        = create_key(dev_id, expires_at)
    user = {
        "device_id":   dev_id,
        "name":        ctx.user_data.get("name", "User"),
        "model":       ctx.user_data.get("model", "Unknown"),
        "os":          ctx.user_data.get("os", "—"),
        "status":      "ACTIVE",
        "expires_at":  expires_at,
        "license_key": key,
    }
    ctx.user_data["pending_user"] = user
    await reply_fn(
        f"📋 <b>Предпросмотр лицензии:</b>\n\n{fmt_card(user)}\n\n"
        f"<b>Всё верно?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Создать",  callback_data="issue_confirm"),
                InlineKeyboardButton("❌ Отмена",   callback_data="issue_cancel_cb"),
            ]
        ]),
    )


# ── Callback-хаб ─────────────────────────────────────────────

@admin_only  # <-- ОБЯЗАТЕЛЬНО ДОБАВЬ ЭТУ СТРОКУ ТУТ
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # ── Навигация ──────────────────────────────────────────────
    if data == "noop":
        return

    if data == "back_to_menu":
        users  = store.users()
        total  = len(users)
        active = sum(1 for u in users if u.get("status") == "ACTIVE")
        await query.edit_message_text(
            f"🔐 <b>VScan Admin</b>\n\n"
            f"📦 Лицензий: <b>{total}</b>  |  🟢 Активных: <b>{active}</b>",
            parse_mode="HTML",
        )
        return

    if data == "back_to_list":
        q = ctx.user_data.get("list_query", "")
        await show_list(query, ctx, query=q, edit=True)
        return

    if data.startswith("page:"):
        _, pg, *q_parts = data.split(":")
        q = ":".join(q_parts)
        ctx.user_data["list_query"] = q
        await show_list(query, ctx, page=int(pg), query=q, edit=True)
        return

    # ── Просмотр карточки ──────────────────────────────────────
    if data.startswith("view:"):
        dev_id = data.split(":", 1)[1]
        await show_user_card(query, dev_id)
        return

    # ── Быстрое продление срока ────────────────────────────────
    if data.startswith("extend:"):
        dev_id = data.split(":", 1)[1]
        user   = store.find(dev_id)
        if not user:
            await query.edit_message_text("❌ Пользователь не найден.")
            return
        # Вычисляем текущий остаток
        try:
            base = datetime.date.fromisoformat(user["expires_at"])
            if base < datetime.date.today():
                base = datetime.date.today()
        except Exception:
            base = datetime.date.today()
        ctx.user_data[f"extend_base_{dev_id}"] = base.isoformat()
        await query.edit_message_text(
            f"⏳ <b>Продление лицензии</b>\n"
            f"👤 {user.get('name','—')} · <code>{dev_id}</code>\n"
            f"📅 Текущий срок: <b>{user.get('expires_at','—')}</b>\n\n"
            f"На сколько продлить?",
            parse_mode="HTML",
            reply_markup=extend_keyboard(dev_id),
        )
        return

    if data.startswith("extend_do:"):
        _, dev_id, days_str = data.split(":")
        days   = int(days_str)
        user   = store.find(dev_id)
        if not user:
            await query.edit_message_text("❌ Пользователь не найден.")
            return
        base_str = ctx.user_data.get(f"extend_base_{dev_id}")
        try:
            base = datetime.date.fromisoformat(base_str) if base_str else datetime.date.today()
        except Exception:
            base = datetime.date.today()
        new_exp = (base + datetime.timedelta(days=days)).isoformat()
        user["expires_at"]  = new_exp
        user["license_key"] = create_key(user["device_id"], new_exp)
        store.update_user(user)
        await query.edit_message_text("⏳ Сохранение…")
        ok = store.save()
        if ok:
            await query.edit_message_text(
                f"✅ Срок продлён на {days} дней\n"
                f"📅 Новая дата: <b>{new_exp}</b>\n\n{fmt_card(user)}",
                parse_mode="HTML",
                reply_markup=user_action_keyboard(dev_id, user.get("status","")),
            )
        else:
            await query.edit_message_text("❌ Ошибка сохранения.")
        return

    # ── Revoke / Restore ──────────────────────────────────────
    if data.startswith("revoke:"):
        dev_id = data.split(":", 1)[1]
        user   = store.find(dev_id)
        name   = user.get("name","—") if user else dev_id
        await query.edit_message_text(
            f"⚠️ Заблокировать <b>{name}</b>?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔴 Да, заблокировать", callback_data=f"revoke_yes:{dev_id}"),
                InlineKeyboardButton("« Назад",              callback_data=f"view:{dev_id}"),
            ]]),
        )
        return

    if data.startswith("revoke_yes:"):
        dev_id = data.split(":", 1)[1]
        store.revoke({dev_id})
        await query.edit_message_text("⏳ Сохранение…")
        ok = store.save()
        user = store.find(dev_id)
        if ok and user:
            await query.edit_message_text(
                f"🔴 Заблокирован\n\n{fmt_card(user)}",
                parse_mode="HTML",
                reply_markup=user_action_keyboard(dev_id, user.get("status","")),
            )
        else:
            await query.edit_message_text("❌ Ошибка." if not ok else "✅ Заблокирован.")
        return

    if data.startswith("restore:"):
        dev_id = data.split(":", 1)[1]
        store.restore({dev_id})
        await query.edit_message_text("⏳ Сохранение…")
        ok = store.save()
        user = store.find(dev_id)
        if ok and user:
            await query.edit_message_text(
                f"🟢 Разблокирован\n\n{fmt_card(user)}",
                parse_mode="HTML",
                reply_markup=user_action_keyboard(dev_id, user.get("status","")),
            )
        else:
            await query.edit_message_text("❌ Ошибка." if not ok else "✅ Разблокирован.")
        return

    # ── Delete ─────────────────────────────────────────────────
    if data.startswith("delete:"):
        dev_id = data.split(":", 1)[1]
        user   = store.find(dev_id)
        name   = user.get("name","—") if user else dev_id
        await query.edit_message_text(
            f"🗑 Удалить запись <b>{name}</b>?\n"
            f"<code>{dev_id}</code>\n"
            f"<i>Это действие необратимо.</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑 Да, удалить", callback_data=f"delete_yes:{dev_id}"),
                InlineKeyboardButton("« Назад",        callback_data=f"view:{dev_id}"),
            ]]),
        )
        return

    if data.startswith("delete_yes:"):
        dev_id = data.split(":", 1)[1]
        store.delete({dev_id})
        await query.edit_message_text("⏳ Сохранение…")
        ok = store.save()
        q = ctx.user_data.get("list_query", "")
        await query.edit_message_text(
            f"{'🗑 Запись удалена.' if ok else '❌ Ошибка сохранения.'}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("« К списку", callback_data=f"page:0:{q}")
            ]]),
        )
        return

    # ── Edit inline (быстрое редактирование) ──────────────────
    if data.startswith("edit:"):
        dev_id = data.split(":", 1)[1]
        await query.edit_message_text(
            f"✏️ Редактирование <code>{dev_id}</code>\n\n"
            f"Используйте команду:\n<code>/edit {dev_id}</code>",
            parse_mode="HTML",
            reply_markup=back_keyboard(f"view:{dev_id}"),
        )
        return

    # ── Issue: кнопки пропуска и выбора дней ──────────────────
    if data == "issue_skip_model":
        existing = ctx.user_data.get("existing", {})
        ctx.user_data["model"] = existing.get("model", "Unknown")
        await _ask_os(query.edit_message_text, ctx)
        return

    if data == "issue_skip_os":
        existing = ctx.user_data.get("existing", {})
        ctx.user_data["os"] = existing.get("os", "—")
        await _ask_days(query.edit_message_text)
        return

    if data.startswith("days:"):
        val = data.split(":", 1)[1]
        if val == "manual":
            await query.edit_message_text(
                "✏️ Введите количество дней вручную:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Отмена", callback_data="issue_cancel_cb")
                ]]),
            )
            # Переключаем состояние на ISSUE_DAYS через флаг
            ctx.user_data["awaiting_days_manual"] = True
            return
        days = int(val)
        await _build_preview(query.edit_message_text, ctx, days)
        # Callback не может вернуть состояние — используем флаг
        ctx.user_data["issue_awaiting_confirm"] = True
        return

    if data == "issue_confirm":
        user = ctx.user_data.get("pending_user")
        if not user:
            await query.edit_message_text("❌ Данные потеряны. Нажмите ➕ Выдать лицензию.")
            return
        await query.edit_message_text("⏳ Сохранение…")
        store.upsert_user(user)
        ok = store.save()
        if ok:
            await query.edit_message_text(
                f"✅ <b>Лицензия создана!</b>\n\n{fmt_card(user)}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("👁 Открыть карточку", callback_data=f"view:{user['device_id']}"),
                    InlineKeyboardButton("📋 К списку",         callback_data="page:0:"),
                ]]),
            )
        else:
            await query.edit_message_text("❌ Ошибка сохранения.")
        ctx.user_data.clear()
        return

    if data == "issue_cancel_cb":
        ctx.user_data.clear()
        await query.edit_message_text("❌ Отменено.")
        return

    if data == "cancel_search":
        await query.edit_message_text("❌ Поиск отменён.")
        return


# ── Роутер текстовых сообщений (кнопки нижней панели) ────────

@admin_only
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == BTN_LIST:
        ctx.user_data["list_query"] = ""
        await show_list(update, ctx)
    elif text == BTN_SEARCH:
        await on_btn_search(update, ctx)
    elif text == BTN_ISSUE:
        # Запускаем диалог выдачи лицензии
        ctx.user_data.clear()
        await update.message.reply_text(
            "➕ <b>Новая лицензия</b>\n\n"
            "<b>Шаг 1 из 5</b> — Введите <b>Device ID</b>:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Отмена", callback_data="issue_cancel_cb")
            ]]),
        )
        ctx.user_data["issue_step"] = "id"
    elif text == BTN_STATS:
        await on_btn_stats(update, ctx)
    elif text == BTN_EXPORT:
        await on_btn_export(update, ctx)
    elif text == BTN_RELOAD:
        await on_btn_reload(update, ctx)
    else:
        # Перехватываем ввод для многошагового issue
        step = ctx.user_data.get("issue_step")
        if step:
            await _handle_issue_step(update, ctx, text)
        elif ctx.user_data.get("awaiting_days_manual"):
            ctx.user_data.pop("awaiting_days_manual", None)
            try:
                days = max(1, int(text.strip()))
            except ValueError:
                await update.message.reply_text("⚠️ Введите число дней:")
                ctx.user_data["awaiting_days_manual"] = True
                return
            await _build_preview(update.message.reply_text, ctx, days)
            ctx.user_data["issue_awaiting_confirm"] = True


async def _handle_issue_step(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str):
    step = ctx.user_data.get("issue_step")

    if step == "id":
        dev_id = text.strip()
        if not dev_id:
            await update.message.reply_text("⚠️ Device ID не может быть пустым:")
            return
        ctx.user_data["device_id"] = dev_id
        existing = store.find(dev_id)
        ctx.user_data["existing"] = existing or {}
        prefix = "⚠️ Этот Device ID уже существует — запись будет обновлена.\n\n" if existing else ""
        await update.message.reply_text(
            prefix + "<b>Шаг 2 из 5</b> — Введите <b>имя пользователя</b>:"
            + (f"\n<i>Текущее: {existing.get('name','—')}</i>" if existing else ""),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Отмена", callback_data="issue_cancel_cb")
            ]]),
        )
        ctx.user_data["issue_step"] = "name"

    elif step == "name":
        existing = ctx.user_data.get("existing", {})
        ctx.user_data["name"] = text.strip() or existing.get("name", "User")
        await update.message.reply_text(
            "<b>Шаг 3 из 5</b> — <b>Модель устройства</b>:\n"
            "<i>(или «-» пропустить)</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭ Пропустить", callback_data="issue_skip_model"),
                InlineKeyboardButton("❌ Отмена",     callback_data="issue_cancel_cb"),
            ]]),
        )
        ctx.user_data["issue_step"] = "model"

    elif step == "model":
        existing = ctx.user_data.get("existing", {})
        val = text.strip()
        ctx.user_data["model"] = existing.get("model", "Unknown") if val == "-" else val
        await update.message.reply_text(
            "<b>Шаг 4 из 5</b> — Версия <b>Android</b>:\n"
            "<i>(или «-» пропустить)</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭ Пропустить", callback_data="issue_skip_os"),
                InlineKeyboardButton("❌ Отмена",     callback_data="issue_cancel_cb"),
            ]]),
        )
        ctx.user_data["issue_step"] = "os"

    elif step == "os":
        existing = ctx.user_data.get("existing", {})
        val = text.strip()
        ctx.user_data["os"] = existing.get("os", "—") if val == "-" else val
        await _ask_days(update.message.reply_text)
        ctx.user_data["issue_step"] = "days"

    elif step == "days":
        try:
            days = max(1, int(text.strip()))
        except ValueError:
            await update.message.reply_text("⚠️ Введите число дней (например: 30):")
            return
        ctx.user_data.pop("issue_step", None)
        await _build_preview(update.message.reply_text, ctx, days)
        ctx.user_data["issue_awaiting_confirm"] = True


# ── /edit — пошаговый диалог (оставляем как команду) ─────────

EDIT_FIELD_LABELS = {
    "name":       "👤 Имя",
    "model":      "📱 Модель",
    "os":         "🤖 Android",
    "expires_at": "📅 Дата истечения",
    "status":     "📊 Статус",
}


@admin_only
async def edit_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text(
            "Использование: /edit <code>&lt;device_id&gt;</code>",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    dev_id = ctx.args[0]
    user   = store.find(dev_id)
    if not user:
        await update.message.reply_text(f"❌ Не найден: <code>{dev_id}</code>", parse_mode="HTML")
        return ConversationHandler.END

    ctx.user_data["edit_user"] = user
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
        f"✏️ <b>Редактирование</b> <code>{dev_id}</code>\n\nВыберите поле:",
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
        f"Сейчас: <code>{user.get(field, '—')}</code>\n\n"
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

    if field == "expires_at":
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            await update.message.reply_text("⚠️ Неверный формат. Нужно YYYY-MM-DD:")
            return EDIT_VALUE

    if field == "status" and value not in ("ACTIVE", "REVOKED"):
        await update.message.reply_text("⚠️ Статус: ACTIVE или REVOKED:")
        return EDIT_VALUE

    user[field] = value
    if field == "expires_at":
        user["license_key"] = create_key(user["device_id"], value)

    msg = await update.message.reply_text("⏳ Сохранение…")
    store.update_user(user)
    ok = store.save()

    await msg.edit_text(
        f"✅ Сохранено!\n\n{fmt_card(user)}" if ok else "❌ Ошибка сохранения.",
        parse_mode="HTML",
        reply_markup=user_action_keyboard(user["device_id"], user.get("status","")) if ok else None,
    )
    return ConversationHandler.END


async def edit_cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено.")
    return ConversationHandler.END


# ── Запуск ────────────────────────────────────────────────────

async def post_init(app: Application) -> None:
    key_hash  = hashlib.sha256(SECRET_KEY).hexdigest()[:12]
    source    = "env VSCAN_SECRET_KEY" if os.environ.get("VSCAN_SECRET_KEY") else "встроенный"
    log.info("SECRET_KEY: %s | sha256[:12]=%s", source, key_hash)

    await app.bot.set_my_commands([
        BotCommand("start",  "Главное меню"),
        BotCommand("edit",   "Редактировать запись"),
    ])

    log.info("Загрузка данных при старте…")
    ok, host = store.load()
    if ok:
        log.info("Загружено %d лицензий с %s", len(store.users()), host)
    else:
        log.warning("Не удалось загрузить данные при старте")


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан.")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # /edit ConversationHandler
    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("edit", edit_start)],
        states={
            EDIT_FIELD: [CallbackQueryHandler(edit_choose_field, pattern="^(editfield:|edit_cancel)")],
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_set_value)],
        },
        fallbacks=[CommandHandler("cancel", edit_cancel_cmd)],
        per_chat=True, per_user=True, per_message=False,
    )

    # Поиск ConversationHandler
    search_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{BTN_SEARCH}$"), on_btn_search)],
        states={
            SEARCH_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_handle),
                CallbackQueryHandler(search_cancel, pattern="^cancel_search$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", search_cancel)],
        per_chat=True, per_user=True, per_message=False,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_start))
    app.add_handler(edit_conv)
    app.add_handler(search_conv)
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        on_text,
    ))

    log.info("Бот запущен…")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
