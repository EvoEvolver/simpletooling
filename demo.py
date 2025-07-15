#!/usr/bin/env python3
"""
Minimal demo of SimpleTooling package.
This script shows the basic usage without complex examples.
"""

from simpletooling import Toolset

# Create a Toolset instance
toolset = Toolset(title="Demo API", version="1.0.0")

# Simple function with basic types
@toolset.add()
def hello(name: str) -> str:
    """Say hello to someone."""
    return f"Hello, {name}!"

# Function with multiple parameters
@toolset.add()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

# Function with custom path
@toolset.add("multiply")
def multiply_numbers(x: float, y: float) -> float:
    """Multiply two numbers."""
    return x * y

if __name__ == "__main__":
    print("🚀 Starting SimpleTooling demo server...")
    print("Available endpoints:")
    print("  POST /hello")
    print("  POST /add") 
    print("  POST /multiply")
    print("  GET  /schema/hello")
    print("  GET  /schema/add")
    print("  GET  /schema/multiply")
    print("  GET  /schema (all schemas)")
    print("  GET  /docs (Swagger UI)")
    print()
    print("Try these curl commands:")
    print('  curl -X POST "http://localhost:8000/hello" -H "Content-Type: application/json" -d \'{"name": "World"}\'')
    print('  curl -X POST "http://localhost:8000/add" -H "Content-Type: application/json" -d \'{"a": 5, "b": 3}\'')
    print('  curl -X POST "http://localhost:8000/multiply" -H "Content-Type: application/json" -d \'{"x": 4.5, "y": 2.0}\'')
    print()
    
    # Start the server
    toolset.serve(host="0.0.0.0", port=8000) 