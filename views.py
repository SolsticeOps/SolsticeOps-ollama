import requests
import json
from django.shortcuts import render, redirect
from django.http import HttpResponse, JsonResponse
from django.contrib.auth.decorators import login_required
from core.models import Tool

@login_required
def pull_model(request):
    if request.method == 'POST':
        model_name = request.POST.get('model_name')
        if model_name:
            try:
                # We do it asynchronously in Ollama, but here we just trigger it
                # For a better UX, we could stream the progress, but for now just send the request
                requests.post("http://localhost:11434/api/pull", json={"name": model_name, "stream": False}, timeout=5)
            except Exception as e:
                return HttpResponse(f"Error pulling model: {str(e)}", status=500)
    return redirect('/tool/ollama/?tab=models')

@login_required
def delete_model(request):
    if request.method == 'POST':
        model_name = request.POST.get('model_name')
        if model_name:
            try:
                requests.delete("http://localhost:11434/api/delete", json={"name": model_name}, timeout=5)
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
        temperature = float(request.POST.get('temperature', 0.7))
        top_p = float(request.POST.get('top_p', 0.9))
        num_ctx = int(request.POST.get('num_ctx', 4096))
        total_tokens = int(request.POST.get('total_tokens', 0))
        
        try:
            history_list = json.loads(history)
        except:
            history_list = []
            
        if not model or not message:
            return HttpResponse("Model and message are required", status=400)
            
        history_list.append({"role": "user", "content": message})
        
        try:
            response = requests.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": model,
                    "messages": history_list,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "top_p": top_p,
                        "num_ctx": num_ctx
                    }
                },
                timeout=120
            )
            
            if response.status_code == 200:
                result = response.json()
                assistant_message = result.get('message', {}).get('content', '')
                
                # Token counts from Ollama
                prompt_tokens = result.get('prompt_eval_count', 0)
                completion_tokens = result.get('eval_count', 0)
                message_tokens = prompt_tokens + completion_tokens
                new_total_tokens = total_tokens + message_tokens
                
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
            else:
                return HttpResponse(f"Ollama error: {response.text}", status=500)
        except Exception as e:
            return HttpResponse(f"Request failed: {str(e)}", status=500)
            
    return HttpResponse("Method not allowed", status=405)
