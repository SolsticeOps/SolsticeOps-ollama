<div align="center">
    <picture>
        <source
            srcset="https://github.com/SolsticeOps/SolsticeOps-core/docs/images/logo_dark.png"
            media="(prefers-color-scheme: light), (prefers-color-scheme: no-preference)"
        />
        <source
            srcset="https://github.com/SolsticeOps/SolsticeOps-core/docs/images/logo_light.png"
            media="(prefers-color-scheme: dark)"
        />
        <img src="https://github.com/SolsticeOps/SolsticeOps-core/docs/images/logo_light.png" />
    </picture>
</div>

# SolsticeOps-ollama

Модуль управления Ollama для SolsticeOps.

[English Version](README.md)

## Возможности
- Управление моделями (загрузка и удаление)
- Интерактивный демо-чат
- Сохранение контекста сессии чата
- Отслеживание использования токенов в реальном времени
- Настройка параметров LLM (Temperature, Top-P, Context Window)
- Поддержка системного промпта и ролей пользователя
- Поддержка токенов API для облачных моделей
- Предпросмотр запросов для cURL, Python и Node.js

## Установка
Добавьте как субмодуль в SolsticeOps-core:
```bash
git submodule add https://github.com/SolsticeOps/SolsticeOps-ollama.git modules/ollama
pip install -r modules/ollama/requirements.txt
```
