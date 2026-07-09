import os
from .base import BaseConnector

class Office365Connector(BaseConnector):
    name = "office365"

    def get_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "o365_search_email",
                    "description": "Cerca email su Microsoft 365",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}}
                    }
                }
            }
        ]

    def execute(self, tool_name: str, args: dict) -> str:
        # TODO: implementa con microsoft-graph-client o O365 lib
        return f"[Office365Connector] {tool_name} non ancora implementato completamente. Configura MS_CLIENT_ID etc."
