class FinanceError(Exception):
    """Базовый класс для ошибок финансов."""
    pass

class InvalidFormatError(FinanceError):
    """Ошибка формата ввода пользователя."""
    pass

class DatabaseError(FinanceError):
    """Ошибка работы с базой данных."""
    pass
