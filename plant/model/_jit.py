"""Optional compiled kernels for the plant's hottest arithmetic (2026-10-16, speed only).

When numba is installed (and VISR_NUMBA is not "0"), `njit` compiles a function, else it leaves
the Python function as it is. A kernel must give the same value as the Python code, bit for bit:
plant/equivalence.py checks the whole plant against the Python path (VISR_NUMBA=0) with zero
tolerance. Checked on the laptop (Windows) and on forge (Linux), 1e6 random values each: numba's
math.exp, sqrt, float and complex arithmetic and x ** y equal CPython's. One exception: numba
computes x ** 2 with a constant exponent as x * x, and CPython calls the C pow(x, 2.0), which
differs in the last bit for about 0.07 % of values. A kernel therefore reads such an exponent at
run time (from its parameter array), so the C pow runs as in Python.
"""
import os

JIT = False
if os.environ.get("VISR_NUMBA", "1") != "0":
    try:
        import numba
        JIT = True
    except ImportError:            # pure Python: the same values, slower
        pass


def njit(fn):
    return numba.njit(cache=True)(fn) if JIT else fn
