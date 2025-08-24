#!/bin/bash

# Usage: ./tools_a.sh <name>
# Prints a friendly greeting

if [ -z "$1" ]; then
  echo "Usage: $0 <name>"
  exit 1
fi

echo "Hello, $1! Welcome to the MCP demo project."
