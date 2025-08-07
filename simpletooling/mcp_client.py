import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import httpx
from fastapi import HTTPException
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPConnection:
    def __init__(self, config: Dict[str, Any], config_hash: str):
        self.config = config
        self.config_hash = config_hash
        self.session: Optional[ClientSession] = None
        self.session_id: Optional[str] = None  # For HTTP MCP sessions
        self.stdio_context = None  # For stdio MCP sessions
        self.tools: Dict[str, Any] = {}
        self.last_access = datetime.now()
        self.is_connected = False
    
    async def connect(self):
        print(f"[MCPConnection] Starting connection for config hash: {self.config_hash}")
        
        if self.is_connected and self.session:
            print(f"[MCPConnection] Already connected, skipping")
            return
        
        try:
            server_config = list(self.config.get("servers", {}).values())[0]
            server_name = list(self.config.get("servers", {}).keys())[0]
            print(f"[MCPConnection] Connecting to server '{server_name}' of type '{server_config.get('type')}'")
            print(f"[MCPConnection] Server URL: {server_config.get('url', 'N/A')}")
            
            if server_config.get("type") == "http":
                print(f"[MCPConnection] Creating HTTP client...")
                # For HTTP MCP servers, we'll use httpx client
                self.session = httpx.AsyncClient(
                    base_url=server_config["url"],
                    headers=server_config.get("headers", {}),
                    timeout=10.0  # Add timeout
                )
                print(f"[MCPConnection] HTTP client created successfully")
                
            elif server_config.get("type") == "stdio":
                print(f"[MCPConnection] Creating custom stdio MCP client...")
                
                # Parse the package URL to extract command and args
                package_url = server_config["url"]
                envs = server_config.get("envs", {})
                
                print(f"[MCPConnection] Package URL: {package_url}")
                print(f"[MCPConnection] Environment vars: {list(envs.keys())}")
                
                # Determine command and args based on package URL format
                if package_url.startswith("@") or package_url.startswith("npm:"):
                    # NPM package format: @scope/package@version or npm:package@version
                    command = "npx"
                    args = ["-y", package_url]
                elif package_url.startswith("uv:"):
                    # Python uv package format: uv:package@version  
                    command = "uvx"
                    args = [package_url[3:]]  # Remove "uv:" prefix
                elif package_url.startswith("pip:"):
                    # Python pip package format: pip:package@version
                    command = "python"
                    args = ["-m", "pip", "install", package_url[4:], "&&", "python", "-m", package_url[4:].split("@")[0]]
                else:
                    # Assume it's a direct command
                    parts = package_url.split()
                    command = parts[0]
                    args = parts[1:] if len(parts) > 1 else []
                
                print(f"[MCPConnection] Resolved command: {command}")
                print(f"[MCPConnection] Resolved args: {args}")
                
                # Use our custom stdio MCP client (similar to your JS implementation)
                await self._connect_custom_stdio(command, args, envs)
            
            print(f"[MCPConnection] Fetching tools from server...")
            tool_fetch_success = await self._fetch_tools()
            print(f"[MCPConnection] Fetched {len(self.tools)} tools")
            
            # Only mark as connected if we successfully communicated with the server
            # (even if it returned 0 tools, at least we got a proper HTTP response)
            self.is_connected = tool_fetch_success
            
            if self.is_connected:
                print(f"[MCPConnection] Connection completed successfully")
            else:
                print(f"[MCPConnection] Connection failed - could not communicate with server")
            
        except Exception as e:
            print(f"[MCPConnection] Connection failed with error: {e}")
            print(f"[MCPConnection] Error type: {type(e)}")
            # Don't raise HTTPException here, let the caller handle it
            raise e
    
    async def _connect_custom_stdio(self, command: str, args: list, envs: dict):
        """Custom stdio MCP client implementation to bypass official library issues."""
        import asyncio
        import json
        import os
        
        print(f"[MCPConnection._connect_custom_stdio] Starting custom stdio MCP client")
        print(f"[MCPConnection._connect_custom_stdio] Command: {command} {' '.join(args)}")
        print(f"[MCPConnection._connect_custom_stdio] Environment vars: {list(envs.keys())}")
        
        try:
            # Prepare environment
            env = {**os.environ, **envs}
            
            # Start the MCP server process
            process = await asyncio.create_subprocess_exec(
                command, *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            
            print(f"[MCPConnection._connect_custom_stdio] Process started with PID: {process.pid}")
            
            # Store process for later cleanup
            self.stdio_process = process
            self.session = process  # Use process as our "session"
            
            # Test communication with initialize message
            init_message = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "simpletooling", "version": "0.1.1"}
                }
            }
            
            print(f"[MCPConnection._connect_custom_stdio] Sending initialize message...")
            message_data = json.dumps(init_message) + "\n"
            process.stdin.write(message_data.encode())
            await process.stdin.drain()
            
            # Read response with timeout
            try:
                response_line = await asyncio.wait_for(
                    process.stdout.readline(), 
                    timeout=10.0
                )
                
                if response_line:
                    response_text = response_line.decode().strip()
                    print(f"[MCPConnection._connect_custom_stdio] Initialize response: {response_text}")
                    
                    try:
                        response_data = json.loads(response_text)
                        if "error" in response_data:
                            raise Exception(f"Initialize error: {response_data['error']}")
                        print(f"[MCPConnection._connect_custom_stdio] Initialize successful")
                    except json.JSONDecodeError as e:
                        print(f"[MCPConnection._connect_custom_stdio] Invalid JSON response: {response_text}")
                        raise Exception(f"Invalid JSON response from MCP server: {e}")
                else:
                    raise Exception("No response from MCP server")
                    
            except asyncio.TimeoutError:
                print(f"[MCPConnection._connect_custom_stdio] Initialize timed out")
                await self._cleanup_stdio_process()
                raise Exception("Initialize timeout - MCP server not responding")
                
        except Exception as e:
            print(f"[MCPConnection._connect_custom_stdio] Custom stdio connection failed: {e}")
            await self._cleanup_stdio_process()
            raise e
    
    async def _cleanup_stdio_process(self):
        """Clean up the stdio process."""
        if hasattr(self, 'stdio_process') and self.stdio_process:
            try:
                if self.stdio_process.returncode is None:  # Process still running
                    # Close stdin to signal end of communication
                    if self.stdio_process.stdin:
                        try:
                            self.stdio_process.stdin.close()
                        except Exception:
                            pass
                    
                    # Wait for process to exit gracefully
                    try:
                        await asyncio.wait_for(self.stdio_process.wait(), timeout=3.0)
                    except asyncio.TimeoutError:
                        # Force terminate if it doesn't exit gracefully
                        self.stdio_process.terminate()
                        try:
                            await asyncio.wait_for(self.stdio_process.wait(), timeout=2.0)
                        except asyncio.TimeoutError:
                            self.stdio_process.kill()
                            await self.stdio_process.wait()
            except Exception as e:
                print(f"[MCPConnection._cleanup_stdio_process] Error during cleanup: {e}")
            finally:
                self.stdio_process = None
    
    async def _send_stdio_message(self, method: str, params: dict = None) -> dict:
        """Send a JSON-RPC message to stdio MCP server."""
        if not hasattr(self, 'stdio_process') or not self.stdio_process:
            raise Exception("No stdio process available")
        
        request_id = str(int(datetime.now().timestamp() * 1000))
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {}
        }
        
        print(f"[MCPConnection._send_stdio_message] Sending: {method}")
        message_data = json.dumps(message) + "\n"
        
        try:
            self.stdio_process.stdin.write(message_data.encode())
            await self.stdio_process.stdin.drain()
            
            # Read response with timeout
            response_line = await asyncio.wait_for(
                self.stdio_process.stdout.readline(),
                timeout=15.0
            )
            
            if response_line:
                response_text = response_line.decode().strip()
                print(f"[MCPConnection._send_stdio_message] Response: {response_text[:200]}...")
                return json.loads(response_text)
            else:
                raise Exception("No response from MCP server")
                
        except asyncio.TimeoutError:
            raise Exception(f"Timeout waiting for {method} response")
        except json.JSONDecodeError as e:
            raise Exception(f"Invalid JSON response: {e}")
        except Exception as e:
            raise Exception(f"Communication error: {e}")
    
    async def _send_jsonrpc_request(self, method: str, params: dict = None) -> dict:
        """Send a JSON-RPC request to the MCP server."""
        if not isinstance(self.session, httpx.AsyncClient):
            raise ValueError("JSON-RPC requests only supported for HTTP clients")
        
        request_id = str(int(datetime.now().timestamp() * 1000))
        jsonrpc_request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {}
        }
        
        headers = {
            "Content-Type": "application/json"
        }
        
        # Add session ID for non-initialize requests
        if method != "initialize" and hasattr(self, 'session_id') and self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        
        print(f"[MCPConnection._send_jsonrpc_request] Sending JSON-RPC request: {method}")
        print(f"[MCPConnection._send_jsonrpc_request] Request data: {jsonrpc_request}")
        
        try:
            response = await self.session.post(
                "",  # Base URL is already set in the client
                json=jsonrpc_request,
                headers=headers,
                timeout=30.0
            )
            
            print(f"[MCPConnection._send_jsonrpc_request] Response status: {response.status_code}")
            print(f"[MCPConnection._send_jsonrpc_request] Response headers: {dict(response.headers)}")
            
            if response.status_code != 200:
                print(f"[MCPConnection._send_jsonrpc_request] HTTP error: {response.text}")
                return None
            
            response_data = response.json()
            print(f"[MCPConnection._send_jsonrpc_request] Response data: {response_data}")
            
            # Store session ID for initialize requests
            if method == "initialize" and response.status_code == 200:
                session_id = (response.headers.get("mcp-session-id") or 
                            response.headers.get("Mcp-Session-Id") or
                            response_data.get("id") or
                            request_id)
                self.session_id = session_id
                print(f"[MCPConnection._send_jsonrpc_request] Stored session ID: {session_id}")
            
            return response_data
            
        except Exception as e:
            print(f"[MCPConnection._send_jsonrpc_request] Request failed: {e}")
            return None

    async def _fetch_tools(self) -> bool:
        """Fetch tools from MCP server using proper MCP protocol. Returns True if communication was successful, False otherwise."""
        print(f"[MCPConnection._fetch_tools] Starting tool fetch...")
        
        if not self.session:
            print(f"[MCPConnection._fetch_tools] No session available, skipping")
            return False
        
        try:
            if isinstance(self.session, httpx.AsyncClient):
                print(f"[MCPConnection._fetch_tools] Using HTTP MCP JSON-RPC protocol")
                
                # Step 1: Initialize the session
                print(f"[MCPConnection._fetch_tools] Step 1: Initializing MCP session...")
                init_response = await self._send_jsonrpc_request("initialize", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "clientInfo": {
                        "name": "simpletooling",
                        "version": "0.1.1"
                    }
                })
                
                if not init_response or "error" in init_response:
                    print(f"[MCPConnection._fetch_tools] Initialize failed: {init_response}")
                    return False
                
                print(f"[MCPConnection._fetch_tools] Initialize successful")
                
                # Step 2: Send initialized notification
                print(f"[MCPConnection._fetch_tools] Step 2: Sending initialized notification...")
                await self._send_jsonrpc_request("notifications/initialized", {})
                
                # Step 3: List tools
                print(f"[MCPConnection._fetch_tools] Step 3: Listing tools...")
                tools_response = await self._send_jsonrpc_request("tools/list", {})
                
                if not tools_response or "error" in tools_response:
                    print(f"[MCPConnection._fetch_tools] Tools list failed: {tools_response}")
                    return False
                
                # Parse tools from response
                result = tools_response.get("result", {})
                tools = result.get("tools", [])
                
                print(f"[MCPConnection._fetch_tools] Received {len(tools)} tools")
                self.tools = {tool["name"]: tool for tool in tools}
                
                for tool_name, tool_data in self.tools.items():
                    print(f"[MCPConnection._fetch_tools] Tool: {tool_name} - {tool_data.get('description', 'No description')}")
                
                return True
                
            else:
                print(f"[MCPConnection._fetch_tools] Using custom stdio client to fetch tools")
                # Custom stdio MCP protocol
                try:
                    # Send initialized notification first
                    await self._send_stdio_message("notifications/initialized", {})
                    
                    # List tools
                    tools_response = await self._send_stdio_message("tools/list", {})
                    
                    if "error" in tools_response:
                        print(f"[MCPConnection._fetch_tools] Tools list error: {tools_response['error']}")
                        return False
                    
                    # Parse tools from response
                    result = tools_response.get("result", {})
                    tools = result.get("tools", [])
                    
                    print(f"[MCPConnection._fetch_tools] Received {len(tools)} tools via custom stdio")
                    self.tools = {tool["name"]: tool for tool in tools}
                    
                    for tool_name, tool_data in self.tools.items():
                        print(f"[MCPConnection._fetch_tools] Tool: {tool_name} - {tool_data.get('description', 'No description')}")
                    
                    return True
                    
                except Exception as e:
                    print(f"[MCPConnection._fetch_tools] Custom stdio error: {e}")
                    self.tools = {}
                    return False
                
        except Exception as e:
            print(f"[MCPConnection._fetch_tools] Failed to fetch tools from MCP server: {e}")
            print(f"[MCPConnection._fetch_tools] Error type: {type(e)}")
            import traceback
            print(f"[MCPConnection._fetch_tools] Traceback: {traceback.format_exc()}")
            self.tools = {}
            return False
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if not self.is_connected:
            await self.connect()
        
        self.last_access = datetime.now()
        
        try:
            if isinstance(self.session, httpx.AsyncClient):
                # HTTP MCP JSON-RPC protocol
                print(f"[MCPConnection.call_tool] Calling tool {tool_name} with args: {arguments}")
                
                response = await self._send_jsonrpc_request("tools/call", {
                    "name": tool_name,
                    "arguments": arguments
                })
                
                if not response:
                    raise HTTPException(status_code=500, detail="No response from MCP server")
                
                if "error" in response:
                    error_info = response["error"]
                    raise HTTPException(
                        status_code=500, 
                        detail=f"MCP tool error: {error_info.get('message', 'Unknown error')}"
                    )
                
                result = response.get("result", {})
                print(f"[MCPConnection.call_tool] Tool result: {result}")
                return result
                
            else:
                # Custom stdio MCP protocol
                print(f"[MCPConnection.call_tool] Calling tool {tool_name} with args: {arguments}")
                
                response = await self._send_stdio_message("tools/call", {
                    "name": tool_name,
                    "arguments": arguments
                })
                
                if "error" in response:
                    error_info = response["error"]
                    raise HTTPException(
                        status_code=500, 
                        detail=f"MCP tool error: {error_info.get('message', 'Unknown error')}"
                    )
                
                result = response.get("result", {})
                print(f"[MCPConnection.call_tool] Tool result: {result}")
                return result
                
        except HTTPException:
            raise  # Re-raise HTTP exceptions as-is
        except Exception as e:
            print(f"[MCPConnection.call_tool] Tool call failed: {e}")
            raise HTTPException(status_code=500, detail=f"Tool execution failed: {str(e)}")
    
    async def disconnect(self):
        if self.session:
            if isinstance(self.session, httpx.AsyncClient):
                await self.session.aclose()
            else:
                # For custom stdio connections, clean up the process
                await self._cleanup_stdio_process()
            self.session = None
            self.is_connected = False
    
    def is_idle(self, idle_timeout: timedelta = timedelta(minutes=30)) -> bool:
        return datetime.now() - self.last_access > idle_timeout