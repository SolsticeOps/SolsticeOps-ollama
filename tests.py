from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.cache import cache
from core.models import Tool
from unittest.mock import patch, MagicMock

User = get_user_model()

class MockModel:
    def __init__(self, name, size):
        self.name = name
        self.size = size
        self.modified_at = "2024-01-01"
    def __getitem__(self, key):
        return getattr(self, key)

class OllamaModuleTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_superuser(username='admin', password='password', email='admin@test.com')
        self.client.login(username='admin', password='password')
        self.tool = Tool.objects.create(name="ollama", status="installed")

    @patch('ollama.Client')
    @patch('modules.ollama.module.run_command')
    @patch('django.core.cache.cache.set')
    def test_ollama_models_partial(self, mock_cache_set, mock_run, mock_ollama):
        mock_run.return_value = b"active"
        mock_client = MagicMock()
        
        # Create a mock model object that has 'name', 'size', and 'modified_at'
        model = MagicMock()
        model.model = "llama3:latest"
        model.size = 4000000000
        model.modified_at = "2024-01-01"
        # If it's accessed as a dict
        model.__getitem__.side_effect = lambda key: {
            'model': 'llama3:latest',
            'size': 4000000000,
            'modified_at': '2024-01-01'
        }.get(key)
        
        mock_client.list.return_value = {'models': [model]}
        mock_ollama.return_value = mock_client
        
        response = self.client.get(reverse('tool_detail', kwargs={'tool_name': 'ollama'}) + "?tab=models", HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "llama3:latest")

    @patch('ollama.Client')
    def test_delete_model(self, mock_ollama):
        mock_client = MagicMock()
        mock_ollama.return_value = mock_client
        
        # Use hardcoded URL if reverse fails during tests due to dynamic registration
        try:
            url = reverse('delete_model')
        except:
            url = '/ollama/model/delete/'
            
        response = self.client.post(url, {'model_name': 'llama3:latest'})
        self.assertEqual(response.status_code, 302)
        mock_client.delete.assert_called_with('llama3:latest')

    @patch('ollama.Client')
    def test_chat_send_streaming(self, mock_ollama):
        mock_client = MagicMock()
        mock_client.chat.return_value = [
            {'message': {'content': 'Hello'}, 'done': False},
            {'message': {'content': '!'}, 'done': True, 'prompt_eval_count': 5, 'eval_count': 5}
        ]
        mock_ollama.return_value = mock_client
        
        try:
            url = reverse('chat_send')
        except:
            url = '/ollama/chat/send/'
            
        response = self.client.post(url, {
            'model': 'llama3',
            'message': 'Hi',
            'history': '[]'
        })
        self.assertEqual(response.status_code, 200)
        # Streaming response check
        content = b"".join(response.streaming_content).decode()
        self.assertIn("Hello", content)
        self.assertIn("!", content)

    @patch('ollama.Client')
    def test_ollama_pull_model_post(self, mock_ollama):
        mock_client = MagicMock()
        mock_client.pull.return_value = [
            {'status': 'pulling manifest'},
            {'completed': 50, 'total': 100},
            {'status': 'success'}
        ]
        mock_ollama.return_value = mock_client
        
        try:
            url = reverse('pull_model')
        except:
            url = '/ollama/model/pull/'
            
        response = self.client.post(url, {'model_name': 'mistral'})
        self.assertEqual(response.status_code, 302)
        
        # Wait for thread
        import time
        time.sleep(0.5)
        self.tool.refresh_from_db()
        # It should have finished and cleaned up config_data
        self.assertNotIn('pulling_model', self.tool.config_data)

    @patch('modules.ollama.module.run_command')
    @patch('django.core.cache.cache.set')
    def test_ollama_module_logic(self, mock_cache_set, mock_run):
        from modules.ollama.module import Module
        module = Module()
        
        # Test version via subprocess
        with patch('modules.ollama.module.subprocess.run') as mock_sub_run:
            mock_sub_run.return_value = MagicMock(returncode=0, stdout="ollama version is 0.15.4")
            self.assertEqual(module.get_service_version(), "0.15.4")
        
        mock_run.return_value = b"active"
        self.assertEqual(module.get_service_status(self.tool), "running")
        
        module.service_start(self.tool)
        mock_run.assert_called_with(["systemctl", "start", "ollama"])
        
        module.service_stop(self.tool)
        mock_run.assert_called_with(["systemctl", "stop", "ollama"])
        
        module.service_restart(self.tool)
        mock_run.assert_called_with(["systemctl", "restart", "ollama"])

    @patch('modules.ollama.module.threading.Thread')
    def test_ollama_install(self, mock_thread):
        from modules.ollama.module import Module
        module = Module()
        self.tool.status = 'not_installed'
        self.tool.save()
        
        module.install(None, self.tool)
        self.tool.refresh_from_db()
        self.assertEqual(self.tool.status, 'installing')
        mock_thread.assert_called_once()

    @patch('modules.ollama.module.run_command')
    @patch('ollama.Client')
    def test_ollama_get_context_data(self, mock_ollama, mock_run):
        from modules.ollama.module import Module
        module = Module()
        mock_run.return_value = b"active"
        mock_client = MagicMock()
        mock_client.list.return_value = {'models': []}
        mock_ollama.return_value = mock_client
        
        context = module.get_context_data(None, self.tool)
        self.assertTrue(context['service_active'])
        
        # Test models as object
        mock_resp = MagicMock()
        mock_resp.models = []
        mock_client.list.return_value = mock_resp
        context = module.get_context_data(None, self.tool)
        self.assertEqual(context['models'], [])

    def test_ollama_handle_hx_request(self):
        from modules.ollama.module import Module
        module = Module()
        request = MagicMock()
        with patch.object(Module, 'get_context_data', return_value={'tool': self.tool}):
            response = module.handle_hx_request(request, self.tool, 'models')
            self.assertIsNotNone(response)
            response = module.handle_hx_request(request, self.tool, 'chat')
            self.assertIsNotNone(response)
            response = module.handle_hx_request(request, self.tool, 'invalid')
            self.assertIsNone(response)

    @patch('ollama.Client')
    def test_pull_model_error(self, mock_ollama):
        mock_client = MagicMock()
        mock_client.pull.side_effect = Exception("pull error")
        mock_ollama.return_value = mock_client
        
        url = '/ollama/model/pull/'
        response = self.client.post(url, {'model_name': 'error-model'})
        self.assertEqual(response.status_code, 302)
        # Wait a bit for thread to fail
        import time
        time.sleep(0.5)
        self.tool.refresh_from_db()

    @patch('modules.ollama.views.ollama.Client')
    def test_chat_send_error(self, mock_ollama):
        mock_client = MagicMock()
        mock_client.chat.side_effect = Exception("chat error")
        mock_ollama.return_value = mock_client
        
        url = '/ollama/chat/send/'
        response = self.client.post(url, {
            'model': 'llama3',
            'message': 'Hi',
            'history': '[]'
        })
        self.assertEqual(response.status_code, 200)
        content = b"".join(response.streaming_content).decode()
        self.assertIn("chat error", content)

    def test_chat_send_invalid_params(self):
        url = '/ollama/chat/send/'
        # Missing model/message
        response = self.client.post(url, {'model': '', 'message': ''})
        self.assertEqual(response.status_code, 400)
        
        # Invalid temperature
        response = self.client.post(url, {'model': 'm', 'message': 'h', 'temperature': 'invalid'})
        self.assertEqual(response.status_code, 400)

    @patch('modules.ollama.module.run_command')
    def test_ollama_status_detection(self, mock_run):
        mock_run.return_value = b"active"
        from core.plugin_system import plugin_registry
        module = plugin_registry.get_module("ollama")
        status = module.get_service_status(self.tool)
        self.assertEqual(status, "running")
