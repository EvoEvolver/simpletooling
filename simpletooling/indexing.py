
import os
import importlib
import importlib.util
import sys
from pathlib import Path


def load_tool_from_module(module):
    """
    Recursively load all Python files in the module's directory 
    and ensure decorators are run by importing them.
    
    Args:
        module: The module object to load tools from
    """
    module_path = Path(module.__file__).parent
    
    def _load_python_files(directory):
        """Recursively load all Python files in directory"""
        for item in directory.iterdir():
            if item.is_file() and item.suffix == '.py' and item.name != '__init__.py':
                # Convert path to module name
                relative_path = item.relative_to(module_path)
                module_name = str(relative_path.with_suffix(''))
                module_name = module_name.replace(os.sep, '.')
                
                # Create full module name
                full_module_name = f"{module.__name__}.{module_name}"
                
                try:
                    # Import the module to trigger decorator execution
                    if full_module_name not in sys.modules:
                        importlib.import_module(full_module_name)
                except ImportError as e:
                    print(f"Warning: Could not import {full_module_name}: {e}")
                    
            elif item.is_dir() and not item.name.startswith('__'):
                # Recursively process subdirectories
                _load_python_files(item)
    
    _load_python_files(module_path)