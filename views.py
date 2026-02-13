import json
import ollama
import threading
import time
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, StreamingHttpResponse
from django.contrib.auth.decorators import login_required
from core.models import Tool

@login_required
def pull_model(request):
    if request.method == 'POST':
        model_name = request.POST.get('model_name')
        if model_name:
            tool = get_object_or_404(Tool, name='ollama')
            
            def run_pull():
                # Use a separate database connection for the background thread to avoid locking issues
                from django import db
                db.connections.close_all()
                
                try:
                    client = ollama.Client(host='http://localhost:11434')
                    # Initialize progress
                    # Refresh tool object from DB
                    from core.models import Tool
                    tool_refresh = Tool.objects.get(pk=tool.pk)
                    tool_refresh.config_data['pulling_model'] = model_name
                    tool_refresh.config_data['pull_progress'] = 0
                    tool_refresh.save()
                    
                    for part in client.pull(model_name, stream=True):
                        if 'completed' in part and 'total' in part:
                            progress = int((part['completed'] / part['total']) * 100)
                            tool_refresh = Tool.objects.get(pk=tool.pk)
                            tool_refresh.config_data['pull_progress'] = progress
                            tool_refresh.save()
                        elif 'status' in part:
                            tool_refresh = Tool.objects.get(pk=tool.pk)
                            tool_refresh.config_data['pull_status'] = part['status']
                            tool_refresh.save()
                            
                    # Cleanup after success
                    tool_refresh = Tool.objects.get(pk=tool.pk)
                    tool_refresh.config_data.pop('pulling_model', None)
                    tool_refresh.config_data.pop('pull_progress', None)
                    tool_refresh.config_data.pop('pull_status', None)
                    tool_refresh.save()
                except Exception as e:
                    try:
                        tool_refresh = Tool.objects.get(pk=tool.pk)
                        tool_refresh.config_data['pull_error'] = str(e)
                        tool_refresh.config_data.pop('pulling_model', None)
                        tool_refresh.save()
                    except:
                        pass

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
        
        try:
            total_tokens = int(request.POST.get('total_tokens', 0))
        except (ValueError, TypeError):
            total_tokens = 0
            
        try:
            history_list = json.loads(history)
        except Exception:
            history_list = []

        try:
            temperature = float(request.POST.get('temperature', 0.7))
            top_p = float(request.POST.get('top_p', 0.9))
            num_ctx = int(request.POST.get('num_ctx', 4096))
            system_prompt = request.POST.get('system_prompt', '').strip()
            user_role = request.POST.get('user_role', 'user').strip()
            api_token = request.POST.get('api_token', '').strip()
        except (ValueError, TypeError) as e:
            return HttpResponse(f"Invalid parameter value: {str(e)}", status=400)
            
        if not model or not message:
            return HttpResponse(f"Model and message are required", status=400)
            
        history_list.append({"role": user_role, "content": message})
        
        api_messages = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        
        for m in history_list:
            api_messages.append({"role": m["role"], "content": m["content"]})
        
        def stream_generator():
            try:
                headers = {}
                if api_token:
                    headers["Authorization"] = f"Bearer {api_token}"
                    
                client = ollama.Client(host='http://localhost:11434', headers=headers)
                
                # Use stream=True for Ollama chat
                stream = client.chat(
                    model=model,
                    messages=api_messages,
                    options={
                        "temperature": temperature,
                        "top_p": top_p,
                        "num_ctx": num_ctx
                    },
                    stream=True
                )
                
                full_content = ""
                message_tokens = 0
                
                # Initial placeholder for the assistant message
                yield f'<div class="d-flex mb-4 animate-fade-in" id="streaming-response-container">' \
                      f'<div class="flex-shrink-0 me-3">' \
                      f'<div class="rounded-circle bg-primary d-flex align-items-center justify-content-center" style="width: 32px; height: 32px;">' \
                      f'<i class="bi bi-robot text-white"></i>' \
                      f'</div>' \
                      f'</div>' \
                      f'<div class="flex-grow-1">' \
                      f'<div class="fw-bold small mb-1">Ollama <span class="text-muted fw-normal">({model})</span></div>' \
                      f'<div class="p-3 rounded-3 border border-secondary border-opacity-25 text-main small shadow-sm markdown-content" ' \
                      f'style="max-width: 85%; background-color: var(--card-bg);" id="streaming-text-target">' \
                      f'<div id="streaming-loader" class="py-1 d-flex gap-1">' \
                      f'<span class="spinner-grow spinner-grow-sm text-primary" role="status" style="width: 8px; height: 8px;"></span>' \
                      f'<span class="spinner-grow spinner-grow-sm text-primary" role="status" style="width: 8px; height: 8px; animation-delay: 0.2s"></span>' \
                      f'<span class="spinner-grow spinner-grow-sm text-primary" role="status" style="width: 8px; height: 8px; animation-delay: 0.4s"></span>' \
                      f'</div>' \
                      f'</div>' \
                      f'<div class="mt-1 ms-1" id="streaming-tokens-target" style="display:none; font-size: 10px; color: var(--muted);">' \
                      f'<i class="bi bi-lightning-charge-fill me-1"></i><span class="token-count">0</span> tokens' \
                      f'</div>' \
                      f'</div></div>'

                for chunk in stream:
                    content = chunk.get('message', {}).get('content', '')
                    full_content += content
                    
                    if chunk.get('done'):
                        prompt_tokens = chunk.get('prompt_eval_count', 0)
                        completion_tokens = chunk.get('eval_count', 0)
                        message_tokens = prompt_tokens + completion_tokens

                    if content:
                        # Use ensure_ascii=False to keep Cyrillic characters
                        safe_content = json.dumps(content, ensure_ascii=False)
                        # Use a script to append content to the target div and trigger scrolling
                        yield f'<script>' \
                              f'var target = document.getElementById("streaming-text-target");' \
                              f'var loader = document.getElementById("streaming-loader");' \
                              f'if(loader) loader.remove();' \
                              f'target.textContent += {safe_content};' \
                              f'document.getElementById("chat-history-container").scrollTop = document.getElementById("chat-history-container").scrollHeight;' \
                              f'</script>'
                
                # Finalize the message: render markdown, update history, tokens, etc.
                history_list.append({
                    "role": "assistant", 
                    "content": full_content,
                    "tokens": message_tokens
                })
                
                new_total_tokens = total_tokens + message_tokens
                
                # Encode once for JS string literal, keeping characters
                safe_full_content = json.dumps(full_content, ensure_ascii=False)
                # For history, we want the input value to be a JSON string.
                # We encode the list to JSON, then encode that string as a JS string literal.
                history_json_str = json.dumps(history_list, ensure_ascii=False)
                safe_history_js_val = json.dumps(history_json_str, ensure_ascii=False)

                yield f'<script>' \
                      f'var container = document.getElementById("streaming-response-container");' \
                      f'var target = document.getElementById("streaming-text-target");' \
                      f'var tokensTarget = document.getElementById("streaming-tokens-target");' \
                      f'var loader = document.getElementById("streaming-loader");' \
                      f'if(loader) loader.remove();' \
                      f'target.setAttribute("data-raw-content", {safe_full_content});' \
                      f'target.removeAttribute("id");' \
                      f'if(tokensTarget) {{' \
                      f'  tokensTarget.querySelector(".token-count").innerText = "{message_tokens}";' \
                      f'  tokensTarget.style.display = "block";' \
                      f'  tokensTarget.removeAttribute("id");' \
                      f'}}' \
                      f'container.removeAttribute("id");' \
                      f'if(window.renderMarkdown) window.renderMarkdown(target);' \
                      f'target.setAttribute("data-rendered", "true");' \
                      f'document.getElementById("history-input").value = {safe_history_js_val};' \
                      f'document.getElementById("total-tokens-input").value = "{new_total_tokens}";' \
                      f'document.getElementById("total-tokens-display").innerText = "{new_total_tokens}";' \
                      f'</script>'
                      
            except Exception as e:
                error_msg = str(e)
                if "unauthorized" in error_msg.lower() or "401" in error_msg:
                    error_msg = "Ollama is unauthorized to use this model."
                
                yield f'<div class="alert alert-danger small mt-2">{error_msg}</div>'

        return StreamingHttpResponse(stream_generator(), content_type='text/html')
            
    return HttpResponse("Method not allowed", status=405)
