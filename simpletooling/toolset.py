import inspect
from functools import wraps
from typing import Any, Callable, Dict, Optional

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import create_model
from pydantic.fields import FieldInfo


class ToolSet:
    """
    A toolset that automatically converts well-typed functions into FastAPI endpoints.
    """

    def __init__(self, title: str = "ToolSet API", version: str = "1.0.0"):
        self.app = FastAPI(title=title, version=version)
        self.endpoints: Dict[str, Dict[str, Any]] = {}
        self._setup_schema_routes()

    def _setup_schema_routes(self):
        """Setup schema routes for all endpoints."""

        @self.app.get("/schema/{endpoint_path:path}")
        async def get_schema(endpoint_path: str):
            """Get OpenAPI schema for a specific endpoint."""
            if endpoint_path not in self.endpoints:
                raise HTTPException(status_code=404, detail=f"Endpoint /{endpoint_path} not found")

            endpoint_info = self.endpoints[endpoint_path]
            schema = endpoint_info.get("schema", {})

            return PlainTextResponse(
                yaml.dump(schema, default_flow_style=False, sort_keys=False),
                media_type="text/yaml"
            )

        @self.app.get("/schema")
        async def get_all_schemas():
            """Get OpenAPI schema for all endpoints."""
            schemas = {}
            for path, info in self.endpoints.items():
                schemas[path] = info.get("schema", {})

            return PlainTextResponse(
                yaml.dump(schemas, default_flow_style=False, sort_keys=False),
                media_type="text/yaml"
            )

    def _validate_function_signature(self, func: Callable) -> Dict[str, Any]:
        """
        Validate that a function has proper type hints and can be converted to a FastAPI endpoint.
        Returns endpoint configuration.
        """
        sig = inspect.signature(func)
        parameters = sig.parameters

        # Check if function has proper type hints
        if sig.return_annotation == inspect.Signature.empty:
            raise ValueError(f"Function {func.__name__} must have a return type annotation")

        # Validate parameters
        request_model = None
        path_params = []
        query_params = []

        for name, param in parameters.items():
            if param.annotation == inspect.Parameter.empty:
                raise ValueError(f"Parameter {name} in function {func.__name__} must have a type annotation")

            # Skip self parameter for methods
            if name == "self":
                continue

            # For now, we'll treat all parameters as request body
            # In a more sophisticated version, we could detect path/query parameters
            if request_model is None:
                request_model = create_model(
                    f"{func.__name__}Request",
                    **{name: (param.annotation, ...) if param.default == inspect.Parameter.empty else (param.annotation,
                                                                                                       param.default)}
                )
            else:
                # Add field to existing model
                field_info = FieldInfo(...) if param.default == inspect.Parameter.empty else FieldInfo(
                    default=param.default)
                request_model.model_fields[name] = field_info

        return {
            "request_model": request_model,
            "return_type": sig.return_annotation,
            "path_params": path_params,
            "query_params": query_params
        }

    def _create_endpoint_function(self, func: Callable, config: Dict[str, Any]) -> Callable:
        """Create a FastAPI endpoint function from the original function."""

        @wraps(func)
        async def endpoint(request: config["request_model"]):
            try:
                # Extract parameters from request model
                params = request.model_dump()

                # Call the original function
                result = func(**params)

                # Handle async functions
                if inspect.iscoroutinefunction(func):
                    result = await result

                return result
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        return endpoint

    def _generate_openapi_schema(self, func: Callable, config: Dict[str, Any], path: str) -> Dict[str, Any]:
        """Generate OpenAPI schema for the endpoint."""

        # Create schema for request model
        request_schema = {}
        if config["request_model"]:
            request_schema = config["request_model"].model_json_schema()

        # Create schema for response
        response_schema = {}
        if hasattr(config["return_type"], "model_json_schema"):
            response_schema = config["return_type"].model_json_schema()
        else:
            # For primitive types, create a simple schema
            response_schema = {"type": "string"}  # Default fallback

        return {
            "openapi": "3.0.0",
            "info": {
                "title": "ToolSet API",
                "version": "1.0.0"
            },
            "paths": {
                f"/{path}": {
                    "post": {
                        "summary": func.__name__,
                        "description": func.__doc__ or f"Endpoint for {func.__name__}",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": request_schema
                                }
                            }
                        },
                        "responses": {
                            "200": {
                                "description": "Successful response",
                                "content": {
                                    "application/json": {
                                        "schema": response_schema
                                    }
                                }
                            },
                            "422": {
                                "description": "Validation error"
                            },
                            "500": {
                                "description": "Internal server error"
                            }
                        }
                    }
                }
            }
        }

    def add(self, path: Optional[str] = None):
        """
        Decorator to add a function as a FastAPI endpoint.
        
        Args:
            path: Optional path for the endpoint. If not provided, uses function name.
        """

        def decorator(func: Callable) -> Callable:
            # Determine endpoint path
            endpoint_path = path or func.__name__

            # Validate function signature
            config = self._validate_function_signature(func)

            # Create endpoint function
            endpoint_func = self._create_endpoint_function(func, config)

            # Add to FastAPI app
            self.app.post(f"/{endpoint_path}")(endpoint_func)

            # Store endpoint information
            schema = self._generate_openapi_schema(func, config, endpoint_path)
            self.endpoints[endpoint_path] = {
                "function": func,
                "config": config,
                "schema": schema
            }

            return func

        return decorator

    def serve(self, host: str = "0.0.0.0", port: int = 8000, **kwargs):
        """
        Run the FastAPI server.
        
        Args:
            host: Host to bind to
            port: Port to bind to
            **kwargs: Additional arguments to pass to uvicorn.run
        """
        import uvicorn

        print(f"Starting ToolSet server on http://{host}:{port}")
        print(f"Available endpoints:")
        for path in self.endpoints.keys():
            print(f"  POST /{path}")
            print(f"  GET  /schema/{path}")
        print(f"  GET  /schema (all schemas)")
        print(f"  GET  /docs (Swagger UI)")

        uvicorn.run(self.app, host=host, port=port, **kwargs)
