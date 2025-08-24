"""
Script:         local_types,py
Author:         AutoForge Team

Description:
    Single point module defining many common types, enumerations, and simple classes
    shared across multiple components of the project.
"""
import os
import sys
from typing import Optional

AUTO_FORGE_MODULE_NAME: str = "LocalTypes"
AUTO_FORGE_MODULE_DESCRIPTION: str = "Project shared types"


class ExceptionGuru:
    """
    A singleton utility class for capturing and exposing the origin (filename and line number)
    of the innermost frame where the most recent exception occurred bty ensuring the exception context
    is captured only once.
    """

    _instance: Optional["ExceptionGuru"] = None
    _context_stored: bool = False

    def __new__(cls) -> "ExceptionGuru":
        """
        Overrides object instantiation to implement the singleton pattern.
        Returns:
            ExceptionGuru: The singleton instance of the class.
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """
        Initializes the exception context (filename and line number).
        The context is captured only once during the lifetime of the singleton instance.
        """
        if not self.__class__._context_stored:
            self._file_name: Optional[str] = "<unknown>"
            self._line_number: Optional[int] = -1
            self._store_context()
            self.__class__._context_stored = True

    def get_context(self) -> tuple[str, int]:
        """
        Retrieves the exception origin information.
        Returns:
            Tuple[str, int]: A tuple containing the base filename and the line number
                             where the exception originally occurred.
        """
        return self._file_name, self._line_number

    def _store_context(self) -> None:
        """
        Captures the filename and line number of the innermost frame where the most recent
        exception occurred. If no exception context is found, defaults to '<unknown>' and -1.
        """
        exc_type, exc_obj, exc_tb = sys.exc_info()

        if exc_tb is None:
            return

        # Traverse to the innermost (deepest) frame
        tb = exc_tb
        while tb.tb_next:
            tb = tb.tb_next

        self._file_name = os.path.basename(tb.tb_frame.f_code.co_filename)
        self._line_number = tb.tb_lineno
