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

# Проверка наличия необходимых переменных окружения
if not all([TELEGRAM_TOKEN, SERVICE_API_TOKEN, SERVICE_API_URL]):
    raise ValueError("Не все необходимые переменные окружения установлены. Проверьте файл .env")

# Состояния диалога
SUBJECT, DESCRIPTION, COMPANY_NAME, INN, FILES, TICKET_NUMBER, FIRST_NAME, LAST_NAME = range(8)

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
        [InlineKeyboardButton("База знаний", url='https://service-lad.ru/projects/service-desk/wiki/index')]
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
            per_message=False
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
            per_message=False
        )

        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(create_ticket_handler)
        application.add_handler(check_status_handler)

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
