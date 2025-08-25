#!/bin/bash

# Usage: ./greet_user.sh <name>
# Prints a friendly greeting

while [ $# -gt 0 ]; do
  case "$1" in
    --name)
      NAME="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

echo "Hello, $NAME! Welcome to the MCP demo project."
