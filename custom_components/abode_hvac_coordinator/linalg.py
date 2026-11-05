"""The only matrix arithmetic the component needs (Section 9) [Der][R18].

Matrices are lists of rows of floats; vectors are lists of floats. The
largest matrix is the heat filter's 25 x 25 uncertainty. Nothing needs
more than multiplying, transposing, an outer product and a 2 x 2
exponential and inverse, so no external package is used.
"""

from __future__ import annotations

import math

type Vector = list[float]
type Matrix = list[list[float]]


def zeros(rows: int, cols: int) -> Matrix:
    """A rows x cols matrix of zeros."""
    return [[0.0] * cols for _ in range(rows)]


def identity(n: int) -> Matrix:
    """The n x n identity."""
    out = zeros(n, n)
    for i in range(n):
        out[i][i] = 1.0
    return out


def copy(a: Matrix) -> Matrix:
    """A deep copy of a matrix."""
    return [row[:] for row in a]


def shape(a: Matrix) -> tuple[int, int]:
    """Rows and columns of a matrix."""
    return len(a), (len(a[0]) if a else 0)


def transpose(a: Matrix) -> Matrix:
    """A^T."""
    return [list(col) for col in zip(*a, strict=True)] if a else []


def multiply(a: Matrix, b: Matrix) -> Matrix:
    """A B."""
    ra, ca = shape(a)
    rb, cb = shape(b)
    if ca != rb:
        raise ValueError(f"cannot multiply {ra}x{ca} by {rb}x{cb}")
    bt = transpose(b)
    return [[math.fsum(x * y for x, y in zip(row, col, strict=True)) for col in bt] for row in a]


def mat_vec(a: Matrix, v: Vector) -> Vector:
    """A v."""
    if a and len(a[0]) != len(v):
        raise ValueError(f"cannot multiply {len(a)}x{len(a[0])} by a vector of {len(v)}")
    return [math.fsum(x * y for x, y in zip(row, v, strict=True)) for row in a]


def add(a: Matrix, b: Matrix) -> Matrix:
    """A + B."""
    if shape(a) != shape(b):
        raise ValueError(f"cannot add {shape(a)} and {shape(b)}")
    return [[x + y for x, y in zip(ra, rb, strict=True)] for ra, rb in zip(a, b, strict=True)]


def subtract(a: Matrix, b: Matrix) -> Matrix:
    """A - B."""
    if shape(a) != shape(b):
        raise ValueError(f"cannot subtract {shape(b)} from {shape(a)}")
    return [[x - y for x, y in zip(ra, rb, strict=True)] for ra, rb in zip(a, b, strict=True)]


def scale(a: Matrix, k: float) -> Matrix:
    """k A."""
    return [[k * x for x in row] for row in a]


def symmetrise(p: Matrix) -> Matrix:
    """(P + P^T) / 2."""
    n, m = shape(p)
    if n != m:
        raise ValueError(f"cannot symmetrise a {n}x{m} matrix")
    return [[(p[i][j] + p[j][i]) / 2.0 for j in range(n)] for i in range(n)]


def column(a: Matrix, j: int) -> Vector:
    """Column j of A."""
    return [row[j] for row in a]


def row(a: Matrix, i: int) -> Vector:
    """Row i of A, as a new list."""
    return a[i][:]


def outer(u: Vector, v: Vector) -> Matrix:
    """u v^T."""
    return [[x * y for y in v] for x in u]


def diagonal(values: Vector) -> Matrix:
    """A square matrix with values on the diagonal."""
    out = zeros(len(values), len(values))
    for i, x in enumerate(values):
        out[i][i] = x
    return out


def is_finite(a: Matrix) -> bool:
    """True when every element is a finite number."""
    return all(math.isfinite(x) for r in a for x in r)


def sandwich(f: Matrix, p: Matrix) -> Matrix:
    """F P F^T, the filter's predict step without Q."""
    return multiply(multiply(f, p), transpose(f))


# --------------------------------------------------------------------------
# 2 x 2: the room's 5-minute matrices (model/physics.py)
# --------------------------------------------------------------------------


def _check_2x2(a: Matrix) -> tuple[float, float, float, float]:
    if shape(a) != (2, 2):
        raise ValueError(f"expected a 2x2 matrix, got {shape(a)}")
    return a[0][0], a[0][1], a[1][0], a[1][1]


def eigenvalues_2x2(a: Matrix) -> tuple[float, float]:
    """The eigenvalues of A = [[p, q], [r, s]], larger first.

    l1, l2 = (p + s)/2 +- sqrt((p - s)^2 / 4 + q r). Raises ValueError if
    they are complex; Section 4.2 shows the room's are always real.
    """
    p, q, r, s = _check_2x2(a)
    disc = (p - s) ** 2 / 4.0 + q * r
    if disc < 0.0:
        raise ValueError("eigenvalues are complex")
    root = math.sqrt(disc)
    mean = (p + s) / 2.0
    return mean + root, mean - root


def expm_2x2(a: Matrix, t: float) -> Matrix:
    """e^(A t) for a 2 x 2 matrix.

    This is Sylvester's formula,
        e^(A t) = (e^(l1 t) (A - l2 I) - e^(l2 t) (A - l1 I)) / (l1 - l2),
    with the terms grouped about the mean eigenvalue mu = (l1 + l2)/2 and
    the half-gap d = (l1 - l2)/2:
        e^(A t) = e^(mu t) (cosh(d t) I + sinh(d t)/d (A - mu I)).
    The two are the same algebraically [Der]. The grouped form stays
    accurate when the eigenvalues are close, where the first divides two
    nearly equal numbers by a small one; sinh(d t)/d tends to t as d -> 0.
    For complex eigenvalues (never the room's) cosh and sinh become cos
    and sin.
    """
    p, q, r, s = _check_2x2(a)
    mu = (p + s) / 2.0
    disc = (p - s) ** 2 / 4.0 + q * r
    d = math.sqrt(abs(disc))
    dt = d * t
    if disc >= 0.0:
        ch = math.cosh(dt)
        sh_over_d = t if dt < 1e-8 else math.sinh(dt) / d
    else:
        ch = math.cos(dt)
        sh_over_d = t if dt < 1e-8 else math.sin(dt) / d
    em = math.exp(mu * t)
    return [
        [em * (ch + sh_over_d * (p - mu)), em * sh_over_d * q],
        [em * sh_over_d * r, em * (ch + sh_over_d * (s - mu))],
    ]


def inverse_2x2(a: Matrix) -> Matrix:
    """A^-1 = [[s, -q], [-r, p]] / (p s - q r). Raises if singular."""
    p, q, r, s = _check_2x2(a)
    det = p * s - q * r
    if det == 0.0 or not math.isfinite(det):
        raise ValueError("matrix is singular")
    return [[s / det, -q / det], [-r / det, p / det]]
