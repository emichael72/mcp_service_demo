# MCP Service Demo

This repository demonstrates a **standalone MCP (Model Context Protocol) service**.
It exposes a few simple shell tools as MCP-compatible JSON-RPC tools, served by a lightweight Python engine.

## Repository Layout

```
mcp_service/
├── engine/                 # Python engine
│   ├── Makefile            # Setup (create venv, install deps)
│   ├── mcp.py              # CLI entry point
│   ├── mcp_service.py      # Core MCP service (JSON-RPC over SSE/HTTP)
│   ├── logger.py           # Simple console logger
│   ├── local_types.py      # Local type definitions
│   ├── platform_tools.py   # Platform helpers
│   └── run_inspector.cmd   # Windows helper script to launch the MCP Inspector
└── project/                # Demo project definition
    ├── mcp_demo.json       # Project metadata + tool definitions
    ├── tools/              # Example tool scripts
    │   ├── tool_a.sh       # Greets a user
    │   ├── tool_b.sh       # Always prints random=1
    │   └── tool_c.sh       # Counts lines in a file
    ├── resources/          # Markdown docs for each tool
    │   ├── tool_a.md
    │   ├── tool_b.md
    │   └── tool_c.md
    └── logs/               # Runtime logs (created by service)
```

## Requirements

- **Python 3.9+** (tested with 3.12)
- `make`
- Linux, macOS, or WSL (Windows Subsystem for Linux)

> On Windows native, you can run via WSL or adapt commands to PowerShell.

## Setup

From inside `engine/`:

```bash
cd engine
make install
```

This will:

- Create a Python virtual environment (`.venv/`)
- Upgrade `pip`
- Install required dependencies (`aiohttp`, `colorama`)

To remove the venv:

```bash
make uninstall
```

## Running the Service

Start the service with the demo project:

```bash
make run
```

The service starts an SSE/JSON-RPC endpoint using the port specified in `mcp_demo.json`.

## Trying Out the MCP service

Follow the on-screen examples to copy & paste `curl` requests against your MCP service.  
For a friendlier experience, consider using [MCP Inspector](https://github.com/modelcontextprotocol/inspector) a
complementary tool for exploring and testing MCP services with an interactive UI.

## Notes

- Tools are executed as subprocesses. Output, exit code, and logs are captured and returned in the JSON-RPC response.
- Relative paths in `mcp_demo.json` are resolved relative to the project directory (`project/`).
- Non-zero exit codes are still returned but marked in the `status` field.

---
