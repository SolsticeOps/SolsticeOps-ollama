import json
import ollama
import threading
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from core.models import Tool

@login_required
def pull_model(request):
    if request.method == 'POST':
        model_name = request.POST.get('model_name')
        if model_name:
            tool = get_object_or_404(Tool, name='ollama')
            
            def run_pull():
                try:
                    client = ollama.Client(host='http://localhost:11434')
                    # Initialize progress
                    tool.config_data['pulling_model'] = model_name
                    tool.config_data['pull_progress'] = 0
                    tool.save()
                    
                    for part in client.pull(model_name, stream=True):
                        if 'completed' in part and 'total' in part:
                            progress = int((part['completed'] / part['total']) * 100)
                            tool.config_data['pull_progress'] = progress
                            tool.save()
                        elif 'status' in part:
                            tool.config_data['pull_status'] = part['status']
                            tool.save()
                            
                    # Cleanup after success
                    tool.config_data.pop('pulling_model', None)
                    tool.config_data.pop('pull_progress', None)
                    tool.config_data.pop('pull_status', None)
                    tool.save()
                except Exception as e:
                    tool.config_data['pull_error'] = str(e)
                    tool.config_data.pop('pulling_model', None)
                    tool.save()

            threading.Thread(target=run_pull).start()
            
    return redirect('/tool/ollama/?tab=models')

@login_required
def delete_model(request):
    if request.method == 'POST':
        model_name = request.POST.get('model_name')
        if model_name:
            try:
                client = ollama.Client(host='http://localhost:11434')
                client.delete(model_name)
            except Exception as e:
                return HttpResponse(f"Error deleting model: {str(e)}", status=500)
    return redirect('/tool/ollama/?tab=models')

@login_required
def chat_send(request):
    if request.method == 'POST':
        model = request.POST.get('model')
        message = request.POST.get('message')
        history = request.POST.get('history', '[]')
        
        # LLM Parameters
        try:
            temperature = float(request.POST.get('temperature', 0.7))
            top_p = float(request.POST.get('top_p', 0.9))
            num_ctx = int(request.POST.get('num_ctx', 4096))
            total_tokens = int(request.POST.get('total_tokens', 0))
        except (ValueError, TypeError) as e:
            return HttpResponse(f"Invalid parameter value: {str(e)}", status=400)
        
        try:
            history_list = json.loads(history)
        except Exception:
            history_list = []
            
        if not model or not message:
            return HttpResponse(f"Model and message are required (Model: {model})", status=400)
            
        # Add user message to history
        history_list.append({"role": "user", "content": message})
        
        # Prepare messages for Ollama (remove custom fields like 'tokens' if they exist)
        api_messages = [{"role": m["role"], "content": m["content"]} for m in history_list]
        
        try:
            client = ollama.Client(host='http://localhost:11434')
            response = client.chat(
                model=model,
                messages=api_messages,
                options={
                    "temperature": temperature,
                    "top_p": top_p,
                    "num_ctx": num_ctx
                }
            )
            
            assistant_message = response.get('message', {}).get('content', '')
            
            # Token counts
            prompt_tokens = response.get('prompt_eval_count', 0)
            completion_tokens = response.get('eval_count', 0)
            message_tokens = prompt_tokens + completion_tokens
            new_total_tokens = total_tokens + message_tokens
            
            # Add assistant message to history
            history_list.append({
                "role": "assistant", 
                "content": assistant_message,
                "tokens": message_tokens
            })
            
            context = {
                'history_json': json.dumps(history_list),
                'history': history_list,
                'model': model,
                'message_tokens': message_tokens,
                'total_tokens': new_total_tokens
            }
            return render(request, 'core/partials/ollama_chat_messages.html', context)
            
        except Exception as e:
            error_msg = str(e)
            # Check for common Ollama errors
            if "unauthorized" in error_msg.lower():
                error_msg = "Ollama is unauthorized to use this model. You might need to sign in via 'ollama box' or use a local model."
            
            return render(request, 'core/partials/ollama_chat_messages.html', {
                'error': error_msg, 
                'model': model
            })
            
    return HttpResponse("Method not allowed", status=405)
