"""Parser for the Structured Text subset. It turns tokens into the AST classes below.

The parser checks syntax only. Names, types, and addresses are checked in compiler.py.

Operator precedence follows IEC 61131-3 table 55, highest first:
  ( ) and function calls, **, unary - and NOT, * / MOD, + -, < > <= >=, = <>, AND &, XOR, OR.
"""
from .errors import CompileError
from .lexer import tokenize


# ------------------------------------------------------------------------- AST --
class Node:
    __slots__ = ("line", "col")


class Literal(Node):
    __slots__ = ("value", "type")

    def __init__(self, value, type_, line, col):
        self.value, self.type, self.line, self.col = value, type_, line, col


class Name(Node):
    __slots__ = ("name", "text")

    def __init__(self, name, text, line, col):
        self.name, self.text, self.line, self.col = name, text, line, col


class Member(Node):
    __slots__ = ("base", "field", "text")

    def __init__(self, base, field, text, line, col):
        self.base, self.field, self.text, self.line, self.col = base, field, text, line, col


class Unary(Node):
    __slots__ = ("op", "operand")

    def __init__(self, op, operand, line, col):
        self.op, self.operand, self.line, self.col = op, operand, line, col


class Binary(Node):
    __slots__ = ("op", "left", "right")

    def __init__(self, op, left, right, line, col):
        self.op, self.left, self.right, self.line, self.col = op, left, right, line, col


class Call(Node):
    __slots__ = ("func", "args")

    def __init__(self, func, args, line, col):
        self.func, self.args, self.line, self.col = func, args, line, col


class Assign(Node):
    __slots__ = ("target", "expr")

    def __init__(self, target, expr, line, col):
        self.target, self.expr, self.line, self.col = target, expr, line, col


class If(Node):
    __slots__ = ("branches", "else_body")

    def __init__(self, branches, else_body, line, col):
        self.branches, self.else_body, self.line, self.col = branches, else_body, line, col


class CaseLabel(Node):
    """One label: lo..hi. Each bound is a Literal or a Name of a constant."""
    __slots__ = ("lo", "hi")

    def __init__(self, lo, hi, line, col):
        self.lo, self.hi, self.line, self.col = lo, hi, line, col


class Case(Node):
    __slots__ = ("selector", "arms", "else_body")

    def __init__(self, selector, arms, else_body, line, col):
        self.selector, self.arms, self.else_body, self.line, self.col = selector, arms, else_body, line, col


class For(Node):
    __slots__ = ("var", "start", "end", "step", "body")

    def __init__(self, var, start, end, step, body, line, col):
        self.var, self.start, self.end, self.step, self.body = var, start, end, step, body
        self.line, self.col = line, col


class FBCall(Node):
    """inst(IN := x, Q => y). args = [(param, param_text, expr_or_target, is_output, line, col)]."""
    __slots__ = ("inst", "args")

    def __init__(self, inst, args, line, col):
        self.inst, self.args, self.line, self.col = inst, args, line, col


class Return(Node):
    __slots__ = ()

    def __init__(self, line, col):
        self.line, self.col = line, col


class Exit(Node):
    __slots__ = ()

    def __init__(self, line, col):
        self.line, self.col = line, col


class VarDecl(Node):
    __slots__ = ("name", "text", "type", "type_line", "type_col", "address", "addr_line",
                 "addr_col", "init", "constant")

    def __init__(self, name, text, line, col):
        self.name, self.text, self.line, self.col = name, text, line, col
        self.type = self.type_line = self.type_col = None
        self.address = self.addr_line = self.addr_col = None
        self.init = None
        self.constant = False


class Program(Node):
    __slots__ = ("name", "decls", "body")

    def __init__(self, name, decls, body, line, col):
        self.name, self.decls, self.body, self.line, self.col = name, decls, body, line, col


class TaskDecl(Node):
    __slots__ = ("name", "params")

    def __init__(self, name, params, line, col):
        self.name, self.params, self.line, self.col = name, params, line, col


class ProgramBinding(Node):
    __slots__ = ("instance", "task", "program")

    def __init__(self, instance, task, program, line, col):
        self.instance, self.task, self.program, self.line, self.col = instance, task, program, line, col


class Configuration(Node):
    __slots__ = ("name", "tasks", "bindings")

    def __init__(self, name, tasks, bindings, line, col):
        self.name, self.tasks, self.bindings, self.line, self.col = name, tasks, bindings, line, col


class SourceFile(Node):
    __slots__ = ("program", "config")

    def __init__(self, program, config):
        self.program, self.config = program, config
        self.line, self.col = 1, 1


_UNSUPPORTED = {
    "WHILE": "WHILE loops are not in the supported subset, use FOR",
    "REPEAT": "REPEAT loops are not in the supported subset, use FOR",
    "FUNCTION": "user FUNCTION blocks are not in the supported subset",
    "FUNCTION_BLOCK": "user FUNCTION_BLOCK types are not in the supported subset",
    "VAR_INPUT": "only VAR ... END_VAR blocks are supported in a PROGRAM",
    "VAR_OUTPUT": "only VAR ... END_VAR blocks are supported in a PROGRAM",
    "VAR_IN_OUT": "only VAR ... END_VAR blocks are supported in a PROGRAM",
    "VAR_GLOBAL": "VAR_GLOBAL is not in the supported subset, declare the variable in the PROGRAM",
    "VAR_EXTERNAL": "VAR_EXTERNAL is not in the supported subset",
    "VAR_TEMP": "VAR_TEMP is not in the supported subset",
    "TYPE": "user TYPE declarations are not in the supported subset",
    "STRUCT": "STRUCT is not in the supported subset",
}


# ---------------------------------------------------------------------- parser --
class Parser:
    def __init__(self, src):
        self.toks = tokenize(src)
        self.pos = 0

    # --- token helpers
    @property
    def tok(self):
        return self.toks[self.pos]

    def peek(self, k=1):
        return self.toks[min(self.pos + k, len(self.toks) - 1)]

    def advance(self):
        t = self.toks[self.pos]
        if t.kind != "EOF":
            self.pos += 1
        return t

    def error(self, message, tok=None):
        tok = tok or self.tok
        return CompileError(message, tok.line, tok.col)

    @staticmethod
    def describe(tok):
        if tok.kind == "EOF":
            return "the end of the source"
        return f"'{tok.text}'"

    def expect_op(self, op):
        if not self.tok.is_op(op):
            raise self.error(f"expected '{op}' but found {self.describe(self.tok)}")
        return self.advance()

    def expect_kw(self, *names):
        if not self.tok.is_kw(*names):
            want = " or ".join(names)
            raise self.error(f"expected {want} but found {self.describe(self.tok)}")
        return self.advance()

    def expect_ident(self, what="a name"):
        if self.tok.kind != "IDENT":
            if self.tok.kind == "KW":
                raise self.error(f"expected {what} but found the keyword {self.tok.text}")
            raise self.error(f"expected {what} but found {self.describe(self.tok)}")
        return self.advance()

    def check_unsupported(self):
        t = self.tok
        if t.kind == "KW" and t.value in _UNSUPPORTED:
            raise self.error(_UNSUPPORTED[t.value])

    # --- file level
    def parse_file(self):
        program = config = None
        while self.tok.kind != "EOF":
            self.check_unsupported()
            if self.tok.is_kw("PROGRAM"):
                if program is not None:
                    raise self.error("a task has exactly one PROGRAM")
                program = self.parse_program()
            elif self.tok.is_kw("CONFIGURATION"):
                if config is not None:
                    raise self.error("a task has at most one CONFIGURATION")
                config = self.parse_configuration()
            else:
                raise self.error(f"expected PROGRAM or CONFIGURATION but found {self.describe(self.tok)}")
        if program is None:
            raise self.error("the source has no PROGRAM ... END_PROGRAM block")
        return SourceFile(program, config)

    def parse_program(self):
        start = self.expect_kw("PROGRAM")
        name = self.expect_ident("a program name")
        decls = []
        while True:
            self.check_unsupported()
            if not self.tok.is_kw("VAR"):
                break
            decls.extend(self.parse_var_block())
        body = self.parse_statements(("END_PROGRAM",))
        self.expect_kw("END_PROGRAM")
        return Program(name.value, decls, body, start.line, start.col)

    def parse_var_block(self):
        self.expect_kw("VAR")
        constant = False
        while self.tok.is_kw("CONSTANT", "RETAIN"):
            if self.advance().value == "CONSTANT":
                constant = True
        decls = []
        while not self.tok.is_kw("END_VAR"):
            if self.tok.kind == "EOF":
                raise self.error("VAR block is not closed, expected END_VAR")
            decls.extend(self.parse_decl(constant))
        self.expect_kw("END_VAR")
        return decls

    def parse_decl(self, constant):
        names = [self.expect_ident("a variable name")]
        while self.tok.is_op(","):
            self.advance()
            names.append(self.expect_ident("a variable name"))
        decls = [VarDecl(t.value, t.text, t.line, t.col) for t in names]
        address = None
        if self.tok.is_kw("AT"):
            if len(names) > 1:
                raise self.error("AT needs exactly one variable name")
            self.advance()
            if self.tok.kind != "ADDR":
                raise self.error(f"expected a direct address like %MW10 but found {self.describe(self.tok)}")
            address = self.advance()
        self.expect_op(":")
        type_tok = self.expect_ident("a type name")
        init = None
        if self.tok.is_op(":="):
            self.advance()
            init = self.parse_expr()
        self.expect_op(";")
        for d in decls:
            d.type, d.type_line, d.type_col = type_tok.value, type_tok.line, type_tok.col
            d.constant = constant
            d.init = init
            if address is not None:
                d.address, d.addr_line, d.addr_col = address.value, address.line, address.col
        return decls

    def parse_configuration(self):
        start = self.expect_kw("CONFIGURATION")
        name = self.expect_ident("a configuration name")
        tasks, bindings = [], []
        while not self.tok.is_kw("END_CONFIGURATION"):
            self.check_unsupported()
            if self.tok.is_kw("RESOURCE"):
                self.advance()
                self.expect_ident("a resource name")
                self.expect_kw("ON")
                self.expect_ident("a processor name")
                while not self.tok.is_kw("END_RESOURCE"):
                    self.check_unsupported()
                    self.parse_config_item(tasks, bindings)
                self.expect_kw("END_RESOURCE")
                if self.tok.is_op(";"):
                    self.advance()
            else:
                self.parse_config_item(tasks, bindings)
        self.expect_kw("END_CONFIGURATION")
        return Configuration(name.value, tasks, bindings, start.line, start.col)

    def parse_config_item(self, tasks, bindings):
        if self.tok.is_kw("TASK"):
            start = self.advance()
            name = self.expect_ident("a task name")
            params = {}
            if self.tok.is_op("("):
                self.advance()
                while not self.tok.is_op(")"):
                    key = self.expect_ident("a task parameter such as INTERVAL")
                    self.expect_op(":=")
                    params[key.value] = self.parse_expr()
                    if self.tok.is_op(","):
                        self.advance()
                    elif not self.tok.is_op(")"):
                        raise self.error(f"expected ',' or ')' but found {self.describe(self.tok)}")
                self.expect_op(")")
            self.expect_op(";")
            tasks.append(TaskDecl(name.value, params, start.line, start.col))
        elif self.tok.is_kw("PROGRAM"):
            start = self.advance()
            inst = self.expect_ident("a program instance name")
            task = None
            if self.tok.is_kw("WITH"):
                self.advance()
                task = self.expect_ident("a task name").value
            self.expect_op(":")
            prog = self.expect_ident("a program type name")
            if self.tok.is_op("("):
                depth = 0
                while True:
                    t = self.advance()
                    if t.is_op("("):
                        depth += 1
                    elif t.is_op(")"):
                        depth -= 1
                        if depth == 0:
                            break
                    elif t.kind == "EOF":
                        raise self.error("program binding is not closed, expected ')'")
            self.expect_op(";")
            bindings.append(ProgramBinding(inst.value, task, prog.value, start.line, start.col))
        elif self.tok.kind == "EOF":
            raise self.error("CONFIGURATION is not closed, expected END_CONFIGURATION")
        else:
            raise self.error(f"expected TASK or PROGRAM in the configuration but found {self.describe(self.tok)}")

    # --- statements
    def parse_statements(self, terminators):
        """Parse statements until a keyword in terminators. The terminator is not consumed."""
        body = []
        while not self.tok.is_kw(*terminators):
            if self.tok.kind == "EOF":
                want = " or ".join(terminators)
                raise self.error(f"expected {want} before the end of the source")
            stmt = self.parse_statement()
            if stmt is not None:
                body.append(stmt)
        return body

    def parse_statement(self):
        t = self.tok
        self.check_unsupported()
        if t.is_op(";"):
            self.advance()
            return None
        if t.is_kw("IF"):
            return self.parse_if()
        if t.is_kw("CASE"):
            return self.parse_case()
        if t.is_kw("FOR"):
            return self.parse_for()
        if t.is_kw("RETURN"):
            self.advance()
            self.expect_op(";")
            return Return(t.line, t.col)
        if t.is_kw("EXIT"):
            self.advance()
            self.expect_op(";")
            return Exit(t.line, t.col)
        if t.kind == "IDENT":
            return self.parse_ident_statement()
        if t.kind == "ADDR":
            raise self.error("direct addresses cannot be used in statements, declare a variable with AT")
        raise self.error(f"expected a statement but found {self.describe(t)}")

    def _optional_semicolon(self):
        if self.tok.is_op(";"):
            self.advance()

    def parse_ident_statement(self):
        first = self.advance()
        if self.tok.is_op(":="):
            op = self.advance()
            expr = self.parse_expr()
            self.expect_op(";")
            return Assign(Name(first.value, first.text, first.line, first.col), expr, op.line, op.col)
        if self.tok.is_op("."):
            self.advance()
            field = self.expect_ident("a member name")
            target = Member(first.value, field.value, f"{first.text}.{field.text}", first.line, first.col)
            op = self.expect_op(":=")
            expr = self.parse_expr()
            self.expect_op(";")
            return Assign(target, expr, op.line, op.col)
        if self.tok.is_op("("):
            self.advance()
            args = []
            while not self.tok.is_op(")"):
                if self.tok.kind != "IDENT" or not self.peek().is_op(":=", "=>"):
                    raise self.error("function block calls need named parameters, for example t1(IN := x, PT := T#5s)")
                p = self.advance()
                arrow = self.advance()
                if arrow.value == ":=":
                    args.append((p.value, p.text, self.parse_expr(), False, p.line, p.col))
                else:
                    tgt = self.expect_ident("a variable to receive the output")
                    args.append((p.value, p.text, Name(tgt.value, tgt.text, tgt.line, tgt.col), True, p.line, p.col))
                if self.tok.is_op(","):
                    self.advance()
                elif not self.tok.is_op(")"):
                    raise self.error(f"expected ',' or ')' but found {self.describe(self.tok)}")
            self.expect_op(")")
            self.expect_op(";")
            return FBCall(first.value, args, first.line, first.col)
        raise self.error(f"expected ':=' or a call after '{first.text}' but found {self.describe(self.tok)}")

    def parse_if(self):
        start = self.expect_kw("IF")
        branches = []
        cond = self.parse_expr()
        self.expect_kw("THEN")
        body = self.parse_statements(("ELSIF", "ELSE", "END_IF"))
        branches.append((cond, body))
        else_body = []
        while self.tok.is_kw("ELSIF"):
            self.advance()
            cond = self.parse_expr()
            self.expect_kw("THEN")
            branches.append((cond, self.parse_statements(("ELSIF", "ELSE", "END_IF"))))
        if self.tok.is_kw("ELSE"):
            self.advance()
            else_body = self.parse_statements(("END_IF",))
        self.expect_kw("END_IF")
        self._optional_semicolon()
        return If(branches, else_body, start.line, start.col)

    def _at_case_label(self):
        t = self.tok
        if t.kind == "INT" or t.is_op("-"):
            return True
        return t.kind == "IDENT" and self.peek().is_op(":", ",", "..")

    def parse_case_bound(self):
        t = self.tok
        if t.is_op("-"):
            self.advance()
            n = self.tok
            if n.kind != "INT":
                raise self.error("expected an integer after '-' in a CASE label")
            self.advance()
            return Literal(-n.value, "ANYINT", t.line, t.col)
        if t.kind == "INT":
            self.advance()
            return Literal(t.value, "ANYINT", t.line, t.col)
        if t.kind == "IDENT":
            self.advance()
            return Name(t.value, t.text, t.line, t.col)
        raise self.error(f"expected a CASE label but found {self.describe(t)}")

    def parse_case(self):
        start = self.expect_kw("CASE")
        selector = self.parse_expr()
        self.expect_kw("OF")
        arms, else_body = [], []
        while not self.tok.is_kw("ELSE", "END_CASE"):
            if not self._at_case_label():
                raise self.error(f"expected a CASE label such as 1: but found {self.describe(self.tok)}")
            labels = []
            while True:
                lt = self.tok
                lo = self.parse_case_bound()
                hi = lo
                if self.tok.is_op(".."):
                    self.advance()
                    hi = self.parse_case_bound()
                labels.append(CaseLabel(lo, hi, lt.line, lt.col))
                if self.tok.is_op(","):
                    self.advance()
                    continue
                break
            self.expect_op(":")
            body = []
            while not (self.tok.is_kw("ELSE", "END_CASE") or self._at_case_label()):
                if self.tok.kind == "EOF":
                    raise self.error("CASE is not closed, expected END_CASE")
                stmt = self.parse_statement()
                if stmt is not None:
                    body.append(stmt)
            arms.append((labels, body))
        if self.tok.is_kw("ELSE"):
            self.advance()
            else_body = self.parse_statements(("END_CASE",))
        self.expect_kw("END_CASE")
        self._optional_semicolon()
        return Case(selector, arms, else_body, start.line, start.col)

    def parse_for(self):
        start = self.expect_kw("FOR")
        v = self.expect_ident("a loop variable")
        self.expect_op(":=")
        first = self.parse_expr()
        self.expect_kw("TO")
        last = self.parse_expr()
        step = None
        if self.tok.is_kw("BY"):
            self.advance()
            step = self.parse_expr()
        self.expect_kw("DO")
        body = self.parse_statements(("END_FOR",))
        self.expect_kw("END_FOR")
        self._optional_semicolon()
        return For(Name(v.value, v.text, v.line, v.col), first, last, step, body, start.line, start.col)

    # --- expressions
    def parse_expr(self):
        left = self.parse_xor()
        while self.tok.is_kw("OR"):
            op = self.advance()
            left = Binary("OR", left, self.parse_xor(), op.line, op.col)
        return left

    def parse_xor(self):
        left = self.parse_and()
        while self.tok.is_kw("XOR"):
            op = self.advance()
            left = Binary("XOR", left, self.parse_and(), op.line, op.col)
        return left

    def parse_and(self):
        left = self.parse_eq()
        while self.tok.is_kw("AND") or self.tok.is_op("&"):
            op = self.advance()
            left = Binary("AND", left, self.parse_eq(), op.line, op.col)
        return left

    def parse_eq(self):
        left = self.parse_cmp()
        while self.tok.is_op("=", "<>"):
            op = self.advance()
            left = Binary(op.value, left, self.parse_cmp(), op.line, op.col)
        return left

    def parse_cmp(self):
        left = self.parse_add()
        while self.tok.is_op("<", ">", "<=", ">="):
            op = self.advance()
            left = Binary(op.value, left, self.parse_add(), op.line, op.col)
        return left

    def parse_add(self):
        left = self.parse_mul()
        while self.tok.is_op("+", "-"):
            op = self.advance()
            left = Binary(op.value, left, self.parse_mul(), op.line, op.col)
        return left

    def parse_mul(self):
        left = self.parse_unary()
        while self.tok.is_op("*", "/") or self.tok.is_kw("MOD"):
            op = self.advance()
            left = Binary(op.value, left, self.parse_unary(), op.line, op.col)
        return left

    def parse_unary(self):
        t = self.tok
        if t.is_op("-"):
            self.advance()
            operand = self.parse_unary()
            if isinstance(operand, Literal) and operand.type in ("ANYINT", "REAL", "TIME"):
                return Literal(-operand.value, operand.type, t.line, t.col)
            return Unary("-", operand, t.line, t.col)
        if t.is_op("+"):
            self.advance()
            return self.parse_unary()
        if t.is_kw("NOT"):
            self.advance()
            return Unary("NOT", self.parse_unary(), t.line, t.col)
        return self.parse_power()

    def parse_power(self):
        left = self.parse_primary()
        while self.tok.is_op("**"):
            op = self.advance()
            if self.tok.is_op("-"):
                right = self.parse_unary()
            else:
                right = self.parse_primary()
            left = Binary("**", left, right, op.line, op.col)
        return left

    def parse_primary(self):
        t = self.tok
        if t.kind == "INT":
            self.advance()
            return Literal(t.value, "ANYINT", t.line, t.col)
        if t.kind == "REAL":
            self.advance()
            return Literal(t.value, "REAL", t.line, t.col)
        if t.kind == "TIME":
            self.advance()
            return Literal(t.value, "TIME", t.line, t.col)
        if t.is_kw("TRUE", "FALSE"):
            self.advance()
            return Literal(t.value == "TRUE", "BOOL", t.line, t.col)
        if t.is_op("("):
            self.advance()
            inner = self.parse_expr()
            self.expect_op(")")
            return inner
        if t.kind == "IDENT":
            self.advance()
            if self.tok.is_op("("):
                self.advance()
                args = []
                while not self.tok.is_op(")"):
                    if self.tok.kind == "IDENT" and self.peek().is_op(":="):
                        raise self.error("function calls take positional arguments, for example MIN(a, b)")
                    args.append(self.parse_expr())
                    if self.tok.is_op(","):
                        self.advance()
                    elif not self.tok.is_op(")"):
                        raise self.error(f"expected ',' or ')' but found {self.describe(self.tok)}")
                self.expect_op(")")
                return Call(t.value, args, t.line, t.col)
            if self.tok.is_op("."):
                self.advance()
                field = self.expect_ident("a member name")
                return Member(t.value, field.value, f"{t.text}.{field.text}", t.line, t.col)
            return Name(t.value, t.text, t.line, t.col)
        if t.kind == "ADDR":
            raise self.error("direct addresses cannot be used in expressions, declare a variable with AT")
        if t.kind == "KW":
            raise self.error(f"expected an expression but found the keyword {t.text}")
        raise self.error(f"expected an expression but found {self.describe(t)}")


def parse(src):
    """Parse a whole task source. Return a SourceFile. Raise CompileError on a syntax error."""
    return Parser(src).parse_file()
