# main.py
#
# To run this, you'll need to install the required packages:
# pip install "fastapi[all]" pydantic pyyaml

import inspect
import json
import os
from typing import Callable, Optional, Type, Dict

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import Field
from pydantic import create_model


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

        @self.app.get("/", include_in_schema=False)
        async def root():
            return RedirectResponse(url="/docs")

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
            name (Optional[str]): The name of the tool. If not provided, the
                                  function's name will be used.

        Returns:
            A decorator that registers the function.

        Raises:
            ValueError: If a tool with the same name already exists.
            TypeError: If the function signature is not valid (e.g., wrong
                       number of parameters or incorrect type hints).
        """
        tool_name = _tool_name or func.__name__
        if tool_name in self.tools:
            raise ValueError(f"Tool '{tool_name}' already exists. Please use a different name.")
        if tool_name in ["openapi", "docs", "redoc", "schema"]:
            raise ValueError(f"Tool name '{tool_name}' is reserved. Please choose a different name.")

        def decorator(func: Callable) -> Callable:
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

    def serve(self, host: str = "127.0.0.1", port: int = 8000):
        """
        Runs the FastAPI server using uvicorn.

        Args:
            host (str): The host to bind the server to.
            port (int): The port to run the server on.
        """
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
