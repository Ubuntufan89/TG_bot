# Support Bot

Telegram бот для поддержки пользователей с возможностью поиска ответов в базе знаний.

## Требования

- Python 3.8 или выше
- Git

## Установка

1. Клонируйте репозиторий:
```bash
git clone https://github.com/Ubuntufan89/TG_bot.git
cd TG_bot
```

2. Создайте виртуальное окружение и активируйте его:
```bash
python -m venv venv
# Для Linux/Mac:
source venv/bin/activate
# Для Windows:
venv\Scripts\activate
```

3. Установите зависимости:
```bash
pip install -r requirements.txt
```

4. Создайте файл .env на основе .env.example:
```bash
cp .env.example .env
```

5. Отредактируйте файл .env и укажите необходимые значения:
- TELEGRAM_TOKEN - токен вашего Telegram бота (получить у @BotFather)
- SERVICE_API_TOKEN - токен для доступа к сервису API (если используется)
- SERVICE_API_URL - URL сервиса API (если используется)

## Запуск бота

```bash
python support_bot.py
```

## Использование

1. Откройте бот в Telegram
2. Нажмите кнопку "Задать вопрос"
3. Введите ваш вопрос
4. Бот найдет наиболее релевантный ответ в базе знаний (wiki.html)

## Структура проекта

- `support_bot.py` - основной файл бота
- `wiki.html` - база знаний в HTML формате
- `inn_list.json` - список ИНН (если используется)
- `requirements.txt` - зависимости проекта
- `.env` - конфигурационный файл с токенами и URL

## Поддержка

При возникновении проблем или вопросов, создайте Issue в репозитории проекта.
