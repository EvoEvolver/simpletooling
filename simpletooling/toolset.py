# main.py
#
# To run this, you'll need to install the required packages:
# pip install "fastapi[all]" pydantic pyyaml

import inspect
import json
import os
import hashlib
import asyncio
from datetime import datetime, timedelta
from typing import Callable, Optional, Type, Dict, Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import Field
from pydantic import create_model
from pydantic import BaseModel
from simpletooling.interpret import interpret_python_code

from mcp import ClientSession, StdioServerParameters
import httpx


class MCPConnection:
    def __init__(self, config: Dict[str, Any], config_hash: str):
        self.config = config
        self.config_hash = config_hash
        self.session: Optional[ClientSession] = None
        self.session_id: Optional[str] = None  # For HTTP MCP sessions
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
                print(f"[MCPConnection] Creating stdio client...")
                # For stdio servers
                server_params = StdioServerParameters(
                    command=server_config["command"],
                    args=server_config.get("args", [])
                )
                self.session = ClientSession(server_params)
                print(f"[MCPConnection] Initializing stdio session...")
                await self.session.initialize()
                print(f"[MCPConnection] Stdio client initialized successfully")
            
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
                print(f"[MCPConnection._fetch_tools] Using stdio client to fetch tools")
                # Stdio MCP protocol (using the official MCP client library)
                try:
                    tools_response = await self.session.list_tools()
                    self.tools = {tool.name: tool.model_dump() for tool in tools_response.tools}
                    print(f"[MCPConnection._fetch_tools] Successfully fetched {len(self.tools)} tools via stdio")
                    return True
                except Exception as e:
                    print(f"[MCPConnection._fetch_tools] Stdio error: {e}")
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
                # Stdio MCP protocol
                result = await self.session.call_tool(tool_name, arguments)
                return result.content
                
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
                await self.session.close()
            self.session = None
            self.is_connected = False
    
    def is_idle(self, idle_timeout: timedelta = timedelta(minutes=30)) -> bool:
        return datetime.now() - self.last_access > idle_timeout


class Toolset:
    """
    A class to create a tool server from Python functions.

    This class uses FastAPI to expose functions as API endpoints. It provides
    a decorator to add functions as "tools" and automatically generates
    OpenAPI schemas for each tool.
    """

    def __init__(self, title: str = "Toolset API", version: str = "1.0.0"):
        """
        Initializes the Toolset and the underlying FastAPI application.

        Args:
            title (str): The title of the API for the OpenAPI documentation.
            version (str): The version of the API.
        """
        self.app = FastAPI(
            title=title,
            version=version,
            description="A server for dynamically added tools with auto-generated schemas."
        )
        # Enable CORS for all origins
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self.tools: Dict[str, Callable] = {}
        self.input_models: Dict[str, Type] = {}
        # Store example values for parameters
        # Map from tool name to a dict of parameter names and their example values
        self.example_map: Dict[str, Dict[str, str]] = {}
        
        # MCP-related attributes
        self.mcp_connections: Dict[str, MCPConnection] = {}  # config_hash -> connection
        self.mcp_tool_schemas: Dict[str, Dict] = {}  # config_hash -> {tool_name: schema}
        self.mcp_config_hashes: Dict[str, str] = {}  # config_hash -> original_config_json
        self.cleanup_task: Optional[asyncio.Task] = None

        @self.app.get("/", include_in_schema=False)
        async def root():
            return RedirectResponse(url="/docs")

        # Add MCP endpoint
        @self.app.post("/addMCP", tags=["MCP"])
        async def add_mcp(config: Dict[str, Any]):
            return await self._add_mcp_server(config)

    def _compute_config_hash(self, config: Dict[str, Any]) -> str:
        """Compute a hash for the MCP configuration to identify identical configs."""
        config_json = json.dumps(config, sort_keys=True)
        return hashlib.sha256(config_json.encode()).hexdigest()[:8]

    async def _add_mcp_server(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Add an MCP server and return its tool schemas."""
        config_hash = self._compute_config_hash(config)
        print(f"[Toolset._add_mcp_server] Starting MCP server addition with config hash: {config_hash}")
        print(f"[Toolset._add_mcp_server] Config: {json.dumps(config, indent=2)}")
        
        # Check if this exact config already exists
        if config_hash in self.mcp_connections:
            print(f"[Toolset._add_mcp_server] Config already exists, returning cached data")
            return {
                "config_hash": config_hash,
                "tools": self.mcp_tool_schemas.get(config_hash, {}),
                "status": "cached",
                "message": f"Using cached connection with {len(self.mcp_tool_schemas.get(config_hash, {}))} tools"
            }
        
        # Create new MCP connection
        print(f"[Toolset._add_mcp_server] Creating new MCP connection...")
        
        try:
            mcp_conn = MCPConnection(config, config_hash)
            print(f"[Toolset._add_mcp_server] MCPConnection object created, starting connection...")
            
            await mcp_conn.connect()
            print(f"[Toolset._add_mcp_server] Connection completed, checking if truly connected...")
            
            # Check if connection was actually successful
            if not mcp_conn.is_connected:
                print(f"[Toolset._add_mcp_server] Connection object reports not connected!")
                return {
                    "config_hash": config_hash,
                    "tools": {},
                    "status": "error",
                    "message": "Connection failed - server not responding"
                }
            
            # Check if we got any tools (real MCP servers should have tools)
            if len(mcp_conn.tools) == 0:
                print(f"[Toolset._add_mcp_server] Warning: Connected but no tools found. May not be a real MCP server.")
            
            print(f"[Toolset._add_mcp_server] Storing connection and schemas...")
            # Store connection and schemas
            self.mcp_connections[config_hash] = mcp_conn
            self.mcp_tool_schemas[config_hash] = mcp_conn.tools
            self.mcp_config_hashes[config_hash] = json.dumps(config, sort_keys=True)
            
            print(f"[Toolset._add_mcp_server] Creating dynamic endpoints for {len(mcp_conn.tools)} tools...")
            # Create dynamic endpoints for all tools
            await self._create_mcp_endpoints(config_hash, mcp_conn.tools)
            
            print(f"[Toolset._add_mcp_server] Starting cleanup task...")
            # Start cleanup task if not already running
            if self.cleanup_task is None or self.cleanup_task.done():
                self.cleanup_task = asyncio.create_task(self._cleanup_idle_connections())
            
            print(f"[Toolset._add_mcp_server] ✅ MCP server added successfully with {len(mcp_conn.tools)} tools")
            
            return {
                "config_hash": config_hash,
                "tools": mcp_conn.tools,
                "status": "success",
                "message": f"Connected with {len(mcp_conn.tools)} tools"
            }
            
        except Exception as e:
            print(f"[Toolset._add_mcp_server] ❌ Exception during MCP server addition: {str(e)}")
            print(f"[Toolset._add_mcp_server] Exception type: {type(e)}")
            import traceback
            print(f"[Toolset._add_mcp_server] Full traceback: {traceback.format_exc()}")
            
            # Return error info but don't raise exception to prevent hanging
            return {
                "config_hash": config_hash,
                "tools": {},
                "status": "error",
                "message": f"Failed to connect: {str(e)}"
            }

    async def _create_mcp_endpoints(self, config_hash: str, tools: Dict[str, Any]):
        """Create FastAPI endpoints for MCP tools."""
        print(f"[Toolset._create_mcp_endpoints] Creating endpoints for {len(tools)} tools with config hash {config_hash}")
        
        if len(tools) == 0:
            print(f"[Toolset._create_mcp_endpoints] No tools to create endpoints for")
            return
            
        for tool_name, tool_schema in tools.items():
            print(f"[Toolset._create_mcp_endpoints] Creating endpoint for tool: {tool_name}")
            endpoint_name = f"{config_hash}_{tool_name}"
            
            # Create input model from tool schema
            input_model = self._create_input_model_from_mcp_schema(tool_schema, endpoint_name)
            
            # Create the endpoint handler with proper closure
            def create_handler(current_tool_name: str, current_config_hash: str, current_input_model: Type):
                async def handler(data: current_input_model):
                    mcp_conn = self.mcp_connections.get(current_config_hash)
                    if not mcp_conn:
                        raise HTTPException(status_code=404, detail="MCP server not found")
                    
                    try:
                        # Filter out placeholder fields from the data
                        tool_args = {k: v for k, v in data.model_dump().items() 
                                   if k != "placeholder__" and v is not None}
                        result = await mcp_conn.call_tool(current_tool_name, tool_args)
                        return result
                    except Exception as e:
                        raise HTTPException(status_code=500, detail=str(e))
                return handler
            
            handler = create_handler(tool_name, config_hash, input_model)
            
            # Add endpoint to FastAPI app
            self.app.post(
                f"/{endpoint_name}",
                name=endpoint_name,
                tags=["MCP Tools"],
                description=tool_schema.get("description", f"MCP tool: {tool_name}"),
                summary=f"MCP Tool: {tool_name}"
            )(handler)

    def _create_input_model_from_mcp_schema(self, tool_schema: Dict[str, Any], model_name: str) -> Type:
        """Convert MCP tool schema to Pydantic model."""
        fields = {}
        
        # Extract parameters from MCP schema
        input_schema = tool_schema.get("inputSchema", {})
        properties = input_schema.get("properties", {})
        required_fields = set(input_schema.get("required", []))
        
        for param_name, param_info in properties.items():
            param_type = param_info.get("type", "string")
            param_desc = param_info.get("description", "")
            
            # Map JSON schema types to Python types
            if param_type == "string":
                python_type = str
            elif param_type == "integer":
                python_type = int
            elif param_type == "number":
                python_type = float
            elif param_type == "boolean":
                python_type = bool
            elif param_type == "array":
                python_type = list
            elif param_type == "object":
                python_type = dict
            else:
                python_type = str
            
            # Set default value based on whether field is required
            default = ... if param_name in required_fields else None
            
            fields[param_name] = (python_type, Field(default, description=param_desc))
        
        # If no fields, create an empty model
        if not fields:
            fields["placeholder__"] = (str, Field(None, description="No parameters required"))
        
        return create_model(f"{model_name}Input", **fields)

    async def _cleanup_idle_connections(self):
        """Background task to cleanup idle MCP connections."""
        while True:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes
                
                idle_connections = []
                for config_hash, mcp_conn in self.mcp_connections.items():
                    if mcp_conn.is_idle():
                        idle_connections.append(config_hash)
                
                for config_hash in idle_connections:
                    await self._cleanup_mcp_connection(config_hash)
                    
            except Exception as e:
                print(f"Error in cleanup task: {e}")

    async def _cleanup_mcp_connection(self, config_hash: str):
        """Clean up a specific MCP connection."""
        mcp_conn = self.mcp_connections.get(config_hash)
        if mcp_conn:
            await mcp_conn.disconnect()
            
            # Remove from tracking
            del self.mcp_connections[config_hash]
            if config_hash in self.mcp_tool_schemas:
                del self.mcp_tool_schemas[config_hash]
            if config_hash in self.mcp_config_hashes:
                del self.mcp_config_hashes[config_hash]
            
            print(f"Cleaned up idle MCP connection: {config_hash}")

    def examples(self, **kwargs):
        """
        A decorator to add example values for parameters in the OpenAPI schema.

        Usage:
            @toolset.examples(param1="example1", param2=123)
            @toolset.add()
            def my_tool(param1: str, param2: int) -> str:
                ...

        The examples will be stored and used when registering the next tool.
        """
        def decorator(func):
            tool_name = func.__name__
            self.example_map[tool_name] = kwargs
            return func
        return decorator


    def add(self, _tool_name: Optional[str] = None) -> Callable:
        """
        A decorator to add a function as a tool to the API.
        Args:
            _tool_name (Optional[str]): The name of the tool. If not provided, the
                                  function's name will be used.

        Returns:
            A decorator that registers the function.

        Raises:
            ValueError: If a tool with the same name already exists.
            TypeError: If the function signature is not valid (e.g., wrong
                       number of parameters or incorrect type hints).
        """


        def decorator(func: Callable) -> Callable:
            tool_name = _tool_name or func.__name__
            if tool_name in self.tools:
                raise ValueError(f"Tool '{tool_name}' already exists. Please use a different name.")
            if tool_name in ["openapi", "docs", "redoc", "schema"]:
                raise ValueError(f"Tool name '{tool_name}' is reserved. Please choose a different name.")
            sig = inspect.signature(func)
            param_desc, return_desc, description = parse_rst_docstring(func.__doc__)
            fields = {}
            for name, param in sig.parameters.items():
                annot = param.annotation
                if annot is inspect._empty:
                    raise TypeError(
                        f"All parameters for tool '{tool_name}' must be type-annotated. "
                        f"Parameter '{name}' is not."
                    )
                default = param.default if param.default is not inspect._empty else ...
                example = self.example_map.get(tool_name, {}).get(name, None)
                if example is None:
                    field_info = Field(default, description=param_desc.get(name, ""))
                else:
                    field_info = Field(default, description=param_desc.get(name, ""), examples=[example])
                fields[name] = (annot, field_info)

            model = create_model(
                f"{tool_name}Input",
                **fields,
            )
            self.input_models[tool_name] = model
            self.tools[tool_name] = func

            # --- 2. Add the main tool endpoint (/tool/{tool_name}) ---
            return_model = sig.return_annotation
            if return_model is inspect.Signature.empty or return_model is None:
                raise TypeError()

            print(f"adding /{tool_name}")
            print(f"input model: {model}")
            print(f"return model: {return_model}")
            print(f"description: {description}")

            @self.app.post(
                f"/{tool_name}",
                name=tool_name,
                tags=["Tools"],
                description=description,
                summary=f"Tool: {tool_name}",
            )
            async def endpoint(data: model):  # type: ignore
                """Dynamically created endpoint for the tool."""
                try:
                    if inspect.iscoroutinefunction(func):
                        result = await func(**data.model_dump())
                    else:
                        result = func(**data.model_dump())
                    return result
                except Exception as e:
                    # In a real application, you would add more robust logging here.
                    raise HTTPException(status_code=500, detail=str(e))


            @self.app.get(
                f"/schema/{tool_name}",
                name=f"schema_{tool_name}",
                tags=["Schemas"],
                description=description,
                summary=f"Schema for tool: {tool_name}",
                response_class=PlainTextResponse,
            )
            async def schema_endpoint():
                temp_app = FastAPI(
                    title=f"Schema for {tool_name}",
                    version=self.app.version,
                    description=description,
                )

                # Closure to bind model & func
                async def temp_ep(input: model):
                    if inspect.iscoroutinefunction(func):
                        return await func(**input.model_dump())
                    else:
                        return func(**input.model_dump())

                temp_app.post(
                    f"/{tool_name}",
                    name=tool_name,
                    tags=["Tools"],
                    description=description,
                    summary=f"Tool: {tool_name}"
                )(temp_ep)

                schema = temp_app.openapi()
                url = os.environ.get('TOOL_URL', None)
                if url is None:
                    url = os.environ.get('RAILWAY_PUBLIC_DOMAIN', None)
                    if url is not None:
                        url = f"https://{url}"
                if url is None:
                    host = getattr(self, '_host', '127.0.0.1')
                    port = getattr(self, '_port', 8000)
                    url = f"http://{host}:{port}"
                schema['servers'] = [
                    {"url": url, "description": "Current server address"}
                ]
                json_str = json.dumps(schema, indent=2)
                return PlainTextResponse(json_str)

            self.tools[tool_name] = func
            print(f"✅ Tool '{tool_name}' added successfully.")
            return func

        return decorator

    def serve(self, host: str = "127.0.0.1", port: int = 8000, interpreter: bool = False):
        """
        Runs the FastAPI server using uvicorn.

        Args:
            host (str): The host to bind the server to.
            port (int): The port to run the server on.
        """
        if interpreter:
            class CodeRequest(BaseModel):
                code: str = Field(..., description="Python code to execute")
            @self.app.post("/interpreter", name="interpreter", tags=["Interpreter"])
            async def python_interpreter(request: CodeRequest):
                result = interpret_python_code(request.code)
                return {"result": result}
        self._host = host  # Store host for schema endpoint
        self._port = port  # Store port for schema endpoint
        print("\n--- Starting Toolset Server ---")
        print(f"➡️  Interactive API docs (Swagger UI): http://{host}:{port}/docs")
        uvicorn.run(self.app, host=host, port=port)


import re


def parse_rst_docstring(docstring):
    param_desc = {}
    return_desc = ""
    description = ""
    if not docstring:
        return param_desc, return_desc, description
    # Extract description before first :param or :return:
    split = re.split(r":param |\s*:return:", docstring, maxsplit=1)
    description = split[0].strip() if split else ""
    # Find :param <name>: <desc>
    for match in re.finditer(r":param (\w+):\s*(.+)", docstring):
        param_desc[match.group(1)] = match.group(2)
    # Find :return: <desc>
    m = re.search(r":return:\s*(.+)", docstring)
    if m:
        return_desc = m.group(1)
    return param_desc, return_desc, description
