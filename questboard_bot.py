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
import redis
import uuid

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Получаем переменные окружения (Render добавит их)
TOKEN = os.getenv("TELEGRAM_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")  # Получим позже из Redis Cloud
MASTER_ID = int(os.getenv("MASTER_ID", "6239609031"))  # Ваш ID в Telegram

# Подключение к Redis (используем облачный Redis, а не локальный)
r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# Константы состояний
MASTER_MENU, GROUP_CREATION, TASK_CREATION, TASK_EDITING, GROUP_EDITING = range(5)
PLAYER_MENU, TASK_VIEW, TASK_STATUS_CHANGE = range(5, 8)

# ID мастера
MASTER_ID = 6239609031

# Функции для работы с Redis
def create_group(name: str) -> str:
    group_id = str(uuid.uuid4())
    r.hset(f'group:{group_id}', mapping={
        'name': name,
        'link': f'https://t.me/QuestBoardBot?start=join_{group_id}'
    })
    return group_id


def get_group(group_id: str) -> dict:
    return r.hgetall(f'group:{group_id}')


def get_all_groups() -> list:
    return [key.split(':')[1] for key in r.keys('group:*')]


def delete_group(group_id: str):
    r.delete(f'group:{group_id}')
    r.srem(f'user:{MASTER_ID}:groups', group_id)
    for user_id in r.smembers(f'group:{group_id}:users'):
        r.srem(f'user:{user_id}:groups', group_id)
    r.delete(f'group:{group_id}:users')


def add_user_to_group(user_id: int, group_id: str):
    r.sadd(f'group:{group_id}:users', user_id)
    r.sadd(f'user:{user_id}:groups', group_id)


def get_user_groups(user_id: int) -> list:
    return list(r.smembers(f'user:{user_id}:groups'))


def create_task(group_id: str, name: str, description: str, customer: str) -> str:
    task_id = str(uuid.uuid4())
    r.hset(f'task:{task_id}', mapping={
        'name': name,
        'description': description,
        'customer': customer,
        'status': 'Не выполнено',
        'group_id': group_id
    })
    r.sadd(f'group:{group_id}:tasks', task_id)
    return task_id


def get_task(task_id: str) -> dict:
    return r.hgetall(f'task:{task_id}')


def get_group_tasks(group_id: str) -> list:
    return list(r.smembers(f'group:{group_id}:tasks'))


def update_task_status(task_id: str, status: str):
    r.hset(f'task:{task_id}', 'status', status)


def delete_task(task_id: str):
    task = get_task(task_id)
    group_id = task.get('group_id')
    if group_id:
        r.srem(f'group:{group_id}:tasks', task_id)
    r.delete(f'task:{task_id}')


# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id

    if context.args and context.args[0].startswith('join_'):
        group_id = context.args[0][5:]
        add_user_to_group(user_id, group_id)
        await update.message.reply_text(f'Вы присоединились к группе "{get_group(group_id)["name"]}"!')
        return await player_menu(update, context)

    if user_id == MASTER_ID:
        return await master_menu(update, context)
    else:
        return await player_menu(update, context)


async def master_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [InlineKeyboardButton("Создать группу", callback_data='create_group')],
        [InlineKeyboardButton("Редактировать группу", callback_data='edit_group')],
        [InlineKeyboardButton("Создать задачу", callback_data='create_task')],
        [InlineKeyboardButton("Редактировать задачу", callback_data='edit_task')],
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
    groups = get_user_groups(user_id)

    if not groups:
        await update.message.reply_text('Вы не состоите ни в одной группе.')
        return ConversationHandler.END

    keyboard = []
    for group_id in groups:
        group = get_group(group_id)
        keyboard.append([InlineKeyboardButton(group['name'], callback_data=f'view_group_{group_id}')])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text('Ваши группы:', reply_markup=reply_markup)
    else:
        await update.message.reply_text('Ваши группы:', reply_markup=reply_markup)

    return PLAYER_MENU


async def create_group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text('Введите название группы:')
    return GROUP_CREATION


async def group_created(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    group_name = update.message.text
    group_id = create_group(group_name)
    r.sadd(f'user:{MASTER_ID}:groups', group_id)
    group = get_group(group_id)

    await update.message.reply_text(
        f'Группа "{group_name}" создана!\n'
        f'Ссылка для приглашения игроков: {group["link"]}'
    )
    return await master_menu(update, context)


async def edit_group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    groups = get_user_groups(MASTER_ID)
    if not groups:
        await query.edit_message_text('У вас нет групп для редактирования.')
        return await master_menu(update, context)

    keyboard = []
    for group_id in groups:
        group = get_group(group_id)
        keyboard.append([
            InlineKeyboardButton(group['name'], callback_data=f'group_actions_{group_id}')
        ])

    keyboard.append([InlineKeyboardButton("Назад", callback_data='back_to_master')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text('Выберите группу для редактирования:', reply_markup=reply_markup)
    return GROUP_EDITING


async def group_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    group_id = query.data.split('_')[-1]
    context.user_data['current_group'] = group_id

    keyboard = [
        [InlineKeyboardButton("Удалить группу", callback_data=f'delete_group_{group_id}')],
        [InlineKeyboardButton("Получить ссылку", callback_data=f'get_link_{group_id}')],
        [InlineKeyboardButton("Назад", callback_data='back_to_group_edit')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text('Действия с группой:', reply_markup=reply_markup)
    return GROUP_EDITING


async def delete_group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    group_id = query.data.split('_')[-1]
    group = get_group(group_id)
    delete_group(group_id)
    await query.edit_message_text(f'Группа "{group["name"]}" удалена!')
    return await master_menu(update, context)


async def get_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    group_id = query.data.split('_')[-1]
    group = get_group(group_id)
    await query.edit_message_text(f'Ссылка для приглашения в группу "{group["name"]}":\n{group["link"]}')
    return await group_actions(update, context)


async def create_task_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    groups = get_user_groups(MASTER_ID)
    if not groups:
        await query.edit_message_text('У вас нет групп для создания задач.')
        return await master_menu(update, context)

    keyboard = []
    for group_id in groups:
        group = get_group(group_id)
        keyboard.append([
            InlineKeyboardButton(group['name'], callback_data=f'select_group_for_task_{group_id}')
        ])

    keyboard.append([InlineKeyboardButton("Назад", callback_data='back_to_master')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text('Выберите группу для задачи:', reply_markup=reply_markup)
    return TASK_CREATION


async def select_group_for_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    group_id = query.data.split('_')[-1]
    context.user_data['task_group'] = group_id
    await query.edit_message_text('Введите название задачи:')
    return 'GET_TASK_NAME'


async def get_task_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    context.user_data['task_name'] = update.message.text
    await update.message.reply_text('Введите описание задачи:')
    return 'GET_TASK_DESCRIPTION'


async def get_task_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    context.user_data['task_description'] = update.message.text
    await update.message.reply_text('Введите имя заказчика:')
    return 'GET_TASK_CUSTOMER'


async def get_task_customer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    customer = update.message.text
    group_id = context.user_data['task_group']
    name = context.user_data['task_name']
    description = context.user_data['task_description']

    task_id = create_task(group_id, name, description, customer)
    await update.message.reply_text(f'Задача "{name}" создана!')

    # Очищаем временные данные
    context.user_data.pop('task_group', None)
    context.user_data.pop('task_name', None)
    context.user_data.pop('task_description', None)

    return await master_menu(update, context)


async def edit_task_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    groups = get_user_groups(MASTER_ID)
    if not groups:
        await query.edit_message_text('У вас нет групп с задачами.')
        return await master_menu(update, context)

    keyboard = []
    for group_id in groups:
        group = get_group(group_id)
        keyboard.append([
            InlineKeyboardButton(group['name'], callback_data=f'select_group_for_edit_{group_id}')
        ])

    keyboard.append([InlineKeyboardButton("Назад", callback_data='back_to_master')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text('Выберите группу:', reply_markup=reply_markup)
    return TASK_EDITING


async def select_group_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    group_id = query.data.split('_')[-1]
    tasks = get_group_tasks(group_id)

    if not tasks:
        await query.edit_message_text('В этой группе нет задач.')
        return await edit_task_handler(update, context)

    keyboard = []
    for task_id in tasks:
        task = get_task(task_id)
        keyboard.append([
            InlineKeyboardButton(task['name'], callback_data=f'select_task_{task_id}')
        ])

    keyboard.append([InlineKeyboardButton("Назад", callback_data='back_to_task_edit')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text('Выберите задачу:', reply_markup=reply_markup)
    return TASK_EDITING


async def select_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    task_id = query.data.split('_')[-1]
    task = get_task(task_id)

    keyboard = [
        [InlineKeyboardButton("Удалить задачу", callback_data=f'delete_task_{task_id}')],
        [InlineKeyboardButton("Назад", callback_data='back_to_task_select')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    message = (
        f"Задача: {task['name']}\n"
        f"Описание: {task['description']}\n"
        f"Заказчик: {task['customer']}\n"
        f"Статус: {task['status']}"
    )
    await query.edit_message_text(message, reply_markup=reply_markup)
    return TASK_EDITING


async def delete_task_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    task_id = query.data.split('_')[-1]
    task = get_task(task_id)
    delete_task(task_id)
    await query.edit_message_text(f'Задача "{task["name"]}" удалена!')
    return await master_menu(update, context)


async def view_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    group_id = query.data.split('_')[-1]
    tasks = get_group_tasks(group_id)

    if not tasks:
        await query.edit_message_text('В этой группе пока нет задач.')
        return await player_menu(update, context)

    keyboard = []
    for task_id in tasks:
        task = get_task(task_id)
        keyboard.append([
            InlineKeyboardButton(
                f"{task['name']} ({task['status']})",
                callback_data=f'view_task_{task_id}'
            )
        ])

    keyboard.append([InlineKeyboardButton("Назад", callback_data='back_to_player')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text('Задачи в группе:', reply_markup=reply_markup)
    return TASK_VIEW


async def view_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    task_id = query.data.split('_')[-1]
    task = get_task(task_id)
    context.user_data['current_task'] = task_id

    keyboard = []
    if task['status'] == 'Не выполнено':
        keyboard.append([InlineKeyboardButton("Взять задание", callback_data='take_task')])
    elif task['status'] == 'Взято задание':
        keyboard.append([InlineKeyboardButton("Выполнено", callback_data='complete_task')])
        keyboard.append([InlineKeyboardButton("Отменить взятие", callback_data='cancel_task')])

    keyboard.append([InlineKeyboardButton("Назад", callback_data=f'back_to_group_{task["group_id"]}')])
    reply_markup = InlineKeyboardMarkup(keyboard)

    message = (
        f"Задача: {task['name']}\n"
        f"Описание: {task['description']}\n"
        f"Заказчик: {task['customer']}\n"
        f"Статус: {task['status']}"
    )
    await query.edit_message_text(message, reply_markup=reply_markup)
    return TASK_STATUS_CHANGE


async def change_task_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    task_id = context.user_data['current_task']
    task = get_task(task_id)

    if query.data == 'take_task':
        update_task_status(task_id, 'Взято задание')
        await query.edit_message_text(f'Вы взяли задание "{task["name"]}"!')
    elif query.data == 'complete_task':
        update_task_status(task_id, 'Выполнено')
        await query.edit_message_text(f'Задание "{task["name"]}" выполнено!')
    elif query.data == 'cancel_task':
        update_task_status(task_id, 'Не выполнено')
        await query.edit_message_text(f'Вы отменили взятие задания "{task["name"]}".')

    return await view_task(update, context)


async def back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == 'back_to_master':
        return await master_menu(update, context)
    elif query.data == 'back_to_player':
        return await player_menu(update, context)
    elif query.data == 'back_to_group_edit':
        return await edit_group_handler(update, context)
    elif query.data == 'back_to_task_edit':
        return await edit_task_handler(update, context)
    elif query.data == 'back_to_task_select':
        group_id = get_task(context.user_data['current_task'])['group_id']
        return await select_group_for_edit(update, context)
    elif query.data.startswith('back_to_group_'):
        group_id = query.data.split('_')[-1]
        context.callback_data = f'view_group_{group_id}'
        return await view_group(update, context)

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Действие отменено.')
    return ConversationHandler.END


def main() -> None:
    # Создаем Application
    application = Application.builder().token(TOKEN).build()

    # Обработчик команды /start
    application.add_handler(CommandHandler('start', start))

    # Обработчики для мастера с правильными настройками
    master_conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.User(user_id=MASTER_ID) & filters.COMMAND, start)
        ],
        states={
            MASTER_MENU: [
                CallbackQueryHandler(create_group_handler, pattern='^create_group$'),
                CallbackQueryHandler(edit_group_handler, pattern='^edit_group$'),
                CallbackQueryHandler(create_task_handler, pattern='^create_task$'),
                CallbackQueryHandler(edit_task_handler, pattern='^edit_task$'),
            ],
            GROUP_CREATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, group_created)
            ],
            GROUP_EDITING: [
                CallbackQueryHandler(group_actions, pattern='^group_actions_'),
                CallbackQueryHandler(delete_group_handler, pattern='^delete_group_'),
                CallbackQueryHandler(get_link_handler, pattern='^get_link_'),
                CallbackQueryHandler(back_handler, pattern='^back_to_'),
            ],
            TASK_CREATION: [
                CallbackQueryHandler(select_group_for_task, pattern='^select_group_for_task_'),
                CallbackQueryHandler(back_handler, pattern='^back_to_'),
            ],
            'GET_TASK_NAME': [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_task_name)
            ],
            'GET_TASK_DESCRIPTION': [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_task_description)
            ],
            'GET_TASK_CUSTOMER': [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_task_customer)
            ],
            TASK_EDITING: [
                CallbackQueryHandler(select_group_for_edit, pattern='^select_group_for_edit_'),
                CallbackQueryHandler(select_task, pattern='^select_task_'),
                CallbackQueryHandler(delete_task_handler, pattern='^delete_task_'),
                CallbackQueryHandler(back_handler, pattern='^back_to_'),
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False  # Изменено на False для смешанных обработчиков
    )

    # Обработчики для игрока
    player_conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(~filters.User(user_id=MASTER_ID) & filters.COMMAND, start)
        ],
        states={
            PLAYER_MENU: [
                CallbackQueryHandler(view_group, pattern='^view_group_'),
            ],
            TASK_VIEW: [
                CallbackQueryHandler(view_task, pattern='^view_task_'),
                CallbackQueryHandler(back_handler, pattern='^back_to_'),
            ],
            TASK_STATUS_CHANGE: [
                CallbackQueryHandler(change_task_status, pattern='^(take_task|complete_task|cancel_task)$'),
                CallbackQueryHandler(back_handler, pattern='^back_to_'),
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False  # Изменено на False для смешанных обработчиков
    )

    application.add_handler(master_conv_handler)
    application.add_handler(player_conv_handler)

    # Запускаем бота
    application.run_polling()

if __name__ == '__main__':
    main()