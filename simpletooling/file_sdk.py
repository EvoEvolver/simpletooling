import os
import random
import string
import tempfile
from datetime import datetime
from typing import Any, Optional, Union, BinaryIO, TextIO
from io import StringIO, BytesIO
from minio import Minio
from minio.error import S3Error


def upload_by_file_path(file_path: str, suggested_filename: str) -> str:
    """
    Upload a file to MinIO/S3 with filename collision handling.
    
    Args:
        file_path: Path to the local file to upload
        suggested_filename: Desired filename for object
        
    Returns:
        str: The URL to access the uploaded file
        
    Raises:
        ValueError: If required environment variables are missing
        Exception: If upload fails
    """
    with open(file_path, 'rb') as file_obj:
        return upload_file_object(file_obj, suggested_filename)


def upload_matplotlib_figure(fig: Any, suggested_filename: str, img_format: str = 'png', **kwargs: Any) -> str:
    """
    Upload a matplotlib figure to MinIO/S3.
    
    Args:
        fig: matplotlib figure object
        suggested_filename: Desired filename (extension will be added if missing)
        img_format: Image format ('png', 'jpg', 'pdf', 'svg', etc.)
        **kwargs: Additional arguments passed to fig.savefig()
        
    Returns:
        str: The URL to access the uploaded figure
    """
    # Ensure filename has correct extension
    name_parts = os.path.splitext(suggested_filename)
    if not name_parts[1] or name_parts[1][1:].lower() != img_format.lower():
        suggested_filename = f"{name_parts[0]}.{img_format}"
    
    # Save figure to temporary file
    with tempfile.NamedTemporaryFile(suffix=f'.{img_format}', delete=False) as temp_file:
        try:
            fig.savefig(temp_file.name, format=img_format, **kwargs)
            return upload_by_file_path(temp_file.name, suggested_filename)
        finally:
            os.unlink(temp_file.name)


def upload_string(content: str, suggested_filename: str, encoding: str = 'utf-8') -> str:
    """
    Upload a string as a text file to MinIO/S3.
    
    Args:
        content: String content to upload
        suggested_filename: Desired filename
        encoding: Text encoding (default: 'utf-8')
        
    Returns:
        str: The URL to access the uploaded file
    """
    # Save string to temporary file
    with tempfile.NamedTemporaryFile(mode='w', encoding=encoding, suffix='.txt', delete=False) as temp_file:
        try:
            temp_file.write(content)
            temp_file.flush()
            return upload_by_file_path(temp_file.name, suggested_filename)
        finally:
            os.unlink(temp_file.name)


def upload_file_object(file_obj: Union[BinaryIO, TextIO, StringIO, BytesIO], suggested_filename: str, content_type: Optional[str] = None) -> str:
    """
    Upload an opened file object to MinIO/S3.
    
    Args:
        file_obj: File-like object (opened file, StringIO, BytesIO, etc.)
        suggested_filename: Desired filename
        content_type: MIME content type (optional)
        
    Returns:
        str: The URL to access the uploaded file
    """
    # Get MinIO client configuration
    endpoint_url = os.getenv('MINIO_URL', 'http://localhost:9000')
    assert endpoint_url.startswith('http://') or endpoint_url.startswith('https://'), "MINIO_URL must start with http:// or https://"
    access_key = os.getenv('MINIO_ACCESS_KEY')
    secret_key = os.getenv('MINIO_SECRET_KEY')
    secure = endpoint_url.startswith('https://')
    endpoint = endpoint_url.replace('http://', '').replace('https://', '')
    # split endpoint to get bucket name and endpoint
    if '/' in endpoint:
        endpoint, bucket_name = endpoint.split('/', 1)
    else:
        bucket_name = 'agent-files'
    
    # Create MinIO client
    if access_key and secret_key:
        client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure
        )
    else:
        client = Minio(endpoint, secure=secure)
    
    filename = suggested_filename
    
    # Check if filename exists and generate alternative if needed
    try:
        client.stat_object(bucket_name, filename)
        # File exists, generate new name with date
        name_parts = os.path.splitext(suggested_filename)
        suffix = datetime.now().strftime('%m%d_%H%M%S')
        filename = f"{name_parts[0]}_{suffix}{name_parts[1]}"
    except S3Error as e:
        if e.code != 'NoSuchKey':
            raise e
    
    # Handle different file object types
    if isinstance(file_obj, StringIO):
        # Convert StringIO to BytesIO
        data = BytesIO(file_obj.getvalue().encode('utf-8'))
        data.seek(0)
        file_obj = data
    elif hasattr(file_obj, 'read'):
        # File-like object, seek to beginning
        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)
    
    # Get file size
    if hasattr(file_obj, 'seek') and hasattr(file_obj, 'tell'):
        current_pos = file_obj.tell()
        file_obj.seek(0, 2)  # Seek to end
        file_size = file_obj.tell()
        file_obj.seek(current_pos)  # Restore position
    else:
        file_size = -1
    
    # Upload the file object
    try:
        client.put_object(
            bucket_name, 
            filename, 
            file_obj, 
            length=file_size,
            content_type=content_type
        )
        # Construct and return the URL
        protocol = 'https' if secure else 'http'
        url = f"{protocol}://{endpoint}/{bucket_name}/{filename}"
        return url
    except Exception as e:
        raise Exception(f"Failed to upload file object to MinIO: {str(e)}")