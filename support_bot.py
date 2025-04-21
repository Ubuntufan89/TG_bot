import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import xml.etree.ElementTree as ET
import xml.dom.minidom
from datetime import datetime
import json
import base64
import signal
import sys
import asyncio
import re
import mimetypes
from bs4 import BeautifulSoup
import traceback

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('/home/admind/support_bot/logs/support_bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Константы из переменных окружения
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
SERVICE_API_TOKEN = os.getenv('SERVICE_API_TOKEN')
SERVICE_API_URL = os.getenv('SERVICE_API_URL')
N8N_WEBHOOK_URL = os.getenv('N8N_WEBHOOK_URL', 'https://webhook.bitpace.com/f163441f-9bee-4a75-b266-5af9e86c2732')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
GOOGLE_SEARCH_ENGINE_ID = os.getenv('GOOGLE_SEARCH_ENGINE_ID')

# Проверка наличия необходимых переменных окружения
if not all([TELEGRAM_TOKEN, SERVICE_API_TOKEN, SERVICE_API_URL]):
    raise ValueError("Не все необходимые переменные окружения установлены. Проверьте файл .env")

# Состояния диалога
SUBJECT, DESCRIPTION, COMPANY_NAME, INN, FILES, TICKET_NUMBER, FIRST_NAME, LAST_NAME = range(8)

def load_inn_list():
    """Загрузка списка ИНН из файла"""
    try:
        with open('inn_list.json', 'r') as file:
            data = json.load(file)
            return data.get('inns', [])
    except Exception as e:
        logger.error(f"Ошибка при загрузке списка ИНН: {str(e)}")
        return []

# Загрузка списка ИНН при старте
INN_LIST = load_inn_list()

class RedmineFileUploader:
    def __init__(self, session, api_url, api_token):
        self.session = session
        self.api_url = api_url
        self.api_token = api_token
        self.headers = {
            'X-Redmine-API-Key': api_token,
            'Accept': 'application/json'
        }

    def upload_file(self, file_data, file_name):
        """Загрузка файла через API"""
        try:
            # Определяем MIME-тип файла
            mime_type = 'application/octet-stream'
            if file_name.lower().endswith('.pdf'):
                mime_type = 'application/pdf'
            elif file_name.lower().endswith(('.doc', '.docx')):
                mime_type = 'application/msword'
            elif file_name.lower().endswith(('.xls', '.xlsx')):
                mime_type = 'application/vnd.ms-excel'
            elif file_name.lower().endswith(('.jpg', '.jpeg')):
                mime_type = 'image/jpeg'
            elif file_name.lower().endswith('.png'):
                mime_type = 'image/png'

            # Добавляем необходимые заголовки
            headers = {
                'X-Redmine-API-Key': self.api_token,
                'Content-Type': 'application/octet-stream',
                'Accept': 'application/json'
            }
            
            # Отправляем файл через API
            upload_response = self.session.post(
                f'{self.api_url}/uploads.json?filename={file_name}',
                headers=headers,
                data=file_data
            )
            
            logger.info(f"Статус загрузки файла: {upload_response.status_code}")
            logger.info(f"Заголовки ответа: {dict(upload_response.headers)}")
            logger.info(f"Текст ответа: {upload_response.text}")
            
            if upload_response.status_code == 201:
                upload_data = upload_response.json()
                return {
                    'token': upload_data.get('upload', {}).get('token'),
                    'filename': file_name,
                    'content_type': mime_type
                }
            else:
                logger.error(f"Ошибка при загрузке файла {file_name}: {upload_response.status_code}")
                logger.error(f"Текст ответа: {upload_response.text}")
                return None
        except Exception as e:
            logger.error(f"Ошибка при загрузке файла {file_name}: {str(e)}")
            return None

    def attach_file_to_issue(self, issue_id, upload_data):
        """Прикрепление файла к тикету"""
        try:
            # Формируем данные для обновления тикета
            data = {
                'issue': {
                    'uploads': [upload_data]
                }
            }
            
            # Добавляем необходимые заголовки
            headers = {
                'X-Redmine-API-Key': self.api_token,
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            # Отправляем запрос на обновление тикета
            attach_response = self.session.put(
                f'{self.api_url}/issues/{issue_id}.json',
                headers=headers,
                json=data
            )
            
            logger.info(f"Статус прикрепления файла: {attach_response.status_code}")
            logger.info(f"Заголовки ответа: {dict(attach_response.headers)}")
            logger.info(f"Текст ответа: {attach_response.text[:1000]}")
            
            if attach_response.status_code in [200, 201, 204]:
                logger.info(f"Файл {upload_data['filename']} успешно прикреплен к тикету #{issue_id}")
                return True
            else:
                logger.error(f"Ошибка при прикреплении файла {upload_data['filename']}: {attach_response.status_code}")
                logger.error(f"Текст ответа: {attach_response.text}")
                return False
        except Exception as e:
            logger.error(f"Ошибка при прикреплении файла {upload_data['filename']}: {str(e)}")
            return False

    def process_files(self, issue_id, files):
        """Обработка всех файлов для тикета"""
        success_count = 0
        for file in files:
            upload_data = self.upload_file(file['data'], file['name'])
            if upload_data and self.attach_file_to_issue(issue_id, upload_data):
                success_count += 1
        return success_count

def create_session():
    """Создание сессии с поддержкой повторных попыток"""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def get_headers(content_type='application/json'):
    """Получение заголовков для API запросов"""
    headers = {
        'X-Redmine-API-Key': SERVICE_API_TOKEN,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0'
    }
    
    if content_type == 'application/json':
        headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
    elif content_type == 'multipart/form-data':
        headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7'
        })
    elif content_type == 'application/x-www-form-urlencoded':
        headers.update({
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': SERVICE_API_URL,
            'Referer': f'{SERVICE_API_URL}/projects/2/issues/new',
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7'
        })
    
    return headers

def get_csrf_token():
    """Получение CSRF-токена"""
    session = create_session()
    headers = get_headers('text/html')
    
    try:
        # Сначала получаем главную страницу
        response = session.get(SERVICE_API_URL, headers=headers)
        logger.info(f"Статус ответа главной страницы: {response.status_code}")
        
        # Проверяем, что мы авторизованы
        if response.status_code == 401:
            logger.error("Не авторизованы. Пробуем авторизоваться...")
            # Пробуем авторизоваться через API
            auth_response = session.get(f'{SERVICE_API_URL}/users/current.json', headers=get_headers())
            if auth_response.status_code != 200:
                logger.error("Не удалось авторизоваться")
                return None, None
        
        # Затем получаем страницу создания тикета
        response = session.get(f'{SERVICE_API_URL}/projects/7/issues/new', headers=headers)
        logger.info(f"Статус ответа страницы создания тикета: {response.status_code}")
        logger.info(f"Текст ответа: {response.text[:200]}...")
        
        if response.status_code == 200:
            # Пробуем разные варианты поиска токена
            patterns = [
                r'name="authenticity_token" value="([^"]+)"',
                r'csrf-token" content="([^"]+)"',
                r'csrf_token" value="([^"]+)"'
            ]
            
            for pattern in patterns:
                csrf_token = re.search(pattern, response.text)
                if csrf_token:
                    token = csrf_token.group(1)
                    logger.info(f"Найден CSRF-токен: {token[:10]}...")
                    return token, session
            
            logger.error("CSRF-токен не найден в ответе")
            return None, None
            
    except Exception as e:
        logger.error(f"Ошибка при получении CSRF-токена: {str(e)}")
        return None, None

def check_api_endpoints():
    """Проверка доступных эндпоинтов API"""
    headers = get_headers()
    endpoints = [
        '/issues.json',
        '/projects/2/issues.json'
    ]
    
    available_endpoints = []
    for endpoint in endpoints:
        url = f'{SERVICE_API_URL}{endpoint}'
        logger.info(f"Проверка эндпоинта: {url}")
        response = requests.get(url, headers=headers)
        logger.info(f"Статус ответа для {url}: {response.status_code}")
        if response.status_code != 404:
            available_endpoints.append(endpoint)
    
    return available_endpoints

def check_api_availability():
    """Проверка доступности API"""
    headers = get_headers()
    headers.update({
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-Redmine-API-Key': SERVICE_API_TOKEN,
        'Authorization': f'Basic {base64.b64encode(f"{SERVICE_API_TOKEN}:".encode()).decode()}'
    })
    
    # Проверяем базовый URL
    try:
        response = requests.get(SERVICE_API_URL, headers=headers)
        logger.info(f"Проверка базового URL: {response.status_code}")
        logger.info(f"Заголовки запроса: {dict(headers)}")
        logger.info(f"Ответ базового URL: {response.text[:200]}...")
        
        # Пробуем получить информацию о текущем пользователе
        user_url = f'{SERVICE_API_URL}/users/current.json'
        response = requests.get(user_url, headers=headers)
        logger.info(f"Проверка текущего пользователя: {response.status_code}")
        logger.info(f"Заголовки запроса: {dict(headers)}")
        logger.info(f"Ответ пользователя: {response.text[:200]}...")
        
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Ошибка при проверке API: {str(e)}")
        return False

def get_projects():
    """Получение списка проектов"""
    headers = get_headers()
    
    # Пробуем разные варианты URL для проектов
    urls_to_try = [
        f'{SERVICE_API_URL}/projects.json',
        f'{SERVICE_API_URL}/api/projects.json',
        f'{SERVICE_API_URL}/redmine/projects.json',
        f'{SERVICE_API_URL}/projects.xml'
    ]
    
    for url in urls_to_try:
        logger.info(f"Пробуем получить проекты через URL: {url}")
        response = requests.get(url, headers=headers)
        logger.info(f"Статус ответа для {url}: {response.status_code}")
        logger.info(f"Текст ответа для {url}: {response.text[:200]}...")
        
        if response.status_code == 200:
            try:
                if url.endswith('.xml'):
                    return []
                return response.json().get('projects', [])
            except Exception as e:
                logger.error(f"Ошибка при парсинге ответа: {str(e)}")
                continue
    
    return []

def get_trackers():
    """Получение списка трекеров"""
    headers = get_headers()
    
    # Пробуем разные варианты URL для трекеров
    urls_to_try = [
        f'{SERVICE_API_URL}/trackers.json',
        f'{SERVICE_API_URL}/api/trackers.json',
        f'{SERVICE_API_URL}/redmine/trackers.json',
        f'{SERVICE_API_URL}/trackers.xml'
    ]
    
    for url in urls_to_try:
        logger.info(f"Пробуем получить трекеры через URL: {url}")
        response = requests.get(url, headers=headers)
        logger.info(f"Статус ответа для {url}: {response.status_code}")
        logger.info(f"Текст ответа для {url}: {response.text[:200]}...")
        
        if response.status_code == 200:
            try:
                if url.endswith('.xml'):
                    return []
                return response.json().get('trackers', [])
            except Exception as e:
                logger.error(f"Ошибка при парсинге ответа: {str(e)}")
                continue
    
    return []

def create_xml_issue(issue_data):
    """Создание XML для тикета"""
    root = ET.Element('issue')
    
    for key, value in issue_data.items():
        if key == 'custom_fields':
            custom_fields = ET.SubElement(root, 'custom_fields')
            for field in value:
                custom_field = ET.SubElement(custom_fields, 'custom_field')
                for k, v in field.items():
                    field_elem = ET.SubElement(custom_field, k)
                    field_elem.text = str(v)
        elif key == 'watcher_user_ids':
            watchers = ET.SubElement(root, 'watcher_user_ids')
            for user_id in value:
                watcher = ET.SubElement(watchers, 'watcher_user_id')
                watcher.text = str(user_id)
        else:
            elem = ET.SubElement(root, key)
            elem.text = str(value)
    
    # Форматируем XML для красивого вывода с поддержкой UTF-8
    xmlstr = xml.dom.minidom.parseString(ET.tostring(root, encoding='unicode')).toprettyxml(indent="  ")
    return xmlstr

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    keyboard = [
        [InlineKeyboardButton("Создать тикет", callback_data='create_ticket')],
        [InlineKeyboardButton("Проверить статус", callback_data='check_status')],
        [InlineKeyboardButton("Задать вопрос", callback_data='ask_question')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        'Добро пожаловать в систему технической поддержки! '
        'Выберите действие:',
        reply_markup=reply_markup
    )

async def create_ticket_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало создания тикета"""
    query = update.callback_query
    await query.answer()
    
    # Очищаем данные о файлах из предыдущего тикета
    context.user_data['files'] = []
    
    await query.message.reply_text(
        'Уважаемый пользователь, пожалуйста, укажите тему вашего обращения:'
    )
    return SUBJECT

async def get_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение темы обращения"""
    context.user_data['subject'] = update.message.text
    await update.message.reply_text(
        'Спасибо! Теперь, пожалуйста, подробно опишите вашу проблему:'
    )
    return DESCRIPTION

async def get_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение описания проблемы"""
    context.user_data['description'] = update.message.text
    await update.message.reply_text(
        'Спасибо за подробное описание! Пожалуйста, укажите название вашей компании:'
    )
    return COMPANY_NAME

async def get_company_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение названия компании"""
    context.user_data['company_name'] = update.message.text
    await update.message.reply_text(
        'Спасибо! Теперь, пожалуйста, укажите ИНН вашей компании:'
    )
    return INN

async def get_inn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение ИНН и переход к запросу имени"""
    inn = update.message.text.strip()
    
    # Проверяем, что ИНН содержит только цифры и имеет длину 10
    if not inn.isdigit() or len(inn) != 10:
        await update.message.reply_text(
            'Уважаемый пользователь, пожалуйста, введите корректный ИНН (10 цифр):'
        )
        return INN
    
    # Проверяем, что ИНН находится в списке
    if inn not in INN_LIST:
        await update.message.reply_text(
            'Пожалуйста, свяжитесь с Вашим менеджером!'
        )
        return ConversationHandler.END
    
    context.user_data['inn'] = inn
    await update.message.reply_text(
        'Спасибо! Теперь, пожалуйста, введите ваше имя:'
    )
    return FIRST_NAME

async def get_first_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение имени пользователя"""
    first_name = update.message.text.strip()
    
    # Проверяем, что имя не пустое и содержит только буквы
    if not first_name or not first_name.replace(' ', '').isalpha():
        await update.message.reply_text(
            'Пожалуйста, введите корректное имя (только буквы):'
        )
        return FIRST_NAME
    
    context.user_data['first_name'] = first_name
    await update.message.reply_text(
        'Спасибо! Теперь, пожалуйста, введите вашу фамилию:'
    )
    return LAST_NAME

async def get_last_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение фамилии пользователя"""
    last_name = update.message.text.strip()
    
    # Проверяем, что фамилия не пустая и содержит только буквы
    if not last_name or not last_name.replace(' ', '').isalpha():
        await update.message.reply_text(
            'Пожалуйста, введите корректную фамилию (только буквы):'
        )
        return LAST_NAME
    
    context.user_data['last_name'] = last_name
    
    # Создаем клавиатуру для загрузки файлов
    keyboard = [
        [InlineKeyboardButton("Пропустить загрузку файлов", callback_data='skip_files')],
        [InlineKeyboardButton("Загрузить файлы", callback_data='upload_files')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        'Хотите ли вы прикрепить файлы к тикету?',
        reply_markup=reply_markup
    )
    return FILES

async def handle_files_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора пользователя по загрузке файлов"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'skip_files':
        context.user_data['files'] = []
        return await create_ticket(update, context)
    else:
        await query.message.reply_text(
            'Пожалуйста, отправьте файлы, которые хотите прикрепить к тикету.\n'
            'Поддерживаемые форматы: PDF, DOC, DOCX, XLS, XLSX, JPG, PNG.\n'
            'Можно отправить несколько файлов.\n'
            'После отправки всех файлов нажмите "Завершить загрузку".'
        )
        return FILES

async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка загруженных файлов"""
    if not update.message.document and not update.message.photo:
        await update.message.reply_text(
            'Пожалуйста, отправьте файл в поддерживаемом формате или нажмите "Завершить загрузку".'
        )
        return FILES
    
    # Инициализируем список файлов, если его еще нет
    if 'files' not in context.user_data:
        context.user_data['files'] = []
    
    # Обработка документа
    if update.message.document:
        file = await update.message.document.get_file()
        file_name = update.message.document.file_name
        file_data = await file.download_as_bytearray()
        context.user_data['files'].append({
            'name': file_name,
            'data': file_data
        })
        await update.message.reply_text(f'Файл "{file_name}" успешно добавлен.')
    
    # Обработка фотографии
    elif update.message.photo:
        file = await update.message.photo[-1].get_file()
        file_name = f'photo_{len(context.user_data["files"]) + 1}.jpg'
        file_data = await file.download_as_bytearray()
        context.user_data['files'].append({
            'name': file_name,
            'data': file_data
        })
        await update.message.reply_text(f'Фотография успешно добавлена.')
    
    # Создаем клавиатуру для завершения загрузки
    keyboard = [[InlineKeyboardButton("Завершить загрузку", callback_data='finish_upload')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f'Загружено файлов: {len(context.user_data["files"])}\n'
        'Хотите загрузить еще файлы или завершить загрузку?',
        reply_markup=reply_markup
    )
    return FILES

async def finish_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершение загрузки файлов и создание тикета"""
    query = update.callback_query
    await query.answer()
    
    return await create_ticket(update, context)

async def create_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создание тикета с прикрепленными файлами"""
    try:
        # Проверяем доступность API
        if not check_api_availability():
            logger.error("API недоступен")
            await update.message.reply_text(
                'Уважаемый пользователь, сервис временно недоступен. Пожалуйста, попробуйте позже.'
            )
            return ConversationHandler.END
            
        # Получаем список проектов и трекеров
        projects = get_projects()
        trackers = get_trackers()
        
        if not projects or not trackers:
            logger.error("Не удалось получить список проектов или трекеров")
            await update.message.reply_text(
                'Уважаемый пользователь, произошла ошибка при получении данных. Пожалуйста, попробуйте позже.'
            )
            return ConversationHandler.END
            
        # Используем проект с ID 7 и первый доступный трекер
        project_id = 7
        tracker_id = trackers[0]['id']
        
        # Получаем CSRF-токен и сессию
        csrf_token, session = get_csrf_token()
        if not csrf_token or not session:
            logger.error("Не удалось получить CSRF-токен")
            await update.message.reply_text(
                'Уважаемый пользователь, произошла ошибка при создании тикета. Пожалуйста, попробуйте позже.'
            )
            return ConversationHandler.END
        
        # Формируем данные для тикета
        form_data = {
            'authenticity_token': csrf_token,
            'issue[subject]': context.user_data['subject'],
            'issue[description]': f'''Тикет создан через телеграм бота
                
                Информация о пользователе:
                - Имя: {context.user_data['first_name']}
                - Фамилия: {context.user_data['last_name']}
                
                Информация о компании:
                - Название: {context.user_data['company_name']}
                - ИНН: {context.user_data['inn']}
                
                Описание проблемы:
                {context.user_data['description']}
                
                Пожалуйста, свяжитесь с пользователем для уточнения деталей.''',
            'issue[priority_id]': 2,
            'issue[project_id]': project_id,
            'issue[tracker_id]': tracker_id,
            'issue[status_id]': 1,
            'issue[custom_fields_attributes][0][id]': 1,
            'issue[custom_fields_attributes][0][value]': str(update.effective_user.id),
            'issue[watcher_user_ids][]': 1,
            'issue[assigned_to_id]': 1,
            'issue[is_private]': 0,
            'issue[lock_version]': 0,
            'issue[category_id]': '',
            'issue[fixed_version_id]': '',
            'issue[parent_issue_id]': '',
            'issue[estimated_hours]': '',
            'issue[due_date]': '',
            'issue[notes]': '',
            'issue[private_notes]': 0,
            'commit': 'Создать'
        }
        
        # Формируем заголовки для запроса
        headers = get_headers('multipart/form-data')
        headers.update({
            'Origin': SERVICE_API_URL,
            'Referer': f'{SERVICE_API_URL}/projects/{project_id}/issues/new',
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7'
        })
        
        # Отправляем запрос на создание тикета
        response = session.post(
            f'{SERVICE_API_URL}/projects/{project_id}/issues',
            headers=headers,
            data=form_data,
            allow_redirects=True
        )
        
        logger.info(f"Статус ответа: {response.status_code}")
        logger.info(f"Заголовки ответа: {dict(response.headers)}")
        logger.info(f"Текст ответа: {response.text[:1000]}...")
        
        if response.status_code in [201, 200, 302]:
            # Получаем ID созданного тикета
            ticket_id = None
            if 'Location' in response.headers:
                ticket_id = response.headers['Location'].split('/')[-1]
            else:
                import re
                ticket_match = re.search(r'/issues/(\d+)', response.text)
                if ticket_match:
                    ticket_id = ticket_match.group(1)
            
            # Если есть файлы, загружаем их через API
            if ticket_id and 'files' in context.user_data and context.user_data['files']:
                logger.info(f"Загрузка файлов для тикета #{ticket_id}")
                file_uploader = RedmineFileUploader(session, SERVICE_API_URL, SERVICE_API_TOKEN)
                success_count = file_uploader.process_files(ticket_id, context.user_data['files'])
                logger.info(f"Успешно загружено файлов: {success_count} из {len(context.user_data['files'])}")
            
            message = 'Уважаемый пользователь, тикет успешно создан!'
            if ticket_id:
                message += f'\nНомер вашего тикета: #{ticket_id}'
            message += '\nНаш специалист свяжется с вами в ближайшее время.'
            
            if update.callback_query:
                await update.callback_query.message.reply_text(message)
            else:
                await update.message.reply_text(message)
        else:
            error_message = f"Ошибка при создании тикета. Статус ответа: {response.status_code}"
            logger.error(error_message)
            logger.error(f"Текст ответа: {response.text}")
            
            if update.callback_query:
                await update.callback_query.message.reply_text(
                    'Уважаемый пользователь, произошла ошибка при создании тикета. Пожалуйста, попробуйте позже.'
                )
            else:
                await update.message.reply_text(
                    'Уважаемый пользователь, произошла ошибка при создании тикета. Пожалуйста, попробуйте позже.'
                )
            
    except Exception as e:
        error_message = f"Неожиданная ошибка при создании тикета: {str(e)}"
        logger.error(error_message)
        if update.callback_query:
            await update.callback_query.message.reply_text(
                'Уважаемый пользователь, произошла ошибка при создании тикета. Пожалуйста, попробуйте позже.'
            )
        else:
            await update.message.reply_text(
                'Уважаемый пользователь, произошла ошибка при создании тикета. Пожалуйста, попробуйте позже.'
            )
    
    return ConversationHandler.END

def get_issue_status(issue_id):
    """Получение статуса тикета"""
    try:
        session = create_session()
        headers = get_headers()
        
        # Добавляем Basic Auth
        auth_token = base64.b64encode(f"{SERVICE_API_TOKEN}:".encode()).decode()
        headers.update({
            'Authorization': f'Basic {auth_token}',
            'X-Redmine-API-Key': SERVICE_API_TOKEN
        })
        
        # Пробуем разные URL для получения тикета
        urls_to_try = [
            f'{SERVICE_API_URL}/projects/2/issues/{issue_id}.json',
            f'{SERVICE_API_URL}/issues/{issue_id}.json',
            f'{SERVICE_API_URL}/redmine/issues/{issue_id}.json',
            f'{SERVICE_API_URL}/api/v2/issues/{issue_id}.json'
        ]
        
        for url in urls_to_try:
            logger.info(f"Пробуем получить статус тикета через URL: {url}")
            logger.info(f"Заголовки запроса: {json.dumps(dict(headers), indent=2)}")
            
            response = session.get(url, headers=headers)
            logger.info(f"Статус ответа: {response.status_code}")
            logger.info(f"Заголовки ответа: {dict(response.headers)}")
            logger.info(f"Текст ответа: {response.text}")
            
            if response.status_code == 200:
                try:
                    response_data = response.json()
                    logger.info(f"Полученные данные: {json.dumps(response_data, indent=2, ensure_ascii=False)}")
                    
                    issue_data = response_data.get('issue', {})
                    if not issue_data:
                        logger.error("Данные тикета не найдены в ответе")
                        continue
                    
                    # Форматируем статус тикета
                    status_text = f"""
Тикет #{issue_id}

Статус: {issue_data.get('status', {}).get('name', 'Неизвестно')}
Приоритет: {issue_data.get('priority', {}).get('name', 'Неизвестно')}
Тема: {issue_data.get('subject', 'Нет темы')}
Описание: {issue_data.get('description', 'Нет описания')}

Создан: {datetime.strptime(issue_data.get('created_on', ''), '%Y-%m-%dT%H:%M:%SZ').strftime('%d.%m.%Y %H:%M')}
Обновлен: {datetime.strptime(issue_data.get('updated_on', ''), '%Y-%m-%dT%H:%M:%SZ').strftime('%d.%m.%Y %H:%M')}

Назначен: {issue_data.get('assigned_to', {}).get('name', 'Не назначен')}
"""
                    return status_text
                except json.JSONDecodeError as e:
                    logger.error(f"Ошибка при разборе JSON: {str(e)}")
                    logger.error(f"Текст ответа: {response.text}")
                    continue
            elif response.status_code == 404:
                logger.info(f"Тикет #{issue_id} не найден через URL {url}")
                continue
            else:
                logger.error(f"Ошибка при получении статуса тикета: {response.status_code}")
                logger.error(f"Текст ответа: {response.text}")
                continue
                
        return f"Тикет #{issue_id} не найден."
        
    except Exception as e:
        logger.error(f"Ошибка при получении статуса тикета: {str(e)}")
        return "Произошла ошибка при получении статуса тикета. Пожалуйста, попробуйте позже."

async def check_ticket_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /status"""
    await update.message.reply_text(
        'Пожалуйста, введите номер тикета (только цифры):'
    )
    return TICKET_NUMBER

async def get_ticket_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получение номера тикета"""
    ticket_number = update.message.text.strip()
    
    # Проверяем, что номер тикета содержит только цифры
    if not ticket_number.isdigit():
        await update.message.reply_text(
            'Пожалуйста, введите корректный номер тикета (только цифры):'
        )
        return TICKET_NUMBER
    
    # Получаем статус тикета
    status = get_issue_status(ticket_number)
    await update.message.reply_text(status)
    
    return ConversationHandler.END

async def check_status_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало проверки статуса тикета"""
    query = update.callback_query
    await query.answer()
    
    await query.message.reply_text(
        'Пожалуйста, введите номер тикета (только цифры):'
    )
    return TICKET_NUMBER

async def ask_question_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало диалога вопроса"""
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "Пожалуйста, введите ваш вопрос:"
    )
    return 'WAITING_QUESTION'

async def search_internet(query: str) -> str:
    """Поиск информации в интернете"""
    if not GOOGLE_API_KEY or not GOOGLE_SEARCH_ENGINE_ID:
        logger.error("Google API ключ или ID поискового движка не установлены")
        return None
        
    try:
        # Определяем ключевые слова для различных ОС
        os_keywords = {
            'astra': ['astralinux', 'astra linux', 'астралинукс', 'астра линукс', 'астра', 'astra'],
            'redos': ['redos', 'редос', 'red os', 'ред ос', 'redos linux', 'редос линукс'],
            'alt': ['базальт', 'альт', 'alt', 'alt linux', 'альт линукс', 'альтсервер', 'alt server']
        }
        
        # Проверяем, содержит ли запрос ключевые слова ОС
        found_os = []
        for os_name, keywords in os_keywords.items():
            if any(keyword in query.lower() for keyword in keywords):
                found_os.append(os_name)
        
        # Если найдены ключевые слова ОС, выполняем поиск для каждой
        if found_os:
            results = []
            for os_name in found_os:
                # Формируем запрос для конкретной ОС
                os_query = f"{query} {os_name}"
                url = f"https://www.googleapis.com/customsearch/v1"
                params = {
                    'key': GOOGLE_API_KEY,
                    'cx': GOOGLE_SEARCH_ENGINE_ID,
                    'q': os_query,
                    'num': 3,  # Получаем 3 результата для каждой ОС
                    'lr': 'lang_ru'
                }
                
                logger.info(f"Отправка запроса для ОС {os_name}: {os_query}")
                response = requests.get(url, params=params)
                response.raise_for_status()
                
                data = response.json()
                if 'items' in data and data['items']:
                    for item in data['items']:
                        results.append({
                            'os': os_name,
                            'snippet': item['snippet'],
                            'link': item['link']
                        })
            
            if results:
                # Формируем ответ с группировкой по ОС
                response_text = ""
                for os_name in found_os:
                    os_results = [r for r in results if r['os'] == os_name]
                    if os_results:
                        response_text += f"\nУстановка на {os_name.upper()}:\n"
                        for result in os_results:
                            response_text += f"\n{result['snippet']}\nПодробнее: {result['link']}\n"
                
                return response_text.strip()
        
        # Если ключевые слова ОС не найдены, выполняем обычный поиск
        url = f"https://www.googleapis.com/customsearch/v1"
        params = {
            'key': GOOGLE_API_KEY,
            'cx': GOOGLE_SEARCH_ENGINE_ID,
            'q': query,
            'num': 1,
            'lr': 'lang_ru'
        }
        
        logger.info(f"Отправка обычного запроса: {query}")
        response = requests.get(url, params=params)
        response.raise_for_status()
        
        data = response.json()
        if 'items' not in data or not data['items']:
            logger.info("Результаты поиска не найдены")
            return None
            
        # Берем первый (наиболее релевантный) результат
        item = data['items'][0]
        
        # Формируем ответ с ссылкой
        result = f"{item['snippet']}\n\nПодробнее можно узнать здесь: {item['link']}"
        
        return result
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка при поиске в интернете: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Неожиданная ошибка при поиске в интернете: {str(e)}")
        return None

async def process_user_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка введенного вопроса пользователя"""
    logger.info("Начало обработки текстового вопроса")
    
    if not update.message:
        logger.error("Update не содержит message")
        return ConversationHandler.END
        
    if not update.message.text:
        logger.error("Message не содержит text")
        await update.message.reply_text("Пожалуйста, введите текстовый вопрос.")
        return 'WAITING_QUESTION'
        
    user_question = update.message.text.strip().lower()
    logger.info(f"Получен вопрос от пользователя: {user_question}")
    
    try:
        # Открываем и читаем HTML-файл базы знаний
        logger.info("Пытаемся открыть файл wiki.html")
        if not os.path.exists('wiki.html'):
            logger.error("Файл wiki.html не найден")
            await update.message.reply_text("База знаний временно недоступна. Пожалуйста, попробуйте позже.")
            return ConversationHandler.END
            
        try:
            with open('wiki.html', 'r', encoding='utf-8') as file:
                html_content = file.read()
                logger.info(f"Файл wiki.html успешно прочитан, размер: {len(html_content)} байт")
        except Exception as e:
            logger.error(f"Ошибка при чтении файла wiki.html: {str(e)}")
            await update.message.reply_text("Ошибка при чтении базы знаний. Пожалуйста, попробуйте позже.")
            return ConversationHandler.END
        
        # Парсим HTML-страницу
        logger.info("Начинаем парсинг HTML")
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            logger.info("HTML успешно распарсен")
        except Exception as e:
            logger.error(f"Ошибка при парсинге HTML: {str(e)}")
            await update.message.reply_text("Ошибка при обработке базы знаний. Пожалуйста, попробуйте позже.")
            return ConversationHandler.END
        
        # Создаем список ключевых слов для поиска
        keywords = {
            'установка': [
                'установить', 'инсталляция', 'развертывание', 'deploy', 'install', 
                'как установить', 'как поставить', 'установка', 'инсталлировать',
                'редактор документов', 'текстовый редактор', 'word', 'writer',
                'табличный редактор', 'excel', 'calc', 'редактор таблиц',
                'редактор презентаций', 'powerpoint', 'impress', 'презентации',
                'pdf редактор', 'редактор pdf', 'редактор изображений'
            ],
            'настройка': ['настроить', 'конфигурация', 'config', 'configure', 'как настроить', 'параметры'],
            'почта': ['email', 'mail', 'письмо', 'correspondence', 'почтовый сервер', 'настройка почты'],
            'портал': ['portal', 'сайт', 'веб-интерфейс', 'вход в систему', 'личный кабинет'],
            'ошибка': ['error', 'проблема', 'не работает', 'некорректно', 'сбой', 'не запускается'],
            'документы': ['document', 'файл', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'открыть файл'],
            'права': ['permission', 'доступ', 'authorization', 'передача прав', 'делегирование', 'назначение прав'],
            'синхронизация': ['sync', 'replication', 'replica', 'обмен данными', 'синхронизировать'],
            'кластер': ['cluster', 'отказоустойчивость', 'ha', 'высокая доступность'],
            'лицензия': ['license', 'активация', 'регистрация', 'пробный период', 'подписка'],
            'пользователь': ['user', 'аккаунт', 'учетная запись', 'сотрудник', 'добавить пользователя'],
            'мобильное': ['мобильное приложение', 'android', 'ios', 'телефон', 'планшет'],
            'безопасность': ['security', 'защита', 'пароль', 'шифрование', 'ssl'],
            'резервное копирование': ['backup', 'восстановление', 'бэкап', 'архивация'],
            'интеграция': ['integration', 'api', 'подключение', '1с', 'битрикс'],
            'обновление': ['update', 'upgrade', 'версия', 'новые функции', 'патч']
        }

        # Добавляем список типичных вопросов
        common_questions = {
            'установка': [
                'Как установить R7-Office на сервер?',
                'Какие системные требования для установки на Linux?',
                'Как установить редакторы на Windows?',
                'Как установить мобильное приложение?',
                'Как установить R7-Office на несколько компьютеров?',
                'Нужно ли что-то делать после установки?',
                'Как проверить, что установка прошла успешно?',
                'Как установить дополнительные компоненты?',
                'Как установить R7-Office без прав администратора?',
                'Как установить R7-Office на терминальный сервер?',
                'Как установить R7-Office на виртуальную машину?',
                'Как установить R7-Office в Docker?',
                'Как установить R7-Office на Mac?',
                'Как установить R7-Office на Ubuntu?',
                'Как установить R7-Office на CentOS?',
                'Как установить R7-Office на Debian?',
                'Как установить R7-Office на Astra Linux?',
                'Как установить R7-Office на Alt Linux?',
                'Как установить R7-Office на RedOS?',
                'Как установить R7-Office на РосАльфа?'
            ],
            'настройка': [
                'Как настроить почтовый сервер для R7-Office?',
                'Как настроить интеграцию с 1С?',
                'Как настроить резервное копирование документов?',
                'Как настроить права доступа для сотрудников?',
                'Как настроить мобильное приложение?',
                'Как настроить общий доступ к документам?',
                'Как настроить автоматическое обновление?',
                'Как настроить уведомления?',
                'Как настроить фильтрацию спама?',
                'Как настроить синхронизацию с облаком?',
                'Как настроить прокси-сервер?',
                'Как настроить SSL-сертификат?',
                'Как настроить LDAP-аутентификацию?',
                'Как настроить Active Directory?',
                'Как настроить двухфакторную аутентификацию?',
                'Как настроить автоматическое резервное копирование?',
                'Как настроить журнал событий?',
                'Как настроить мониторинг системы?',
                'Как настроить балансировку нагрузки?',
                'Как настроить кластер?'
            ],
            'почта': [
                'Как настроить почтовый сервер?',
                'Как добавить новый почтовый ящик?',
                'Как настроить переадресацию писем?',
                'Как настроить фильтрацию спама?',
                'Как восстановить удаленные письма?',
                'Как настроить подпись в письмах?',
                'Как настроить автоответчик?',
                'Как настроить правила обработки писем?',
                'Как настроить почту на мобильном устройстве?',
                'Как настроить защиту от вирусов в почте?',
                'Как настроить шифрование писем?',
                'Как настроить архивацию писем?',
                'Как настроить квоты на почтовые ящики?',
                'Как настроить черный список отправителей?',
                'Как настроить белый список отправителей?',
                'Как настроить DKIM?',
                'Как настроить SPF?',
                'Как настроить DMARC?',
                'Как настроить антиспам?',
                'Как настроить антивирус для почты?'
            ],
            'права': [
                'Как передать права другому пользователю?',
                'Как настроить права доступа к документам?',
                'Как ограничить доступ к определенным функциям?',
                'Как создать группу пользователей?',
                'Как назначить администратора?',
                'Как настроить права на редактирование документов?',
                'Как ограничить доступ к определенным папкам?',
                'Как настроить права для внешних пользователей?',
                'Как настроить временный доступ к документам?',
                'Как отозвать права у пользователя?',
                'Как настроить роли пользователей?',
                'Как настроить права на уровне групп?',
                'Как настроить права на уровне документов?',
                'Как настроить права на уровне папок?',
                'Как настроить права на уровне разделов?',
                'Как настроить права на уровне портала?',
                'Как настроить права на уровне сервера?',
                'Как настроить права на уровне базы данных?',
                'Как настроить права на уровне файловой системы?',
                'Как настроить права на уровне API?'
            ],
            'мобильное': [
                'Как установить мобильное приложение?',
                'Как синхронизировать с десктопной версией?',
                'Как работать с документами на телефоне?',
                'Как настроить уведомления?',
                'Как восстановить доступ к мобильному приложению?',
                'Как настроить автономный режим?',
                'Как настроить синхронизацию документов?',
                'Как работать с документами без интернета?',
                'Как настроить безопасность на мобильном устройстве?',
                'Как настроить быстрый доступ к документам?',
                'Как настроить мобильное приложение для Android?',
                'Как настроить мобильное приложение для iOS?',
                'Как настроить мобильное приложение для Huawei?',
                'Как настроить мобильное приложение для Honor?',
                'Как настроить мобильное приложение для Xiaomi?',
                'Как настроить мобильное приложение для Samsung?',
                'Как настроить мобильное приложение для Honor?',
                'Как настроить мобильное приложение для Vivo?',
                'Как настроить мобильное приложение для Oppo?',
                'Как настроить мобильное приложение для Realme?'
            ],
            'безопасность': [
                'Как настроить двухфакторную аутентификацию?',
                'Как защитить документы паролем?',
                'Как настроить SSL-сертификат?',
                'Как ограничить доступ по IP?',
                'Как настроить политику безопасности?',
                'Как настроить защиту от вирусов?',
                'Как настроить шифрование документов?',
                'Как настроить журнал безопасности?',
                'Как настроить блокировку при подозрительной активности?',
                'Как настроить автоматическое резервное копирование?',
                'Как настроить защиту от DDoS?',
                'Как настроить защиту от брутфорса?',
                'Как настроить защиту от SQL-инъекций?',
                'Как настроить защиту от XSS?',
                'Как настроить защиту от CSRF?',
                'Как настроить защиту от фишинга?',
                'Как настроить защиту от спама?',
                'Как настроить защиту от вирусов?',
                'Как настроить защиту от шпионского ПО?',
                'Как настроить защиту от руткитов?'
            ],
            'резервное копирование': [
                'Как настроить автоматическое резервное копирование?',
                'Как восстановить данные из резервной копии?',
                'Как часто нужно делать бэкап?',
                'Где хранятся резервные копии?',
                'Как проверить целостность резервной копии?',
                'Как настроить резервное копирование на внешний диск?',
                'Как настроить резервное копирование в облако?',
                'Как восстановить отдельные файлы из резервной копии?',
                'Как настроить автоматическое удаление старых копий?',
                'Как проверить, что резервное копирование работает?',
                'Как настроить резервное копирование на FTP?',
                'Как настроить резервное копирование на SFTP?',
                'Как настроить резервное копирование на SMB?',
                'Как настроить резервное копирование на NFS?',
                'Как настроить резервное копирование на WebDAV?',
                'Как настроить резервное копирование на Amazon S3?',
                'Как настроить резервное копирование на Google Cloud?',
                'Как настроить резервное копирование на Яндекс.Облако?',
                'Как настроить резервное копирование на Mail.ru Cloud?',
                'Как настроить резервное копирование на Selectel?'
            ],
            'интеграция': [
                'Как настроить интеграцию с 1С?',
                'Как подключить Битрикс24?',
                'Как настроить API?',
                'Как синхронизировать с CRM?',
                'Как настроить обмен с другими системами?',
                'Как настроить интеграцию с почтовыми серверами?',
                'Как настроить интеграцию с календарем?',
                'Как настроить интеграцию с мессенджерами?',
                'Как настроить интеграцию с облачными хранилищами?',
                'Как настроить интеграцию с системами документооборота?',
                'Как настроить интеграцию с SAP?',
                'Как настроить интеграцию с Oracle?',
                'Как настроить интеграцию с Microsoft Dynamics?',
                'Как настроить интеграцию с Salesforce?',
                'Как настроить интеграцию с Zoho?',
                'Как настроить интеграцию с amoCRM?',
                'Как настроить интеграцию с Мегаплан?',
                'Как настроить интеграцию с Контур.Эльба?',
                'Как настроить интеграцию с МойСклад?',
                'Как настроить интеграцию с Террасофт?'
            ],
            'обновление': [
                'Как обновить R7-Office?',
                'Как проверить наличие обновлений?',
                'Какие новые функции в последней версии?',
                'Как откатить обновление?',
                'Нужно ли обновлять лицензию при обновлении?',
                'Как настроить автоматическое обновление?',
                'Как обновить отдельные компоненты?',
                'Как проверить совместимость с другими программами?',
                'Как обновить мобильное приложение?',
                'Как обновить серверную часть?',
                'Как обновить клиентскую часть?',
                'Как обновить базу данных?',
                'Как обновить конфигурацию?',
                'Как обновить плагины?',
                'Как обновить шаблоны?',
                'Как обновить шрифты?',
                'Как обновить словари?',
                'Как обновить локализацию?',
                'Как обновить документацию?',
                'Как обновить справку?'
            ],
            'документы': [
                'Как открыть документ Word?',
                'Как открыть документ Excel?',
                'Как открыть документ PowerPoint?',
                'Как открыть PDF файл?',
                'Как открыть архив?',
                'Как открыть документ в режиме только для чтения?',
                'Как открыть поврежденный документ?',
                'Как открыть документ без установленного редактора?',
                'Как открыть документ на мобильном устройстве?',
                'Как открыть документ из облачного хранилища?',
                'Как открыть документ из 1С?',
                'Как открыть документ из Битрикс24?',
                'Как открыть документ из CRM?',
                'Как открыть документ из ERP?',
                'Как открыть документ из СЭД?',
                'Как открыть документ из ECM?',
                'Как открыть документ из DMS?',
                'Как открыть документ из BPM?',
                'Как открыть документ из WMS?',
                'Как открыть документ из HRM?'
            ],
            'редакторы': [
                'Как запустить текстовый редактор?',
                'Как запустить табличный редактор?',
                'Как запустить редактор презентаций?',
                'Как запустить редактор PDF?',
                'Как запустить редактор изображений?',
                'Как запустить редактор из командной строки?',
                'Как запустить редактор без установки?',
                'Как запустить редактор на мобильном устройстве?',
                'Как запустить редактор в безопасном режиме?',
                'Как запустить редактор с определенными параметрами?',
                'Как запустить редактор в режиме совместимости?',
                'Как запустить редактор в режиме отладки?',
                'Как запустить редактор в режиме восстановления?',
                'Как запустить редактор в режиме обслуживания?',
                'Как запустить редактор в режиме диагностики?',
                'Как запустить редактор в режиме тестирования?',
                'Как запустить редактор в режиме разработки?',
                'Как запустить редактор в режиме администрирования?',
                'Как запустить редактор в режиме аудита?',
                'Как запустить редактор в режиме мониторинга?'
            ]
        }
        
        # Нормализуем вопрос пользователя
        normalized_question = user_question.lower()
        
        # Проверяем наличие ключевых слов установки и редакторов
        is_installation_question = any(word in normalized_question for word in [
            'установить', 'установка', 'инсталляция', 'инсталлировать', 'поставить'
        ])
        
        is_editor_question = any(word in normalized_question for word in [
            'редактор', 'word', 'excel', 'powerpoint', 'writer', 'calc', 'impress'
        ])
        
        # Если вопрос об установке редактора, добавляем соответствующие ключевые слова
        if is_installation_question and is_editor_question:
            normalized_question += ' установка редактор документов'
            logger.info("Определен вопрос об установке редактора")
        
        # Добавляем ключевые слова из вопроса
        question_keywords = set()
        for word in normalized_question.split():
            for category, words in keywords.items():
                if word in words or any(syn in normalized_question for syn in words):
                    question_keywords.add(category)
        
        # Добавляем найденные категории в нормализованный вопрос
        normalized_question += ' ' + ' '.join(question_keywords)
        logger.info(f"Нормализованный вопрос с ключевыми словами: {normalized_question}")
        
        # Ищем все заголовки и их содержимое
        best_match = None
        best_score = 0
        sections = []
        
        # Собираем все заголовки и их содержимое
        headers = soup.find_all(['h1', 'h2', 'h3'])
        logger.info(f"Найдено {len(headers)} заголовков в HTML")
        
        for header in headers:
            section_text = header.get_text().strip().lower()
            if not section_text:
                continue
                
            # Пропускаем технические заголовки и предупреждения
            if any(x in section_text for x in ['wiki', 'оглавление', 'содержание', '¶', 'внимание', '!!!']):
                continue
                
            # Получаем содержимое секции
            content = []
            next_node = header.next_sibling
            while next_node and next_node.name not in ['h1', 'h2', 'h3']:
                if next_node.name == 'p':
                    content.append(next_node.get_text().strip())
                next_node = next_node.next_sibling
                
            section_content = ' '.join(content)
            sections.append({
                'title': section_text,
                'content': section_content,
                'header': header
            })
        
        logger.info(f"Найдено {len(sections)} секций в базе знаний")
        
        # Ищем наиболее релевантную секцию
        for section in sections:
            # Считаем релевантность по заголовку
            title_score = 0
            for word in normalized_question.split():
                if word in section['title']:
                    title_score += 3  # Больший вес для совпадений в заголовке
                elif any(syn in section['title'] for syn in keywords.get(word, [])):
                    title_score += 2  # Средний вес для синонимов в заголовке
            
            # Считаем релевантность по содержимому
            content_score = 0
            for word in normalized_question.split():
                if word in section['content']:
                    content_score += 1
                elif any(syn in section['content'] for syn in keywords.get(word, [])):
                    content_score += 0.5  # Меньший вес для синонимов в содержимом
            
            # Дополнительные проверки для ключевых слов
            for keyword in question_keywords:
                if keyword in section['title']:
                    title_score += 2
                if keyword in section['content']:
                    content_score += 1
            
            # Проверяем наличие всех ключевых слов в разделе
            if all(keyword in section['title'].lower() + ' ' + section['content'].lower() for keyword in question_keywords):
                title_score += 5  # Большой бонус за наличие всех ключевых слов
            
            total_score = title_score + content_score
            
            if total_score > best_score:
                best_score = total_score
                best_match = section
        
        if best_match and best_score >= 3:  # Повысили минимальный порог релевантности
            logger.info(f"Найден лучший ответ с оценкой {best_score} в разделе: {best_match['title']}")
            # Формируем ответ
            answer = f"{best_match['title']}\n\n"
            
            # Добавляем первые 3 абзаца содержимого
            paragraphs = best_match['content'].split('\n\n')[:3]
            answer += '\n\n'.join(paragraphs)
            
            # Если есть больше контента, добавляем информацию о продолжении
            if len(best_match['content'].split('\n\n')) > 3:
                answer += "\n\nЭто основная информация по вашему вопросу. Если нужны дополнительные детали, дайте знать."
            
            await update.message.reply_text(answer)
            logger.info("Ответ отправлен пользователю")
        else:
            logger.info("Подходящий ответ не найден в базе знаний")
            # Пробуем найти ответ в интернете
            internet_result = await search_internet(user_question)
            if internet_result:
                await update.message.reply_text(internet_result)
                logger.info("Ответ из интернета отправлен пользователю")
            else:
                await update.message.reply_text(
                    "Извините, не смог найти ответ на ваш вопрос. "
                    "Попробуйте задать его по-другому или обратитесь к администратору."
                )
            
    except Exception as e:
        logger.error(f"Ошибка при обработке вопроса: {str(e)}")
        logger.error(f"Тип ошибки: {type(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        await update.message.reply_text(
            "Произошла ошибка при поиске ответа. Пожалуйста, попробуйте позже или обратитесь к администратору."
        )
    
    return ConversationHandler.END

def main():
    """Основная функция"""
    try:
        # Создаем приложение
        application = Application.builder().token(TELEGRAM_TOKEN).build()

        # Создаем обработчик диалога создания тикета
        create_ticket_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(create_ticket_start, pattern='^create_ticket$')],
            states={
                SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_subject)],
                DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_description)],
                COMPANY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_company_name)],
                INN: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_inn)],
                FIRST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_first_name)],
                LAST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_last_name)],
                FILES: [
                    CallbackQueryHandler(handle_files_choice, pattern='^(skip_files|upload_files)$'),
                    CallbackQueryHandler(finish_file_upload, pattern='^finish_upload$'),
                    MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file_upload)
                ]
            },
            fallbacks=[],
            per_message=True
        )

        # Создаем обработчик диалога проверки статуса тикета
        check_status_handler = ConversationHandler(
            entry_points=[
                CommandHandler("status", check_ticket_status),
                CallbackQueryHandler(check_status_start, pattern='^check_status$')
            ],
            states={
                TICKET_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ticket_number)]
            },
            fallbacks=[],
            per_message=True
        )

        # Создаем обработчик диалога вопросов
        question_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(ask_question_start, pattern='^ask_question$')],
            states={
                'WAITING_QUESTION': [MessageHandler(filters.TEXT & ~filters.COMMAND, process_user_question)]
            },
            fallbacks=[],
            per_message=True
        )

        # Добавляем все обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(create_ticket_handler)
        application.add_handler(check_status_handler)
        application.add_handler(question_handler)

        # Добавляем обработчик для всех текстовых сообщений
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_user_question))

        # Добавляем обработчик ошибок
        application.add_error_handler(error_handler)

        # Запускаем бота
        logger.info("Бот запущен")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {str(e)}")
        if "Conflict" in str(e):
            logger.error("Бот уже запущен в другом процессе")
        raise

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик ошибок для бота"""
    logger.error(f"Произошла ошибка при обработке обновления {update}: {context.error}")
    
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "Извините, произошла ошибка при обработке вашего запроса. "
                "Пожалуйста, попробуйте позже или обратитесь к администратору."
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения об ошибке: {str(e)}")

if __name__ == '__main__':
    import sys
    import os
    
    def signal_handler(signum, frame):
        """Обработчик сигналов для корректного завершения"""
        logger.info("Получен сигнал завершения, останавливаем бота...")
        sys.exit(0)
    
    # Регистрируем обработчики сигналов
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Проверяем наличие pid файла
    pid_file = "bot.pid"
    if os.path.exists(pid_file):
        try:
            with open(pid_file, 'r') as f:
                old_pid = int(f.read().strip())
            try:
                # Проверяем, запущен ли процесс
                os.kill(old_pid, 0)
                logger.error(f"Бот уже запущен (PID: {old_pid})")
                sys.exit(1)
            except OSError:
                # Процесс не существует, удаляем старый pid файл
                os.remove(pid_file)
        except Exception as e:
            logger.error(f"Ошибка при проверке pid файла: {str(e)}")
            if os.path.exists(pid_file):
                os.remove(pid_file)
    
    # Записываем текущий pid
    try:
        with open(pid_file, 'w') as f:
            f.write(str(os.getpid()))
    except Exception as e:
        logger.error(f"Ошибка при создании pid файла: {str(e)}")
    
    try:
        if len(sys.argv) > 1 and sys.argv[1] == '--no-bot':
            # Тестовый режим
            logger.info("Запуск в тестовом режиме")
            
            # Проверяем доступность API
            if check_api_availability():
                logger.info("✅ API доступен")
            else:
                logger.error("❌ API недоступен")
                sys.exit(1)
                
            # Получаем список проектов
            projects = get_projects()
            if projects:
                logger.info(f"✅ Получен список проектов: {len(projects)} проектов")
            else:
                logger.error("❌ Не удалось получить список проектов")
                sys.exit(1)
                
            # Получаем список трекеров
            trackers = get_trackers()
            if trackers:
                logger.info(f"✅ Получен список трекеров: {len(trackers)} трекеров")
            else:
                logger.error("❌ Не удалось получить список трекеров")
                sys.exit(1)
                
            # Проверяем статус тикета
            test_ticket_id = "243"  # Используем существующий тикет
            status = get_issue_status(test_ticket_id)
            if "не найден" not in status:
                logger.info(f"✅ Статус тикета #{test_ticket_id} получен")
                logger.info(status)
            else:
                logger.error(f"❌ Не удалось получить статус тикета #{test_ticket_id}")
                sys.exit(1)
                
            logger.info("✅ Все тесты успешно пройдены")
        else:
            # Запуск бота
            main()
    finally:
        # Удаляем pid файл при завершении
        if os.path.exists(pid_file):
            os.remove(pid_file) 
