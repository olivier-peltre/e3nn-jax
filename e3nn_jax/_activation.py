from functools import lru_cache

import jax
import jax.numpy as jnp

from e3nn_jax import Irreps


@lru_cache(maxsize=None)
def normalize_act(phi):
    k = jax.random.PRNGKey(0)
    x = jax.random.normal(k, (1_000_000,))
    c = jnp.mean(phi(x)**2)**0.5

    def rho(x):
        return phi(x) / c
    return rho


@lru_cache(maxsize=None)
def parity_act(phi):
    x = jnp.linspace(0.0, 10.0, 256)

    a1, a2 = phi(x), phi(-x)
    if jnp.max(jnp.abs(a1 - a2)) < 1e-5:
        return 1
    elif jnp.max(jnp.abs(a1 + a2)) < 1e-5:
        return -1
    else:
        return 0


for phi in [jax.nn.gelu, jnp.abs, jnp.tanh, jax.nn.sigmoid, jax.nn.silu, jax.nn.relu]:
    normalize_act(phi)
    parity_act(phi)


class Activation:
    irreps_in: Irreps
    irreps_out: Irreps

    def __init__(self, irreps_in, acts):
        irreps_in = Irreps(irreps_in)
        assert len(irreps_in) == len(acts), (irreps_in, acts)

        irreps_out = []
        for (mul, (l_in, p_in)), act in zip(irreps_in, acts):
            if act is not None:
                if l_in != 0:
                    raise ValueError("Activation: cannot apply an activation function to a non-scalar input.")

                p_out = parity_act(act) if p_in == -1 else p_in
                irreps_out.append((mul, (0, p_out)))

                if p_out == 0:
                    raise ValueError("Activation: the parity is violated! The input scalar is odd but the activation is neither even nor odd.")
            else:
                irreps_out.append((mul, (l_in, p_in)))

        # normalize the second moment
        acts = [normalize_act(act) if act is not None else None for act in acts]

        self.irreps_in = irreps_in
        self.irreps_out = Irreps(irreps_out)
        self.acts = acts

    def __call__(self, features):
        output = []
        index = 0
        for (mul, ir), act in zip(self.irreps_in, self.acts):
            if act is not None:
                output.append(act(features[..., index: index + mul]))
            else:
                output.append(features[..., index: index + mul * ir.dim])
            index += mul * ir.dim

        if len(output) > 1:
            return jnp.concatenate(output, axis=-1)
        elif len(output) == 1:
            return output[0]
        else:
            return jnp.zeros_like(features)
