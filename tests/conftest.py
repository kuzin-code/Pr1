import os

# Ускоряет хеширование демо-паролей в тестах; число итераций хранится в самом хеше.
os.environ.setdefault("APP_PBKDF2_ITERATIONS", "1000")
