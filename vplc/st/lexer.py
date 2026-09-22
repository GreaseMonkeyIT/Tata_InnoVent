"""Tokenizer for the Structured Text subset.

Token kinds:
  IDENT   an identifier, value = the upper-case name, text = the name as written
  KW      a keyword, value = the upper-case keyword
  INT     an integer literal (123, 1_000, 16#FF, 8#17, 2#1010), value = int
  REAL    a real literal (1.5, 2.0E3), value = float
  TIME    a duration literal (T#250ms, TIME#1m30s), value = milliseconds as int
  ADDR    a direct address (%IX0.1, %MW20), value = the upper-case text
  OP      an operator or punctuation, value = the operator text
  EOF     the end of the source

Keywords and identifiers are case-insensitive. Comments are (* ... *) and // to the end of the line.
"""
from .errors import CompileError

KEYWORDS = {
    "PROGRAM", "END_PROGRAM", "VAR", "END_VAR", "CONSTANT", "RETAIN", "AT",
    "IF", "THEN", "ELSIF", "ELSE", "END_IF", "CASE", "OF", "END_CASE",
    "FOR", "TO", "BY", "DO", "END_FOR", "RETURN", "EXIT",
    "NOT", "AND", "OR", "XOR", "MOD", "TRUE", "FALSE",
    "CONFIGURATION", "END_CONFIGURATION", "RESOURCE", "END_RESOURCE", "ON", "TASK", "WITH",
    # Reserved words outside the subset. The parser names them in a clear error.
    "WHILE", "END_WHILE", "REPEAT", "UNTIL", "END_REPEAT", "FUNCTION", "END_FUNCTION",
    "FUNCTION_BLOCK", "END_FUNCTION_BLOCK", "VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT",
    "VAR_GLOBAL", "VAR_EXTERNAL", "VAR_TEMP", "TYPE", "END_TYPE", "STRUCT", "END_STRUCT",
}

# Longest operators first, so ':=' wins over ':' and '**' wins over '*'.
OPERATORS = [":=", "=>", "<=", ">=", "<>", "**", "..", "+", "-", "*", "/", "=", "<", ">",
             "&", "(", ")", ",", ";", ":", "."]

_TIME_UNITS = [("MS", 1), ("D", 86_400_000), ("H", 3_600_000), ("M", 60_000), ("S", 1000)]


class Token:
    __slots__ = ("kind", "value", "text", "line", "col")

    def __init__(self, kind, value, text, line, col):
        self.kind, self.value, self.text, self.line, self.col = kind, value, text, line, col

    def is_kw(self, *names):
        return self.kind == "KW" and self.value in names

    def is_op(self, *ops):
        return self.kind == "OP" and self.value in ops

    def __repr__(self):
        return f"Token({self.kind}, {self.value!r}, {self.line}:{self.col})"


def tokenize(src):
    """Return the list of tokens for src. Raise CompileError with line and column on bad input."""
    toks = []
    i, n = 0, len(src)
    line, line_start = 1, 0

    def col_of(pos):
        return pos - line_start + 1

    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            line_start = i
            continue
        if c in " \t\r\f\v":
            i += 1
            continue
        if src.startswith("(*", i):
            end = src.find("*)", i + 2)
            if end < 0:
                raise CompileError("comment is not closed, expected '*)'", line, col_of(i))
            for j in range(i, end):
                if src[j] == "\n":
                    line += 1
                    line_start = j + 1
            i = end + 2
            continue
        if src.startswith("//", i):
            end = src.find("\n", i)
            i = n if end < 0 else end
            continue
        start, scol = i, col_of(i)
        if c.isalpha() or c == "_":
            while i < n and (src[i].isalnum() or src[i] == "_"):
                i += 1
            word = src[start:i]
            up = word.upper()
            if i < n and src[i] == "#" and up in ("T", "TIME"):
                i, ms = _lex_time(src, i + 1, line, scol)
                toks.append(Token("TIME", ms, src[start:i], line, scol))
                continue
            toks.append(Token("KW" if up in KEYWORDS else "IDENT", up, word, line, scol))
            continue
        if c.isdigit():
            i, tok = _lex_number(src, i, line, scol)
            toks.append(tok)
            continue
        if c == "%":
            i += 1
            while i < n and (src[i].isalnum() or src[i] == "."):
                i += 1
            text = src[start:i]
            if len(text) < 3:
                raise CompileError(f"bad direct address '{text}'", line, scol)
            toks.append(Token("ADDR", text.upper(), text, line, scol))
            continue
        for op in OPERATORS:
            if src.startswith(op, i):
                toks.append(Token("OP", op, op, line, scol))
                i += len(op)
                break
        else:
            if c in "'\"":
                raise CompileError("string literals are not in the supported subset", line, scol)
            raise CompileError(f"unexpected character {c!r}", line, scol)
    toks.append(Token("EOF", None, "", line, col_of(i)))
    return toks


def _digits(src, i, allowed):
    """Read digits (with '_' separators) from allowed. Return (end, digit string)."""
    n, out = len(src), []
    while i < n and (src[i] in allowed or src[i] == "_"):
        if src[i] != "_":
            out.append(src[i])
        i += 1
    return i, "".join(out)


def _lex_number(src, i, line, col):
    n = len(src)
    start = i
    i, whole = _digits(src, i, "0123456789")
    if i < n and src[i] == "#":
        base = int(whole)
        if base not in (2, 8, 16):
            raise CompileError(f"number base {base} is not supported, use 2, 8 or 16", line, col)
        allowed = {2: "01", 8: "01234567", 16: "0123456789ABCDEFabcdef"}[base]
        i, digits = _digits(src, i + 1, allowed)
        if not digits or (i < n and (src[i].isalnum())):
            raise CompileError(f"bad base {base} literal '{src[start:i + 1]}'", line, col)
        return i, Token("INT", int(digits, base), src[start:i], line, col)
    # A real needs a digit after the dot. '1..5' is a range, not a real.
    if i + 1 < n and src[i] == "." and src[i + 1].isdigit():
        i, frac = _digits(src, i + 1, "0123456789")
        text = f"{whole}.{frac}"
        if i < n and src[i] in "eE":
            j = i + 1
            if j < n and src[j] in "+-":
                j += 1
            j2, exp = _digits(src, j, "0123456789")
            if not exp:
                raise CompileError("real literal has an empty exponent", line, col)
            text += src[i:j].replace("_", "") + exp
            i = j2
        return i, Token("REAL", float(text), src[start:i], line, col)
    if i < n and (src[i].isalpha() or src[i] == "_"):
        raise CompileError(f"bad number '{src[start:i + 1]}'", line, col)
    return i, Token("INT", int(whole), src[start:i], line, col)


def _lex_time(src, i, line, col):
    """Parse the part after T# or TIME#. Return (end, milliseconds)."""
    n = len(src)
    total = 0.0
    parts = 0
    while i < n:
        if src[i] == "_":
            i += 1
            continue
        if not src[i].isdigit():
            break
        i, whole = _digits(src, i, "0123456789")
        value = float(whole)
        if i + 1 < n and src[i] == "." and src[i + 1].isdigit():
            i, frac = _digits(src, i + 1, "0123456789")
            value = float(f"{whole}.{frac}")
        rest = src[i:i + 2].upper()
        for unit, scale in _TIME_UNITS:
            if rest.startswith(unit):
                # 'M' followed by 'S' is milliseconds and matched first above.
                total += value * scale
                i += len(unit)
                parts += 1
                break
        else:
            raise CompileError("time literal needs a unit: d, h, m, s or ms", line, col)
    if parts == 0:
        raise CompileError("empty time literal, write for example T#250ms", line, col)
    if i < n and (src[i].isalnum()):
        raise CompileError("bad time literal", line, col)
    return i, int(round(total))
