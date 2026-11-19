class FinanceError(Exception):
    """Базовая ошибка приложения."""


class InvalidFormatError(FinanceError):
    """Неверный формат ввода пользователем."""


class DatabaseError(FinanceError):
    """Ошибка работы с базой данных."""
