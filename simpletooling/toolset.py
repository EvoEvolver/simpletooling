# main.py
#
# To run this, you'll need to install the required packages:
# pip install "fastapi[all]" pydantic pyyaml

from typing import Callable, Optional, Dict, Any

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import Field, BaseModel
from simpletooling.interpret import interpret_python_code

from .mcp_manager import MCPManager
from .tool_registry import ToolRegistry


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
        # Initialize components
        self.mcp_manager = MCPManager()
        self.tool_registry = ToolRegistry(self.app)
        
        # Set up MCP callback for tool registry
        self.tool_registry.set_mcp_callback(self.mcp_manager.call_tool)

        @self.app.get("/", include_in_schema=False)
        async def root():
            return RedirectResponse(url="/docs")

        # Add MCP endpoints
        @self.app.post("/addMCP", tags=["MCP"])
        async def add_mcp(config: Dict[str, Any]):
            result = await self.mcp_manager.add_server(config)
            # If successful, register the tools
            if result["status"] == "success" and result["tools"]:
                self.tool_registry.register_mcp_tools(result["config_hash"], result["tools"])
            return result

        # Health check endpoint
        @self.app.post("/health", tags=["MCP"])
        async def mcp_health(request: Dict[str, str]):
            return await self.mcp_manager.health_check(request.get("config_hash"))

        # Close/cleanup endpoint
        @self.app.post("/close", tags=["MCP"])
        async def mcp_close(request: Dict[str, str]):
            return await self.mcp_manager.close_connection(request.get("config_hash"))








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
            self.tool_registry.add_examples(tool_name, kwargs)
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
            self.tool_registry.register_function_tool(func, _tool_name)
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
                parameters: Dict[str, Any] = Field(
                    default_factory=dict, description="Parameters to pass to the code"
                )
            @self.app.post("/interpreter", name="interpreter", tags=["Interpreter"])
            async def python_interpreter(request: CodeRequest):
                result = interpret_python_code(request.code, request.parameters)
                return {"result": result}
        self.app._host = host  # Store host for schema endpoint
        self.app._port = port  # Store port for schema endpoint
        print("\n--- Starting Toolset Server ---")
        print(f"➡️  Interactive API docs (Swagger UI): http://{host}:{port}/docs")
        uvicorn.run(self.app, host=host, port=port)


