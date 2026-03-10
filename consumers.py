import json
import ollama
import httpx
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from core.models import Tool

class OllamaChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get('user')
        if not self.user or not self.user.is_authenticated:
            await self.close()
            return
        self.chat_task = None
        await self.accept()

    async def disconnect(self, close_code):
        if self.chat_task and not self.chat_task.done():
            self.chat_task.cancel()

    async def receive(self, text_data):
        try:
            # Cancel any existing task if user sends a new message while one is processing
            if self.chat_task and not self.chat_task.done():
                self.chat_task.cancel()
            
            data = json.loads(text_data)
            # ... (rest of the logic)
            model = data.get('model')
            message = data.get('message')
            history = data.get('history', [])
            system_prompt = data.get('system_prompt', '')
            temperature = float(data.get('temperature', 0.7))
            top_p = float(data.get('top_p', 0.9))
            num_ctx = int(data.get('num_ctx', 4096))
            selected_tools_ids = data.get('selected_tools', [])
            api_token = data.get('api_token', '')
            thinking_enabled = data.get('thinking', False)

            if not model or not message:
                await self.send_error("Model and message are required")
                return

            # Get tool definitions from DB
            all_tools = await self.get_ollama_tools()
            selected_tools = [t for t in all_tools if t['id'] in selected_tools_ids]
            
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

            # Prepare messages
            api_messages = []
            
            # Handle thinking instructions
            thinking_instruction = "Always reason step by step inside <thought> tags before providing your final answer. You MUST start your response with a <thought> block."
            if thinking_enabled:
                if system_prompt:
                    system_prompt += f"\n{thinking_instruction}"
                else:
                    system_prompt = thinking_instruction
            else:
                suppress_instruction = "Do not use <thought> tags or reasoning process. Answer directly."
                if system_prompt:
                    system_prompt += f"\n{suppress_instruction}"
                else:
                    system_prompt = suppress_instruction

            if system_prompt:
                api_messages.append({"role": "system", "content": system_prompt})

            for m in history:
                msg = {"role": m["role"], "content": m["content"]}
                if "images" in m:
                    msg["images"] = m["images"]
                if "tool_calls" in m:
                    msg["tool_calls"] = m["tool_calls"]
                api_messages.append(msg)

            # Add current user message
            user_msg = {"role": "user", "content": message}
            if data.get('images'):
                user_msg["images"] = data.get('images')
            api_messages.append(user_msg)

            # Start Ollama interaction in a task so it can be cancelled
            self.chat_task = asyncio.create_task(
                self.process_chat(model, api_messages, api_tools, temperature, top_p, num_ctx, api_token, all_tools)
            )

        except Exception as e:
            await self.send_error(str(e))

    async def process_chat(self, model, messages, api_tools, temperature, top_p, num_ctx, api_token, all_tools_defs):
        headers = {}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"

        client = ollama.AsyncClient(
            host='http://localhost:11434', 
            headers=headers,
            timeout=httpx.Timeout(None)
        )

        current_messages = messages.copy()
        total_tokens = 0
        accumulated_content = ""

        while True:
            full_content = ""
            is_reasoning_mode = False
            tool_calls = []
            
            # Notify client that we are starting a new turn
            await self.send(json.dumps({
                'type': 'start_turn',
                'model': model
            }))

            try:
                async for chunk in await client.chat(
                    model=model,
                    messages=current_messages,
                    tools=api_tools if api_tools else None,
                    options={
                        "temperature": temperature,
                        "top_p": top_p,
                        "num_ctx": num_ctx
                    },
                    stream=True
                ):
                    # Handle thinking/reasoning content
                    reasoning = chunk.get('message', {}).get('reasoning_content', '')
                    content = chunk.get('message', {}).get('content', '')
                    
                    # Collect tool calls
                    chunk_tool_calls = chunk.get('message', {}).get('tool_calls', [])
                    if chunk_tool_calls:
                        tool_calls.extend(chunk_tool_calls)

                    if reasoning:
                        if not is_reasoning_mode:
                            is_reasoning_mode = True
                            full_content += "<thought>\n"
                            await self.send_content("<thought>\n")
                        full_content += reasoning
                        await self.send_content(reasoning)

                    if content:
                        # If we were in reasoning mode from reasoning_content, close it
                        if is_reasoning_mode: 
                            is_reasoning_mode = False
                            full_content += "\n</thought>\n\n"
                            await self.send_content("\n</thought>\n\n")
                        
                        full_content += content
                        await self.send_content(content)

                    if chunk.get('done'):
                        total_tokens += (chunk.get('prompt_eval_count', 0) + chunk.get('eval_count', 0))

            except Exception as e:
                await self.send_error(f"Ollama Error: {str(e)}")
                return

            if is_reasoning_mode:
                full_content += "\n</thought>"
                await self.send_content("\n</thought>")

            accumulated_content += full_content

            if tool_calls:
                # Add assistant message with tool calls to history
                assistant_msg = {'role': 'assistant', 'content': full_content, 'tool_calls': tool_calls}
                current_messages.append(assistant_msg)
                
                # Execute tools
                for tool_call in tool_calls:
                    func_name = tool_call.get('function', {}).get('name')
                    func_args = tool_call.get('function', {}).get('arguments', {})
                    
                    await self.send(json.dumps({
                        'type': 'tool_call',
                        'name': func_name,
                        'args': func_args
                    }))
                    
                    result = "Tool execution failed or not implemented."
                    tool_def = next((t for t in all_tools_defs if t['name'] == func_name), None)
                    
                    if tool_def and tool_def.get('python_code'):
                        try:
                            # Run in a separate thread to not block the event loop
                            result = await asyncio.to_thread(self.execute_python_tool, tool_def['python_code'], func_args)
                        except Exception as e:
                            result = f"Error executing tool: {str(e)}"
                    elif tool_def:
                        result = f"Mock result for {func_name}"
                    
                    current_messages.append({
                        'role': 'tool',
                        'content': result,
                    })
                    
                    await self.send(json.dumps({
                        'type': 'tool_result',
                        'name': func_name,
                        'result': result
                    }))
                
                # Continue the loop for the next model response
                continue
            else:
                # No tool calls, we are done
                break

        # Finalize
        await self.send(json.dumps({
            'type': 'done',
            'full_content': accumulated_content,
            'total_tokens': total_tokens,
            'history_update': current_messages # Send back the full history for the client to store
        }))

    def execute_python_tool(self, code, args):
        exec_globals = {'args': args, 'result': None}
        exec(code, exec_globals)
        return str(exec_globals.get('result', 'Success (no result returned)'))

    @database_sync_to_async
    def get_ollama_tools(self):
        try:
            tool_obj = Tool.objects.get(name='ollama')
            return tool_obj.config_data.get('ollama_tools', [])
        except Tool.DoesNotExist:
            return []

    async def send_content(self, content):
        await self.send(json.dumps({
            'type': 'content',
            'content': content
        }))

    async def send_error(self, message):
        await self.send(json.dumps({
            'type': 'error',
            'message': message
        }))
