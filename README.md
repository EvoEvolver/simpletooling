# SimpleTooling

A Python package that automatically converts well-typed functions into FastAPI endpoints using Pydantic for type validation.

## Features

- **Automatic FastAPI Endpoint Creation**: Convert any well-typed function into a FastAPI endpoint
- **Pydantic Type Validation**: Ensures functions have proper type hints and validates requests
- **OpenAPI Schema Generation**: Automatically generates OpenAPI schemas for all endpoints
- **Schema Endpoints**: Access individual endpoint schemas via `/schema/{endpoint_name}` or all schemas via `/schema`
- **Error Handling**: Provides clear error messages for improperly typed functions

## Installation

```bash
pip install git+https://github.com/EvoEvolver/simpletooling.git
```

Or install from source:

```bash
git clone https://github.com/EvoEvolver/simpletooling.git
cd simpletooling
pip install -e .
```

## Quick Start

```python
from simpletooling import Toolset
from pydantic import BaseModel

# Create a Toolset instance
toolset = Toolset()

# Define your data models
class User(BaseModel):
    name: str
    age: int
    email: str

class UserResponse(BaseModel):
    id: int
    name: str
    age: int
    email: str
    message: str

# Create a well-typed function
@toolset.add()
def create_user(name: str, age: int, email: str) -> UserResponse:
    """Create a new user."""
    return UserResponse(
        id=1,
        name=name,
        age=age,
        email=email,
        message=f"User {name} created successfully!"
    )

# Add another function with custom path
@toolset.add("calculate")
def add_numbers(a: int, b: int) -> int:
    """Add two numbers together."""
    return a + b

# Run the server
if __name__ == "__main__":
    toolset.serve(host="0.0.0.0", port=8000)
```

## Usage

### Basic Function Decorator

```python
@toolset.add()
def my_function(param1: str, param2: int) -> str:
    return f"Result: {param1} and {param2}"
```

### Custom Endpoint Path

```python
@toolset.add("custom/path")
def my_function(param1: str, param2: int) -> str:
    return f"Result: {param1} and {param2}"
```

### Using Pydantic Models

```python
from pydantic import BaseModel

class InputModel(BaseModel):
    name: str
    value: int

class OutputModel(BaseModel):
    result: str
    processed: bool

@toolset.add()
def process_data(data: InputModel) -> OutputModel:
    return OutputModel(
        result=f"Processed {data.name}",
        processed=True
    )
```

## API Endpoints

When you run the server, the following endpoints are automatically created:

- `POST /tool/{function_name}` - Your function endpoints
- `GET /schema/{endpoint_name}` - OpenAPI schema for specific endpoint
- `GET /docs` - Swagger UI documentation
- `GET /openapi.json` - Full OpenAPI specification

## Error Handling

The package will raise errors if:

1. **Missing Return Type**: Function doesn't have a return type annotation
2. **Missing Parameter Types**: Function parameters don't have type annotations
3. **Invalid Types**: Types that can't be converted to Pydantic models

Example error:
```python
# This will raise an error
@toolset.add()
def bad_function(param):  # Missing type annotation
    return "result"

# This will also raise an error
def another_bad_function(param: str):  # Missing return type
    return "result"
```