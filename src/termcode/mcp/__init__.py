"""MCP 客户端的配置、连接管理和工具适配。"""

from termcode.mcp.config import Config, ServerConfig, load_config
from termcode.mcp.manager import Manager, new_manager
from termcode.mcp.tool import McpTool

__all__ = ["Config", "ServerConfig", "Manager", "load_config", "new_manager", "McpTool"]
