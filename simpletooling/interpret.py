



import subprocess
import sys
import tempfile
import os
import json


def interpret_python_code(code: str) -> str:
    """
    Interprets a given Python code snippet and returns the result. The result will be the output of the code execution.
    The result Include all the html displayed by IPython
    :param code:
    :return:
    """
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as temp_file:
        # Properly indent the user code
        indented_code = '\n'.join('        ' + line for line in code.split('\n'))
        
        wrapper_code = f'''import sys
import io
import contextlib
import json
try:
    from IPython.display import display, HTML
    from IPython.core.display import DisplayObject
except ImportError:
    def display(obj, **kwargs):
        print(obj)
import traceback

class OutputCapture:
    def __init__(self):
        self.stdout_buffer = io.StringIO()
        self.stderr_buffer = io.StringIO()
        self.display_outputs = []
        
    def capture_display(self, obj, **kwargs):
        if hasattr(obj, '_repr_html_'):
            html_repr = obj._repr_html_()
            if html_repr:
                self.display_outputs.append({{'type': 'html', 'data': html_repr}})
        elif hasattr(obj, '__str__'):
            self.display_outputs.append({{'type': 'text', 'data': str(obj)}})

# Monkey patch display function to capture IPython outputs
try:
    original_display = display
    def patched_display(obj, **kwargs):
        capture.capture_display(obj, **kwargs)
        return original_display(obj, **kwargs)
    display = patched_display
except:
    pass

capture = OutputCapture()

try:
    with contextlib.redirect_stdout(capture.stdout_buffer), \\
         contextlib.redirect_stderr(capture.stderr_buffer):
        
        # Execute the user code
{indented_code}
        
    # Collect all outputs
    result = {{
        'stdout': capture.stdout_buffer.getvalue(),
        'stderr': capture.stderr_buffer.getvalue(),
        'display_outputs': capture.display_outputs,
        'success': True
    }}
    
except Exception as e:
    result = {{
        'stdout': capture.stdout_buffer.getvalue(),
        'stderr': capture.stderr_buffer.getvalue() + traceback.format_exc(),
        'display_outputs': capture.display_outputs,
        'success': False,
        'error': str(e)
    }}

print("__RESULT_START__")
print(json.dumps(result))
print("__RESULT_END__")
'''
        temp_file.write(wrapper_code)
        temp_file.flush()
        
        try:
            result = subprocess.run(
                [sys.executable, temp_file.name],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            
            # Extract the JSON result from the output
            start_marker = "__RESULT_START__"
            end_marker = "__RESULT_END__"
            
            if start_marker in output and end_marker in output:
                start_idx = output.find(start_marker) + len(start_marker)
                end_idx = output.find(end_marker)
                json_str = output[start_idx:end_idx].strip()
                
                try:
                    result_data = json.loads(json_str)
                    
                    # Format the final output
                    final_output = []
                    
                    if result_data['stdout']:
                        final_output.append(f"STDOUT:\n{result_data['stdout']}")
                    
                    if result_data['stderr']:
                        final_output.append(f"STDERR:\n{result_data['stderr']}")
                    
                    if result_data['display_outputs']:
                        final_output.append("DISPLAY OUTPUTS:")
                        for display_output in result_data['display_outputs']:
                            if display_output['type'] == 'html':
                                final_output.append(f"HTML: {display_output['data']}")
                            else:
                                final_output.append(f"TEXT: {display_output['data']}")
                    
                    if not result_data['success']:
                        final_output.append(f"ERROR: {result_data.get('error', 'Unknown error')}")
                    
                    return '\n\n'.join(final_output) if final_output else "No output"
                    
                except json.JSONDecodeError:
                    return f"Failed to parse execution result:\n{output}"
            else:
                return f"Execution output:\n{output}"
                
        except subprocess.TimeoutExpired:
            return "Code execution timed out after 30 seconds"
        except Exception as e:
            return f"Failed to execute code: {str(e)}"
        finally:
            os.unlink(temp_file.name)