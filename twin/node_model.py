"""The small model of one node (ideas.md 12.3, step 3), with the same method for every node.

History without hand-made features: each input passes through a bank of first-order exponential
filters with time constants from seconds to tens of minutes. A first-order filter bank is the
simplest orthonormal-basis model of a linear system's memory (Laguerre and Kautz models: Wahlberg,
"System identification using Laguerre models", IEEE Trans. Automatic Control 36(5), 1991). The
network then maps the present inputs and their filtered history to the node's outputs. Nothing in
it is specific to one machine type: a new node gives its input and output signal names only.

The network: a linear map plus a small multilayer perceptron (tanh) on the same features,
y = L x + MLP(x), trained in two stages. First L by ridge least squares, then the MLP on what L
leaves (Adam on the mean squared error of the standardized outputs, early stopping on a
validation split, fixed seed). The linear part carries the near-linear relations (an outlet
temperature that follows its inlet), so the model extends them past the range of its training
data. A tanh network alone saturates there, and one trained jointly with L takes part of the
linear relation and saturates with it.

Coverage: the model keeps the range of every feature in its training data. `coverage()` gives the
share of samples whose features stay inside that range (with a margin). Outside it the residual
means "the model does not know", not "fault" (ideas.md 12.7, cold start).

Prediction = value, residual = measured - predicted. The model never feeds its own outputs back
(an output-error structure on the inputs only), so a fault cannot hide in the model's memory of
the faulty output.
"""
import json

import numpy as np

DEFAULT_TAUS = (5.0, 30.0, 120.0, 600.0, 1800.0)


class FilterBank:
    """First-order filters y' = (u - y) / tau for every input and every tau. Exact discrete step
    for a sample time dt with the input held over the step."""

    def __init__(self, n_in, taus=DEFAULT_TAUS):
        self.taus = np.asarray(taus, dtype=float)
        self.n_in = n_in
        self.state = None

    def reset(self, u0):
        self.state = np.repeat(np.asarray(u0, dtype=float)[:, None], len(self.taus), axis=1)

    def step(self, u, dt):
        u = np.asarray(u, dtype=float)
        if self.state is None:
            self.reset(u)
        a = np.exp(-dt / self.taus)[None, :]
        self.state = u[:, None] + (self.state - u[:, None]) * a
        return self.state.ravel()

    def run(self, U, dt):
        """Filter a whole input series U (T x n_in). Returns T x (n_in * n_tau)."""
        self.reset(U[0])
        return np.stack([self.step(u, dt) for u in U])


class NodeModel:
    def __init__(self, inputs, outputs, hidden=(32, 32), taus=DEFAULT_TAUS, seed=7):
        self.inputs, self.outputs = list(inputs), list(outputs)
        self.hidden, self.taus, self.seed = tuple(hidden), tuple(taus), seed
        self.params = None
        self.norm = None

    # ------------------------------------------------------------------ features --
    def features(self, U, dt):
        fb = FilterBank(len(self.inputs), self.taus)
        return np.hstack([U, fb.run(U, dt)])

    # --------------------------------------------------------------------- network --
    def _init(self, n_x, n_y):
        rng = np.random.default_rng(self.seed)
        sizes = [n_x, *self.hidden, n_y]
        self.params = []
        for a, b in zip(sizes[:-1], sizes[1:]):
            self.params.append([rng.normal(0.0, np.sqrt(1.0 / a), (a, b)), np.zeros(b)])
        self.params.append([np.zeros((n_x, n_y)), np.zeros(n_y)])     # the linear path

    def _forward(self, X):
        mlp = self.params[:-1]
        acts = [X]
        h = X
        for i, (W, b) in enumerate(mlp):
            z = h @ W + b
            h = np.tanh(z) if i < len(mlp) - 1 else z
            acts.append(h)
        Wl, bl = self.params[-1]
        acts[-1] = acts[-1] + X @ Wl + bl
        return acts

    def _grads(self, X, Y):
        acts = self._forward(X)
        n = X.shape[0]
        d = 2.0 * (acts[-1] - Y) / (n * Y.shape[1])
        grads = [None] * len(self.params)
        grads[-1] = [X.T @ d, d.sum(axis=0)]
        mlp = self.params[:-1]
        for i in range(len(mlp) - 1, -1, -1):
            W, b = mlp[i]
            grads[i] = [acts[i].T @ d, d.sum(axis=0)]
            if i > 0:
                d = (d @ W.T) * (1.0 - acts[i] ** 2)
        return grads, float(np.mean((acts[-1] - Y) ** 2))

    def n_weights(self):
        return int(sum(W.size + b.size for W, b in self.params))

    # -------------------------------------------------------------------- training --
    def fit(self, U, Y, dt, epochs=200, batch=256, lr=3e-3, val_frac=0.2, patience=20, log=None):
        """U: T x n_in inputs, Y: T x n_out outputs, sampled every dt seconds. The validation
        split is the last val_frac of the series (no shuffling across it: the test is on time
        the model has not seen)."""
        X = self.features(np.asarray(U, float), dt)
        Y = np.asarray(Y, float)
        self.norm = {"xm": X.mean(0), "xs": X.std(0) + 1e-9, "ym": Y.mean(0), "ys": Y.std(0) + 1e-9,
                     "xlo": X.min(0), "xhi": X.max(0)}
        Xn = (X - self.norm["xm"]) / self.norm["xs"]
        Yn = (Y - self.norm["ym"]) / self.norm["ys"]
        n_val = int(len(Xn) * val_frac)
        Xt, Yt, Xv, Yv = Xn[:-n_val], Yn[:-n_val], Xn[-n_val:], Yn[-n_val:]
        self._init(Xn.shape[1], Yn.shape[1])
        # stage 1: the linear path by ridge least squares on the training part
        A = np.hstack([Xt_ := Xn[:-n_val], np.ones((len(Xt_), 1))])
        lam = 1e-3 * len(A)
        sol = np.linalg.solve(A.T @ A + lam * np.eye(A.shape[1]), A.T @ Yn[:-n_val])
        self.params[-1] = [sol[:-1], sol[-1]]
        rng = np.random.default_rng(self.seed + 1)
        m = [[np.zeros_like(W), np.zeros_like(b)] for W, b in self.params]
        v = [[np.zeros_like(W), np.zeros_like(b)] for W, b in self.params]
        b1, b2, step = 0.9, 0.999, 0
        best, best_params, wait = np.inf, None, 0
        for ep in range(epochs):
            idx = rng.permutation(len(Xt))
            for k in range(0, len(idx), batch):
                j = idx[k:k + batch]
                g, _ = self._grads(Xt[j], Yt[j])
                step += 1
                for p, gg, mm, vv in zip(self.params[:-1], g[:-1], m[:-1], v[:-1]):   # stage 2: MLP only
                    for q in range(2):
                        mm[q] = b1 * mm[q] + (1 - b1) * gg[q]
                        vv[q] = b2 * vv[q] + (1 - b2) * gg[q] ** 2
                        mh = mm[q] / (1 - b1 ** step)
                        vh = vv[q] / (1 - b2 ** step)
                        p[q] = p[q] - lr * mh / (np.sqrt(vh) + 1e-8)
            val = float(np.mean((self._forward(Xv)[-1] - Yv) ** 2))
            if log:
                log(ep, val)
            if val < best - 1e-6:
                best, wait = val, 0
                best_params = [[W.copy(), b.copy()] for W, b in self.params]
            else:
                wait += 1
                if wait >= patience:
                    break
        self.params = best_params
        return best

    # ------------------------------------------------------------------ prediction --
    def predict(self, U, dt):
        X = (self.features(np.asarray(U, float), dt) - self.norm["xm"]) / self.norm["xs"]
        return self._forward(X)[-1] * self.norm["ys"] + self.norm["ym"]

    def residuals(self, U, Y, dt):
        return np.asarray(Y, float) - self.predict(U, dt)

    def covered(self, U, dt, margin=0.10):
        """Per sample: True when every feature lies inside its training range, widened by
        `margin` of that range on each side."""
        X = self.features(np.asarray(U, float), dt)
        span = self.norm["xhi"] - self.norm["xlo"]
        lo, hi = self.norm["xlo"] - margin * span, self.norm["xhi"] + margin * span
        return np.all((X >= lo) & (X <= hi), axis=1)

    # --------------------------------------------------------------------- storage --
    def save(self, path):
        arrs = {f"W{i}": W for i, (W, _) in enumerate(self.params)}
        arrs.update({f"b{i}": b for i, (_, b) in enumerate(self.params)})
        arrs.update({f"n_{k}": v for k, v in self.norm.items()})
        meta = {"inputs": self.inputs, "outputs": self.outputs, "hidden": self.hidden,
                "taus": self.taus, "seed": self.seed}
        np.savez(path, meta=json.dumps(meta), **arrs)

    @classmethod
    def load(cls, path):
        z = np.load(path)
        meta = json.loads(str(z["meta"]))
        m = cls(meta["inputs"], meta["outputs"], meta["hidden"], meta["taus"], meta["seed"])
        n = len(meta["hidden"]) + 2
        m.params = [[z[f"W{i}"], z[f"b{i}"]] for i in range(n)]
        m.norm = {k: z[f"n_{k}"] for k in ("xm", "xs", "ym", "ys", "xlo", "xhi")}
        return m
