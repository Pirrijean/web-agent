import os
import re
import sys
import json
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class RPC:
    """Cliente RPC ligero para servidores MCP aislados vía stdio."""

    def __init__(self, module: str, config_path: str, allowed_secret_env: tuple[str, ...] = ()):
        self.module = module
        self.config_path = str(config_path)
        self.allowed_secret_env = set(allowed_secret_env)
        self.stack = AsyncExitStack()
        self.session = None

    async def __aenter__(self):
        env = {}
        for key, value in os.environ.items():
            sensitive = re.search(
                r"(?i)(secret|token|key|password|credential)", key
            )
            if not sensitive or key in self.allowed_secret_env:
                env[key] = value

        env["FRONTIER_CONFIG"] = self.config_path

        try:
            streams = await self.stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=sys.executable,
                        args=["-m", self.module],
                        env=env,
                    )
                )
            )
            self.session = await self.stack.enter_async_context(
                ClientSession(*streams)
            )
            await self.session.initialize()
            return self
        except BaseException:
            await self.stack.aclose()
            raise

    async def call(self, name: str, **arguments):
        result = await self.session.call_tool(name, arguments=arguments)
        if result.isError:
            raise RuntimeError("La herramienta MCP rechazó la operación.")

        texts = [
            item.text
            for item in result.content
            if getattr(item, "type", None) == "text"
        ]
        if len(texts) != 1:
            raise RuntimeError("Respuesta MCP inválida.")

        return json.loads(texts[0])

    async def __aexit__(self, *args):
        await self.stack.aclose()
