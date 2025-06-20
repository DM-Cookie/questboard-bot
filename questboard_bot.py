import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters
)
from deta import Deta  # Импорт Deta SDK
import uuid

# --- Настройка логирования ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Инициализация Deta Base (замена Redis) ---
deta = Deta()  # Ключ автоматически подхватывается из окружения Deta
db = deta.Base("questboard_data")  # Создаем "базу данных"

# --- Константы ---
MASTER_ID = 6239609031  # Ваш ID в Telegram
TOKEN = os.getenv("TELEGRAM_TOKEN")  # Получаем токен из переменных окружения

# --- Состояния ConversationHandler ---
MASTER_MENU, GROUP_CREATION, TASK_CREATION = range(3)
PLAYER_MENU, TASK_VIEW = range(2, 4)

# --- Функции для работы с Deta Base ---
def create_group(name: str) -> str:
    group_id = str(uuid.uuid4())
    db.put({
        "key": f"group_{group_id}",
        "name": name,
        "link": f"https://t.me/your_bot?start=join_{group_id}",
        "users": [],
        "tasks": []
    })
    return group_id

def get_group(group_id: str) -> dict:
    return db.get(f"group_{group_id}")

def add_user_to_group(user_id: int, group_id: str):
    group = db.get(f"group_{group_id}")
    if group and str(user_id) not in group.get("users", []):
        group["users"].append(str(user_id))
        db.put(group)

def create_task(group_id: str, name: str, description: str, customer: str) -> str:
    task_id = str(uuid.uuid4())
    task = {
        "id": task_id,
        "name": name,
        "description": description,
        "customer": customer,
        "status": "Не выполнено"
    }
    group = db.get(f"group_{group_id}")
    if group:
        group["tasks"].append(task)
        db.put(group)
    return task_id

# --- Обработчики команд ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    
    if context.args and context.args[0].startswith('join_'):
        group_id = context.args[0][5:]
        add_user_to_group(user_id, group_id)
        await update.message.reply_text(f'Вы в группе "{get_group(group_id)["name"]}"!')
        return PLAYER_MENU
    
    if user_id == MASTER_ID:
        return await master_menu(update, context)
    else:
        return await player_menu(update, context)

async def master_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [InlineKeyboardButton("Создать группу", callback_data='create_group')],
        [InlineKeyboardButton("Создать задачу", callback_data='create_task')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text('Меню мастера:', reply_markup=reply_markup)
    else:
        await update.message.reply_text('Меню мастера:', reply_markup=reply_markup)
    
    return MASTER_MENU

async def player_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    # Здесь должна быть логика получения групп пользователя
    await update.message.reply_text("Меню игрока")
    return PLAYER_MENU

async def create_group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text('Введите название группы:')
    return GROUP_CREATION

async def group_created(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    group_name = update.message.text
    group_id = create_group(group_name)
    await update.message.reply_text(f'Группа "{group_name}" создана!')
    return await master_menu(update, context)

# --- Главная функция ---
def main() -> None:
    application = Application.builder().token(TOKEN).build()
    
    # Обработчики для мастера
    master_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MASTER_MENU: [
                CallbackQueryHandler(create_group_handler, pattern="^create_group$"),
                CallbackQueryHandler(create_task_handler, pattern="^create_task$")
            ],
            GROUP_CREATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, group_created)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )
    
    application.add_handler(master_conv)
    application.run_polling()

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Действие отменено.')
    return ConversationHandler.END

if __name__ == '__main__':
    main()
