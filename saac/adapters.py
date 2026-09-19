"""Three integration shapes, one SDK, zero authority decisions.

MCPAdapter models tools/list and tools/call; it is deliberately not an MCP wire
server or a claim of protocol conformance. The protected rail remains mandatory
when every adapter is removed or compromised.
"""
from .models import require
from .sdk import SAACClient

MODES = ("native", "harness", "mcp")


class NativeApplication:
    def __init__(self, sdk: SAACClient):
        self.sdk = sdk

    def propose(self, intent, preview=False, *, request_id=None):
        return self.sdk.propose(intent, preview=preview, harness="native-app", request_id=request_id)


class AgentHarness:
    def __init__(self, sdk: SAACClient):
        self.sdk = sdk

    def propose(self, tool_call, preview=False, *, request_id=None):
        require(isinstance(tool_call, dict) and set(tool_call) == {"name", "arguments"} and tool_call["name"] == "propose_action"
                and isinstance(tool_call['arguments'], dict),
                "TOOL", "The model may propose a structured action through propose_action.")
        return self.sdk.propose(tool_call["arguments"], preview=preview, harness="agent-harness", request_id=request_id)


class MCPAdapter:
    def __init__(self, sdk: SAACClient):
        self.sdk = sdk

    def list_tools(self):
        return {"tools": [
            {"name": "saac.propose", "description": "Request authority for a structured intent; reuse request_id after a lost response.",
             "inputSchema": {"type": "object", "properties": {"intent": {"type": "object"}, "preview": {"type": "boolean"}, "request_id": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": "^[A-Za-z0-9._:-]+$"}}, "required": ["intent"], "additionalProperties": False}},
            {"name": "saac.execute", "description": "Present exact intent and κ to the protected rail.",
             "inputSchema": {"type": "object", "properties": {"proposal": {"type": "object"}, "kappa": {"type": ["object", "null"]}}, "required": ["proposal", "kappa"], "additionalProperties": False}},
            {"name": "saac.reconcile", "description": "Submit signed rail evidence to SAAC.",
             "inputSchema": {"type": "object", "properties": {"receipt": {"type": "object"}}, "required": ["receipt"], "additionalProperties": False}},
        ]}

    def call_tool(self, name, arguments):
        schemas = {t["name"]: t["inputSchema"] for t in self.list_tools()["tools"]}
        require(name in schemas, "TOOL", "Unknown SAAC tool.")
        schema = schemas[name]
        require(isinstance(arguments, dict) and set(schema["required"]) <= set(arguments) <= set(schema["properties"]),
                "TOOL_ARGUMENTS", "Tool arguments must match the declared structured interface.")
        require(all(isinstance(arguments[k], dict) for k in schema['required'] if k != 'kappa')
                and ('kappa' not in arguments or arguments['kappa'] is None or isinstance(arguments['kappa'], dict)),
                "TOOL_ARGUMENTS", "Intent, proposal, capability and receipt must be structured objects.")
        if name == "saac.propose":
            require(type(arguments.get("preview", False)) is bool, "TOOL_ARGUMENTS", "preview must be boolean.")
            require('request_id' not in arguments or isinstance(arguments['request_id'], str), "TOOL_ARGUMENTS", "request_id must be a string.")
            result = self.sdk.propose(arguments["intent"], preview=arguments.get("preview", False), harness="mcp-adapter", request_id=arguments.get("request_id"))
        elif name == "saac.execute":
            result = self.sdk.execute(arguments["proposal"], arguments["kappa"])
        else:
            result = self.sdk.reconcile(arguments["receipt"])
        return {"structuredContent": result, "isError": result.get("accepted") is False or result.get("status") == "denied"}


def propose_via(mode, sdk, intent, preview=False, *, request_id=None):
    require(mode in MODES, "INTEGRATION", "Choose native, harness or mcp integration.")
    if mode == "native":
        return NativeApplication(sdk).propose(intent, preview, request_id=request_id)
    if mode == "harness":
        return AgentHarness(sdk).propose({"name": "propose_action", "arguments": intent}, preview, request_id=request_id)
    arguments = {"intent": intent, "preview": preview}
    if request_id is not None: arguments["request_id"] = request_id
    return MCPAdapter(sdk).call_tool("saac.propose", arguments)["structuredContent"]
