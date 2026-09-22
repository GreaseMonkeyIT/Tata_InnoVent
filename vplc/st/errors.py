"""Errors of the Structured Text toolchain. Both carry the source line and column (1-based)."""


class CompileError(Exception):
    """The source is not valid for this subset. A failed load keeps the old task running."""

    def __init__(self, message, line=None, col=None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col

    def __str__(self):
        if self.line is None:
            return self.message
        return f"line {self.line} col {self.col}: {self.message}"

    def as_dict(self):
        return {"line": self.line, "col": self.col, "error": self.message}


class STRuntimeError(Exception):
    """The task failed during a scan (division by zero, loop limit). The runtime goes to FAULT."""

    def __init__(self, message, line=None, col=None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col

    def __str__(self):
        if self.line is None:
            return self.message
        return f"line {self.line} col {self.col}: {self.message}"
