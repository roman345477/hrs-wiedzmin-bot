import os
import logging
import sqlite3
import calendar
from datetime import datetime, timedelta, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

TOKEN = os.environ.get("TOKEN")

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

DB_FILE = "work_hours.db"

DAYS_UA = {0: "Понеділок", 1: "Вівторок", 2: "Середа", 3: "Четвер", 4: "Пʼятниця", 5: "Субота", 6: "Неділя"}
DAYS_SHORT = {0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Нд"}


# ─── DATABASE ────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS work_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            pause_minutes INTEGER NOT NULL DEFAULT 60,
            net_hours REAL NOT NULL,
            UNIQUE(user_id, date)
        )
    """)
    conn.commit()
    conn.close()


def save_log(user_id, date_str, start, end, pause_min, net_hours):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO work_log (user_id, date, start_time, end_time, pause_minutes, net_hours)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(user_id, date) DO UPDATE SET
            start_time=excluded.start_time, end_time=excluded.end_time,
            pause_minutes=excluded.pause_minutes, net_hours=excluded.net_hours
    """, (user_id, date_str, start, end, pause_min, net_hours))
    conn.commit()
    conn.close()


def get_log(user_id, date_str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT start_time, end_time, pause_minutes, net_hours FROM work_log WHERE user_id=? AND date=?",
              (user_id, date_str))
    row = c.fetchone()
    conn.close()
    return row


def get_week_logs(user_id, week_start, week_end):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT date, start_time, end_time, pause_minutes, net_hours FROM work_log WHERE user_id=? AND date>=? AND date<=? ORDER BY date",
              (user_id, week_start, week_end))
    rows = c.fetchall()
    conn.close()
    return rows


def delete_log(user_id, date_str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM work_log WHERE user_id=? AND date=?", (user_id, date_str))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def parse_time(s):
    for fmt in ("%H:%M", "%H.%M", "%H,%M"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            pass
    return None


def format_hours(h):
    total_min = round(h * 60)
    hrs = total_min // 60
    mins = total_min % 60
    if mins == 0:
        return f"{hrs} год"
    return f"{hrs} год {mins} хв"


def get_week_range(offset=0):
    today = date.today()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    friday = monday + timedelta(days=4)
    return monday, friday


def day_status_emoji(date_str, user_id):
    row = get_log(user_id, date_str)
    if row:
        return "✅"
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    if d > date.today():
        return "⏳"
    if d.weekday() >= 5:
        return "🏖"
    return "❌"


def build_day_result_text(date_str, user_id):
    row = get_log(user_id, date_str)
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    day_name = DAYS_UA[d.weekday()]
    if not row:
        return f"📋 *{day_name}, {d.strftime('%d.%m')}*\n\n📭 Немає запису."
    start_t, end_t, pause_min, net_hours = row
    diff = net_hours - 8.0
    note = f"📈 +{format_hours(diff)} понаднормово" if diff > 0.1 else (
        f"📉 -{format_hours(abs(diff))} до норми" if diff < -0.1 else "🎯 Рівно норма!")
    return (
        f"📋 *{day_name}, {d.strftime('%d.%m')}*\n\n"
        f"🕐 Початок: `{start_t}`\n"
        f"🕕 Кінець: `{end_t}`\n"
        f"⏸ Пауза: `{pause_min} хв`\n"
        f"━━━━━━━━━━━━━━\n"
        f"💼 Відпрацьовано: *{format_hours(net_hours)}*\n"
        f"{note}"
    )


# ─── KEYBOARDS ───────────────────────────────────────────────────────────────

def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Записати день", callback_data="action:log"),
         InlineKeyboardButton("✏️ Редагувати", callback_data="action:edit")],
        [InlineKeyboardButton("📊 Тиждень", callback_data="week:0"),
         InlineKeyboardButton("📅 Місяць", callback_data="action:month")],
        [InlineKeyboardButton("📋 Сьогодні", callback_data="action:today"),
         InlineKeyboardButton("🗑 Видалити запис", callback_data="action:delete")],
    ])


def day_select_kb(user_id, mode, week_offset=0):
    monday, friday = get_week_range(week_offset)
    week_dates = [monday + timedelta(days=i) for i in range(5)]
    keyboard = []
    row = []
    for i, d in enumerate(week_dates):
        status = day_status_emoji(d.isoformat(), user_id)
        label = f"{status} {DAYS_SHORT[i]} {d.strftime('%d.%m')}"
        row.append(InlineKeyboardButton(label, callback_data=f"day:{mode}:{d.isoformat()}"))
        if len(row) == 2 or i == 4:
            keyboard.append(row)
            row = []
    nav = []
    if week_offset > -8:
        nav.append(InlineKeyboardButton("◀️ Раніше", callback_data=f"daysnav:{mode}:{week_offset-1}"))
    if week_offset < 0:
        nav.append(InlineKeyboardButton("▶️ Пізніше", callback_data=f"daysnav:{mode}:{week_offset+1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="action:menu")])
    return InlineKeyboardMarkup(keyboard)


def pause_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("0 хв", callback_data="pause:0"),
         InlineKeyboardButton("15 хв", callback_data="pause:15"),
         InlineKeyboardButton("30 хв", callback_data="pause:30")],
        [InlineKeyboardButton("45 хв", callback_data="pause:45"),
         InlineKeyboardButton("⭐ 60 хв", callback_data="pause:60"),
         InlineKeyboardButton("90 хв", callback_data="pause:90")],
        [InlineKeyboardButton("⌨️ Інша кількість хвилин", callback_data="pause:manual")],
        [InlineKeyboardButton("🔙 Скасувати", callback_data="action:menu")],
    ])


def edit_field_kb(date_str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🕐 Змінити початок", callback_data=f"editfield:start:{date_str}"),
         InlineKeyboardButton("🕕 Змінити кінець", callback_data=f"editfield:end:{date_str}")],
        [InlineKeyboardButton("⏸ Змінити паузу", callback_data=f"editfield:pause:{date_str}"),
         InlineKeyboardButton("🗑 Видалити день", callback_data=f"editfield:delete:{date_str}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="action:menu")],
    ])


def after_save_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Записати ще день", callback_data="action:log")],
        [InlineKeyboardButton("📊 Статистика тижня", callback_data="week:0")],
        [InlineKeyboardButton("🏠 Головне меню", callback_data="action:menu")],
    ])


# ─── HANDLERS ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    text = "🏠 *hrs · wiedzmin*\n_Облік робочих годин_\n\nОбери дію:"
    kb = main_menu_kb()
    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    # ── Головне меню ──
    if data == "action:menu":
        context.user_data.clear()
        await query.edit_message_text("🏠 *hrs · wiedzmin*\n_Облік робочих годин_\n\nОбери дію:",
                                      parse_mode="Markdown", reply_markup=main_menu_kb())

    # ── Сьогодні ──
    elif data == "action:today":
        today = date.today().isoformat()
        text = build_day_result_text(today, user_id)
        await query.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="action:menu")]]))

    # ── Записати день ──
    elif data == "action:log":
        context.user_data.clear()
        context.user_data["mode"] = "log"
        await query.edit_message_text("📝 *Записати день*\n\nВибери день:",
                                      parse_mode="Markdown", reply_markup=day_select_kb(user_id, "log"))

    # ── Редагувати ──
    elif data == "action:edit":
        context.user_data.clear()
        context.user_data["mode"] = "edit"
        await query.edit_message_text("✏️ *Редагувати запис*\n\nВибери день:",
                                      parse_mode="Markdown", reply_markup=day_select_kb(user_id, "edit"))

    # ── Видалити ──
    elif data == "action:delete":
        context.user_data.clear()
        context.user_data["mode"] = "delete"
        await query.edit_message_text("🗑 *Видалити запис*\n\nВибери день:",
                                      parse_mode="Markdown", reply_markup=day_select_kb(user_id, "delete"))

    # ── Навігація по тижнях (вибір дня) ──
    elif data.startswith("daysnav:"):
        _, mode, offset_str = data.split(":")
        offset = int(offset_str)
        monday, friday = get_week_range(offset)
        await query.edit_message_text(
            f"📅 *{monday.strftime('%d.%m')} – {friday.strftime('%d.%m')}*\n\nВибери день:",
            parse_mode="Markdown", reply_markup=day_select_kb(user_id, mode, offset))

    # ── День вибрано ──
    elif data.startswith("day:"):
        _, mode, date_str = data.split(":", 2)

        if mode == "delete":
            deleted = delete_log(user_id, date_str)
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            text = (f"🗑 Запис за *{DAYS_UA[d.weekday()]}, {d.strftime('%d.%m')}* видалено."
                    if deleted else
                    f"📭 Немає запису за *{DAYS_UA[d.weekday()]}, {d.strftime('%d.%m')}*.")
            await query.edit_message_text(text, parse_mode="Markdown",
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Меню", callback_data="action:menu")]]))

        elif mode == "edit":
            row = get_log(user_id, date_str)
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            if not row:
                await query.edit_message_text(
                    f"❌ Немає запису за *{DAYS_UA[d.weekday()]}, {d.strftime('%d.%m')}*",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📝 Записати цей день", callback_data=f"day:log:{date_str}")],
                        [InlineKeyboardButton("🔙 Назад", callback_data="action:menu")],
                    ]))
            else:
                context.user_data["edit_date"] = date_str
                s, e, p, net = row
                await query.edit_message_text(
                    f"✏️ *{DAYS_UA[d.weekday()]}, {d.strftime('%d.%m')}*\n\n"
                    f"🕐 `{s}` → 🕕 `{e}`  ⏸ `{p} хв`\n"
                    f"💼 *{format_hours(net)}*\n\nЩо змінити?",
                    parse_mode="Markdown", reply_markup=edit_field_kb(date_str))

        elif mode == "log":
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            existing = get_log(user_id, date_str)
            hint = f"\n_Поточний: {existing[0]}–{existing[1]}, пауза {existing[2]} хв_" if existing else ""
            context.user_data["log_date"] = date_str
            context.user_data["step"] = "start"
            await query.edit_message_text(
                f"📝 *{DAYS_UA[d.weekday()]}, {d.strftime('%d.%m')}*{hint}\n\n"
                f"🕐 Введи час *початку* роботи:\n`9:00` або `9.30`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Скасувати", callback_data="action:menu")]]))

    # ── Поле для редагування ──
    elif data.startswith("editfield:"):
        _, field, date_str = data.split(":", 2)
        if field == "delete":
            delete_log(user_id, date_str)
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            await query.edit_message_text(
                f"🗑 Запис за *{DAYS_UA[d.weekday()]}, {d.strftime('%d.%m')}* видалено.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Меню", callback_data="action:menu")]]))
        else:
            context.user_data["edit_date"] = date_str
            context.user_data["edit_field"] = field
            context.user_data["step"] = f"edit_{field}"
            prompts = {
                "start": "🕐 Введи новий час *початку*:",
                "end": "🕕 Введи новий час *закінчення*:",
                "pause": "⏸ Введи нову тривалість паузи в хвилинах:",
            }
            await query.edit_message_text(prompts[field], parse_mode="Markdown",
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Скасувати", callback_data="action:menu")]]))

    # ── Вибір паузи ──
    elif data.startswith("pause:"):
        val = data.split(":")[1]
        if val == "manual":
            context.user_data["step"] = "pause_manual"
            await query.edit_message_text("⌨️ Введи кількість хвилин паузи (наприклад `45`):",
                                          parse_mode="Markdown",
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Скасувати", callback_data="action:menu")]]))
        else:
            await _save_and_show(query, context, user_id, int(val))

    # ── Статистика тижня ──
    elif data.startswith("week:"):
        offset = int(data.split(":")[1])
        monday, friday = get_week_range(offset)
        rows = get_week_logs(user_id, monday.isoformat(), friday.isoformat())
        logs_by_date = {r[0]: r for r in rows}

        title = "📊 *Поточний тиждень*" if offset == 0 else (
            "📊 *Минулий тиждень*" if offset == -1 else
            f"📊 *{monday.strftime('%d.%m')} – {friday.strftime('%d.%m')}*")

        lines = [title, f"_{monday.strftime('%d.%m')} – {friday.strftime('%d.%m')}_\n"]
        total = 0.0
        logged_days = 0

        for i in range(5):
            d = monday + timedelta(days=i)
            d_str = d.isoformat()
            short = DAYS_SHORT[i]
            if d_str in logs_by_date:
                _, s, e, pause, net = logs_by_date[d_str]
                total += net
                logged_days += 1
                filled = int(net / 8 * 8)
                bar = "█" * filled + "░" * (8 - filled)
                lines.append(f"*{short}* {d.strftime('%d.%m')}  `{s}`–`{e}`  *{format_hours(net)}*\n`{bar}`")
            elif d > date.today():
                lines.append(f"*{short}* {d.strftime('%d.%m')} — ⏳ попереду")
            else:
                lines.append(f"*{short}* {d.strftime('%d.%m')} — не записано")

        lines.append("━━━━━━━━━━━━━━")
        if logged_days > 0:
            norm = logged_days * 8.0
            diff = total - norm
            sign = "+" if diff >= 0 else ""
            lines.append(f"💼 Всього: *{format_hours(total)}*")
            lines.append(f"⚖️ Баланс: *{sign}{format_hours(diff)}*")
            lines.append(f"📈 Середньо: *{format_hours(total/logged_days)}*/день")
        else:
            lines.append("📭 Немає записів за цей тиждень")

        nav = []
        nav.append(InlineKeyboardButton("◀️ Попередній", callback_data=f"week:{offset-1}"))
        if offset < 0:
            nav.append(InlineKeyboardButton("▶️ Наступний", callback_data=f"week:{offset+1}"))
        kb = InlineKeyboardMarkup([nav, [InlineKeyboardButton("🔙 Назад", callback_data="action:menu")]])
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb)

    # ── Статистика місяця ──
    elif data == "action:month":
        now = datetime.now()
        year_month = now.strftime("%Y-%m")
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT date, net_hours FROM work_log WHERE user_id=? AND date LIKE ? ORDER BY date",
                  (user_id, f"{year_month}%"))
        rows = c.fetchall()
        conn.close()

        month_name = now.strftime("%B %Y")
        work_days = sum(1 for d in range(1, calendar.monthrange(now.year, now.month)[1] + 1)
                        if datetime(now.year, now.month, d).weekday() < 5)

        if not rows:
            text = f"📅 *{month_name}*\n\n📭 Немає записів за цей місяць."
        else:
            total = sum(r[1] for r in rows)
            days = len(rows)
            avg = total / days
            norm = work_days * 8.0
            diff = total - (days * 8.0)
            sign = "+" if diff >= 0 else ""

            text = (
                f"📅 *{month_name}*\n\n"
                f"📆 Записано: *{days}* з {work_days} робочих днів\n"
                f"💼 Відпрацьовано: *{format_hours(total)}*\n"
                f"📈 Середньо/день: *{format_hours(avg)}*\n"
                f"🎯 Норма місяця: *{format_hours(norm)}*\n"
                f"━━━━━━━━━━━━━━\n"
                f"⚖️ Баланс: *{sign}{format_hours(diff)}*"
            )
            remaining = work_days - days
            if remaining > 0 and date.today().month == now.month:
                projected = total + remaining * avg
                text += f"\n🔮 Прогноз: *{format_hours(projected)}*"

        await query.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="action:menu")]]))


async def _save_and_show(query_or_msg, context, user_id, pause_min):
    date_str = context.user_data["log_date"]
    start_str = context.user_data["log_start"]
    end_str = context.user_data["log_end"]

    start_t = parse_time(start_str)
    end_t = parse_time(end_str)
    net_min = max(0, (end_t - start_t).seconds // 60 - pause_min)
    net_hours = net_min / 60

    save_log(user_id, date_str, start_str, end_str, pause_min, net_hours)
    context.user_data.clear()

    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    day_name = DAYS_UA[d.weekday()]
    diff = net_hours - 8.0
    note = (f"📈 +{format_hours(diff)} понаднормово" if diff > 0.1 else
            f"📉 -{format_hours(abs(diff))} до норми" if diff < -0.1 else "🎯 Рівно норма!")

    text = (
        f"✅ *Записано!*\n\n"
        f"📅 {day_name}, {d.strftime('%d.%m')}\n"
        f"🕐 `{start_str}` → 🕕 `{end_str}`\n"
        f"⏸ Пауза: `{pause_min} хв`\n"
        f"━━━━━━━━━━━━━━\n"
        f"💼 Відпрацьовано: *{format_hours(net_hours)}*\n"
        f"{note}"
    )

    if hasattr(query_or_msg, 'edit_message_text'):
        await query_or_msg.edit_message_text(text, parse_mode="Markdown", reply_markup=after_save_kb())
    else:
        await query_or_msg.reply_text(text, parse_mode="Markdown", reply_markup=after_save_kb())


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    step = context.user_data.get("step")

    if not step:
        await update.message.reply_text("Обери дію:", reply_markup=main_menu_kb())
        return

    # ── Введення початку ──
    if step == "start":
        t = parse_time(text)
        if not t:
            await update.message.reply_text("❌ Формат: `9:00` або `9.30`", parse_mode="Markdown")
            return
        context.user_data["log_start"] = text
        context.user_data["step"] = "end"
        await update.message.reply_text(
            f"✅ Початок: `{text}`\n\n🕕 Тепер введи час *закінчення*:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Скасувати", callback_data="action:menu")]]))

    # ── Введення кінця ──
    elif step == "end":
        t = parse_time(text)
        s = parse_time(context.user_data.get("log_start", ""))
        if not t:
            await update.message.reply_text("❌ Формат: `18:30`", parse_mode="Markdown")
            return
        if s and t <= s:
            await update.message.reply_text("❌ Час кінця має бути *після* початку!", parse_mode="Markdown")
            return
        context.user_data["log_end"] = text
        context.user_data["step"] = "pause"
        total = (t - s).seconds // 60
        await update.message.reply_text(
            f"✅ Кінець: `{text}`\n"
            f"_Загальний час: {total//60} год {total%60} хв_\n\n"
            f"⏸ *Скільки тривали паузи?*",
            parse_mode="Markdown", reply_markup=pause_kb())

    # ── Ручне введення паузи ──
    elif step == "pause_manual":
        try:
            pause_min = int(text)
            if pause_min < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Введи ціле число хвилин, наприклад `45`", parse_mode="Markdown")
            return
        context.user_data["step"] = None
        await _save_and_show(update.message, context, user_id, pause_min)

    # ── Редагування поля ──
    elif step and step.startswith("edit_"):
        field = step[5:]
        date_str = context.user_data.get("edit_date")
        row = get_log(user_id, date_str)
        if not row:
            await update.message.reply_text("❌ Запис не знайдено.", reply_markup=main_menu_kb())
            context.user_data.clear()
            return

        start_t, end_t, pause_min, _ = row

        if field in ("start", "end"):
            t = parse_time(text)
            if not t:
                await update.message.reply_text("❌ Формат: `9:00`", parse_mode="Markdown")
                return
            new_start = text if field == "start" else start_t
            new_end = text if field == "end" else end_t
            s = parse_time(new_start)
            e = parse_time(new_end)
            if e <= s:
                await update.message.reply_text("❌ Час кінця має бути після початку!", parse_mode="Markdown")
                return
            new_net = max(0, (e - s).seconds // 60 - pause_min) / 60
            save_log(user_id, date_str, new_start, new_end, pause_min, new_net)

        elif field == "pause":
            try:
                new_pause = int(text)
            except ValueError:
                await update.message.reply_text("❌ Введи число хвилин", parse_mode="Markdown")
                return
            s = parse_time(start_t)
            e = parse_time(end_t)
            new_net = max(0, (e - s).seconds // 60 - new_pause) / 60
            save_log(user_id, date_str, start_t, end_t, new_pause, new_net)

        row = get_log(user_id, date_str)
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ *Оновлено!*\n\n"
            f"📅 {DAYS_UA[d.weekday()]}, {d.strftime('%d.%m')}\n"
            f"🕐 `{row[0]}` → 🕕 `{row[1]}`\n"
            f"⏸ Пауза: `{row[2]} хв`\n"
            f"💼 *{format_hours(row[3])}*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="action:menu")]]))


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    logger.info("Бот запущено...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
