# main.py
#
# To run this, you'll need to install the required packages:
# pip install "fastapi[all]" pydantic pyyaml

import inspect
import yaml
from typing import Callable, Any, Optional, Type, Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, create_model
import uvicorn

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
        self.tools: Dict[str, Callable] = {}
        self.input_models: Dict[str, Type] = {}

    def add(self, _tool_name: Optional[str] = None) -> Callable:
        """
        A decorator to add a function as a tool to the API.

        The decorated function MUST have exactly one parameter, and that
        parameter's type hint MUST be a Pydantic BaseModel. The function's
        return type hint can also be a Pydantic model, which will be used
        for the response schema.

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

        def decorator(func: Callable) -> Callable:
            tool_name = _tool_name or func.__name__
            sig = inspect.signature(func)
            fields = {}
            for name, param in sig.parameters.items():
                annot = param.annotation
                if annot is inspect._empty:
                    raise TypeError(
                        f"All parameters for tool '{tool_name}' must be type-annotated. "
                        f"Parameter '{name}' is not."
                    )
                default = param.default if param.default is not inspect._empty else ...
                fields[name] = (annot, default)
            model = create_model(f"{tool_name}Input", **fields)
            self.input_models[tool_name] = model
            self.tools[tool_name] = func

            # --- 2. Add the main tool endpoint (/tool/{tool_name}) ---
            return_model = sig.return_annotation
            if return_model is inspect.Signature.empty:
                return_model = None  # Let FastAPI infer the response model
            print(f"adding /tool/{tool_name}")
            @self.app.post(f"/tool/{tool_name}", name=tool_name, tags=["Tools"])
            async def endpoint(data: model): # type: ignore
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

            # --- 3. Add the schema endpoint (/schema/{tool_name}) ---
            @self.app.get(f"/schema/{tool_name}", response_class=PlainTextResponse, tags=["Schemas"])
            def get_schema() -> str:
                """Returns a minimal, self-contained OpenAPI spec for the tool in YAML format."""
                if not self.app.openapi_schema:
                    # The openapi_schema is cached, this will generate it the first time
                    self.app.openapi()

                openapi_spec = self.app.openapi_schema
                if not openapi_spec:
                    return "Could not generate OpenAPI schema."

                path_key = f"/tool/{tool_name}"

                if path_key not in openapi_spec.get("paths", {}):
                    return PlainTextResponse(f"Schema for tool '{tool_name}' not found.", status_code=404)

                path_spec = openapi_spec["paths"][path_key]

                # Recursively find all referenced schemas within the path spec
                referenced_schemas = {}
                full_schemas = openapi_spec.get("components", {}).get("schemas", {})

                def find_and_add_refs(obj: Any):
                    if isinstance(obj, dict):
                        for key, value in obj.items():
                            if key == "$ref" and isinstance(value, str) and value.startswith("#/components/schemas/"):
                                schema_name = value.split("/")[-1]
                                if schema_name in full_schemas and schema_name not in referenced_schemas:
                                    referenced_schemas[schema_name] = full_schemas[schema_name]
                                    # Recursively check the added schema for more references
                                    find_and_add_refs(full_schemas[schema_name])
                            else:
                                find_and_add_refs(value)
                    elif isinstance(obj, list):
                        for item in obj:
                            find_and_add_refs(item)

                find_and_add_refs(path_spec)

                # Build the minimal OpenAPI spec for this specific tool
                tool_openapi_spec = {
                    "openapi": openapi_spec.get("openapi", "3.1.0"),
                    "info": {
                        "title": f"Tool: {tool_name}",
                        "version": self.app.version,
                        "description": func.__doc__ or f"Schema for {tool_name}"
                    },
                    "paths": {
                        path_key: path_spec
                    },
                    "components": {
                        "schemas": referenced_schemas
                    } if referenced_schemas else {}
                }

                return yaml.dump(tool_openapi_spec, sort_keys=False, indent=2)

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
        print("\n--- Starting Toolset Server ---")
        print(f"➡️  Interactive API docs (Swagger UI): http://{host}:{port}/docs")
        print(f"➡️  Alternative API docs (ReDoc):    http://{host}:{port}/redoc")
        uvicorn.run(self.app, host=host, port=port)




# 5. Run the server
if __name__ == "__main__":

    # --- Example Usage ---

    # 1. Create an instance of the Toolset
    toolset = Toolset()

    # 2. Define Pydantic models for tool inputs and outputs
    class CalculatorRequest(BaseModel):
        a: float
        b: float
        operation: str = "add"

    class CalculatorResponse(BaseModel):
        result: float
        comment: str

    class GreetRequest(BaseModel):
        name: str
        greeting: str = "Hello"

    class GreetResponse(BaseModel):
        message: str

    # 3. Define functions and decorate them to turn them into tools

    @toolset.add("calculator")
    def simple_calculator(req: CalculatorRequest) -> CalculatorResponse:
        """Performs a simple arithmetic operation (add, subtract, multiply, divide)."""
        if req.operation == "add":
            res = req.a + req.b
        elif req.operation == "subtract":
            res = req.a - req.b
        elif req.operation == "multiply":
            res = req.a * req.b
        elif req.operation == "divide":
            if req.b == 0:
                raise ValueError("Cannot divide by zero.")
            res = req.a / req.b
        else:
            raise ValueError(f"Unknown operation: {req.operation}")

        return CalculatorResponse(result=res, comment=f"Successfully performed {req.operation}.")

    @toolset.add()  # Decorator uses the function name 'greet_user' as the tool name
    async def greet_user(req: GreetRequest) -> GreetResponse:
        """Greets a user. This is an example of an async tool function."""
        return GreetResponse(message=f"{req.greeting}, {req.name}!")

    # 4. Demonstrate the error handling for an invalid tool definition
    print("\n--- Demonstrating Error Handling ---")
    try:
        @toolset.add("invalid_tool")
        def invalid_tool_signature(a: int, b: int):
            """This function will fail to be added as a tool."""
            return a + b
    except TypeError as e:
        print(f"💥 Caught expected error for invalid signature: {e}")

    try:
        @toolset.add("another_invalid_tool")
        def invalid_tool_type(data: dict):
            """This function will also fail due to incorrect type hint."""
            return data
    except TypeError as e:
        print(f"💥 Caught expected error for invalid type hint: {e}")

    toolset.serve(host="127.0.0.1", port=8000)
