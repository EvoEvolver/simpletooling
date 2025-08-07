import re
from typing import Dict, Any, Type
from pydantic import Field, create_model


class SchemaGenerator:
    """Handles creation of Pydantic models and schema generation."""

    @staticmethod
    def parse_rst_docstring(docstring: str):
        """Parse RST-style docstrings for parameter and return descriptions."""
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

    @staticmethod
    def create_input_model_from_function(func, tool_name: str, example_map: Dict[str, Dict[str, str]]) -> Type:
        """Create a Pydantic input model from a function signature."""
        import inspect
        
        sig = inspect.signature(func)
        param_desc, return_desc, description = SchemaGenerator.parse_rst_docstring(func.__doc__)
        fields = {}
        
        for name, param in sig.parameters.items():
            annot = param.annotation
            if annot is inspect._empty:
                raise TypeError(
                    f"All parameters for tool '{tool_name}' must be type-annotated. "
                    f"Parameter '{name}' is not."
                )
            default = param.default if param.default is not inspect._empty else ...
            example = example_map.get(tool_name, {}).get(name, None)
            if example is None:
                field_info = Field(default, description=param_desc.get(name, ""))
            else:
                field_info = Field(default, description=param_desc.get(name, ""), examples=[example])
            fields[name] = (annot, field_info)

        return create_model(f"{tool_name}Input", **fields)

    @staticmethod
    def create_input_model_from_mcp_schema(tool_schema: Dict[str, Any], model_name: str) -> Type:
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