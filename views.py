import json
import ollama
import httpx
import threading
import time
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, StreamingHttpResponse
from django.contrib.auth.decorators import login_required
from core.models import Tool
from core.utils import devops_admin_required

@login_required
@devops_admin_required
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
                        # Get a fresh tool object to ensure we don't overwrite other changes
                        from core.models import Tool
                        tool_refresh = Tool.objects.get(pk=tool.pk)
                        
                        if 'completed' in part and 'total' in part:
                            progress = int((part['completed'] / part['total']) * 100)
                            tool_refresh.config_data['pull_progress'] = progress
                            tool_refresh.config_data['pulling_model'] = model_name
                            tool_refresh.save()
                        elif 'status' in part:
                            # If status is 'success', we can finish early
                            if part.get('status') == 'success':
                                break
                            tool_refresh.config_data['pull_status'] = part['status']
                            tool_refresh.config_data['pulling_model'] = model_name
                            tool_refresh.save()
                            
                    # Final cleanup after success
                    # Wait a tiny bit to ensure the last save from loop is finished
                    time.sleep(0.5)
                    tool_refresh = Tool.objects.get(pk=tool.pk)
                    tool_refresh.config_data.pop('pulling_model', None)
                    tool_refresh.config_data.pop('pull_progress', None)
                    tool_refresh.config_data.pop('pull_status', None)
                    tool_refresh.config_data.pop('pull_error', None) # Also clear old errors
                    tool_refresh.save()
                except Exception as e:
                    try:
                        from core.models import Tool
                        tool_refresh = Tool.objects.get(pk=tool.pk)
                        tool_refresh.config_data['pull_error'] = str(e)
                        tool_refresh.config_data.pop('pulling_model', None)
                        tool_refresh.save()
                    except:
                        pass

            threading.Thread(target=run_pull).start()
            
    return redirect('/tool/ollama/?tab=models')

@login_required
@devops_admin_required
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
@devops_admin_required
def save_tool(request):
    if request.method == 'POST':
        tool_id = request.POST.get('tool_id')
        name = request.POST.get('name')
        description = request.POST.get('description')
        parameters = request.POST.get('parameters', '{}')
        python_code = request.POST.get('python_code', '')
        
        try:
            # Validate parameters is valid JSON
            params_dict = json.loads(parameters)
        except Exception as e:
            return HttpResponse(f"Invalid JSON in parameters: {str(e)}", status=400)
            
        tool = get_object_or_404(Tool, name='ollama')
        if 'ollama_tools' not in tool.config_data:
            tool.config_data['ollama_tools'] = []
            
        new_tool = {
            'id': tool_id or str(int(time.time())),
            'name': name,
            'description': description,
            'parameters': params_dict,
            'python_code': python_code,
            'updated_at': time.time()
        }
        
        if tool_id:
            # Update existing
            for i, t in enumerate(tool.config_data['ollama_tools']):
                if t['id'] == tool_id:
                    tool.config_data['ollama_tools'][i] = new_tool
                    break
            else:
                # Not found, add as new
                tool.config_data['ollama_tools'].append(new_tool)
        else:
            # Add new
            tool.config_data['ollama_tools'].append(new_tool)
            
        tool.save()
        return redirect('/tool/ollama/?tab=tools')
    return HttpResponse("Method not allowed", status=405)

@login_required
@devops_admin_required
def delete_tool(request):
    if request.method == 'POST':
        tool_id = request.POST.get('tool_id')
        if tool_id:
            tool = get_object_or_404(Tool, name='ollama')
            if 'ollama_tools' in tool.config_data:
                tool.config_data['ollama_tools'] = [t for t in tool.config_data['ollama_tools'] if t['id'] != tool_id]
                tool.save()
        return redirect('/tool/ollama/?tab=tools')
    return HttpResponse("Method not allowed", status=405)

@login_required
def chat_send(request):
    if request.method == 'POST':
        model = request.POST.get('model')
        message = request.POST.get('message')
        history = request.POST.get('history', '[]')
        
        # Handle file upload
        images = []
        attachment_file = request.FILES.get('attachment')
        if attachment_file:
            import base64
            # Check if it's an image
            if attachment_file.content_type.startswith('image/'):
                try:
                    image_data = attachment_file.read()
                    image_base64 = base64.b64encode(image_data).decode('utf-8')
                    images.append(image_base64)
                except Exception as e:
                    logger.error(f"Failed to process image attachment: {e}")
            elif attachment_file.content_type.startswith('text/') or attachment_file.name.endswith(('.py', '.js', '.json', '.md', '.txt', '.sh', '.yaml', '.yml')):
                try:
                    file_content = attachment_file.read().decode('utf-8')
                    # Prepend file content to message for context
                    message = f"File: {attachment_file.name}\n---\n{file_content}\n---\n\n{message}"
                except Exception as e:
                    logger.error(f"Failed to process text attachment: {e}")
            else:
                # For other types, we just add the name for now as Ollama doesn't support direct video/audio yet
                message = f"(Attached file: {attachment_file.name})\n\n{message}"

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
            thinking_enabled = request.POST.get('thinking', 'false') == 'true'
            selected_tools_ids = request.POST.getlist('selected_tools')
        except (ValueError, TypeError) as e:
            return HttpResponse(f"Invalid parameter value: {str(e)}", status=400)
            
        if not model or not message:
            return HttpResponse(f"Model and message are required", status=400)

        # Get tool definitions
        tool_obj = get_object_or_404(Tool, name='ollama')
        all_tools = tool_obj.config_data.get('ollama_tools', [])
        selected_tools = [t for t in all_tools if t['id'] in selected_tools_ids]
        
        # Format for Ollama API
        api_tools = []
        for t in selected_tools:
            api_tools.append({
                'type': 'function',
                'function': {
                    'name': t['name'],
                    'description': t['description'],
                    'parameters': t['parameters']
                }
            })
            
        # Handle thinking instructions
        thinking_instruction = "Always reason step by step inside <thought> tags before providing your final answer. You MUST start your response with a <thought> block."
        if thinking_enabled:
            if system_prompt:
                system_prompt += f"\n{thinking_instruction}"
            else:
                system_prompt = thinking_instruction
        else:
            # Try to suppress thinking if disabled
            suppress_instruction = "Do not use <thought> tags or reasoning process. Answer directly."
            if system_prompt:
                system_prompt += f"\n{suppress_instruction}"
            else:
                system_prompt = suppress_instruction
            
        user_message = {"role": user_role, "content": message}
        if images:
            user_message["images"] = images
            
        history_list.append(user_message)
        
        api_messages = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        
        for m in history_list:
            msg = {"role": m["role"], "content": m["content"]}
            if "images" in m:
                msg["images"] = m["images"]
            api_messages.append(msg)
        
        def stream_generator():
            try:
                headers = {}
                if api_token:
                    headers["Authorization"] = f"Bearer {api_token}"
                
                # Configure client with no timeout for model generation and tool execution
                client = ollama.Client(
                    host='http://localhost:11434', 
                    headers=headers,
                    timeout=httpx.Timeout(None)
                )
                
                current_messages = api_messages.copy()
                total_message_tokens = 0
                accumulated_full_content = ""
                
                # We use a loop to handle potential tool calls and model's final response
                while True:
                    # Use stream=True for Ollama chat
                    stream = client.chat(
                        model=model,
                        messages=current_messages,
                        tools=api_tools if api_tools else None,
                        options={
                            "temperature": temperature,
                            "top_p": top_p,
                            "num_ctx": num_ctx
                        },
                        stream=True
                    )
                    
                    full_content = ""
                    current_turn_tokens = 0
                    is_reasoning_mode = False
                    tool_calls = []
                    
                    # Check if we already yielded the container
                    if 'container_yielded' not in locals():
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
                        container_yielded = True

                    try:
                        for chunk in stream:
                            # Handle thinking/reasoning content if present
                            reasoning = chunk.get('message', {}).get('reasoning_content', '')
                            content = chunk.get('message', {}).get('content', '')
                            
                            # Collect tool calls if present
                            chunk_tool_calls = chunk.get('message', {}).get('tool_calls', [])
                            if chunk_tool_calls:
                                tool_calls.extend(chunk_tool_calls)
                            
                            if reasoning:
                                if not is_reasoning_mode:
                                    is_reasoning_mode = True
                                    full_content += "<thought>\n"
                                    yield f'<script>document.getElementById("streaming-text-target").textContent += "<thought>\\n";</script>'
                                full_content += reasoning
                                safe_reasoning = json.dumps(reasoning, ensure_ascii=False)
                                yield f'<script>' \
                                      f'var target = document.getElementById("streaming-text-target");' \
                                      f'var loader = document.getElementById("streaming-loader");' \
                                      f'if(loader) loader.remove();' \
                                      f'target.textContent += {safe_reasoning};' \
                                      f'document.getElementById("chat-history-container").scrollTop = document.getElementById("chat-history-container").scrollHeight;' \
                                      f'</script>'
                            
                            if content:
                                if is_reasoning_mode:
                                    is_reasoning_mode = False
                                    full_content += "\n</thought>\n\n"
                                    yield f'<script>document.getElementById("streaming-text-target").textContent += "\\n</thought>\\n\\n";</script>'
                                
                                full_content += content
                                safe_content = json.dumps(content, ensure_ascii=False)
                                yield f'<script>' \
                                      f'var target = document.getElementById("streaming-text-target");' \
                                      f'var loader = document.getElementById("streaming-loader");' \
                                      f'if(loader) loader.remove();' \
                                      f'target.textContent += {safe_content};' \
                                      f'document.getElementById("chat-history-container").scrollTop = document.getElementById("chat-history-container").scrollHeight;' \
                                      f'</script>'
                            
                            if chunk.get('done'):
                                current_turn_tokens = chunk.get('prompt_eval_count', 0) + chunk.get('eval_count', 0)
                                total_message_tokens += current_turn_tokens
                    except (GeneratorExit, ConnectionResetError):
                        return

                    if is_reasoning_mode:
                        full_content += "\n</thought>"
                        yield f'<script>document.getElementById("streaming-text-target").textContent += "\\n</thought>";</script>'
                    
                    accumulated_full_content += full_content
                    
                    if tool_calls:
                        assistant_msg = {'role': 'assistant', 'content': full_content, 'tool_calls': tool_calls}
                        current_messages.append(assistant_msg)
                        
                        for tool_call in tool_calls:
                            func_name = tool_call.get('function', {}).get('name')
                            func_args = tool_call.get('function', {}).get('arguments', {})
                            
                            yield f'<script>' \
                                  f'var target = document.getElementById("streaming-text-target");' \
                                  f'var loader = document.getElementById("streaming-loader");' \
                                  f'if(loader) loader.remove();' \
                                  f'target.innerHTML += \'<div class="alert alert-info py-2 px-3 mt-2 mb-0 d-flex align-items-center gap-2 small border-0 shadow-sm" style="background: rgba(var(--bs-info-rgb), 0.1);"><i class="bi bi-cpu"></i> Calling tool: <b>{func_name}</b>({json.dumps(func_args)})</div>\';' \
                                  f'document.getElementById("chat-history-container").scrollTop = document.getElementById("chat-history-container").scrollHeight;' \
                                  f'</script>'
                            
                            result = "Tool execution failed or not implemented."
                            tool_def = next((t for t in all_tools if t['name'] == func_name), None)
                            if tool_def and tool_def.get('python_code'):
                                try:
                                    exec_globals = {'args': func_args, 'result': None}
                                    exec(tool_def['python_code'], exec_globals)
                                    result = str(exec_globals.get('result', 'Success (no result returned)'))
                                except Exception as e:
                                    result = f"Error executing tool: {str(e)}"
                            elif tool_def:
                                result = f"Mock result for {func_name} with args {json.dumps(func_args)}"
                            
                            current_messages.append({
                                'role': 'tool',
                                'content': result,
                            })
                            
                            yield f'<script>' \
                                  f'var target = document.getElementById("streaming-text-target");' \
                                  f'target.innerHTML += \'<div class="alert alert-success py-1 px-3 mt-1 mb-2 d-flex align-items-center gap-2 x-small border-0 shadow-sm" style="background: rgba(var(--bs-success-rgb), 0.1); font-family: monospace;"><i class="bi bi-check-circle"></i> Result: {json.dumps(result)}</div>\';' \
                                  f'document.getElementById("chat-history-container").scrollTop = document.getElementById("chat-history-container").scrollHeight;' \
                                  f'</script>'
                        continue
                    else:
                        break
                
                final_assistant_msg = {"role": "assistant", "content": full_content}
                if tool_calls:
                    final_assistant_msg["tool_calls"] = tool_calls
                
                new_history = []
                for m in current_messages:
                    if m['role'] == 'system':
                        continue
                    new_history.append(m)
                
                if not tool_calls:
                    new_history.append(final_assistant_msg)
                
                history_list = new_history
                new_total_tokens = total_tokens + total_message_tokens
                
                safe_full_content = json.dumps(accumulated_full_content, ensure_ascii=False)
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
                      f'  tokensTarget.querySelector(".token-count").innerText = "{total_message_tokens}";' \
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
