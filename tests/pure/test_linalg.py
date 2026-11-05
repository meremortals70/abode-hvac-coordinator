"""linalg.py: the component's matrix arithmetic (Section 9)."""

from __future__ import annotations

import math
import random
import unittest

from tests.pure._load import load

la = load("linalg")


def taylor_expm(a: list[list[float]], t: float, terms: int = 60) -> list[list[float]]:
    """e^(A t) by its power series, an independent check."""
    out = la.identity(2)
    term = la.identity(2)
    for n in range(1, terms):
        term = la.scale(la.multiply(term, a), t / n)
        out = la.add(out, term)
    return out


def close(testcase: unittest.TestCase, a: list[list[float]], b: list[list[float]], tol: float) -> None:
    for ra, rb in zip(a, b, strict=True):
        for x, y in zip(ra, rb, strict=True):
            testcase.assertLess(abs(x - y), tol, (a, b))


def room_a(a: float, b: float, c: float, k: float) -> list[list[float]]:
    return [[-(a + b), b], [c, -(c + k)]]


class Basics(unittest.TestCase):
    def test_multiply_transpose_add(self) -> None:
        a = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
        b = [[7.0, 8.0], [9.0, 10.0], [11.0, 12.0]]
        self.assertEqual(la.multiply(a, b), [[58.0, 64.0], [139.0, 154.0]])
        self.assertEqual(la.transpose(a), [[1.0, 4.0], [2.0, 5.0], [3.0, 6.0]])
        self.assertEqual(la.add(a, a), [[2.0, 4.0, 6.0], [8.0, 10.0, 12.0]])
        self.assertEqual(la.subtract(a, a), la.zeros(2, 3))
        self.assertEqual(la.scale(a, 2.0), la.add(a, a))
        self.assertEqual(la.mat_vec(a, [1.0, 0.0, -1.0]), [-2.0, -2.0])

    def test_shape_errors(self) -> None:
        with self.assertRaises(ValueError):
            la.multiply([[1.0, 2.0]], [[1.0, 2.0]])
        with self.assertRaises(ValueError):
            la.add([[1.0]], [[1.0, 2.0]])
        with self.assertRaises(ValueError):
            la.mat_vec([[1.0, 2.0]], [1.0])
        with self.assertRaises(ValueError):
            la.symmetrise([[1.0, 2.0]])

    def test_outer_column_row_symmetrise(self) -> None:
        self.assertEqual(la.outer([1.0, 2.0], [3.0, 4.0]), [[3.0, 4.0], [6.0, 8.0]])
        p = [[1.0, 2.0], [4.0, 5.0]]
        self.assertEqual(la.column(p, 1), [2.0, 5.0])
        r = la.row(p, 0)
        r[0] = 99.0
        self.assertEqual(p[0][0], 1.0)
        self.assertEqual(la.symmetrise(p), [[1.0, 3.0], [3.0, 5.0]])

    def test_filter_step_identities_on_25x25(self) -> None:
        rng = random.Random(7)
        n = 25
        f = [[rng.uniform(-1, 1) for _ in range(n)] for _ in range(n)]
        g = [[rng.uniform(-1, 1) for _ in range(n)] for _ in range(n)]
        p = la.multiply(g, la.transpose(g))
        fpft = la.sandwich(f, p)
        close(self, fpft, la.transpose(fpft), 1e-9)
        # P - K (row T of P) with K = column T of P / S keeps P symmetric
        s = p[0][0] + 0.1
        k = [x / s for x in la.column(p, 0)]
        updated = la.subtract(p, la.outer(k, la.row(p, 0)))
        close(self, updated, la.transpose(updated), 1e-9)
        self.assertTrue(la.is_finite(updated))
        updated[3][4] = math.nan
        self.assertFalse(la.is_finite(updated))


class Edges(unittest.TestCase):
    def test_copy_is_deep(self) -> None:
        a = [[1.0, 2.0], [3.0, 4.0]]
        b = la.copy(a)
        b[0][0] = 9.0
        self.assertEqual(a[0][0], 1.0)

    def test_subtract_and_two_by_two_shapes(self) -> None:
        self.assertEqual(la.subtract([[3.0]], [[1.0]]), [[2.0]])
        with self.assertRaises(ValueError):
            la.subtract([[1.0]], [[1.0, 2.0]])
        with self.assertRaises(ValueError):
            la.eigenvalues_2x2(la.identity(3))


class TwoByTwo(unittest.TestCase):
    def test_eigenvalues(self) -> None:
        self.assertEqual(la.eigenvalues_2x2([[2.0, 0.0], [0.0, -1.0]]), (2.0, -1.0))
        with self.assertRaises(ValueError):
            la.eigenvalues_2x2([[0.0, -1.0], [1.0, 0.0]])

    def test_room_matrix_is_stable_real_distinct(self) -> None:
        # Section 4.2: every figure >= 0, b, c > 0 -> real, distinct, negative
        rng = random.Random(11)
        for _ in range(2000):
            a, k = rng.uniform(0, 2), rng.uniform(0, 2)
            if a == 0 and k == 0:
                continue
            b, c = math.exp(rng.uniform(-8, 2)), math.exp(rng.uniform(-8, 2))
            l1, l2 = la.eigenvalues_2x2(room_a(a, b, c, k))
            self.assertLess(l1, 0.0)
            self.assertLess(l2, l1)

    def test_expm_matches_power_series(self) -> None:
        t = 5.0 / 60.0
        for mat in (
            room_a(0.2, 0.2, 0.3, 0.1),
            room_a(0.10, 0.50, 0.15, 0.05),
            [[-1.0, 0.0], [0.0, -1.0]],
            [[0.0, -1.0], [1.0, 0.0]],
            room_a(0.1, 1e-7, 1e-7, 0.1),  # eigenvalues nearly equal
        ):
            close(self, la.expm_2x2(mat, t), taylor_expm(mat, t), 1e-12)
        mat = room_a(0.3, 0.8, 0.4, 0.2)
        close(self, la.expm_2x2(mat, 6.0), taylor_expm(mat, 6.0, 120), 1e-10)

    def test_expm_is_sylvester(self) -> None:
        a = room_a(0.2, 0.2, 0.3, 0.1)
        t = 1.0 / 12.0
        l1, l2 = la.eigenvalues_2x2(a)
        i = la.identity(2)
        syl = la.scale(
            la.subtract(
                la.scale(la.subtract(a, la.scale(i, l2)), math.exp(l1 * t)),
                la.scale(la.subtract(a, la.scale(i, l1)), math.exp(l2 * t)),
            ),
            1.0 / (l1 - l2),
        )
        close(self, la.expm_2x2(a, t), syl, 1e-14)

    def test_inverse(self) -> None:
        a = room_a(0.2, 0.2, 0.3, 0.1)
        close(self, la.multiply(a, la.inverse_2x2(a)), la.identity(2), 1e-14)
        with self.assertRaises(ValueError):
            la.inverse_2x2([[1.0, 2.0], [2.0, 4.0]])


if __name__ == "__main__":
    unittest.main()
