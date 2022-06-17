r"""Spherical Harmonics as polynomials of x, y, z
"""
import math
from functools import partial
from typing import Dict, List, Tuple, Union

import jax
import jax.numpy as jnp
import sympy

from e3nn_jax import Irreps, IrrepsData, clebsch_gordan
from e3nn_jax.util.sympy import sqrtQarray_to_sympy


def spherical_harmonics(
    irreps_out: Union[Irreps, int], input: Union[IrrepsData, jnp.ndarray], normalize: bool, normalization: str = "integral"
) -> IrrepsData:
    r"""Spherical harmonics

    .. image:: https://user-images.githubusercontent.com/333780/79220728-dbe82c00-7e54-11ea-82c7-b3acbd9b2246.gif

    | Polynomials defined on the 3d space :math:`Y^l: \mathbb{R}^3 \longrightarrow \mathbb{R}^{2l+1}`
    | Usually restricted on the sphere (with ``normalize=True``) :math:`Y^l: S^2 \longrightarrow \mathbb{R}^{2l+1}`
    | who satisfies the following properties:

    * are polynomials of the cartesian coordinates ``x, y, z``
    * is equivariant :math:`Y^l(R x) = D^l(R) Y^l(x)`
    * are orthogonal :math:`\int_{S^2} Y^l_m(x) Y^j_n(x) dx = \text{cste} \; \delta_{lj} \delta_{mn}`

    The value of the constant depends on the choice of normalization.

    It obeys the following property:

    .. math::

        Y^{l+1}_i(x) &= \text{cste}(l) \; & C_{ijk} Y^l_j(x) x_k

        \partial_k Y^{l+1}_i(x) &= \text{cste}(l) \; (l+1) & C_{ijk} Y^l_j(x)

    Where :math:`C` are the `clebsch_gordan`.

    .. note::

        This function match with this table of standard real spherical harmonics from Wikipedia_
        when ``normalize=True``, ``normalization='integral'`` and is called with the argument in the order ``y,z,x``
        (instead of ``x,y,z``).

    .. _Wikipedia: https://en.wikipedia.org/wiki/Table_of_spherical_harmonics#Real_spherical_harmonics

    Args:
        irreps_out (`Irreps` or int): output irreps
        input (`IrrepsData` or `jnp.ndarray`): cartesian coordinates
        normalize (bool): if True, the polynomials are restricted to the sphere
        normalization (str): normalization of the constant :math:`\text{cste}`. Default is 'integral'

    Returns:
        `jnp.ndarray`: polynomials of the spherical harmonics
    """
    assert normalization in ["integral", "component", "norm"]

    if isinstance(irreps_out, int):
        l = irreps_out
        assert isinstance(input, IrrepsData)
        [(mul, ir)] = input.irreps
        irreps_out = Irreps([(1, (l, ir.p**l))])

    irreps_out = Irreps(irreps_out)

    assert all([l % 2 == 1 or p == 1 for _, (l, p) in irreps_out])
    assert len(set([p for _, (l, p) in irreps_out if l % 2 == 1])) <= 1

    if isinstance(input, IrrepsData):
        [(mul, ir)] = input.irreps
        assert mul == 1
        assert ir.l == 1
        assert all([ir.p == p for _, (l, p) in irreps_out if l % 2 == 1])
        x = input.contiguous
    else:
        x = input

    assert x.shape[-1] == 3
    if normalize:
        r = jnp.linalg.norm(x, ord=2, axis=-1, keepdims=True)
        x = x / jnp.where(r == 0.0, 1.0, r)

    sh = _jited_spherical_harmonics(tuple(ir.l for _, ir in irreps_out), x, normalization)
    sh = [jnp.repeat(y[..., None, :], mul, -2) for (mul, ir), y in zip(irreps_out, sh)]
    return IrrepsData.from_list(irreps_out, sh, x.shape[:-1])


@partial(jax.jit, static_argnums=(0, 2), inline=True)
def _jited_spherical_harmonics(ls: Tuple[int, ...], x: jnp.ndarray, normalization: str) -> List[jnp.ndarray]:
    return _custom_vjp_spherical_harmonics(ls, x, normalization)


@partial(jax.custom_vjp, nondiff_argnums=(0, 2))
def _custom_vjp_spherical_harmonics(ls: Tuple[int, ...], x: jnp.ndarray, normalization: str) -> List[jnp.ndarray]:
    return _legendre_spherical_harmonics(ls, x, False, normalization)
    # context = dict()
    # for l in ls:
    #     _recursive_spherical_harmonics(l, context, x, normalization)
    #     # results are stored in context[l]

    # return [context[l] for l in ls]


def _fwd(ls: Tuple[int, ...], x: jnp.ndarray, normalization: str) -> Tuple[List[jnp.ndarray], List[jnp.ndarray]]:
    js = tuple(max(0, l - 1) for l in ls)
    output = _custom_vjp_spherical_harmonics(ls + js, x, normalization)

    return output[: len(ls)], output[len(ls) :]


def _bwd(ls: Tuple[int, ...], normalization: str, res: List[jnp.ndarray], grad: List[jnp.ndarray]) -> jnp.ndarray:
    def h(l):
        if normalization == "norm":
            return ((2 * l + 1) * l * (2 * l - 1)) ** 0.5
        return l**0.5 * (2 * l + 1)

    return (
        sum(
            [
                jnp.einsum("ijk,...i,...j->...k", h(l) * clebsch_gordan(l - 1, l, 1), r, g)
                if l > 0
                else jnp.zeros_like(r, shape=r.shape[:-1] + (3,))
                for l, r, g in zip(ls, res, grad)
            ]
        ),
    )


_custom_vjp_spherical_harmonics.defvjp(_fwd, _bwd)


def _recursive_spherical_harmonics(
    l: int, context: Dict[int, jnp.ndarray], input: jnp.ndarray, normalization: str
) -> sympy.Array:
    context.update(dict(jnp=jnp, clebsch_gordan=clebsch_gordan))

    if 0 not in context:
        if normalization == "integral":
            context[0] = math.sqrt(1 / (4 * math.pi)) * jnp.ones_like(input[..., :1])
            context[1] = math.sqrt(3 / (4 * math.pi)) * input
        elif normalization == "component":
            context[0] = jnp.ones_like(input[..., :1])
            context[1] = math.sqrt(3) * input
        else:
            context[0] = jnp.ones_like(input[..., :1])
            context[1] = input

    if l == 0:
        return sympy.Array([1])

    if l == 1:
        return sympy.Array([1, 0, 0])

    def sh_var(l):
        return [sympy.symbols(f"sh{l}_{m}") for m in range(2 * l + 1)]

    l2 = biggest_power_of_two(l - 1)
    l1 = l - l2

    w = sqrtQarray_to_sympy(clebsch_gordan(l1, l2, l))
    yx = sympy.Array(
        [
            sum(sh_var(l1)[i] * sh_var(l2)[j] * w[i, j, k] for i in range(2 * l1 + 1) for j in range(2 * l2 + 1))
            for k in range(2 * l + 1)
        ]
    )

    sph_1_l1 = _recursive_spherical_harmonics(l1, context, input, normalization)
    sph_1_l2 = _recursive_spherical_harmonics(l2, context, input, normalization)

    y1 = yx.subs(zip(sh_var(l1), sph_1_l1)).subs(zip(sh_var(l2), sph_1_l2))
    norm = sympy.sqrt(sum(y1.applyfunc(lambda x: x**2)))
    y1 = y1 / norm

    if l not in context:
        if normalization == "integral":
            x = math.sqrt((2 * l + 1) / (4 * math.pi)) / (
                math.sqrt((2 * l1 + 1) / (4 * math.pi)) * math.sqrt((2 * l2 + 1) / (4 * math.pi))
            )
        elif normalization == "component":
            x = math.sqrt((2 * l + 1) / ((2 * l1 + 1) * (2 * l2 + 1)))
        else:
            x = 1

        w = (x / float(norm)) * clebsch_gordan(l1, l2, l)
        context[l] = jnp.einsum("...i,...j,ijk->...k", context[l1], context[l2], w)

    return y1


def biggest_power_of_two(n):
    return 2 ** (n.bit_length() - 1)


def _legendre(ls, z, y2):
    r"""
    en.wikipedia.org/wiki/Associated_Legendre_polynomials
    - remove two times (-1)^m
    - use another normalization such that P(l, -m) = P(l, m)
    - remove (-1)^l

    y = sqrt(1 - z^2)
    y2 = y^2
    """
    for l in ls:
        l = sympy.Integer(l)
        out = []
        for m in range(l + 1):
            m = sympy.Integer(abs(m))
            zz, yy2 = sympy.symbols("z y2", real=True)
            ex = 1 / (2**l * sympy.factorial(l)) * yy2 ** (m / 2) * sympy.diff((zz**2 - 1) ** l, zz, l + m)
            ex *= sympy.sqrt((2 * l + 1) / (4 * sympy.pi) * sympy.factorial(l - m) / sympy.factorial(l + m))
            out += [eval(str(sympy.N(ex)), {str(zz): z, str(yy2): y2})]
        yield jnp.stack([out[abs(m)] for m in range(-l, l + 1)], axis=-1)


def _sh_alpha(l, alpha):
    alpha = alpha[..., None]  # [..., 1]
    m = jnp.arange(1, l + 1)  # [1, 2, 3, ..., l]
    cos = jnp.cos(m * alpha)  # [..., m]

    m = jnp.arange(l, 0, -1)  # [l, l-1, l-2, ..., 1]
    sin = jnp.sin(m * alpha)  # [..., m]

    return jnp.concatenate(
        [
            jnp.sqrt(2) * sin,
            jnp.ones_like(alpha),
            jnp.sqrt(2) * cos,
        ],
        axis=-1,
    )


def _legendre_spherical_harmonics(ls: List[int], x: jnp.ndarray, normalize: bool, normalization: str) -> jnp.ndarray:
    alpha = jnp.arctan2(x[..., 0], x[..., 2])
    sh_alpha = _sh_alpha(max(ls), alpha)

    n = jnp.linalg.norm(x, axis=-1, keepdims=True)
    x = x / jnp.where(n > 0, n, 1.0)

    sh_y = _legendre(ls, x[..., 1], x[..., 0] ** 2 + x[..., 2] ** 2)
    out = [(1 if normalize else n**l) * sh_alpha[..., max(ls) - l : max(ls) + l + 1] * y for l, y in zip(ls, sh_y)]

    if normalization == "norm":
        out = [(jnp.sqrt(4 * jnp.pi) / jnp.sqrt(2 * l + 1)) * y for l, y in zip(ls, out)]
    elif normalization == "component":
        out = [jnp.sqrt(4 * jnp.pi) * y for y in out]

    return out
