import unittest
import logging
from support_bot import (
    create_session,
    get_headers,
    get_issue_status,
    check_api_availability,
    get_projects,
    get_trackers
)

# Настройка логирования для тестов
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class TestSupportBot(unittest.TestCase):
    """Тесты для функций поддержки бота"""

    def setUp(self):
        """Подготовка к тестам"""
        self.session = create_session()
        self.headers = get_headers()

    def test_session_creation(self):
        """Тест создания сессии"""
        self.assertIsNotNone(self.session)
        self.assertTrue(hasattr(self.session, 'get'))
        self.assertTrue(hasattr(self.session, 'post'))

    def test_headers(self):
        """Тест заголовков запросов"""
        self.assertIn('X-Redmine-API-Key', self.headers)
        self.assertIn('Content-Type', self.headers)
        self.assertIn('Accept', self.headers)

    def test_api_availability(self):
        """Тест доступности API"""
        is_available = check_api_availability()
        self.assertTrue(is_available)

    def test_get_projects(self):
        """Тест получения списка проектов"""
        projects = get_projects()
        self.assertIsInstance(projects, list)
        self.assertTrue(len(projects) > 0)
        
        # Проверяем наличие проекта Service desk
        service_desk = next((p for p in projects if p.get('name') == 'Service desk'), None)
        self.assertIsNotNone(service_desk)
        self.assertEqual(service_desk.get('id'), 2)

    def test_get_trackers(self):
        """Тест получения списка трекеров"""
        trackers = get_trackers()
        self.assertIsInstance(trackers, list)
        self.assertTrue(len(trackers) > 0)
        
        # Проверяем наличие основных трекеров
        tracker_names = [t.get('name') for t in trackers]
        self.assertIn('Ошибка', tracker_names)
        self.assertIn('Поддержка', tracker_names)

    def test_get_issue_status(self):
        """Тест получения статуса тикета"""
        # Тестируем с несуществующим тикетом
        status = get_issue_status('999999')
        self.assertIn('не найден', status)
        
        # Тестируем с существующим тикетом (замените на реальный ID)
        # status = get_issue_status('1')
        # self.assertIn('Статус:', status)
        # self.assertIn('Приоритет:', status)
        # self.assertIn('Тема:', status)

    def test_error_handling(self):
        """Тест обработки ошибок"""
        # Тест с некорректным ID тикета
        status = get_issue_status('abc')
        self.assertIn('не найден', status)
        
        # Тест с пустым ID тикета
        status = get_issue_status('')
        self.assertIn('не найден', status)

def run_tests():
    """Запуск тестов"""
    logger.info("Начало тестирования...")
    
    # Создаем тестовый набор
    suite = unittest.TestLoader().loadTestsFromTestCase(TestSupportBot)
    
    # Запускаем тесты
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    
    # Выводим результаты
    logger.info(f"Тестов выполнено: {result.testsRun}")
    logger.info(f"Ошибок: {len(result.errors)}")
    logger.info(f"Неудачных тестов: {len(result.failures)}")
    
    if result.wasSuccessful():
        logger.info("✅ Все тесты успешно пройдены!")
    else:
        logger.error("❌ Тесты завершились с ошибками!")
        
        if result.errors:
            logger.error("\nОшибки:")
            for error in result.errors:
                logger.error(f"- {error[1]}")
                
        if result.failures:
            logger.error("\nНеудачные тесты:")
            for failure in result.failures:
                logger.error(f"- {failure[1]}")

if __name__ == '__main__':
    run_tests() 