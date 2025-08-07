import inspect
import json
import os
from typing import Callable, Dict, Any, Type, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from .schema_generator import SchemaGenerator


class ToolRegistry:
    """Manages tool registration and endpoint creation."""
    
    def __init__(self, app: FastAPI):
        self.app = app
        self.tools: Dict[str, Callable] = {}
        self.input_models: Dict[str, Type] = {}
        self.example_map: Dict[str, Dict[str, str]] = {}

    def add_examples(self, tool_name: str, examples: Dict[str, str]):
        """Add example values for a tool's parameters."""
        self.example_map[tool_name] = examples

    def register_function_tool(self, func: Callable, tool_name: Optional[str] = None) -> str:
        """Register a Python function as a tool."""
        actual_tool_name = tool_name or func.__name__
        
        # Validation
        if actual_tool_name in self.tools:
            raise ValueError(f"Tool '{actual_tool_name}' already exists. Please use a different name.")
        if actual_tool_name in ["openapi", "docs", "redoc", "schema"]:
            raise ValueError(f"Tool name '{actual_tool_name}' is reserved. Please choose a different name.")

        # Create input model
        input_model = SchemaGenerator.create_input_model_from_function(
            func, actual_tool_name, self.example_map
        )
        
        # Validate return type
        sig = inspect.signature(func)
        return_model = sig.return_annotation
        if return_model is inspect.Signature.empty or return_model is None:
            raise TypeError("Tool function must have a return type annotation")

        # Store the tool
        self.input_models[actual_tool_name] = input_model
        self.tools[actual_tool_name] = func

        # Get description from docstring
        param_desc, return_desc, description = SchemaGenerator.parse_rst_docstring(func.__doc__)

        # Create endpoints
        self._create_tool_endpoint(actual_tool_name, func, input_model, description)
        self._create_schema_endpoint(actual_tool_name, func, input_model, description)

        print(f"✅ Tool '{actual_tool_name}' added successfully.")
        return actual_tool_name

    def register_mcp_tools(self, config_hash: str, tools: Dict[str, Any]):
        """Register MCP tools as endpoints."""
        print(f"[ToolRegistry.register_mcp_tools] Creating endpoints for {len(tools)} tools with config hash {config_hash}")
        
        if len(tools) == 0:
            print(f"[ToolRegistry.register_mcp_tools] No tools to create endpoints for")
            return
            
        for tool_name, tool_schema in tools.items():
            print(f"[ToolRegistry.register_mcp_tools] Creating endpoint for tool: {tool_name}")
            endpoint_name = f"{config_hash}_{tool_name}"
            
            # Create input model from tool schema
            input_model = SchemaGenerator.create_input_model_from_mcp_schema(tool_schema, endpoint_name)
            
            # Create the endpoint handler with proper closure
            def create_handler(current_tool_name: str, current_config_hash: str, current_input_model: Type):
                async def handler(data: current_input_model):
                    # This will be handled by the callback provided during registration
                    return await self.mcp_tool_callback(current_config_hash, current_tool_name, data.model_dump())
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

    def set_mcp_callback(self, callback):
        """Set the callback function for MCP tool calls."""
        self.mcp_tool_callback = callback

    def _create_tool_endpoint(self, tool_name: str, func: Callable, input_model: Type, description: str):
        """Create the main tool endpoint."""
        @self.app.post(
            f"/{tool_name}",
            name=tool_name,
            tags=["Tools"],
            description=description,
            summary=f"Tool: {tool_name}",
        )
        async def endpoint(data: input_model):  # type: ignore
            """Dynamically created endpoint for the tool."""
            try:
                if inspect.iscoroutinefunction(func):
                    result = await func(**data.model_dump())
                else:
                    result = func(**data.model_dump())
                return result
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

    def _create_schema_endpoint(self, tool_name: str, func: Callable, input_model: Type, description: str):
        """Create the schema endpoint for the tool."""
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
            async def temp_ep(input: input_model):
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
                host = getattr(self.app, '_host', '127.0.0.1')
                port = getattr(self.app, '_port', 8000)
                url = f"http://{host}:{port}"
            schema['servers'] = [
                {"url": url, "description": "Current server address"}
            ]
            json_str = json.dumps(schema, indent=2)
            return PlainTextResponse(json_str)