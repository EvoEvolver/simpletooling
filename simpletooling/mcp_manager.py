import asyncio
import hashlib
import json
from typing import Dict, Any, Optional
import httpx
from fastapi import HTTPException
from .mcp_client import MCPConnection


class MCPManager:
    """Manages MCP server connections and their lifecycle."""
    
    def __init__(self):
        self.connections: Dict[str, MCPConnection] = {}  # config_hash -> connection
        self.tool_schemas: Dict[str, Dict] = {}  # config_hash -> {tool_name: schema}
        self.config_hashes: Dict[str, str] = {}  # config_hash -> original_config_json
        self.cleanup_task: Optional[asyncio.Task] = None

    def compute_config_hash(self, config: Dict[str, Any]) -> str:
        """Compute a hash for the MCP configuration to identify identical configs."""
        config_json = json.dumps(config, sort_keys=True)
        return hashlib.sha256(config_json.encode()).hexdigest()[:8]

    async def add_server(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Add an MCP server and return its tool schemas."""
        config_hash = self.compute_config_hash(config)
        print(f"[MCPManager.add_server] Starting MCP server addition with config hash: {config_hash}")
        print(f"[MCPManager.add_server] Config: {json.dumps(config, indent=2)}")
        
        # Check if this exact config already exists
        if config_hash in self.connections:
            print(f"[MCPManager.add_server] Config already exists, returning cached data")
            return {
                "config_hash": config_hash,
                "tools": self.tool_schemas.get(config_hash, {}),
                "status": "cached",
                "message": f"Using cached connection with {len(self.tool_schemas.get(config_hash, {}))} tools"
            }
        
        # Create new MCP connection
        print(f"[MCPManager.add_server] Creating new MCP connection...")
        
        try:
            mcp_conn = MCPConnection(config, config_hash)
            print(f"[MCPManager.add_server] MCPConnection object created, starting connection...")
            
            await mcp_conn.connect()
            print(f"[MCPManager.add_server] Connection completed, checking if truly connected...")
            
            # Check if connection was actually successful
            if not mcp_conn.is_connected:
                print(f"[MCPManager.add_server] Connection object reports not connected!")
                return {
                    "config_hash": config_hash,
                    "tools": {},
                    "status": "error",
                    "message": "Connection failed - server not responding"
                }
            
            # Check if we got any tools (real MCP servers should have tools)
            if len(mcp_conn.tools) == 0:
                print(f"[MCPManager.add_server] Warning: Connected but no tools found. May not be a real MCP server.")
            
            print(f"[MCPManager.add_server] Storing connection and schemas...")
            # Store connection and schemas
            self.connections[config_hash] = mcp_conn
            self.tool_schemas[config_hash] = mcp_conn.tools
            self.config_hashes[config_hash] = json.dumps(config, sort_keys=True)
            
            print(f"[MCPManager.add_server] Starting cleanup task...")
            # Start cleanup task if not already running
            if self.cleanup_task is None or self.cleanup_task.done():
                self.cleanup_task = asyncio.create_task(self._cleanup_idle_connections())
            
            print(f"[MCPManager.add_server] ✅ MCP server added successfully with {len(mcp_conn.tools)} tools")
            
            return {
                "config_hash": config_hash,
                "tools": mcp_conn.tools,
                "status": "success",
                "message": f"Connected with {len(mcp_conn.tools)} tools"
            }
            
        except Exception as e:
            print(f"[MCPManager.add_server] ❌ Exception during MCP server addition: {str(e)}")
            print(f"[MCPManager.add_server] Exception type: {type(e)}")
            import traceback
            print(f"[MCPManager.add_server] Full traceback: {traceback.format_exc()}")
            
            # Return error info but don't raise exception to prevent hanging
            return {
                "config_hash": config_hash,
                "tools": {},
                "status": "error",
                "message": f"Failed to connect: {str(e)}"
            }

    async def health_check(self, config_hash: str) -> Dict[str, Any]:
        """Check if MCP connection is still active and healthy."""
        if not config_hash:
            raise HTTPException(status_code=400, detail="config_hash is required")
        
        print(f"[MCPManager.health_check] Checking health for config_hash: {config_hash}")
        
        # Check if connection exists
        mcp_conn = self.connections.get(config_hash)
        if not mcp_conn:
            return {
                "config_hash": config_hash,
                "healthy": False,
                "status": "not_found",
                "message": "MCP connection not found"
            }
        
        # Check if connection is still active
        is_healthy = mcp_conn.is_connected and not mcp_conn.is_idle()
        
        # Determine connection type
        if isinstance(mcp_conn.session, httpx.AsyncClient):
            connection_type = "http"
        elif hasattr(mcp_conn, 'stdio_process') and mcp_conn.stdio_process:
            connection_type = "stdio"
        else:
            connection_type = "unknown"
        
        print(f"[MCPManager.health_check] Health check result for {config_hash}: {'HEALTHY' if is_healthy else 'UNHEALTHY'}")
        
        return {
            "config_hash": config_hash,
            "healthy": is_healthy,
            "status": "active" if is_healthy else "idle",
            "tools_count": len(mcp_conn.tools),
            "last_access": mcp_conn.last_access.isoformat(),
            "connection_type": connection_type
        }

    async def close_connection(self, config_hash: str) -> Dict[str, Any]:
        """Immediately cleanup and close MCP connection."""
        if not config_hash:
            raise HTTPException(status_code=400, detail="config_hash is required")
        
        print(f"[MCPManager.close_connection] Closing MCP connection for config_hash: {config_hash}")
        
        # Check if connection exists
        mcp_conn = self.connections.get(config_hash)
        if not mcp_conn:
            return {
                "config_hash": config_hash,
                "closed": False,
                "status": "not_found",
                "message": "MCP connection not found"
            }
        
        try:
            # Force cleanup
            await self._cleanup_connection(config_hash)
            
            print(f"[MCPManager.close_connection] Successfully closed connection for {config_hash}")
            
            return {
                "config_hash": config_hash,
                "closed": True,
                "status": "success",
                "message": "MCP connection closed and cleaned up"
            }
            
        except Exception as e:
            print(f"[MCPManager.close_connection] Error closing connection {config_hash}: {e}")
            raise HTTPException(status_code=500, detail=f"Close operation failed: {str(e)}")

    async def get_connection(self, config_hash: str) -> Optional[MCPConnection]:
        """Get an MCP connection by config hash."""
        return self.connections.get(config_hash)

    async def call_tool(self, config_hash: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call a tool on a specific MCP connection."""
        mcp_conn = self.connections.get(config_hash)
        if not mcp_conn:
            raise HTTPException(status_code=404, detail="MCP server not found")
        
        try:
            # Filter out placeholder fields from the arguments
            tool_args = {k: v for k, v in arguments.items() 
                       if k != "placeholder__" and v is not None}
            result = await mcp_conn.call_tool(tool_name, tool_args)
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    async def _cleanup_idle_connections(self):
        """Background task to cleanup idle MCP connections."""
        while True:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes
                
                idle_connections = []
                for config_hash, mcp_conn in self.connections.items():
                    if mcp_conn.is_idle():
                        idle_connections.append(config_hash)
                
                for config_hash in idle_connections:
                    await self._cleanup_connection(config_hash)
                    
            except Exception as e:
                print(f"Error in cleanup task: {e}")

    async def _cleanup_connection(self, config_hash: str):
        """Clean up a specific MCP connection."""
        mcp_conn = self.connections.get(config_hash)
        if mcp_conn:
            await mcp_conn.disconnect()
            
            # Remove from tracking
            del self.connections[config_hash]
            if config_hash in self.tool_schemas:
                del self.tool_schemas[config_hash]
            if config_hash in self.config_hashes:
                del self.config_hashes[config_hash]
            
            print(f"Cleaned up idle MCP connection: {config_hash}")