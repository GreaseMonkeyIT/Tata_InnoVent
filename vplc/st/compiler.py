"""Compiler for the Structured Text subset: name and type checks, then Python closures.

compile_task(source) returns a CompiledTask. A CompiledTask is immutable. instantiate(image)
makes a ProgramInstance with fresh variables and function blocks bound to one image. The runtime
calls instance.scan(now_ms) once per scan. The scan is deterministic: the only time source is
the now_ms argument.

Type rules (strict where a mistake is easy, lenient where the subset has no conversion function):
  - INT is 16-bit signed and DINT is 32-bit signed. Both wrap on every operation and assignment.
  - An integer literal adapts to the other operand. A literal that does not fit its target fails.
  - Integer types convert to each other and to REAL on assignment. REAL to INT needs REAL_TO_INT.
  - BOOL and integers do not mix. Use BOOL_TO_INT and INT_TO_BOOL.
  - TIME combines only with TIME, except TIME * int and TIME / int. TIME_TO_INT gives INT.
  - IF conditions must be BOOL. AND, OR, XOR, NOT work on BOOL, or bitwise on integers.
  - Integer / truncates toward zero. / or MOD by zero is a runtime error and faults the task.
  - ** always gives REAL.

Located variables read and write the image directly: %IX and %QX hold BOOL, the word areas hold
INT. Inputs (%IX, %IW) are read-only for the task. %MW0..%MW4 are reserved for the runtime.
"""
import hashlib
import math
import zlib
from operator import attrgetter

import image as image_mod

from .errors import CompileError, STRuntimeError
from .fbs import FB_TYPES
from .parser import (Assign, Binary, Call, Case, Exit, FBCall, For, If, Literal, Member, Name,
                     Return, Unary, parse)

ELEMENTARY = ("BOOL", "INT", "DINT", "REAL", "TIME")
DEFAULTS = {"BOOL": False, "INT": 0, "DINT": 0, "REAL": 0.0, "TIME": 0}
INT_TYPES = ("ANYINT", "INT", "DINT")
NUMERIC = ("ANYINT", "INT", "DINT", "REAL")
_RANK = {"ANYINT": 0, "INT": 1, "DINT": 2, "REAL": 3}
FUNCTIONS = ("MIN", "MAX", "LIMIT", "ABS", "SQRT", "INT_TO_REAL", "REAL_TO_INT", "BOOL_TO_INT",
             "INT_TO_BOOL", "TIME_TO_INT")

DEFAULT_INTERVAL_MS = 100
LOOP_BUDGET = 100_000          # FOR iterations allowed per scan before the task faults

RETURN, EXIT = 1, 2


def wrap16(v):
    return ((int(v) + 0x8000) & 0xFFFF) - 0x8000


def wrap32(v):
    return ((int(v) + 0x80000000) & 0xFFFFFFFF) - 0x80000000


def _identity(v):
    return v


_WRAP = {"INT": wrap16, "DINT": wrap32, "TIME": wrap32}
_RANGES = {"INT": (-32768, 32767), "DINT": (-(2 ** 31), 2 ** 31 - 1)}


def _wrapper(t):
    return _WRAP.get(t, _identity)


def _round_half_away(v):
    return int(math.floor(abs(v) + 0.5)) * (1 if v >= 0 else -1)


class Symbol:
    """A declared name. kind is 'local', 'located', or 'fb'."""
    __slots__ = ("name", "text", "kind", "type", "slot", "address", "constant", "init", "decl")

    def __init__(self, name, text, kind, type_, decl):
        self.name, self.text, self.kind, self.type, self.decl = name, text, kind, type_, decl
        self.slot = None
        self.address = None
        self.constant = False
        self.init = None


class ProgramInstance:
    """The live state of one loaded task: variable values, FB instances, and the bound image."""
    __slots__ = ("task", "vals", "fbs", "img", "now", "budget")

    def __init__(self, task, img):
        self.task = task
        self.img = img
        self.vals = list(task.local_inits)
        self.fbs = [cls() for cls in task.fb_classes]
        self.now = 0
        self.budget = LOOP_BUDGET

    def scan(self, now_ms):
        """Run the program body once. now_ms is the scan time stamp in milliseconds."""
        self.now = int(now_ms)
        self.budget = LOOP_BUDGET
        self.task.body(self)

    def get(self, name):
        """Read a variable by name (for tests and diagnostics)."""
        sym = self.task.symbols[name.upper()]
        if sym.kind == "local":
            return self.vals[sym.slot]
        if sym.kind == "located":
            return self.img.area(sym.address.area)[sym.address.index]
        return self.fbs[sym.slot]


class CompiledTask:
    """The result of a successful compile."""

    def __init__(self, source, program_name, interval_ms, symbols, local_inits, fb_classes, body,
                 config_task):
        self.source = source
        self.program_name = program_name
        self.interval_ms = interval_ms
        self.symbols = symbols
        self.local_inits = local_inits
        self.fb_classes = fb_classes
        self.body = body
        self.config_task = config_task
        data = source.encode("utf-8")
        self.sha256 = hashlib.sha256(data).hexdigest()
        self.crc15 = zlib.crc32(data) & 0x7FFF

    @property
    def located(self):
        return [s for s in self.symbols.values() if s.kind == "located"]

    def instantiate(self, img):
        return ProgramInstance(self, img)

    def apply_initial_values(self, img):
        """Write the declared initial values of located outputs and memory words into img.
        Inputs belong to the field port and are not touched."""
        for s in self.located:
            if s.address.is_input:
                continue
            area = img.area(s.address.area)
            area[s.address.index] = s.init

    def addresses(self):
        return {s.address.text: s.text for s in self.located}


class _Compiler:
    def __init__(self, source, reserve_system_words):
        self.source = source
        self.reserve = reserve_system_words
        self.symbols = {}
        self.local_inits = []
        self.fb_classes = []
        self.loop_depth = 0

    # ------------------------------------------------------------ declarations --
    def declare(self, decls):
        used_addresses = {}
        for d in decls:
            if d.name in self.symbols:
                raise CompileError(f"'{d.text}' is declared twice", d.line, d.col)
            if d.name in FUNCTIONS or d.name in FB_TYPES or d.name in ELEMENTARY:
                raise CompileError(f"'{d.text}' is a reserved name", d.line, d.col)
            t = d.type
            if t in FB_TYPES:
                if d.address is not None:
                    raise CompileError(f"function block '{d.text}' cannot have an address", d.addr_line, d.addr_col)
                if d.init is not None:
                    raise CompileError(f"function block '{d.text}' cannot have an initial value", d.line, d.col)
                if d.constant:
                    raise CompileError(f"function block '{d.text}' cannot be CONSTANT", d.line, d.col)
                sym = Symbol(d.name, d.text, "fb", t, d)
                sym.slot = len(self.fb_classes)
                self.fb_classes.append(FB_TYPES[t])
                self.symbols[d.name] = sym
                continue
            if t not in ELEMENTARY:
                raise CompileError(f"unknown type '{t}', use BOOL, INT, DINT, REAL, TIME or a standard function block",
                                   d.type_line, d.type_col)
            init = DEFAULTS[t]
            if d.init is not None:
                init = self.constant_value(d.init, t, d.text)
            elif d.constant:
                raise CompileError(f"CONSTANT '{d.text}' needs an initial value", d.line, d.col)
            if d.address is None:
                sym = Symbol(d.name, d.text, "local", t, d)
                sym.slot = len(self.local_inits)
                sym.constant = d.constant
                sym.init = init
                self.local_inits.append(init)
                self.symbols[d.name] = sym
                continue
            try:
                addr = image_mod.parse_address(d.address)
            except ValueError as e:
                raise CompileError(str(e), d.addr_line, d.addr_col) from None
            if self.reserve and addr.area == "MW" and addr.index < image_mod.SYSTEM_WORDS:
                raise CompileError(f"{addr.text} is a system word owned by the runtime, tasks may use %MW5..%MW63",
                                   d.addr_line, d.addr_col)
            if addr.is_bit and t != "BOOL":
                raise CompileError(f"{addr.text} is a bit, declare '{d.text}' as BOOL", d.type_line, d.type_col)
            if not addr.is_bit and t != "INT":
                raise CompileError(f"{addr.text} is a 16-bit word, declare '{d.text}' as INT", d.type_line, d.type_col)
            if addr in used_addresses:
                raise CompileError(f"{addr.text} is already used by '{used_addresses[addr]}'", d.addr_line, d.addr_col)
            if addr.is_input and d.init is not None:
                raise CompileError(f"{addr.text} is an input written by the field port, it cannot have an initial value",
                                   d.line, d.col)
            if d.constant:
                raise CompileError(f"located variable '{d.text}' cannot be CONSTANT", d.line, d.col)
            used_addresses[addr] = d.text
            sym = Symbol(d.name, d.text, "located", t, d)
            sym.address = addr
            sym.init = init
            self.symbols[d.name] = sym

    def constant_value(self, node, t, what):
        if not isinstance(node, Literal):
            raise CompileError(f"the initial value of '{what}' must be a literal", node.line, node.col)
        conv = self.assign_conv(t, node.type, node, what)
        return conv(node.value)

    # ------------------------------------------------------------------- types --
    def assign_conv(self, target, source, node, what):
        """Return the conversion for storing a value of type source into type target."""
        if isinstance(node, Literal) and node.type == "ANYINT" and target in _RANGES:
            lo, hi = _RANGES[target]
            if not lo <= node.value <= hi:
                raise CompileError(f"the constant {node.value} does not fit {target} ({lo}..{hi})", node.line, node.col)
        if target in ("INT", "DINT") and source in INT_TYPES:
            return _wrapper(target)
        if target == "REAL" and source in NUMERIC:
            return float
        if target == source and target in ("BOOL", "TIME"):
            return _identity
        hint = ""
        if target in INT_TYPES and source == "REAL":
            hint = ", use REAL_TO_INT"
        elif target in INT_TYPES and source == "BOOL":
            hint = ", use BOOL_TO_INT"
        elif target == "BOOL" and source in INT_TYPES:
            hint = ", use INT_TO_BOOL"
        elif target in INT_TYPES and source == "TIME":
            hint = ", use TIME_TO_INT"
        elif target == "TIME" and source in NUMERIC:
            hint = ", write a duration such as T#500ms"
        shown = "integer literal" if source == "ANYINT" else source
        raise CompileError(f"cannot assign {shown} to {what} of type {target}{hint}", node.line, node.col)

    @staticmethod
    def unify_numeric(a, b):
        return a if _RANK[a] >= _RANK[b] else b

    # -------------------------------------------------------------- statements --
    def block(self, stmts):
        fns = [self.statement(s) for s in stmts]
        if not fns:
            return lambda x: 0
        if len(fns) == 1:
            return fns[0]

        def run_block(x):
            for f in fns:
                r = f(x)
                if r:
                    return r
            return 0
        return run_block

    def statement(self, s):
        if isinstance(s, Assign):
            return self.assign(s)
        if isinstance(s, If):
            return self.if_stmt(s)
        if isinstance(s, Case):
            return self.case_stmt(s)
        if isinstance(s, For):
            return self.for_stmt(s)
        if isinstance(s, FBCall):
            return self.fb_call(s)
        if isinstance(s, Return):
            return lambda x: RETURN
        if isinstance(s, Exit):
            if self.loop_depth == 0:
                raise CompileError("EXIT is only allowed inside a FOR loop", s.line, s.col)
            return lambda x: EXIT
        raise CompileError("unsupported statement", s.line, s.col)

    def lookup(self, name_node):
        sym = self.symbols.get(name_node.name)
        if sym is None:
            if name_node.name in FUNCTIONS:
                raise CompileError(f"'{name_node.text}' is a function, call it with arguments", name_node.line, name_node.col)
            raise CompileError(f"undeclared variable '{name_node.text}'", name_node.line, name_node.col)
        return sym

    def setter(self, name_node, what="variable"):
        """Return (store(x, value), type) for a writable variable. The value is already converted."""
        sym = self.lookup(name_node)
        if sym.kind == "fb":
            raise CompileError(f"cannot assign to function block '{sym.text}'", name_node.line, name_node.col)
        if sym.constant:
            raise CompileError(f"cannot assign to CONSTANT '{sym.text}'", name_node.line, name_node.col)
        if sym.kind == "local":
            slot = sym.slot

            def store_local(x, v):
                x.vals[slot] = v
            return store_local, sym.type
        addr = sym.address
        if addr.is_input:
            raise CompileError(f"'{sym.text}' is the input {addr.text}, the task cannot write it",
                               name_node.line, name_node.col)
        idx = addr.index
        if addr.area == "QX":
            def store_qx(x, v):
                x.img.qx[idx] = v
            return store_qx, sym.type
        if addr.area == "QW":
            def store_qw(x, v):
                x.img.qw[idx] = v
            return store_qw, sym.type

        def store_mw(x, v):
            x.img.mw[idx] = v
        return store_mw, sym.type

    def assign(self, s):
        ef, et = self.expr(s.expr)
        if isinstance(s.target, Member):
            sym = self.fb_symbol(s.target.base, s.target)
            cls = FB_TYPES[sym.type]
            field = s.target.field
            if field in cls.OUTPUTS:
                raise CompileError(f"'{s.target.text}' is an output of {sym.type}, it is read-only",
                                   s.target.line, s.target.col)
            if field not in cls.INPUTS:
                raise CompileError(f"{sym.type} has no input '{field}'", s.target.line, s.target.col)
            conv = self.assign_conv(cls.INPUTS[field], et, s.expr, s.target.text)
            slot = sym.slot

            def assign_member(x):
                setattr(x.fbs[slot], field, conv(ef(x)))
                return 0
            return assign_member
        store, tt = self.setter(s.target)
        conv = self.assign_conv(tt, et, s.expr, f"'{s.target.text}'")
        if conv is _identity:
            def assign_plain(x):
                store(x, ef(x))
                return 0
            return assign_plain

        def assign_conv(x):
            store(x, conv(ef(x)))
            return 0
        return assign_conv

    def fb_symbol(self, base, node):
        sym = self.symbols.get(base)
        if sym is None:
            raise CompileError(f"undeclared function block instance '{base}'", node.line, node.col)
        if sym.kind != "fb":
            raise CompileError(f"'{sym.text}' is not a function block instance", node.line, node.col)
        return sym

    def condition(self, node, what):
        f, t = self.expr(node)
        if t != "BOOL":
            raise CompileError(f"{what} must be BOOL, found {self.type_name(t)}", node.line, node.col)
        return f

    @staticmethod
    def type_name(t):
        return "an integer literal" if t == "ANYINT" else t

    def if_stmt(self, s):
        branches = [(self.condition(c, "the IF condition"), self.block(b)) for c, b in s.branches]
        else_fn = self.block(s.else_body)

        def run_if(x):
            for cond, body in branches:
                if cond(x):
                    return body(x)
            return else_fn(x)
        return run_if

    def case_value(self, node):
        if isinstance(node, Literal):
            return node.value
        sym = self.symbols.get(node.name)
        if sym is None or sym.kind != "local" or not sym.constant or sym.type not in ("INT", "DINT"):
            raise CompileError(f"CASE label '{node.text}' must be an integer literal or an integer CONSTANT",
                               node.line, node.col)
        return sym.init

    def case_stmt(self, s):
        sel, st = self.expr(s.selector)
        if st not in INT_TYPES:
            raise CompileError(f"the CASE selector must be an integer, found {self.type_name(st)}",
                               s.selector.line, s.selector.col)
        arms = []
        for labels, body in s.arms:
            ranges = []
            for lab in labels:
                lo, hi = self.case_value(lab.lo), self.case_value(lab.hi)
                if lo > hi:
                    raise CompileError(f"CASE range {lo}..{hi} is empty", lab.line, lab.col)
                ranges.append((lo, hi))
            arms.append((tuple(ranges), self.block(body)))
        arms = tuple(arms)
        else_fn = self.block(s.else_body)

        def run_case(x):
            v = sel(x)
            for ranges, body in arms:
                for lo, hi in ranges:
                    if lo <= v <= hi:
                        return body(x)
            return else_fn(x)
        return run_case

    def for_stmt(self, s):
        store, vt = self.setter(s.var)
        if vt not in ("INT", "DINT"):
            raise CompileError(f"the FOR variable '{s.var.text}' must be INT or DINT", s.var.line, s.var.col)
        read, _ = self.expr(s.var)
        wrap = _wrapper(vt)
        start_f, t1 = self.expr(s.start)
        end_f, t2 = self.expr(s.end)
        for node, t in ((s.start, t1), (s.end, t2)):
            if t not in INT_TYPES:
                raise CompileError(f"FOR bounds must be integers, found {self.type_name(t)}", node.line, node.col)
        if s.step is not None:
            step_f, t3 = self.expr(s.step)
            if t3 not in INT_TYPES:
                raise CompileError(f"the FOR step must be an integer, found {self.type_name(t3)}",
                                   s.step.line, s.step.col)
        else:
            def step_f(x):
                return 1
        self.loop_depth += 1
        body = self.block(s.body)
        self.loop_depth -= 1
        line, col = s.line, s.col

        def run_for(x):
            i = start_f(x)
            end = end_f(x)
            step = step_f(x)
            if step == 0:
                raise STRuntimeError("the FOR step is 0", line, col)
            store(x, wrap(i))
            while (i <= end) if step > 0 else (i >= end):
                x.budget -= 1
                if x.budget < 0:
                    raise STRuntimeError(f"more than {LOOP_BUDGET} FOR iterations in one scan", line, col)
                r = body(x)
                if r == RETURN:
                    return RETURN
                if r == EXIT:
                    break
                i = read(x) + step
                store(x, wrap(i))
            return 0
        return run_for

    def fb_call(self, s):
        sym = self.lookup(Name(s.inst, s.inst, s.line, s.col))
        if sym.kind != "fb":
            if s.inst in FUNCTIONS:
                raise CompileError(f"the result of function {s.inst} is not used", s.line, s.col)
            raise CompileError(f"'{sym.text}' is not a function block instance", s.line, s.col)
        cls = FB_TYPES[sym.type]
        slot = sym.slot
        inputs, outputs, seen = [], [], set()
        for param, text, node, is_output, line, col in s.args:
            if param in seen:
                raise CompileError(f"parameter '{text}' is given twice", line, col)
            seen.add(param)
            if is_output:
                if param not in cls.OUTPUTS:
                    raise CompileError(f"{sym.type} has no output '{text}'", line, col)
                store, tt = self.setter(node)
                conv = self.assign_conv(tt, cls.OUTPUTS[param], node, f"'{node.text}'")
                outputs.append((attrgetter(param), store, conv))
            else:
                if param not in cls.INPUTS:
                    if param in cls.OUTPUTS:
                        raise CompileError(f"'{text}' is an output of {sym.type}, use {text} => variable", line, col)
                    raise CompileError(f"{sym.type} has no input '{text}'", line, col)
                ef, et = self.expr(node)
                conv = self.assign_conv(cls.INPUTS[param], et, node, f"input {text}")
                inputs.append((param, ef, conv))
        inputs, outputs = tuple(inputs), tuple(outputs)

        def run_fb(x):
            fb = x.fbs[slot]
            for name, ef, conv in inputs:
                setattr(fb, name, conv(ef(x)))
            fb.execute(x.now)
            for get, store, conv in outputs:
                store(x, conv(get(fb)))
            return 0
        return run_fb

    # ------------------------------------------------------------- expressions --
    def expr(self, n):
        """Return (fn(x) -> value, type)."""
        if isinstance(n, Literal):
            v = n.value
            return (lambda x: v), n.type
        if isinstance(n, Name):
            return self.name_expr(n)
        if isinstance(n, Member):
            sym = self.fb_symbol(n.base, n)
            cls = FB_TYPES[sym.type]
            t = cls.OUTPUTS.get(n.field) or cls.INPUTS.get(n.field)
            if t is None:
                raise CompileError(f"{sym.type} has no member '{n.field}'", n.line, n.col)
            get, slot = attrgetter(n.field), sym.slot
            return (lambda x: get(x.fbs[slot])), t
        if isinstance(n, Unary):
            return self.unary(n)
        if isinstance(n, Binary):
            return self.binary(n)
        if isinstance(n, Call):
            return self.call(n)
        raise CompileError("unsupported expression", n.line, n.col)

    def name_expr(self, n):
        sym = self.lookup(n)
        if sym.kind == "fb":
            raise CompileError(f"'{sym.text}' is a function block, read an output such as {sym.text}.Q",
                               n.line, n.col)
        if sym.kind == "local":
            slot = sym.slot
            return (lambda x: x.vals[slot]), sym.type
        idx, area = sym.address.index, sym.address.area
        if area == "IX":
            return (lambda x: x.img.ix[idx]), sym.type
        if area == "QX":
            return (lambda x: x.img.qx[idx]), sym.type
        if area == "IW":
            return (lambda x: x.img.iw[idx]), sym.type
        if area == "QW":
            return (lambda x: x.img.qw[idx]), sym.type
        return (lambda x: x.img.mw[idx]), sym.type

    def unary(self, n):
        f, t = self.expr(n.operand)
        if n.op == "-":
            if t in NUMERIC or t == "TIME":
                w = _wrapper(t)
                return (lambda x: w(-f(x))), t
            raise CompileError(f"unary '-' needs a number, found {self.type_name(t)}", n.line, n.col)
        if t == "BOOL":
            return (lambda x: not f(x)), "BOOL"
        if t in INT_TYPES:
            w = _wrapper(t)
            return (lambda x: w(~f(x))), t
        raise CompileError(f"NOT needs BOOL or an integer, found {self.type_name(t)}", n.line, n.col)

    def binary(self, n):
        lf, lt = self.expr(n.left)
        rf, rt = self.expr(n.right)
        op, line, col = n.op, n.line, n.col

        def bad():
            return CompileError(f"operator '{op}' cannot combine {self.type_name(lt)} and {self.type_name(rt)}",
                                line, col)

        if op in ("AND", "OR", "XOR"):
            if lt == "BOOL" and rt == "BOOL":
                if op == "AND":
                    return (lambda x: bool(lf(x)) & bool(rf(x))), "BOOL"
                if op == "OR":
                    return (lambda x: bool(lf(x)) | bool(rf(x))), "BOOL"
                return (lambda x: bool(lf(x)) ^ bool(rf(x))), "BOOL"
            if lt in INT_TYPES and rt in INT_TYPES:
                t = self.unify_numeric(lt, rt)
                w = _wrapper(t)
                if op == "AND":
                    return (lambda x: w(lf(x) & rf(x))), t
                if op == "OR":
                    return (lambda x: w(lf(x) | rf(x))), t
                return (lambda x: w(lf(x) ^ rf(x))), t
            raise bad()

        if op in ("=", "<>", "<", ">", "<=", ">="):
            ok = (lt in NUMERIC and rt in NUMERIC) or (lt == rt and lt in ("BOOL", "TIME"))
            if not ok:
                raise bad()
            fn = {
                "=": lambda x: lf(x) == rf(x), "<>": lambda x: lf(x) != rf(x),
                "<": lambda x: lf(x) < rf(x), ">": lambda x: lf(x) > rf(x),
                "<=": lambda x: lf(x) <= rf(x), ">=": lambda x: lf(x) >= rf(x),
            }[op]
            return fn, "BOOL"

        if op in ("+", "-"):
            if lt in NUMERIC and rt in NUMERIC:
                t = self.unify_numeric(lt, rt)
            elif lt == "TIME" and rt == "TIME":
                t = "TIME"
            else:
                raise bad()
            w = _wrapper(t)
            if op == "+":
                return (lambda x: w(lf(x) + rf(x))), t
            return (lambda x: w(lf(x) - rf(x))), t

        if op == "*":
            if lt in NUMERIC and rt in NUMERIC:
                t = self.unify_numeric(lt, rt)
            elif (lt == "TIME" and rt in INT_TYPES) or (lt in INT_TYPES and rt == "TIME"):
                t = "TIME"
            else:
                raise bad()
            w = _wrapper(t)
            return (lambda x: w(lf(x) * rf(x))), t

        if op in ("/", "MOD"):
            if lt in NUMERIC and rt in NUMERIC:
                t = self.unify_numeric(lt, rt)
            elif op == "/" and lt == "TIME" and rt in INT_TYPES:
                t = "TIME"
            else:
                raise bad()
            if op == "MOD" and t == "REAL":
                raise CompileError("MOD needs integers", line, col)
            w = _wrapper(t)
            if t == "REAL":
                def real_div(x):
                    b = rf(x)
                    if b == 0:
                        raise STRuntimeError("division by zero", line, col)
                    return lf(x) / b
                return real_div, t

            def int_div(x):
                a, b = lf(x), rf(x)
                if b == 0:
                    raise STRuntimeError("division by zero" if op == "/" else "MOD by zero", line, col)
                q = abs(a) // abs(b)
                if (a < 0) != (b < 0):
                    q = -q
                return w(q) if op == "/" else w(a - b * q)
            return int_div, t

        if op == "**":
            if lt not in NUMERIC or rt not in NUMERIC:
                raise bad()

            def power(x):
                try:
                    return math.pow(float(lf(x)), float(rf(x)))
                except (ValueError, OverflowError, ZeroDivisionError):
                    raise STRuntimeError("invalid ** operation", line, col) from None
            return power, "REAL"
        raise CompileError(f"unsupported operator '{op}'", line, col)

    def call(self, n):
        name, line, col = n.func, n.line, n.col
        if name not in FUNCTIONS:
            sym = self.symbols.get(name)
            if sym is not None and sym.kind == "fb":
                raise CompileError(f"'{sym.text}' is a function block, call it as a statement", line, col)
            raise CompileError(f"unknown function '{name}'", line, col)
        compiled = [self.expr(a) for a in n.args]
        fns = tuple(f for f, _ in compiled)
        types = [t for _, t in compiled]

        def need(count):
            if len(fns) != count:
                raise CompileError(f"{name} takes {count} argument{'s' if count != 1 else ''}, got {len(fns)}",
                                   line, col)

        def arg_error(i, want):
            a = n.args[i]
            return CompileError(f"{name} argument {i + 1} must be {want}, found {self.type_name(types[i])}",
                                a.line, a.col)

        if name in ("MIN", "MAX", "LIMIT"):
            if name == "LIMIT":
                need(3)
            elif len(fns) < 2:
                raise CompileError(f"{name} takes at least 2 arguments", line, col)
            if all(t in NUMERIC for t in types):
                t = types[0]
                for other in types[1:]:
                    t = self.unify_numeric(t, other)
            elif all(t == "TIME" for t in types):
                t = "TIME"
            else:
                raise CompileError(f"{name} arguments must all be numbers or all be TIME", line, col)
            if name == "MIN":
                return (lambda x: min(f(x) for f in fns)), t
            if name == "MAX":
                return (lambda x: max(f(x) for f in fns)), t
            mn, v, mx = fns
            return (lambda x: min(max(v(x), mn(x)), mx(x))), t

        need(1)
        f, t = fns[0], types[0]
        if name == "ABS":
            if t not in NUMERIC:
                raise arg_error(0, "a number")
            w = _wrapper(t)
            return (lambda x: w(abs(f(x)))), t
        if name == "SQRT":
            if t not in NUMERIC:
                raise arg_error(0, "a number")

            def sqrt(x):
                v = f(x)
                if v < 0:
                    raise STRuntimeError("SQRT of a negative number", line, col)
                return math.sqrt(v)
            return sqrt, "REAL"
        if name == "INT_TO_REAL":
            if t not in INT_TYPES:
                raise arg_error(0, "an integer")
            return (lambda x: float(f(x))), "REAL"
        if name == "REAL_TO_INT":
            if t not in NUMERIC:
                raise arg_error(0, "REAL")

            def real_to_int(x):
                v = f(x)
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    raise STRuntimeError("REAL_TO_INT of a value that is not finite", line, col)
                return wrap16(_round_half_away(v))
            return real_to_int, "INT"
        if name == "BOOL_TO_INT":
            if t != "BOOL":
                raise arg_error(0, "BOOL")
            return (lambda x: 1 if f(x) else 0), "INT"
        if name == "INT_TO_BOOL":
            if t not in INT_TYPES:
                raise arg_error(0, "an integer")
            return (lambda x: f(x) != 0), "BOOL"
        if name == "TIME_TO_INT":
            if t != "TIME":
                raise arg_error(0, "TIME")
            return (lambda x: wrap16(f(x))), "INT"
        raise CompileError(f"unknown function '{name}'", line, col)

    # ------------------------------------------------------------ configuration --
    def interval(self, sf):
        prog, cfg = sf.program, sf.config
        if cfg is None:
            return DEFAULT_INTERVAL_MS, None
        tasks = {t.name: t for t in cfg.tasks}
        chosen = None
        for b in cfg.bindings:
            if b.program != prog.name:
                raise CompileError(f"the configuration binds program '{b.program}' but the source defines "
                                   f"'{prog.name}'", b.line, b.col)
            if b.task is not None:
                if b.task not in tasks:
                    raise CompileError(f"the configuration has no TASK '{b.task}'", b.line, b.col)
                chosen = chosen or tasks[b.task]
        if chosen is None and cfg.tasks:
            chosen = cfg.tasks[0]
        if chosen is None:
            return DEFAULT_INTERVAL_MS, None
        node = chosen.params.get("INTERVAL")
        if node is None:
            return DEFAULT_INTERVAL_MS, chosen.name
        if not isinstance(node, Literal) or node.type != "TIME":
            raise CompileError("INTERVAL must be a duration such as T#100ms", node.line, node.col)
        if node.value < 1:
            raise CompileError("INTERVAL must be at least T#1ms", node.line, node.col)
        return node.value, chosen.name


def compile_task(source, reserve_system_words=True):
    """Compile a task source. Return a CompiledTask or raise CompileError with line and column.

    reserve_system_words=False lets a legacy OpenPLC program declare %MW0..%MW4. The runtime never
    loads a task that way, because the runtime owns those words (FLEET.md 3.2)."""
    if not isinstance(source, str):
        raise CompileError("the task source must be text")
    sf = parse(source)
    c = _Compiler(source, reserve_system_words)
    c.declare(sf.program.decls)
    interval_ms, task_name = c.interval(sf)
    body = c.block(sf.program.body)
    return CompiledTask(source, sf.program.name, interval_ms, c.symbols, c.local_inits, c.fb_classes, body,
                        task_name)
