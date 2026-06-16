"""Точка входа для PyInstaller.

main.py лежит внутри пакета src/ и использует абсолютные импорты `from src...`,
поэтому замораживать нужно из корня проекта через этот тонкий launcher.
"""
from src.main import main

if __name__ == "__main__":
    main()
