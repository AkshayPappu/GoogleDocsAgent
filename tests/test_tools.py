import json
import unittest

from mcp.server.mcpserver import MCPServer

from gdocs_agent.agent import _trim, summarize_result
from gdocs_agent.tools import TOOL_FUNCTIONS, _color, _heading_style, _text_style


class ToolHelperTests(unittest.TestCase):
    def test_heading_and_color(self):
        self.assertEqual(_heading_style("heading 2"), "HEADING_2")
        self.assertEqual(_heading_style("h1"), "HEADING_1")
        self.assertEqual(_heading_style("normal"), "NORMAL_TEXT")
        color = _color("#fff")
        self.assertEqual(color["color"]["rgbColor"]["red"], 1)
        style, fields = _text_style(True, None, "Georgia", 12, "#112233")
        self.assertEqual(style["bold"], True)
        self.assertIn("fontSize", fields)
        self.assertEqual(style["fontSize"]["magnitude"], 12)

    def test_summarize_and_trim(self):
        self.assertEqual(
            summarize_result(json.dumps({"changed": "Inserted text after p2."})),
            "Inserted text after p2.",
        )
        messages = [{"role": "assistant", "content": "old"}]
        messages.extend(
            {"role": "user", "content": f"turn {index}"} for index in range(45)
        )
        _trim(messages)
        self.assertLessEqual(len(messages), 40)
        self.assertEqual(messages[0]["role"], "user")

    def test_mcp_registers_every_tool(self):
        server = MCPServer("gdocs-agent")
        for function in TOOL_FUNCTIONS.values():
            server.tool()(function)
        names = {tool.name for tool in server._tool_manager.list_tools()}
        self.assertEqual(names, set(TOOL_FUNCTIONS))


if __name__ == "__main__":
    unittest.main()
