#!/usr/bin/env python3
"""
Script:         mcp,py
Author:         DevOps Team

Description:
    MCP Service Demo Engine.
    Minimal entrypoint for the MCP demo service.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Any

# Third-party
from colorama import Fore, Style, init

# MCP Service imports
from local_types import ExceptionGuru
from mcp_service import CoreMCPService

MCP_ENGINE_VERSION = "1.0"


def parse_args():
    """
    Parse command-line arguments for the MCP demo engine.
    Returns:
        argparse.Namespace: Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        prog="mcp",
        description="Standalone MCP demo engine",
    )

    group = parser.add_mutually_exclusive_group(required=True)

    group.add_argument("-v", "--version", action="store_true", help="Show MCP engine version and exit", )
    group.add_argument("-p", "--project", help="Path to project JSON file for MCP service", )

    return parser.parse_args()


def load_project(json_path: Path) -> Optional[dict[str, Any]]:
    """
    Load and parse the project JSON file.
    Args:
        json_path: Absolute path to the JSON file.
    Returns:
        Parsed project dictionary, or None on failure.
    """
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            project = json.load(f)
        return project
    except Exception as e:
        raise RuntimeError(f"Failed to load {json_path}: {e}", file=sys.stderr)


def main() -> int:
    """
    MCP service demo entry point
    Returns:
        Shell status, 0 success, else failure.
    """
    result: int = 1  # Default to error
    init(autoreset=True)  # Init colorama

    try:
        args = parse_args()

        if args.version:
            print(f"MCP Engine Version: {MCP_ENGINE_VERSION}")
            result = 0


        elif args.project:

            # Expand user (~) and env vars, then resolve to absolute

            expanded = os.path.expanduser(os.path.expandvars(str(args.project)))
            json_path = Path(expanded).resolve()
            if not json_path.is_file():
                raise RuntimeError(f"Project file not found: {json_path}")

            # Save old working directory
            old_cwd = Path.cwd()
            try:
                # Switch to the directory containing the project file
                os.chdir(json_path.parent)
                project_data = load_project(json_path)
                mcp_service = CoreMCPService(
                    project_data=project_data,
                    tools_prefix="",
                    patch_vscode_config=False,
                    show_usage_examples=True, )

                mcp_service.start()
                result = 0

            finally:
                # Always restore original CWD
                os.chdir(old_cwd)

    except KeyboardInterrupt:
        print(f"\n\n{Fore.YELLOW}Interrupted by user, shutting down.{Style.RESET_ALL}\n")

    except Exception as runtime_error:
        # Retrieve information about the original exception that triggered this handler.
        file_name, line_number = ExceptionGuru().get_context()
        invocation = " ".join(sys.argv)
        print(f"\n{Fore.RED}Exception:{Style.RESET_ALL} {runtime_error}.\nFile: {file_name}\nLine: {line_number}")
        print(f"Invocation: {invocation}\n")

    return result


if __name__ == "__main__":
    sys.exit(main())
