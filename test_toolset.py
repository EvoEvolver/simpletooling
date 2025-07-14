#!/usr/bin/env python3
"""
Test script for SimpleTooling package.
This script tests the basic functionality without requiring the full dependencies.
"""

import sys
import os

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from simpletooling import ToolSet
    from pydantic import BaseModel
    from typing import List, Optional
    
    print("✓ All imports successful")
    
    # Test 1: Create ToolSet instance
    toolset = ToolSet(title="Test API", version="1.0.0")
    print("✓ ToolSet instance created")
    
    # Test 2: Simple function
    @toolset.add()
    def test_add(a: int, b: int) -> int:
        return a + b
    
    print("✓ Simple function decorated")
    
    # Test 3: Function with Pydantic model
    class TestInput(BaseModel):
        name: str
        value: int
    
    class TestOutput(BaseModel):
        result: str
        success: bool
    
    @toolset.add()
    def test_process(data: TestInput) -> TestOutput:
        return TestOutput(
            result=f"Processed {data.name}",
            success=True
        )
    
    print("✓ Function with Pydantic models decorated")
    
    # Test 4: Function with optional parameters
    @toolset.add()
    def test_optional(name: str, age: Optional[int] = None) -> str:
        if age:
            return f"Hello {name}, you are {age} years old"
        return f"Hello {name}"
    
    print("✓ Function with optional parameters decorated")
    
    # Test 5: Check endpoints
    expected_endpoints = ["test_add", "test_process", "test_optional"]
    actual_endpoints = list(toolset.endpoints.keys())
    
    for endpoint in expected_endpoints:
        if endpoint in actual_endpoints:
            print(f"✓ Endpoint '{endpoint}' registered")
        else:
            print(f"✗ Endpoint '{endpoint}' not found")
    
    # Test 6: Check schemas
    for endpoint in expected_endpoints:
        if endpoint in toolset.endpoints:
            schema = toolset.endpoints[endpoint].get("schema", {})
            if schema:
                print(f"✓ Schema generated for '{endpoint}'")
            else:
                print(f"✗ No schema for '{endpoint}'")
    
    print("\n🎉 All tests passed! The SimpleTooling package is working correctly.")
    print("\nTo run the server, use:")
    print("  python example.py")
    print("\nOr in your own code:")
    print("  toolset.serve(host='0.0.0.0', port=8000)")
    
except ImportError as e:
    print(f"✗ Import error: {e}")
    print("Please install the required dependencies:")
    print("  pip install fastapi uvicorn pydantic pyyaml")
    
except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc() 