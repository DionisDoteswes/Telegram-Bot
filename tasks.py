import os
from celery import Celery
from celery.utils.log import get_task_logger # 1. Импортируем профессиональный логгер
import whisper
import requests
from dotenv import load_dotenv
import gdown
import subprocess
import yadisk

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# --- Настройка Celery ---
app = Celery('tasks', broker='redis://localhost:6379/0', backend='redis://localhost:6379/0')

# --- Настройка Логгера ---
# Создаем специальный логгер для наших задач
logger = get_task_logger(__name__)

# --- Конфигурация и загрузка "станка" ---
# 2. Делаем выбор модели гибким через переменные окружения
# Если переменная не задана, по умолчанию будет "base"
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base") 
logger.info(f"Загружаю модель Whisper: {WHISPER_MODEL}...")
model = whisper.load_model(WHISPER_MODEL)
logger.info("Модель Whisper успешно загружена.")

# --- "Производственная линия" (Сама задача) ---

@app.task(bind=True)
def transcribe_from_google_drive_task(self, file_url: str) -> str:
    local_filename = f"downloads/{self.request.id}.tmp"
    logger.info(f"Задача {self.request.id}: Начинаю обработку URL с Google Диска...")

    try:
        # --- ФАЗА 1: СКАЧИВАНИЕ ---
        file_id = file_url.split('/d/')[1].split('/')[0]
        download_url = f'https://drive.google.com/uc?export=download&id={file_id}'
        logger.info(f"Задача {self.request.id}: Преобразовал URL в прямую ссылку.")

        # --- ИСПОЛЬЗУЕМ WGET - ОН УМЕЕТ "ДОКАЧИВАТЬ"! ---
        # --continue (-c): Если скачивание оборвется, он продолжит с того же места!
        # --timeout=60: Ждать ответа от сервера не более 60 секунд
        # --tries=10: Попробовать скачать 10 раз, прежде чем сдаться
        command = [
            'wget',
            '-c', '-O', local_filename,
            '--timeout=60', '--tries=10',
            download_url
        ]
        
        logger.info(f"Задача {self.request.id}: Запускаю скачивание с помощью wget...")
        subprocess.run(command, check=True) # check=True вызовет ошибку, если wget провалится
        
        logger.info(f"Задача {self.request.id}: Файл успешно скачан.")

        # --- ФАЗА 2: ОБРАБОТКА ---
        logger.info(f"Задача {self.request.id}: Начинаю транскрибацию...")
        result = model.transcribe(local_filename, language="ru")
        transcribed_text = result["text"]
        logger.info(f"Задача {self.request.id}: Транскрибация успешно завершена.")
        return transcribed_text

    except subprocess.CalledProcessError as e:
        logger.error(f"Задача {self.request.id}: Wget не смог скачать файл. Код ошибки: {e.returncode}")
        raise # Поднимаем исключение, чтобы Celery знал о провале
    except Exception as e:
        logger.error(f"Задача {self.request.id}: Произошла ошибка: {e}", exc_info=True)
        raise
    finally:
        # --- ГАРАНТИРОВАННАЯ УБОРКА ---
        logger.info(f"Задача {self.request.id}: Выполняю очистку временного файла {local_filename}")
        if os.path.exists(local_filename):
            os.remove(local_filename)

@app.task(bind=True)
def transcribe_from_yandex_disk_task(self, file_url: str) -> str:
    local_filename = f"downloads/{self.request.id}.tmp"
    logger.info(f"Задача {self.request.id}: Начинаю обработку URL с Яндекс.Диска...")

    try:
        # --- ФАЗА 1: СКАЧИВАНИЕ ---
        
        # Создаем анонимный (без токена) клиент для Яндекс.Диска
        y = yadisk.YaDisk()
        
        # Библиотека сама "разберет" публичную ссылку и скачает файл
        y.download_public(file_url, local_filename)
        
        logger.info(f"Задача {self.request.id}: Файл с Яндекс.Диска успешно скачан.")

        # --- ФАЗА 2: ОБРАБОТКА ---
        logger.info(f"Задача {self.request.id}: Начинаю транскрибацию...")
        result = model.transcribe(local_filename, language="ru")
        transcribed_text = result["text"]
        logger.info(f"Задача {self.request.id}: Транскрибация успешно завершена.")
        return transcribed_text

    except Exception as e:
        logger.error(f"Задача {self.request.id}: Произошла ошибка: {e}", exc_info=True)
        raise
    finally:
        # --- ФАЗА 3: УБОРКА  ---
        logger.info(f"Задача {self.request.id}: Выполняю очистку временного файла {local_filename}")
        if os.path.exists(local_filename):
            os.remove(local_filename)
@app.task(bind=True)
def transcribe_audio_task(self, file_path_on_server: str) -> str:
    # --- СКАЧИВАНИЕ ФАЙЛА ПО ССЫЛКЕ ---
    # 1. Строим полную, секретную ссылку для скачивания
    file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path_on_server}"
    
    # 2. Даем уникальное имя для сохранения
    local_filename = f"downloads/{file_path_on_server.split('/')[-1]}"
    
    logger.info(f"Задача {self.request.id}: Начинаю скачивание файла с {file_url}")
    
    # 3. Скачиваем файл
    try:
        with requests.get(file_url, stream=True) as r:
            r.raise_for_status()
            with open(local_filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): 
                    f.write(chunk)
        logger.info(f"Задача {self.request.id}: Файл {local_filename} успешно скачан.")
    except Exception as e:
        logger.error(f"Задача {self.request.id}: Ошибка при скачивании файла: {e}")
        raise

    # Мы передаем в Whisper путь к ЛОКАЛЬНОМУ, уже скачанному файлу
    try:
        result = model.transcribe(local_filename, language="ru") 
        transcribed_text = result["text"]
        return transcribed_text
        
    except Exception as e:
        logger.error(f"Задача {self.request.id}: Ошибка при обработке файла {local_filename}: {e}", exc_info=True)
        # Поднимаем исключение дальше, чтобы Celery зафиксировал провал
        raise
        
    finally:
        # 3. Гарантированная уборка.
        logger.info(f"Задача {self.request.id}: Выполняю очистку временного файла {local_filename}")
        if os.path.exists(local_filename):
            os.remove(local_filename)
