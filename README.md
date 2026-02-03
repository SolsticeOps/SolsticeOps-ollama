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

Ollama management module for SolsticeOps.

[Русская версия](README-ru_RU.md)

## Features
- Model management (pull and delete)
- Interactive demo chat interface
- Chat session context persistence
- Real-time token usage tracking
- LLM parameter configuration (Temperature, Top-P, Context Window)
- System prompt and User role support
- Cloud API token support
- Request preview for cURL, Python, and Node.js

## Installation
Add as a submodule to SolsticeOps-core:
```bash
git submodule add https://github.com/SolsticeOps/SolsticeOps-ollama.git modules/ollama
pip install -r modules/ollama/requirements.txt
```
