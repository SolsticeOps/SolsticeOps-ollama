import threading
import subprocess
import requests
import logging
from django.shortcuts import render
from django.urls import path
from core.plugin_system import BaseModule
from core.utils import run_sudo_command

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

    def get_service_version(self):
        try:
            process = subprocess.run(["ollama", "--version"], capture_output=True, text=True)
            if process.returncode == 0:
                # Output is like "ollama version is 0.15.4"
                return process.stdout.strip().split("is")[-1].strip()
        except Exception:
            pass
        return None

    def get_context_data(self, request, tool):
        context = {}
        context['config_data'] = tool.config_data
        
        # Check service status
        try:
            status_process = run_sudo_command(["systemctl", "is-active", "ollama"])
            context['service_active'] = (status_process.decode().strip() == "active")
            
            # If service is active but tool status is not 'installed', we might want to sync it
            if context['service_active'] and tool.status == 'not_installed':
                tool.status = 'installed'
                tool.save()
        except Exception:
            context['service_active'] = False

        if tool.status == 'installed':
            try:
                import ollama
                client = ollama.Client(host='http://localhost:11434')
                models_response = client.list()
                
                # Handle both dict and object responses
                if hasattr(models_response, 'models'):
                    context['models'] = models_response.models
                elif isinstance(models_response, dict):
                    context['models'] = models_response.get('models', [])
                else:
                    context['models'] = []
            except Exception as e:
                context['ollama_error'] = f"Could not connect to Ollama API: {str(e)}"
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
                # The ollama script might use sudo internally, but we can try running it with our utility
                # if we pipe it to sh. 
                run_sudo_command("curl -fsSL https://ollama.com/install.sh | sh", shell=True, capture_output=False)
                
                tool.status = 'installed'
                tool.current_stage = "Installation completed successfully"
            except Exception as e:
                tool.status = 'error'
                tool.config_data['error_log'] = str(e)
            tool.save()

        threading.Thread(target=run_install).start()

    def get_resource_tabs(self):
        return [
            {'id': 'models', 'label': 'Models', 'template': 'core/partials/ollama_models.html', 'hx_get': '/tool/ollama/?tab=models'},
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

    def get_extra_content_template_name(self):
        return "core/modules/ollama_scripts.html"

    def get_custom_icon_svg(self):
        import os
        
        # Path to the SVG file within the module's static directory
        svg_path = os.path.join(os.path.dirname(__file__), 'static', 'ollama.svg')
        
        try:
            if os.path.exists(svg_path):
                with open(svg_path, 'r') as f:
                    return f.read()
        except Exception as e:
            logger.error(f"Failed to read custom icon SVG for Ollama: {e}")
            
        return None
