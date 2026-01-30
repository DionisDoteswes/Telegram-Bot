# main.py - ВЕРСИЯ 3.0 (Финальный релиз-кандидат)

import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.enums import ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv

# --- Интеграция с Celery ---
from tasks import transcribe_audio_task, transcribe_from_google_drive_task 
from tasks import transcribe_from_yandex_disk_task

# --- Конфигурация (Пит-лейн) ---
# Загружаем секреты из файла .env
load_dotenv() 

# Токен бота - твой ключ зажигания
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set")

# Настройка логирования - "Телеметрия"
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- Состояния пользователя (Бортовой компьютер пилота) ---
class UserState(StatesGroup):
    idle = State()
    waiting_for_audio = State()
    processing_audio = State()


# --- Обработчики команд (Переключатели на руле) ---

@dp.message(CommandStart())
async def start_command(message: types.Message, state: FSMContext):
    await message.answer('Привет! Я могу расшифровывать лекции. Пришли мне аудиофайл или ссылку, но прежде чем начать, убедись, что в аудио собеседник говорит четко и нет посторонних шумов.')
    await state.set_state(UserState.waiting_for_audio)

# --- НОВЫЙ, ЕДИНЫЙ БЛОК ОБРАБОТЧИКОВ ---

# 1. СНАЧАЛА - самое специфичное (АУДИО)
@dp.message(F.content_type == ContentType.AUDIO, UserState.waiting_for_audio)
async def handle_audio(message: types.Message, state: FSMContext):
    await state.set_state(UserState.processing_audio)
    status_message = await message.answer("Получил аудиофайл. Отправляю в обработку... 🚀")
    
    audio_file_info = await bot.get_file(message.audio.file_id)
    file_path_on_server = audio_file_info.file_path
    
    task = transcribe_audio_task.delay(file_path_on_server)
    logging.info(f"Задача {task.id} (аудио) отправлена в Celery.")

    # Общий код для ожидания результата (можно вынести в отдельную функцию!)
    await wait_and_process_result(task, message, status_message, state)


# 2. ВТОРОЕ - специфичное правило для ТЕКСТА
@dp.message(F.content_type == ContentType.TEXT, UserState.waiting_for_audio)
async def handle_text(message: types.Message, state: FSMContext):
    # Проверяем, есть ли в сообщении ссылки
    if message.entities:
        for entity in message.entities:
            if entity.type == "url":
                url = entity.extract_from(message.text)
                
                # --- ЕСЛИ НАШЛИ ССЫЛКУ ---
                await state.set_state(UserState.processing_audio)
                status_message = await message.answer(f"Получил ссылку: {url}\nНачинаю обработку... 🚀")

                # Проверяем, что за ссылка, и вызываем нужную задачу
                if "drive.google.com" in url:
                    task = transcribe_from_google_drive_task.delay(url)
                    logging.info(f"Задача {task.id} (Google Drive) отправлена в Celery.")
                    await wait_and_process_result(task, message, status_message, state)
                elif "disk.yandex.ru" in url: # <--- ТВОЙ НОВЫЙ БЛОК
                    task = transcribe_from_yandex_disk_task.delay(url)
                    logging.info(f"Задача {task.id} (Яндекс.Диск) отправлена в Celery.")
                    await wait_and_process_result(task, message, status_message, state)
                # Тут можно будет добавить elif для YouTube и т.д.
                else:
                    await message.answer("❌ Извините, я поддерживаю только ссылки на Google Диск и Яндекс.Диск.")
                    await state.set_state(UserState.waiting_for_audio)
                    await status_message.delete()
                
                return # Выходим, чтобы не обработать как "простой текст"

    # --- ЕСЛИ ССЫЛОК НЕ НАШЛИ ---
    await message.answer("Это не похоже на аудиофайл или ссылку. Пожалуйста, пришли мне аудиофайл или ссылку на него.")

# --- Вспомогательная функция для ожидания результата (Принцип DRY!) ---
async def wait_and_process_result(task, message, status_message, state):
    try:
        result_text = await asyncio.to_thread(task.get, timeout=28800)
        await message.answer("✅ Расшифровка готова:")
        if result_text:
            for i in range(0, len(result_text), 4000):
                await message.answer(result_text[i:i + 4000])
        else:
            await message.answer("Не удалось распознать текст.")
    except Exception as e:
        logging.error(f"Задача {task.id} провалилась: {e}")
        await message.answer("❌ Произошла ошибка во время обработки.")
    finally:
        await status_message.delete()
        await state.set_state(UserState.idle)
        logging.info(f"Обработка для пользователя {message.from_user.id} завершена.")



# --- Главная функция запуска бота (Старт гонки) ---
async def main():
    os.makedirs("downloads", exist_ok=True)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


# --- Запуск приложения (Поворот ключа зажигания) ---
if __name__ == "__main__":
    logging.info("Бот запускается...")
    asyncio.run(main())