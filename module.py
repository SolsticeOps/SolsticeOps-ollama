import threading
import subprocess
import requests
import logging
import os
from django.shortcuts import render
from django.urls import path
from core.plugin_system import BaseModule
from core.utils import run_command

logger = logging.getLogger(__name__)

class Module(BaseModule):
    @property
    def module_id(self):
        return "ollama"

    @property
    def module_name(self):
        return "Ollama"

    description = "Manage Ollama models and test them in a chat interface."
    
    @property
    def version(self):
        try:
            return subprocess.check_output(['git', '-C', os.path.dirname(__file__), 'describe', '--tags', '--abbrev=0']).decode().strip()
        except:
            return "1.1.0"

    def get_service_version(self):
        try:
            process = subprocess.run(["ollama", "--version"], capture_output=True, text=True)
            if process.returncode == 0:
                # Output is like "ollama version is 0.15.4"
                return process.stdout.strip().split("is")[-1].strip()
        except Exception:
            pass
        return None

    def get_service_status(self, tool):
        try:
            status_process = run_command(["systemctl", "is-active", "ollama"], log_errors=False)
            status = status_process.decode().strip()
            if status == "active":
                return 'running'
            elif status in ["inactive", "failed", "deactivating"]:
                return 'stopped'
            return 'error'
        except Exception:
            return 'stopped'

    def service_start(self, tool):
        run_command(["systemctl", "start", "ollama"])

    def service_stop(self, tool):
        run_command(["systemctl", "stop", "ollama"])

    def service_restart(self, tool):
        run_command(["systemctl", "restart", "ollama"])

    def update(self, request, tool):
        if tool.status != 'installed':
            return

        tool.status = 'installing'
        tool.current_stage = "Updating Ollama..."
        tool.save()

        def run_update():
            try:
                # Run the official installation script again to update
                run_command("curl -fsSL https://ollama.com/install.sh | sh", shell=True, capture_output=False, timeout=600)
                
                tool.status = 'installed'
                tool.current_stage = "Update completed successfully"
            except Exception as e:
                tool.status = 'error'
                tool.config_data['error_log'] = str(e)
            tool.save()

        threading.Thread(target=run_update).start()

    def get_context_data(self, request, tool):
        context = {}
        context['config_data'] = tool.config_data
        
        # Check service status
        try:
            status_process = run_command(["systemctl", "is-active", "ollama"])
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
                    models = models_response.models
                elif isinstance(models_response, dict):
                    models = models_response.get('models', [])
                else:
                    models = []

                # Fetch and enrich model capabilities
                enriched_models = []
                capabilities_cache = tool.config_data.get('capabilities_cache', {})
                cache_updated = False

                import time
                import re

                # Auto-cleanup stale pull progress if model is already in list or progress is 100%
                pulling_model = tool.config_data.get('pulling_model')
                if pulling_model:
                    is_pulled = any((m.model if hasattr(m, 'model') else m.get('model')) == pulling_model for m in models)
                    if is_pulled or tool.config_data.get('pull_progress') == 100:
                        tool.config_data.pop('pulling_model', None)
                        tool.config_data.pop('pull_progress', None)
                        tool.config_data.pop('pull_status', None)
                        tool.save()

                for model in models:
                    # Get the base model name (e.g., 'llama3.1:latest' -> 'llama3.1')
                    model_full_name = model.model if hasattr(model, 'model') else model.get('model', '')
                    model_base_name = model_full_name.split(':')[0] if ':' in model_full_name else model_full_name
                    
                    # Check cache (valid for 24 hours)
                    cache_entry = capabilities_cache.get(model_base_name)
                    current_time = time.time()
                    
                    if not cache_entry or (current_time - cache_entry.get('timestamp', 0) > 86400):
                        # Fetch from Ollama library
                        try:
                            response = requests.get(f"https://ollama.com/library/{model_base_name}", timeout=5)
                            if response.status_code == 200:
                                html = response.text
                                caps = {
                                    'tools': 'tools' in html.lower(),
                                    'thinking': 'thinking' in html.lower(),
                                    'vision': 'vision' in html.lower(),
                                    'embedding': 'embedding' in html.lower(),
                                    'timestamp': current_time
                                }
                                capabilities_cache[model_base_name] = caps
                                cache_updated = True
                            else:
                                caps = cache_entry or {'tools': False, 'thinking': False, 'vision': False, 'embedding': False}
                        except Exception:
                            caps = cache_entry or {'tools': False, 'thinking': False, 'vision': False, 'embedding': False}
                    else:
                        caps = cache_entry

                    # Create a dictionary representation of the model to avoid Pydantic/object immutability issues
                    if hasattr(model, 'model_dump'): # Pydantic v2
                        model_dict = model.model_dump()
                    elif hasattr(model, 'dict'): # Pydantic v1
                        model_dict = model.dict()
                    elif hasattr(model, '__dict__'):
                        model_dict = model.__dict__.copy()
                    else:
                        model_dict = dict(model)
                    
                    model_dict['capabilities'] = caps.copy() # Use copy to avoid shared dict issues
                    
                    # Determine cloud status based on tag ONLY
                    model_tag = model_full_name.split(':')[-1] if ':' in model_full_name else 'latest'
                    model_dict['capabilities']['cloud'] = 'cloud' in model_tag.lower()
                    
                    enriched_models.append(model_dict)

                if cache_updated:
                    tool.config_data['capabilities_cache'] = capabilities_cache
                    tool.save()

                # Search and Pagination
                from core.utils import paginate_list
                search_query = request.GET.get('search', '')
                page = request.GET.get('page', 1)
                per_page = request.GET.get('per_page', 10)
                
                pagination = paginate_list(
                    enriched_models, 
                    page, 
                    per_page, 
                    search_query=search_query, 
                    search_fields=['model']
                )
                
                context['models'] = pagination['items']
                context['pagination'] = pagination
                context['search_query'] = search_query
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
        if tool.status not in ['not_installed', 'error']:
            return

        tool.status = 'installing'
        tool.save()

        def run_install():
            try:
                tool.current_stage = "Downloading and running Ollama installation script..."
                tool.save()
                # Run the official installation script. Assumes running as root.
                run_command("curl -fsSL https://ollama.com/install.sh | sh", shell=True, capture_output=False, timeout=600)
                
                tool.status = 'installed'
                tool.current_stage = "Installation completed successfully"
            except Exception as e:
                tool.status = 'error'
                tool.config_data['error_log'] = str(e)
            tool.save()

        threading.Thread(target=run_install).start()

    def get_resource_tabs(self):
        return [
            {
                'id': 'models', 
                'label': 'Models', 
                'template': 'core/partials/ollama_models.html', 
                'hx_get': '/tool/ollama/?tab=models', 
                'hx_auto_refresh': 'every 5s [document.getElementById(\'ollama-pull-input\') && document.getElementById(\'ollama-pull-input\').value === \'\' && document.activeElement.tagName !== \'INPUT\' && document.activeElement.tagName !== \'SELECT\' && document.activeElement.tagName !== \'TEXTAREA\']'
            },
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
