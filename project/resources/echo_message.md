# Echo a message

Demonstrates how static arguments (defined in JSON) and dynamic parameters (provided at runtime) work together.

## Usage

```bash
python3 tools/echo_message.py [OPTIONS] <message>
```

## Parameters

- **message** *(string, required)*  
  The message text to echo.

- **repeat** *(integer, optional, default: 1)*  
  Number of times to repeat the message.

- **--uppercase** *(flag, static)*  
  Always applied if configured in the JSON. Converts the message to uppercase.

## Examples

```bash
# Basic usage
python3 tools/echo_message.py "Hello world"

# Repeat 3 times
python3 tools/echo_message.py "Hello" --repeat 3

# With static --uppercase (enabled in JSON)
python3 tools/echo_message.py "Hello MCP" --repeat 2
```

---
This tool is mainly for testing **argparse** integration and how MCP tools can mix static and dynamic arguments.
