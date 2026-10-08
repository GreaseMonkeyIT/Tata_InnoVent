"""Small numeric helpers with no dependencies outside the standard library.

nelder_mead: a minimizer for the parameter fits (motor equivalent circuit from datasheet points).
golden_max: the maximum of a function with one peak on an interval.
"""
import math


def nelder_mead(f, x0, step=0.3, tol=1e-12, max_iter=6000):
    """Minimize f over R^n from x0. Returns (x, f(x)). Standard coefficients (1, 2, 0.5, 0.5)."""
    n = len(x0)
    pts = [list(x0)]
    for i in range(n):
        p = list(x0)
        p[i] += step
        pts.append(p)
    vals = [f(p) for p in pts]
    for _ in range(max_iter):
        order = sorted(range(n + 1), key=vals.__getitem__)
        pts = [pts[i] for i in order]
        vals = [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) <= tol * (1.0 + abs(vals[0])):
            break
        cen = [sum(p[i] for p in pts[:-1]) / n for i in range(n)]
        worst = pts[-1]
        xr = [c + (c - w) for c, w in zip(cen, worst)]
        fr = f(xr)
        if fr < vals[0]:
            xe = [c + 2.0 * (c - w) for c, w in zip(cen, worst)]
            fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            if fr < vals[-1]:
                xc = [c + 0.5 * (r - c) for c, r in zip(cen, xr)]
            else:
                xc = [c + 0.5 * (w - c) for c, w in zip(cen, worst)]
            fc = f(xc)
            if fc < min(fr, vals[-1]):
                pts[-1], vals[-1] = xc, fc
            else:
                best = pts[0]
                pts = [best] + [[b + 0.5 * (p - b) for b, p in zip(best, q)] for q in pts[1:]]
                vals = [vals[0]] + [f(p) for p in pts[1:]]
    i = min(range(n + 1), key=vals.__getitem__)
    return pts[i], vals[i]


def golden_max(f, a, b, tol=1e-6):
    """The x in [a, b] where f has its maximum, for f with one peak on [a, b]."""
    g = (math.sqrt(5.0) - 1.0) / 2.0
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = f(c), f(d)
    while abs(b - a) > tol * (1.0 + abs(a)):
        if fc > fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = f(d)
    return (a + b) / 2.0
