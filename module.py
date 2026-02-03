import threading
import subprocess
import requests
import logging
from django.shortcuts import render
from django.urls import path
from core.plugin_system import BaseModule

logger = logging.getLogger(__name__)

class Module(BaseModule):
    @property
    def module_id(self):
        return "ollama"

    @property
    def module_name(self):
        return "Ollama"

    description = "Manage Ollama models and test them in a chat interface."
    version = "1.0.0"

    def get_context_data(self, request, tool):
        context = {}
        if tool.status == 'installed':
            try:
                response = requests.get("http://localhost:11434/api/tags", timeout=5)
                if response.status_code == 200:
                    context['models'] = response.json().get('models', [])
                else:
                    context['ollama_error'] = f"Ollama API returned status {response.status_code}"
            except Exception as e:
                context['ollama_error'] = f"Could not connect to Ollama: {str(e)}"
        return context

    def handle_hx_request(self, request, tool, target):
        context = self.get_context_data(request, tool)
        context['tool'] = tool
        if target == 'models':
            return render(request, 'core/partials/ollama_models.html', context)
        elif target == 'chat':
            return render(request, 'core/partials/ollama_chat.html', context)
        return None

    def install(self, request, tool):
        if tool.status != 'not_installed':
            return

        tool.status = 'installing'
        tool.save()

        def run_install():
            try:
                tool.current_stage = "Downloading and running Ollama installation script..."
                tool.save()
                # Use -y or non-interactive if possible, but ollama install script is usually fine
                process = subprocess.run("curl -fsSL https://ollama.com/install.sh | sh", shell=True, capture_output=True, text=True)
                if process.returncode != 0:
                    raise Exception(process.stderr)
                
                tool.status = 'installed'
                tool.current_stage = "Installation completed successfully"
            except Exception as e:
                tool.status = 'error'
                tool.config_data['error_log'] = str(e)
            tool.save()

        threading.Thread(target=run_install).start()

    def get_resource_tabs(self):
        return [
            {'id': 'models', 'label': 'Models', 'template': 'core/partials/ollama_models.html', 'hx_get': '/tool/ollama/?tab=models', 'hx_auto_refresh': 'every 30s'},
            {'id': 'chat', 'label': 'Demo Chat', 'template': 'core/partials/ollama_chat.html', 'hx_get': '/tool/ollama/?tab=chat'},
        ]

    def get_urls(self):
        from . import views
        return [
            path('ollama/model/pull/', views.pull_model, name='ollama_pull_model'),
            path('ollama/model/delete/', views.delete_model, name='ollama_delete_model'),
            path('ollama/chat/send/', views.chat_send, name='ollama_chat_send'),
        ]

    def get_icon_class(self):
        return "simpleicons-ollama"

    def get_custom_icon_svg(self):
        return """
        <svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
            <path d="M9.307 0C13.11 0 15.14 2.19 15.14 5.742v1.834c0 .412-.033.812-.094 1.197.63-.34 1.343-.543 2.1-.543 2.44 0 3.547 1.43 3.547 3.61v6.43c0 .41-.03.81-.09 1.19.63-.34 1.34-.54 2.1-.54 1.15 0 1.8.48 1.8 1.35v.47c0 .87-.65 1.35-1.8 1.35-.76 0-1.47-.2-2.1-.54.06.38.09.78.09 1.19v.47c0 .87-.65 1.35-1.8 1.35s-1.8-.48-1.8-1.35v-.47c0-.41.03-.81.09-1.19-.63.34-1.34.54-2.1.54-2.44 0-3.547-1.43-3.547-3.61v-6.43c0-.41.03-.81.09-1.19-.63.34-1.34.54-2.1.54-3.8 0-5.83-2.19-5.83-5.742V5.742C3.477 2.19 5.507 0 9.307 0zm0 3.31c-1.63 0-2.227.91-2.227 2.432v6.516c0 1.522.597 2.432 2.227 2.432 1.63 0 2.227-.91 2.227-2.432V5.742c0-1.522-.597-2.432-2.227-2.432zm7.833 8.3c-1.15 0-1.447.61-1.447 1.71v6.43c0 1.1.297 1.71 1.447 1.71 1.15 0 1.447-.61 1.447-1.71v-6.43c0-1.1-.297-1.71-1.447-1.71z"/>
        </svg>
        """
