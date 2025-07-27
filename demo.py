#!/usr/bin/env python3
"""
Minimal demo of SimpleTooling package.
This script shows the basic usage without complex examples.
"""
import os

from simpletooling import Toolset

# Create a Toolset instance
toolset = Toolset(title="Demo API", version="1.0.0")

# Simple function with basic types
@toolset.add()
def hello(name: str) -> str:
    """
    A simple function that greets the user.
    :param name: the name of the user. example: "World"
    :return: the greeting message. example: "Hello, World!"
    """
    return f"Hello, {name}!"

# Function with multiple parameters
@toolset.add()
def add(a: int, b: int) -> int:
    """
    Add two integers together.
    :param a: the first integer
    :param b: the second integer
    :return: the sum of a and b
    """
    return a + b

# Function with custom path
@toolset.add("multiply")
def multiply_numbers(x: float, y: float) -> float:
    """
    Multiply two floating-point numbers.
    :param x: the first number
    :param y: the second number
    :return: the product of x and y
    """
    return x * y


os.environ["MINIO_URL"] = "https://storage.treer.ai"
@toolset.add()
def upload_txt_file(file_content: str) -> str:
    """
    Simulate file upload.
    :param file_content: the file content as bytes
    :return: the url of the uploaded file
    """
    from simpletooling.file_sdk import upload_string
    url = upload_string(
        file_content,
        suggested_filename="demo.txt",
    )
    return f"File uploaded to: {url}"


if __name__ == "__main__":
    # Start the server
    toolset.serve(host="0.0.0.0", port=8001, interpreter=True)