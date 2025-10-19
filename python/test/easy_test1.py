"""Grid logger hardware module for Easy Test 1.

This module demonstrates building a small hardware design with Assassyn.
It iterates through all `(i, j)` pairs in the inclusive ranges `[0, n]` and
`[0, m]`, logging each pair through the simulator `log` primitive. The design
uses internal registers to hold the loop indices and terminates the simulation
with `finish()` at the final pair.
"""

from __future__ import annotations

import argparse

from assassyn.frontend import (
    Condition,
    Module,
    RegArray,
    UInt,
    finish,
    log,
    module,
)
from assassyn.frontend import SysBuilder
from assassyn.backend import elaborate, config
from assassyn import utils


class Driver(Module):
    """Special driver module that walks the grid and logs every `(i, j)` pair."""

    def __init__(self, n: int, m: int) -> None:
        super().__init__(ports={})
        if n < 0 or m < 0:
            raise ValueError("n and m must be non-negative integers")
        self._n = n
        self._m = m

    @module.combinational
    def build(self) -> None:
        """Emit all index pairs up to the configured maxima."""

        i_reg = RegArray(UInt(32), 1)
        j_reg = RegArray(UInt(32), 1)

        current_i = i_reg[0]
        current_j = j_reg[0]

        max_i = UInt(32)(self._n)
        max_j = UInt(32)(self._m)

        log('[Driver] i={}, j={}', current_i, current_j)

        is_last_i = current_i == max_i
        is_last_j = current_j == max_j
        is_last_pair = is_last_i & is_last_j

        with Condition(~is_last_pair):
            with Condition(is_last_j):
                (i_reg & self)[0] <= current_i + UInt(32)(1)
                (j_reg & self)[0] <= UInt(32)(0)
            with Condition(~is_last_j):
                (j_reg & self)[0] <= current_j + UInt(32)(1)

        with Condition(is_last_pair):
            finish()


def build_system(n: int, m: int) -> SysBuilder:
    """Construct the Assassyn system that wraps the grid driver."""

    sys = SysBuilder('easy_test1')
    with sys:
        Driver(n, m).build()
    return sys


def simulate(n: int, m: int, *, show_full_log: bool = False) -> str:
    """Elaborate and simulate the system, returning the simulator output."""

    sys = build_system(n, m)
    expected_cycles = (n + 1) * (m + 1)

    cfg = config()
    cfg.update({
        'sim_threshold': expected_cycles + 5,
        'idle_threshold': expected_cycles + 5,
        'verilog': False,
    })

    simulator_manifest, _ = elaborate(sys, **cfg)
    raw = utils.run_simulator(simulator_manifest)

    if show_full_log:
        print(raw.rstrip())
    else:
        filtered = [
            line for line in raw.splitlines()
            if '[Driver]' in line
        ]
        print('\n'.join(filtered))

    return raw


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run the Easy Test 1 grid logger')
    parser.add_argument('n', type=int, help='Upper bound for the i index (inclusive)')
    parser.add_argument('m', type=int, help='Upper bound for the j index (inclusive)')
    parser.add_argument('--full-log', action='store_true', help='Print the entire simulator output')
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    simulate(args.n, args.m, show_full_log=args.full_log)


if __name__ == '__main__':  # pragma: no cover - CLI entry point
    main()
