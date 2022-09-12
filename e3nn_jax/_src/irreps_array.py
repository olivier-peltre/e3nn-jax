import math
import operator
import warnings
from typing import List, Optional, Tuple, Union

import jax
import jax.numpy as jnp
import jax.scipy
import numpy as np

import e3nn_jax as e3nn
from e3nn_jax import Irreps, axis_angle_to_angles, config, matrix_to_angles, quaternion_to_angles
from e3nn_jax._src.irreps import IntoIrreps


def _infer_backend(pytree):
    any_numpy = any(isinstance(x, np.ndarray) for x in jax.tree_util.tree_leaves(pytree))
    any_jax = any(isinstance(x, jnp.ndarray) for x in jax.tree_util.tree_leaves(pytree))
    if any_numpy and any_jax:
        raise ValueError("Cannot mix numpy and jax arrays")
    if any_numpy:
        return np
    if any_jax:
        return jnp
    return np


class IrrepsArray:
    r"""Data along with its irreps.

    The IrrepsArray class enforce equivariance by storing an array of data (``.array``)
    along with its representation (``.irreps``).

    Args:
        irreps (Irreps): representation of the data
        array (jax.numpy.ndarray): the data, an array of shape ``(..., irreps.dim)``
        list (list of jax.numpy.ndarray or None, optional): the same data in a list format.
            It can contain ``None`` to represent zeros otherwise the shape has to be ``(..., mul, ir.dim)``.

    Examples:
        >>> import e3nn_jax as e3nn
        >>> x = e3nn.IrrepsArray("1o + 2x0e", jnp.ones(5))
        >>> y = e3nn.IrrepsArray.from_list("1o + 2x0e", [None, jnp.ones((2, 1))], ())
        >>> x + y
        1x1o+2x0e [1. 1. 1. 2. 2.]

        Example of indexing:

        >>> x = IrrepsArray("0e + 1o", jnp.arange(2 * 4).reshape(2, 4))
        >>> x[0]
        1x0e+1x1o [0 1 2 3]
        >>> x[1, "0e"]
        1x0e [4]
        >>> x[:, 1:]
        1x1o
        [[1 2 3]
         [5 6 7]]
        >>> IrrepsArray("5x0e", jnp.arange(5))[1:3]
        2x0e [1 2]
    """

    irreps: Irreps
    array: jnp.ndarray  # this field is mendatory because it contains the shape
    _list: List[Optional[jnp.ndarray]]  # this field is lazy, it is computed only when needed

    def __init__(
        self, irreps: IntoIrreps, array: jnp.ndarray, list: List[Optional[jnp.ndarray]] = None, _perform_checks: bool = True
    ):
        """Create an IrrepsArray."""
        self.irreps = Irreps(irreps)
        self.array = array
        self._list = list

        if _perform_checks:
            if self.array.shape[-1] != self.irreps.dim:
                raise ValueError(
                    f"IrrepsArray: Array shape {self.array.shape} incompatible with irreps {self.irreps}. "
                    f"{self.array.shape[-1]} != {self.irreps.dim}"
                )
            if self._list is not None:
                if len(self._list) != len(self.irreps):
                    raise ValueError(f"IrrepsArray: List length {len(self._list)} incompatible with irreps {self.irreps}.")
                for x, (mul, ir) in zip(self._list, self.irreps):
                    if x is not None:
                        if x.shape != self.array.shape[:-1] + (mul, ir.dim):
                            raise ValueError(
                                f"IrrepsArray: List shapes {[None if x is None else x.shape for x in self._list]} "
                                f"incompatible with array shape {self.array.shape} and irreps {self.irreps}. "
                                f"Expecting {[self.array.shape[:-1] + (mul, ir.dim) for (mul, ir) in self.irreps]}."
                            )

    @staticmethod
    def from_list(irreps: IntoIrreps, list, leading_shape: Tuple[int]) -> "IrrepsArray":
        r"""Create an IrrepsArray from a list of arrays.

        Args:
            irreps (Irreps): irreps
            list (list of optional `jax.numpy.ndarray`): list of arrays
            leading_shape (tuple of int): leading shape of the arrays (without the irreps)

        Returns:
            IrrepsArray
        """
        jnp = _infer_backend(list)

        irreps = Irreps(irreps)
        if len(irreps) != len(list):
            raise ValueError(f"IrrepsArray.from_list: len(irreps) != len(list), {len(irreps)} != {len(list)}")

        if not all(x is None or isinstance(x, jnp.ndarray) for x in list):
            raise ValueError(f"IrrepsArray.from_list: list contains non-array elements type={[type(x) for x in list]}")

        if not all(x is None or x.shape == leading_shape + (mul, ir.dim) for x, (mul, ir) in zip(list, irreps)):
            raise ValueError(
                f"IrrepsArray.from_list: list shapes {[None if x is None else x.shape for x in list]} "
                f"incompatible with leading shape {leading_shape} and irreps {irreps}. "
                f"Expecting {[leading_shape + (mul, ir.dim) for (mul, ir) in irreps]}."
            )

        if irreps.dim > 0:
            array = jnp.concatenate(
                [
                    jnp.zeros(leading_shape + (mul_ir.dim,)) if x is None else x.reshape(leading_shape + (mul_ir.dim,))
                    for mul_ir, x in zip(irreps, list)
                ],
                axis=-1,
            )
        else:
            array = jnp.zeros(leading_shape + (0,))
        return IrrepsArray(irreps=irreps, array=array, list=list)

    @staticmethod
    def zeros(irreps: IntoIrreps, leading_shape) -> "IrrepsArray":
        r"""Create an IrrepsArray of zeros."""
        irreps = Irreps(irreps)
        return IrrepsArray(irreps=irreps, array=jnp.zeros(leading_shape + (irreps.dim,)), list=[None] * len(irreps))

    @staticmethod
    def ones(irreps: IntoIrreps, leading_shape) -> "IrrepsArray":
        r"""Create an IrrepsArray of ones."""
        irreps = Irreps(irreps)
        return IrrepsArray(
            irreps=irreps,
            array=jnp.ones(leading_shape + (irreps.dim,)),
            list=[jnp.ones(leading_shape + (mul, ir.dim)) for mul, ir in irreps],
        )

    @property
    def list(self) -> List[Optional[jnp.ndarray]]:
        r"""List of arrays matching each item of the ``.irreps``.

        Example:
            >>> x = IrrepsArray("2x0e + 0e", jnp.arange(3))
            >>> len(x.list)
            2
            >>> x.list[0]
            DeviceArray([[0],
                         [1]], dtype=int32)
            >>> x.list[1]
            DeviceArray([[2]], dtype=int32)

            The follwing is always true:

            >>> all(e is None or e.shape == x.shape[:-1] + (mul, ir.dim) for (mul, ir), e in zip(x.irreps, x.list))
            True
        """
        jnp = _infer_backend(self.array)

        if self._list is None:
            leading_shape = self.array.shape[:-1]
            if len(self.irreps) == 1:
                mul, ir = self.irreps[0]
                list = [jnp.reshape(self.array, leading_shape + (mul, ir.dim))]
            else:
                list = [
                    jnp.reshape(self.array[..., i], leading_shape + (mul, ir.dim))
                    for i, (mul, ir) in zip(self.irreps.slices(), self.irreps)
                ]
            self._list = list
        return self._list

    @property
    def shape(self):
        r"""Shape. Equivalent to ``self.array.shape``."""
        return self.array.shape

    @property
    def ndim(self):
        r"""Number of dimensions. Equivalent to ``self.array.ndim``."""
        return len(self.shape)

    # def __jax_array__(self):
    #     if self.irreps.lmax > 0:
    #         return NotImplemented
    #     return self.array
    #
    # Note: - __jax_array__ seems to be incompatible with register_pytree_node
    #       - __jax_array__ cause problem for the multiplication: jnp.array * IrrepsArray -> jnp.array

    def __repr__(self):  # noqa: D105
        r = str(self.array)
        if "\n" in r:
            return f"{self.irreps}\n{r}"
        return f"{self.irreps} {r}"

    def __len__(self):  # noqa: D105
        return len(self.array)

    def __eq__(self: "IrrepsArray", other: Union["IrrepsArray", jnp.ndarray]) -> "IrrepsArray":  # noqa: D105
        jnp = _infer_backend(self.array)

        if isinstance(other, IrrepsArray):
            if self.irreps != other.irreps:
                raise ValueError("IrrepsArray({self.irreps}) == IrrepsArray({other.irreps}) is not equivariant.")

            leading_shape = jnp.broadcast_shapes(self.shape[:-1], other.shape[:-1])

            def eq(mul: int, x: jnp.ndarray, y: jnp.ndarray) -> jnp.ndarray:
                if x is None and y is None:
                    return jnp.ones(leading_shape + (mul,), dtype="bool")
                if x is None:
                    x = 0.0
                if y is None:
                    y = 0.0

                return jnp.all(x == y, axis=-1)

            list = [eq(mul, x, y)[..., None] for (mul, ir), x, y in zip(self.irreps, self.list, other.list)]
            return IrrepsArray.from_list([(mul, "0e") for mul, _ in self.irreps], list, leading_shape)

        other = jnp.asarray(other)
        if self.irreps.lmax > 0 or (other.ndim > 0 and other.shape[-1] != 1):
            raise ValueError(f"IrrepsArray({self.irreps}) == scalar(shape={other.shape}) is not equivariant.")
        return IrrepsArray(irreps=self.irreps, array=self.array == other)

    def __add__(self: "IrrepsArray", other: Union["IrrepsArray", jnp.ndarray]) -> "IrrepsArray":  # noqa: D105
        jnp = _infer_backend(self.array)

        if not isinstance(other, IrrepsArray):
            if all(ir == "0e" for _, ir in self.irreps):
                other = jnp.asarray(other)
                return IrrepsArray(irreps=self.irreps, array=self.array + other)
            raise ValueError(f"IrrepsArray({self.irreps}) + scalar is not equivariant.")

        if self.irreps != other.irreps:
            raise ValueError(f"IrrepsArray({self.irreps}) + IrrepsArray({other.irreps}) is not equivariant.")

        list = [x if y is None else (y if x is None else x + y) for x, y in zip(self.list, other.list)]
        return IrrepsArray(irreps=self.irreps, array=self.array + other.array, list=list)

    def __sub__(self: "IrrepsArray", other: Union["IrrepsArray", jnp.ndarray]) -> "IrrepsArray":  # noqa: D105
        jnp = _infer_backend(self.array)

        if not isinstance(other, IrrepsArray):
            if all(ir == "0e" for _, ir in self.irreps):
                other = jnp.asarray(other)
                return IrrepsArray(irreps=self.irreps, array=self.array - other)
            raise ValueError(f"IrrepsArray({self.irreps}) - scalar is not equivariant.")

        if self.irreps != other.irreps:
            raise ValueError(f"IrrepsArray({self.irreps}) - IrrepsArray({other.irreps}) is not equivariant.")
        list = [x if y is None else (-y if x is None else x - y) for x, y in zip(self.list, other.list)]
        return IrrepsArray(irreps=self.irreps, array=self.array - other.array, list=list)

    def __mul__(self: "IrrepsArray", other: Union["IrrepsArray", jnp.ndarray]) -> "IrrepsArray":  # noqa: D105
        jnp = _infer_backend(self.array)

        if isinstance(other, IrrepsArray):
            if self.irreps.lmax > 0 and other.irreps.lmax > 0:
                raise ValueError(
                    "x * y with both x and y non scalar and ambiguous. Use e3nn.elementwise_tensor_product instead."
                )
            return e3nn.elementwise_tensor_product(self, other)

        other = jnp.asarray(other)
        if self.irreps.lmax > 0 and other.ndim > 0 and other.shape[-1] != 1:
            raise ValueError(f"IrrepsArray({self.irreps}) * scalar(shape={other.shape}) is not equivariant.")
        list = [None if x is None else x * other[..., None] for x in self.list]
        return IrrepsArray(irreps=self.irreps, array=self.array * other, list=list)

    def __rmul__(self: "IrrepsArray", other: jnp.ndarray) -> "IrrepsArray":  # noqa: D105
        return self * other

    def __truediv__(self: "IrrepsArray", other: Union["IrrepsArray", jnp.ndarray]) -> "IrrepsArray":  # noqa: D105
        jnp = _infer_backend(self.array)

        if isinstance(other, IrrepsArray):
            if len(other.irreps) == 0 or other.irreps.lmax > 0 or self.irreps.num_irreps != other.irreps.num_irreps:
                raise ValueError(f"IrrepsArray({self.irreps}) / IrrepsArray({other.irreps}) is not equivariant.")

            if any(x is None for x in other.list):
                raise ValueError("There are deterministic Zeros in the array of the lhs. Cannot divide by Zero.")
            other = 1.0 / other
            return e3nn.elementwise_tensor_product(self, other)

        other = jnp.asarray(other)
        if self.irreps.lmax > 0 and other.ndim > 0 and other.shape[-1] != 1:
            raise ValueError(f"IrrepsArray({self.irreps}) / scalar(shape={other.shape}) is not equivariant.")
        list = [None if x is None else x / other[..., None] for x in self.list]
        return IrrepsArray(irreps=self.irreps, array=self.array / other, list=list)

    def __rtruediv__(self: "IrrepsArray", other: jnp.ndarray) -> "IrrepsArray":  # noqa: D105
        jnp = _infer_backend((self.array, other))

        other = jnp.asarray(other)
        if self.irreps.lmax > 0:
            raise ValueError(f"scalar(shape={other.shape}) / IrrepsArray({self.irreps}) is not equivariant.")
        if any(x is None for x in self.list):
            raise ValueError("There are deterministic Zeros in the array of the lhs. Cannot divide by Zero.")

        return IrrepsArray(irreps=self.irreps, array=other / self.array, list=[other[..., None] / x for x in self.list])

    def __pow__(self, exponent) -> "IrrepsArray":  # noqa: D105
        if all(ir == "0e" for _, ir in self.irreps):
            return IrrepsArray(irreps=self.irreps, array=self.array**exponent, list=[x**exponent for x in self.list])

        if exponent % 1.0 == 0.0 and self.irreps.lmax == 0:
            irreps = self.irreps
            if exponent % 2.0 == 0.0:
                irreps = [(mul, "0e") for mul, ir in self.irreps]
            return IrrepsArray(irreps, array=self.array**exponent, list=[x**exponent for x in self.list])

        raise ValueError(f"IrrepsArray({self.irreps}) ** scalar is not equivariant.")

    def __iter__(self):  # noqa: D105
        if self.ndim <= 1:
            raise ValueError("Can't iterate over IrrepsArray with ndim <= 1")
        for i in range(len(self)):
            yield self[i]

    def __getitem__(self, index) -> "IrrepsArray":  # noqa: D105
        if not isinstance(index, tuple):
            index = (index,)

        def is_ellipse(x):
            return type(x) == type(Ellipsis)

        def is_none_slice(x):
            return isinstance(x, slice) and x == slice(None)

        # Support of x[..., "1e + 2e"]
        if isinstance(index[-1], (e3nn.Irrep, e3nn.MulIrrep, Irreps, str)):
            if not (any(map(is_ellipse, index[:-1])) or len(index) == self.ndim):
                raise ValueError("Irreps index must be the last index")

            irreps = Irreps(index[-1])

            ii = [i for i in range(len(self.irreps)) if self.irreps[i : i + len(irreps)] == irreps]
            if len(ii) != 1:
                raise ValueError(f"Can't slice with {irreps} because it doesn't appear exactly once in {self.irreps}")
            i = ii[0]

            return IrrepsArray(
                irreps,
                self.array[..., self.irreps[:i].dim : self.irreps[: i + len(irreps)].dim],
                self.list[i : i + len(irreps)],
            )[index[:-1] + (slice(None),)]

        # Support of x[..., 3:32]
        if (
            (any(map(is_ellipse, index[:-1])) or len(index) == self.ndim)
            and isinstance(index[-1], slice)
            and index[-1].step is None
            and isinstance(index[-1].start, (int, type(None)))
            and isinstance(index[-1].stop, (int, type(None)))
            and (index[-1].start is not None or index[-1].stop is not None)
        ):
            start = index[-1].start if index[-1].start is not None else 0
            stop = index[-1].stop if index[-1].stop is not None else self.shape[-1]

            if start < 0:
                start += self.shape[-1]
            if stop < 0:
                stop += self.shape[-1]

            start = min(max(0, start), self.shape[-1])
            stop = min(max(0, stop), self.shape[-1])

            irreps_start = None
            irreps_stop = None

            for i in range(len(self.irreps) + 1):
                if self.irreps[:i].dim == start:
                    irreps_start = i

                if irreps_start is None and start < self.irreps[:i].dim:
                    # "2x1e"[3:]
                    mul, ir = self.irreps[i - 1]
                    if (start - self.irreps[: i - 1].dim) % ir.dim == 0:
                        mul1 = (start - self.irreps[: i - 1].dim) // ir.dim
                        return self.convert(
                            self.irreps[: i - 1] + e3nn.Irreps([(mul1, ir), (mul - mul1, ir)]) + self.irreps[i:]
                        )[index]

                if self.irreps[:i].dim == stop:
                    irreps_stop = i
                    break

                if irreps_stop is None and stop < self.irreps[:i].dim:
                    # "2x1e"[:3]
                    mul, ir = self.irreps[i - 1]
                    if (stop - self.irreps[: i - 1].dim) % ir.dim == 0:
                        mul1 = (stop - self.irreps[: i - 1].dim) // ir.dim
                        return self.convert(
                            self.irreps[: i - 1] + e3nn.Irreps([(mul1, ir), (mul - mul1, ir)]) + self.irreps[i:]
                        )[index]

            if irreps_start is None or irreps_stop is None:
                raise ValueError(f"Can't slice with {index[-1]} because it doesn't match with {self.irreps}")

            return IrrepsArray(
                self.irreps[irreps_start:irreps_stop], self.array[..., start:stop], self.list[irreps_start:irreps_stop]
            )[index[:-1] + (slice(None),)]

        if len(index) == self.ndim or any(map(is_ellipse, index)):
            if not (is_ellipse(index[-1]) or is_none_slice(index[-1])):
                raise IndexError(f"Indexing with {index[-1]} in the irreps dimension is not supported.")

        # Support of x[index, :]
        return IrrepsArray(
            self.irreps,
            array=self.array[index],
            list=[None if x is None else x[index + (slice(None),)] for x in self.list],
        )

    def reshape(self, shape) -> "IrrepsArray":
        r"""Reshape the array.

        Args:
            shape (tuple): new shape

        Returns:
            IrrepsArray: new IrrepsArray

        Example:
            >>> IrrepsArray("2x0e + 1o", jnp.ones((6, 5))).reshape((2, 3, 5))
            2x0e+1x1o
            [[[1. 1. 1. 1. 1.]
              [1. 1. 1. 1. 1.]
              [1. 1. 1. 1. 1.]]
            <BLANKLINE>
             [[1. 1. 1. 1. 1.]
              [1. 1. 1. 1. 1.]
              [1. 1. 1. 1. 1.]]]
        """
        assert shape[-1] == self.irreps.dim or shape[-1] == -1
        shape = shape[:-1]
        list = [None if x is None else x.reshape(shape + (mul, ir.dim)) for (mul, ir), x in zip(self.irreps, self.list)]
        return IrrepsArray(irreps=self.irreps, array=self.array.reshape(shape + (self.irreps.dim,)), list=list)

    def replace_none_with_zeros(self) -> "IrrepsArray":
        r"""Replace all None in ``.list`` with zeros."""
        jnp = _infer_backend(self.array)

        list = [jnp.zeros(self.shape[:-1] + (mul, ir.dim)) if x is None else x for (mul, ir), x in zip(self.irreps, self.list)]
        return IrrepsArray(irreps=self.irreps, array=self.array, list=list)

    def remove_nones(self) -> "IrrepsArray":
        r"""Remove all None in ``.list`` and ``.irreps``."""
        if any(x is None for x in self.list):
            irreps = [mul_ir for mul_ir, x in zip(self.irreps, self.list) if x is not None]
            list = [x for x in self.list if x is not None]
            return IrrepsArray.from_list(irreps, list, self.shape[:-1])
        return self

    def simplify(self) -> "IrrepsArray":
        r"""Simplify the irreps.

        Examples:
            >>> IrrepsArray("0e + 0e + 0e", jnp.ones(3)).simplify()
            3x0e [1. 1. 1.]

            >>> IrrepsArray("0e + 0x1e + 0e", jnp.ones(2)).simplify()
            2x0e [1. 1.]
        """
        return self.convert(self.irreps.simplify())

    def unify(self) -> "IrrepsArray":
        r"""Unify the irreps.

        Example:
            >>> IrrepsArray("0e + 0x1e + 0e", jnp.ones(2)).unify()
            1x0e+0x1e+1x0e [1. 1.]
        """
        return self.convert(self.irreps.unify())

    def sorted(self) -> "IrrepsArray":
        r"""Sort the irreps.

        Example:
            >>> IrrepsArray("0e + 1o + 2x0e", jnp.arange(6)).sorted()
            1x0e+2x0e+1x1o [0 4 5 1 2 3]
        """
        irreps, p, inv = self.irreps.sort()
        return IrrepsArray.from_list(irreps, [self.list[i] for i in inv], self.shape[:-1])

    def repeat_irreps_by_last_axis(self) -> "IrrepsArray":
        r"""Repeat the irreps by the last axis of the array.

        Example:
            >>> x = IrrepsArray("0e + 1e", jnp.arange(2 * 4).reshape(2, 4))
            >>> x.repeat_irreps_by_last_axis()
            1x0e+1x1e+1x0e+1x1e [0 1 2 3 4 5 6 7]
        """
        assert len(self.shape) >= 2
        irreps = (self.shape[-2] * self.irreps).simplify()
        array = self.array.reshape(self.shape[:-2] + (irreps.dim,))
        return IrrepsArray(irreps, array)

    def repeat_mul_by_last_axis(self) -> "IrrepsArray":
        r"""Repeat the multiplicity by the last axis of the array.

        Example:
            >>> x = IrrepsArray("0e + 1e", jnp.arange(2 * 4).reshape(2, 4))
            >>> x.repeat_mul_by_last_axis()
            2x0e+2x1e [0 4 1 2 3 5 6 7]
        """
        assert len(self.shape) >= 2
        irreps = Irreps([(self.shape[-2] * mul, ir) for mul, ir in self.irreps])
        list = [None if x is None else x.reshape(self.shape[:-2] + (mul, ir.dim)) for (mul, ir), x in zip(irreps, self.list)]
        return IrrepsArray.from_list(irreps, list, self.shape[:-2])

    def factor_irreps_to_last_axis(self) -> "IrrepsArray":  # noqa: D102
        raise NotImplementedError

    def factor_mul_to_last_axis(self, factor=None) -> "IrrepsArray":
        r"""Create a new axis in the previous last position by factoring the multiplicities.

        Example:
            >>> x = IrrepsArray("6x0e + 3x1e", jnp.arange(15))
            >>> x.factor_mul_to_last_axis()
            2x0e+1x1e
            [[ 0  1  6  7  8]
             [ 2  3  9 10 11]
             [ 4  5 12 13 14]]
        """
        if factor is None:
            factor = math.gcd(*(mul for mul, _ in self.irreps))

        if not all(mul % factor == 0 for mul, _ in self.irreps):
            raise ValueError(f"factor {factor} does not divide all multiplicities")

        irreps = Irreps([(mul // factor, ir) for mul, ir in self.irreps])
        list = [
            None if x is None else x.reshape(self.shape[:-1] + (factor, mul, ir.dim))
            for (mul, ir), x in zip(irreps, self.list)
        ]
        return IrrepsArray.from_list(irreps, list, self.shape[:-1] + (factor,))

    def transform_by_angles(self, alpha: float, beta: float, gamma: float, k: int = 0) -> "IrrepsArray":
        r"""Rotate the data by angles according to the irreps.

        Args:
            alpha (float): third rotation angle around the second axis (in radians)
            beta (float): second rotation angle around the first axis (in radians)
            gamma (float): first rotation angle around the second axis (in radians)
            k (int): parity operation

        Returns:
            `IrrepsArray`: rotated data

        Example:
            >>> np.set_printoptions(precision=3, suppress=True)
            >>> x = IrrepsArray("2e", jnp.array([0.1, 0, 1.0, 1, 1]))
            >>> x.transform_by_angles(jnp.pi, 0, 0)
            1x2e [ 0.1  0.   1.  -1.   1. ]
        """
        # Optimization: we use only the list of arrays, not the array data
        D = {ir: ir.D_from_angles(alpha, beta, gamma, k) for ir in {ir for _, ir in self.irreps}}
        new_list = [
            jnp.reshape(jnp.einsum("ij,...uj->...ui", D[ir], x), self.shape[:-1] + (mul, ir.dim)) if x is not None else None
            for (mul, ir), x in zip(self.irreps, self.list)
        ]
        return IrrepsArray.from_list(self.irreps, new_list, self.shape[:-1])

    def transform_by_quaternion(self, q: jnp.ndarray, k: int = 0) -> "IrrepsArray":
        r"""Rotate data by a rotation given by a quaternion.

        Args:
            q (`jax.numpy.ndarray`): quaternion
            k (int): parity operation

        Returns:
            `IrrepsArray`: rotated data
        """
        return self.transform_by_angles(*quaternion_to_angles(q), k)

    def transform_by_axis_angle(self, axis: jnp.ndarray, angle: float, k: int = 0) -> "IrrepsArray":
        r"""Rotate data by a rotation given by an axis and an angle.

        Args:
            axis (`jax.numpy.ndarray`): axis
            angle (float): angle (in radians)
            k (int): parity operation

        Returns:
            `IrrepsArray`: rotated data
        """
        return self.transform_by_angles(*axis_angle_to_angles(axis, angle), k)

    def transform_by_matrix(self, R: jnp.ndarray) -> "IrrepsArray":
        r"""Rotate data by a rotation given by a matrix.

        Args:
            R (`jax.numpy.ndarray`): rotation matrix

        Returns:
            `IrrepsArray`: rotated data
        """
        d = jnp.sign(jnp.linalg.det(R))
        R = d[..., None, None] * R
        k = (1 - d) / 2
        return self.transform_by_angles(*matrix_to_angles(R), k)

    def convert(self, irreps: IntoIrreps) -> "IrrepsArray":
        r"""Convert the list property into an equivalent irreps.

        Args:
            irreps (Irreps): new irreps

        Returns:
            `IrrepsArray`: data with the new irreps

        Raises:
            ValueError: if the irreps are not compatible

        Example:
            >>> x = IrrepsArray.from_list("6x0e + 4x0e", [None, jnp.ones((4, 1))], ())
            >>> x.convert("5x0e + 5x0e").list
            [None, DeviceArray([[0.],
                         [1.],
                         [1.],
                         [1.],
                         [1.]], dtype=float32)]
        """
        jnp = _infer_backend(self.array)

        # Optimization: we use only the list of arrays, not the array data
        irreps = Irreps(irreps)
        assert self.irreps.simplify() == irreps.simplify(), (self.irreps, irreps)
        # TODO test cases with mul == 0

        leading_shape = self.shape[:-1]

        new_list = []
        current_array = 0

        while len(new_list) < len(irreps) and irreps[len(new_list)].mul == 0:
            new_list.append(None)

        for mul_ir, y in zip(self.irreps, self.list):
            mul, _ = mul_ir

            while mul > 0:
                if isinstance(current_array, int):
                    current_mul = current_array
                else:
                    current_mul = current_array.shape[-2]

                needed_mul = irreps[len(new_list)].mul - current_mul

                if mul <= needed_mul:
                    x = y
                    m = mul
                    mul = 0
                elif mul > needed_mul:
                    if y is None:
                        x = None
                    else:
                        x, y = jnp.split(y, [needed_mul], axis=-2)
                    m = needed_mul
                    mul -= needed_mul

                if x is None:
                    if isinstance(current_array, int):
                        current_array += m
                    else:
                        current_array = jnp.concatenate(
                            [current_array, jnp.zeros(leading_shape + (m, mul_ir.ir.dim))], axis=-2
                        )
                else:
                    if isinstance(current_array, int):
                        if current_array == 0:
                            current_array = x
                        else:
                            current_array = jnp.concatenate(
                                [jnp.zeros(leading_shape + (current_array, mul_ir.ir.dim)), x], axis=-2
                            )
                    else:
                        current_array = jnp.concatenate([current_array, x], axis=-2)

                if isinstance(current_array, int):
                    if current_array == irreps[len(new_list)].mul:
                        new_list.append(None)
                        current_array = 0
                else:
                    if current_array.shape[-2] == irreps[len(new_list)].mul:
                        new_list.append(current_array)
                        current_array = 0

                while len(new_list) < len(irreps) and irreps[len(new_list)].mul == 0:
                    new_list.append(None)

        assert current_array == 0

        assert len(new_list) == len(irreps)
        assert all(x is None or isinstance(x, jnp.ndarray) for x in new_list), [type(x) for x in new_list]
        assert all(x is None or x.shape[-2:] == (mul, ir.dim) for x, (mul, ir) in zip(new_list, irreps))

        return IrrepsArray(irreps=irreps, array=self.array, list=new_list)

    def split(self, indices: Union[List[int], List[Irreps]]) -> List["IrrepsArray"]:
        """Split the array into subarrays.

        Examples:
            >>> IrrepsArray("0e + 1e", jnp.array([1.0, 2, 3, 4])).split(["0e", "1e"])
            [1x0e [1.], 1x1e [2. 3. 4.]]

            >>> IrrepsArray("0e + 1e", jnp.array([1.0, 2, 3, 4])).split([1])
            [1x0e [1.], 1x1e [2. 3. 4.]]
        """
        jnp = _infer_backend(self.array)

        if all(isinstance(i, int) for i in indices):
            array_parts = jnp.split(self.array, [self.irreps[:i].dim for i in indices], axis=-1)
            assert len(array_parts) == len(indices) + 1
            return [
                IrrepsArray(irreps=self.irreps[i:j], array=array, list=self.list[i:j])
                for (i, j), array in zip(zip([0] + indices, indices + [len(self.irreps)]), array_parts)
            ]

        irrepss = [Irreps(i) for i in indices]
        assert self.irreps.simplify() == sum(irrepss, Irreps()).simplify()
        x = self
        array_parts = []
        for irreps in irrepss:
            array_parts.append(x[..., : irreps.dim])
            x = x[..., irreps.dim :]
        return array_parts

    def broadcast_to(self, shape) -> "IrrepsArray":
        """Broadcast the array to a new shape."""
        jnp = _infer_backend(self.array)

        assert isinstance(shape, tuple)
        assert shape[-1] == self.irreps.dim or shape[-1] == -1
        leading_shape = shape[:-1]
        array = jnp.broadcast_to(self.array, leading_shape + (self.irreps.dim,))
        list = [
            None if x is None else jnp.broadcast_to(x, leading_shape + (mul, ir.dim))
            for (mul, ir), x in zip(self.irreps, self.list)
        ]
        return IrrepsArray(irreps=self.irreps, array=array, list=list)

    @staticmethod
    def cat(args, axis=-1) -> "IrrepsArray":  # noqa: D102
        warnings.warn("IrrepsArray.cat is deprecated, use e3nn.concatenate instead", DeprecationWarning)
        return concatenate(args, axis=axis)

    @staticmethod
    def randn(irreps, key, leading_shape=(), *, normalization=None):  # noqa: D102
        warnings.warn("IrrepsArray.randn is deprecated, use e3nn.normal instead", DeprecationWarning)
        return normal(irreps, key, leading_shape=leading_shape, normalization=normalization)


jax.tree_util.register_pytree_node(
    IrrepsArray,
    lambda x: ((x.array, x.list), x.irreps),
    lambda x, data: IrrepsArray(irreps=x, array=data[0], list=data[1], _perform_checks=False),
)


def _standardize_axis(axis: Union[None, int, Tuple[int, ...]], ndim: int) -> Tuple[int, ...]:
    if axis is None:
        return tuple(range(ndim))
    try:
        axis = (operator.index(axis),)
    except TypeError:
        axis = tuple(operator.index(i) for i in axis)

    if not all(-ndim <= i < ndim for i in axis):
        raise ValueError("axis out of range")
    axis = tuple(i % ndim for i in axis)

    return tuple(sorted(set(axis)))


def _reduce(op, array: IrrepsArray, axis: Union[None, int, Tuple[int, ...]] = None, keepdims: bool = False) -> IrrepsArray:
    axis = _standardize_axis(axis, array.ndim)

    if axis == ():
        return array

    if axis[-1] < array.ndim - 1:
        # irrep dimension is not affected by mean
        return IrrepsArray(
            array.irreps,
            op(array.array, axis=axis, keepdims=keepdims),
            [None if x is None else op(x, axis=axis, keepdims=keepdims) for x in array.list],
        )

    array = _reduce(op, array, axis=axis[:-1], keepdims=keepdims)
    return IrrepsArray.from_list(
        Irreps([(1, ir) for _, ir in array.irreps]),
        [None if x is None else op(x, axis=-2, keepdims=True) for x in array.list],
        array.shape[:-1],
    )


def mean(array: IrrepsArray, axis: Union[None, int, Tuple[int, ...]] = None, keepdims: bool = False) -> IrrepsArray:
    """Mean of IrrepsArray along the specified axis.

    Args:
        array (`IrrepsArray`): input array
        axis (optional int or tuple of ints): axis along which the mean is computed.

    Returns:
        `IrrepsArray`: mean of the input array

    Examples:
        >>> x = e3nn.IrrepsArray("3x0e + 2x0e", jnp.arange(2 * 5).reshape(2, 5))
        >>> e3nn.mean(x, axis=0)
        3x0e+2x0e [2.5 3.5 4.5 5.5 6.5]
        >>> e3nn.mean(x, axis=1)
        1x0e+1x0e
        [[1.  3.5]
         [6.  8.5]]
        >>> e3nn.mean(x)
        1x0e+1x0e [3.5 6. ]
    """
    jnp = _infer_backend(array.array)
    return _reduce(jnp.mean, array, axis, keepdims)


def sum_(array: IrrepsArray, axis: Union[None, int, Tuple[int, ...]] = None, keepdims: bool = False) -> IrrepsArray:
    """Sum of IrrepsArray along the specified axis.

    Args:
        array (`IrrepsArray`): input array
        axis (optional int or tuple of ints): axis along which the sum is computed.

    Returns:
        `IrrepsArray`: sum of the input array

    Examples:
        >>> x = e3nn.IrrepsArray("3x0e + 2x0e", jnp.arange(2 * 5).reshape(2, 5))
        >>> e3nn.sum(x, axis=0)
        3x0e+2x0e [ 5  7  9 11 13]
        >>> e3nn.sum(x, axis=1)
        1x0e+1x0e
        [[ 3  7]
         [18 17]]
        >>> e3nn.sum(x)
        1x0e+1x0e [21 24]
    """
    jnp = _infer_backend(array.array)
    return _reduce(jnp.sum, array, axis, keepdims)


def concatenate(arrays: List[IrrepsArray], axis: int = -1) -> IrrepsArray:
    r"""Concatenate a list of IrrepsArray.

    Args:
        arrays (list of `IrrepsArray`): list of data to concatenate
        axis (int): axis to concatenate on

    Returns:
        `IrrepsArray`: concatenated array

    Examples:
        >>> x = e3nn.IrrepsArray("3x0e + 2x0o", jnp.arange(2 * 5).reshape(2, 5))
        >>> y = e3nn.IrrepsArray("3x0e + 2x0o", jnp.arange(2 * 5).reshape(2, 5) + 10)
        >>> e3nn.concatenate([x, y], axis=0)
        3x0e+2x0o
        [[ 0  1  2  3  4]
         [ 5  6  7  8  9]
         [10 11 12 13 14]
         [15 16 17 18 19]]
        >>> e3nn.concatenate([x, y], axis=1)
        3x0e+2x0o+3x0e+2x0o
        [[ 0  1  2  3  4 10 11 12 13 14]
         [ 5  6  7  8  9 15 16 17 18 19]]
    """
    if len(arrays) == 0:
        raise ValueError("Cannot concatenate empty list of IrrepsArray")

    axis = _standardize_axis(axis, arrays[0].ndim)[0]

    jnp = _infer_backend([x.array for x in arrays])

    if axis == arrays[0].ndim - 1:
        irreps = Irreps(sum([x.irreps for x in arrays], Irreps("")))
        return IrrepsArray(
            irreps=irreps,
            array=jnp.concatenate([x.array for x in arrays], axis=-1),
            list=sum([x.list for x in arrays], []),
        )

    if {x.irreps for x in arrays} != {arrays[0].irreps}:
        raise ValueError("Irreps must be the same for all arrays")

    arrays = [x.replace_none_with_zeros() for x in arrays]  # TODO this could be optimized
    return IrrepsArray(
        irreps=arrays[0].irreps,
        array=jnp.concatenate([x.array for x in arrays], axis=axis),
        list=[jnp.concatenate(xs, axis=axis) for xs in zip(*[x.list for x in arrays])],
    )


def stack(arrays: List[IrrepsArray], axis=0) -> IrrepsArray:
    r"""Stack a list of IrrepsArray.

    Args:
        arrays (list of `IrrepsArray`): list of data to stack
        axis (int): axis to stack on

    Returns:
        `IrrepsArray`: stacked array

    Examples:
        >>> x = e3nn.IrrepsArray("3x0e + 2x0o", jnp.arange(2 * 5).reshape(2, 5))
        >>> y = e3nn.IrrepsArray("3x0e + 2x0o", jnp.arange(2 * 5).reshape(2, 5) + 10)
        >>> e3nn.stack([x, y], axis=0)
        3x0e+2x0o
        [[[ 0  1  2  3  4]
          [ 5  6  7  8  9]]
        <BLANKLINE>
         [[10 11 12 13 14]
          [15 16 17 18 19]]]
        >>> e3nn.stack([x, y], axis=1)
        3x0e+2x0o
        [[[ 0  1  2  3  4]
          [10 11 12 13 14]]
        <BLANKLINE>
         [[ 5  6  7  8  9]
          [15 16 17 18 19]]]
    """
    if len(arrays) == 0:
        raise ValueError("Cannot stack empty list of IrrepsArray")

    result_ndim = arrays[0].ndim + 1
    axis = _standardize_axis(axis, result_ndim)[0]

    jnp = _infer_backend([x.array for x in arrays])

    if axis == result_ndim - 1:
        raise ValueError(
            "IrrepsArray cannot be stacked on the last axis because the last axis is reserved for the irreps dimension"
        )

    if {x.irreps for x in arrays} != {arrays[0].irreps}:
        raise ValueError("Irreps must be the same for all arrays")

    arrays = [x.replace_none_with_zeros() for x in arrays]  # TODO this could be optimized
    return IrrepsArray(
        irreps=arrays[0].irreps,
        array=jnp.stack([x.array for x in arrays], axis=axis),
        list=[jnp.stack(xs, axis=axis) for xs in zip(*[x.list for x in arrays])],
    )


def norm(array: IrrepsArray, *, squared: bool = False) -> IrrepsArray:
    """Norm of IrrepsArray.

    Args:
        array (IrrepsArray): input array
        squared (bool): if True, return the squared norm

    Returns:
        IrrepsArray: norm of the input array

    Example:
        >>> x = e3nn.IrrepsArray("2x0e + 1e + 2e", jnp.arange(10))
        >>> e3nn.norm(x)
        2x0e+1x0e+1x0e [ 0.     1.     5.385 15.969]
    """
    jnp = _infer_backend(array.array)

    def f(x):
        x = jnp.sum(x**2, axis=-1, keepdims=True)
        if not squared:
            x = jnp.sqrt(x)
        return x

    return IrrepsArray.from_list(
        [(mul, "0e") for mul, _ in array.irreps],
        [f(x) for x in array.list],
        array.shape[:-1],
    )


def normal(
    irreps: IntoIrreps, key: jnp.ndarray, leading_shape: Tuple[int, ...] = (), *, normalization: Optional[str] = None
) -> IrrepsArray:
    r"""Random array with normal distribution.

    Args:
        irreps (Irreps): irreps of the output array
        key (jnp.ndarray): random key
        leading_shape (tuple of int): shape of the leading dimensions
        normalization (str): normalization of the output array, ``"component"`` or ``"norm"``

    Returns:
        IrrepsArray: random array

    Examples:
        >>> np.set_printoptions(precision=2, suppress=True)

        Generate a random array with normalization ``"component"``

        >>> x = e3nn.normal("0e + 5e", jax.random.PRNGKey(0), (), normalization="component")
        >>> x
        1x0e+1x5e [ 1.19 -1.1   0.44  0.6  -0.39  0.69  0.46 -2.07 -0.21 -0.99 -0.68  0.27]
        >>> e3nn.norm(x, squared=True)
        1x0e+1x0e [1.42 8.45]

        Generate a random array with normalization ``"norm"``

        >>> x = e3nn.normal("0e + 5e", jax.random.PRNGKey(0), (), normalization="norm")
        >>> x
        1x0e+1x5e [-1.    0.12 -0.26 -0.43  0.4   0.08  0.16 -0.41  0.37 -0.44  0.03 -0.19]
        >>> e3nn.norm(x, squared=True)
        1x0e+1x0e [1. 1.]
    """
    irreps = Irreps(irreps)

    if normalization is None:
        normalization = config("irrep_normalization")

    if normalization == "component":
        return IrrepsArray(irreps, jax.random.normal(key, leading_shape + (irreps.dim,)))
    elif normalization == "norm":
        list = []
        for mul, ir in irreps:
            key, k = jax.random.split(key)
            r = jax.random.normal(k, leading_shape + (mul, ir.dim))
            r = r / jnp.linalg.norm(r, axis=-1, keepdims=True)
            list.append(r)
        return IrrepsArray.from_list(irreps, list, leading_shape)
    else:
        raise ValueError("Normalization needs to be 'norm' or 'component'")