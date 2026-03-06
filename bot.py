import logging
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

import os
TOKEN = os.environ.get("TOKEN")

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

DB_FILE = "work_hours.db"

PAUSE_PRESETS = {
    "☕ 15 хв": 15,
    "☕☕ 30 хв": 30,
    "🍽 60 хв (15+15+30)": 60,
    "⌨️ Ввести вручну": -1,
}

DAYS_UA = {0: "Понеділок", 1: "Вівторок", 2: "Середа", 3: "Четвер", 4: "Пʼятниця", 5: "Субота", 6: "Неділя"}


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
            net_hours REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def save_log(user_id: int, date: str, start: str, end: str, pause_min: int, net_hours: float):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Видалити якщо вже є запис за цей день
    c.execute("DELETE FROM work_log WHERE user_id=? AND date=?", (user_id, date))
    c.execute(
        "INSERT INTO work_log (user_id, date, start_time, end_time, pause_minutes, net_hours) VALUES (?,?,?,?,?,?)",
        (user_id, date, start, end, pause_min, net_hours),
    )
    conn.commit()
    conn.close()


def get_week_logs(user_id: int, week_start: str, week_end: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "SELECT date, start_time, end_time, pause_minutes, net_hours FROM work_log "
        "WHERE user_id=? AND date>=? AND date<=? ORDER BY date",
        (user_id, week_start, week_end),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def get_month_logs(user_id: int, year_month: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "SELECT date, net_hours FROM work_log WHERE user_id=? AND date LIKE ? ORDER BY date",
        (user_id, f"{year_month}%"),
    )
    rows = c.fetchall()
    conn.close()
    return rows


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def parse_time(s: str):
    """Parse HH:MM or H:MM"""
    for fmt in ("%H:%M", "%H.%M"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            pass
    return None


def current_week_range():
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    return monday.isoformat(), friday.isoformat()


def format_hours(h: float) -> str:
    total_min = round(h * 60)
    hrs = total_min // 60
    mins = total_min % 60
    if mins == 0:
        return f"{hrs} год"
    return f"{hrs} год {mins} хв"


# ─── STATES (in-memory) ───────────────────────────────────────────────────────
# user_id -> {"start": str, "end": str, "date": str}
pending = {}


# ─── HANDLERS ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 Привіт! Я рахую твої робочі години.\n\n"
        "📌 *Команди:*\n"
        "/log `9:00 18:30` — записати день\n"
        "/today — переглянути сьогодні\n"
        "/week — підсумок тижня (Пн–Пт)\n"
        "/month — підсумок місяця\n"
        "/delete — видалити запис за сьогодні\n"
        "/help — показати підказку\n\n"
        "Паузи за замовчуванням: *60 хв* (15+15+30)"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /log 9:00 18:30          — запис з паузою 60 хв (за замовчуванням)
    /log 9:00 18:30 45       — запис з власною паузою 45 хв
    /log 9:00 18:30 0        — без пауз
    """
    user_id = update.effective_user.id
    args = context.args

    if len(args) < 2:
        await update.message.reply_text(
            "❌ Формат: `/log 9:00 18:30` або `/log 9:00 18:30 45` (хвилини паузи)",
            parse_mode="Markdown",
        )
        return

    start_t = parse_time(args[0])
    end_t = parse_time(args[1])

    if not start_t or not end_t:
        await update.message.reply_text("❌ Не можу розпізнати час. Формат: `9:00` або `9.00`", parse_mode="Markdown")
        return

    if end_t <= start_t:
        await update.message.reply_text("❌ Час кінця має бути після початку.")
        return

    # Пауза
    if len(args) >= 3:
        try:
            pause_min = int(args[2])
        except ValueError:
            await update.message.reply_text("❌ Пауза має бути числом хвилин, наприклад `60`", parse_mode="Markdown")
            return
    else:
        # Зберегти стан і запитати про паузу
        date_str = datetime.now().date().isoformat()
        pending[user_id] = {"start": args[0], "end": args[1], "date": date_str}

        keyboard = [
            ["☕ 15 хв", "☕☕ 30 хв"],
            ["🍽 60 хв (15+15+30)", "⌨️ Ввести вручну"],
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text("⏸ Скільки тривали паузи?", reply_markup=reply_markup)
        return

    await _save_and_reply(update, user_id, datetime.now().date().isoformat(), args[0], args[1], pause_min)


async def handle_pause_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if user_id not in pending:
        return  # не наш стан

    if text == "⌨️ Ввести вручну":
        await update.message.reply_text(
            "✏️ Введи кількість хвилин паузи (наприклад `45`):",
            parse_mode="Markdown",
        )
        pending[user_id]["waiting_manual"] = True
        return

    if pending[user_id].get("waiting_manual"):
        try:
            pause_min = int(text)
        except ValueError:
            await update.message.reply_text("❌ Введи просто число, наприклад `45`")
            return
    else:
        pause_min = PAUSE_PRESETS.get(text)
        if pause_min is None:
            return  # невідоме повідомлення

    data = pending.pop(user_id)
    await _save_and_reply(update, user_id, data["date"], data["start"], data["end"], pause_min)


async def _save_and_reply(update, user_id, date_str, start_str, end_str, pause_min):
    start_t = parse_time(start_str)
    end_t = parse_time(end_str)

    total_min = (end_t - start_t).seconds // 60
    net_min = max(0, total_min - pause_min)
    net_hours = net_min / 60

    save_log(user_id, date_str, start_str, end_str, pause_min, net_hours)

    date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
    day_name = DAYS_UA[date_obj.weekday()]

    text = (
        f"✅ *{day_name}, {date_obj.strftime('%d.%m')}*\n"
        f"🕐 {start_str} — {end_str}\n"
        f"⏸ Пауза: {pause_min} хв\n"
        f"💼 Чиста робота: *{format_hours(net_hours)}*"
    )
    from telegram import ReplyKeyboardRemove
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    today = datetime.now().date().isoformat()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "SELECT start_time, end_time, pause_minutes, net_hours FROM work_log WHERE user_id=? AND date=?",
        (user_id, today),
    )
    row = c.fetchone()
    conn.close()

    if not row:
        await update.message.reply_text("📭 Сьогодні ще немає записів.\n\nВикористай `/log 9:00 18:30`", parse_mode="Markdown")
        return

    start_t, end_t, pause_min, net_hours = row
    date_obj = datetime.strptime(today, "%Y-%m-%d").date()
    day_name = DAYS_UA[date_obj.weekday()]

    text = (
        f"📋 *{day_name}, {date_obj.strftime('%d.%m')}*\n"
        f"🕐 {start_t} — {end_t}\n"
        f"⏸ Пауза: {pause_min} хв\n"
        f"💼 Чиста робота: *{format_hours(net_hours)}*"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def week_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    week_start, week_end = current_week_range()
    rows = get_week_logs(user_id, week_start, week_end)

    start_dt = datetime.strptime(week_start, "%Y-%m-%d").date()
    end_dt = datetime.strptime(week_end, "%Y-%m-%d").date()

    header = f"📊 *Тиждень {start_dt.strftime('%d.%m')}–{end_dt.strftime('%d.%m')}*\n\n"

    if not rows:
        await update.message.reply_text(header + "📭 Записів немає.", parse_mode="Markdown")
        return

    logs_by_date = {r[0]: r for r in rows}
    lines = []
    total = 0.0

    for i in range(5):  # Пн–Пт
        d = start_dt + timedelta(days=i)
        d_str = d.isoformat()
        day_name = DAYS_UA[i]
        if d_str in logs_by_date:
            _, s, e, pause, net = logs_by_date[d_str]
            total += net
            lines.append(f"*{day_name[:2]}* {d.strftime('%d.%m')}  {s}–{e}  ⏸{pause}хв  → *{format_hours(net)}*")
        else:
            lines.append(f"*{day_name[:2]}* {d.strftime('%d.%m')}  —")

    sep = "─" * 28
    text = header + "\n".join(lines) + f"\n{sep}\n🔢 Всього: *{format_hours(total)}*"
    await update.message.reply_text(text, parse_mode="Markdown")


async def month_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    now = datetime.now()
    year_month = now.strftime("%Y-%m")
    rows = get_month_logs(user_id, year_month)

    if not rows:
        await update.message.reply_text(f"📭 Немає записів за {now.strftime('%B %Y')}.")
        return

    total = sum(r[1] for r in rows)
    days = len(rows)
    avg = total / days if days else 0

    text = (
        f"📅 *{now.strftime('%B %Y')}*\n\n"
        f"📆 Робочих днів записано: {days}\n"
        f"💼 Всього годин: *{format_hours(total)}*\n"
        f"📈 Середньо на день: *{format_hours(avg)}*"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    today = datetime.now().date().isoformat()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM work_log WHERE user_id=? AND date=?", (user_id, today))
    deleted = c.rowcount
    conn.commit()
    conn.close()

    if deleted:
        await update.message.reply_text("🗑 Запис за сьогодні видалено.")
    else:
        await update.message.reply_text("📭 Сьогодні немає записів для видалення.")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("log", log_cmd))
    app.add_handler(CommandHandler("today", today_cmd))
    app.add_handler(CommandHandler("week", week_cmd))
    app.add_handler(CommandHandler("month", month_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))

    # Обробник вибору пауз (кнопки або ручне введення)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pause_choice))

    logger.info("Бот запущено...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
