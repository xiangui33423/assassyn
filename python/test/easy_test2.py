"""Grid logger hardware design for Easy Test 2.

This module set demonstrates a modular approach to generating a 2-D index
traversal in hardware with the Assassyn frontend. Two counter modules expose
index values through asynchronous calls, and a dedicated logger consumes the
indices so that logging happens in the simulated hardware timeline. The driver
module orchestrates the counters, forwards the current indices to the logger,
and terminates the simulation exactly after logging the final pair.
"""

from __future__ import annotations

import argparse

from assassyn.frontend import (
    Array,
    Bits,
    Condition,
    Module,
    Port,
    RegArray,
    UInt,
    finish,
    log,
    module,
)
from assassyn.frontend import SysBuilder
from assassyn.backend import elaborate, config
from assassyn import utils

_UINT_WIDTH = 32


class Counter(Module):
    """Generic counter that advances when triggered and exposes its value."""

    def __init__(self, limit: int, *, wrap: bool) -> None:
        if limit < 0:
            raise ValueError("Counter limit must be a non-negative integer")
        super().__init__(ports={
            'step': Port(Bits(1)),
        })
        self._limit = limit
        self._wrap = wrap

    @module.combinational
    def build(self, state: Array) -> None:
        step = self.pop_all_ports(True)
        current = state[0]
        maximum = UInt(_UINT_WIDTH)(self._limit)
        wrap_flag = Bits(1)(1) if self._wrap else Bits(1)(0)

        is_step = step == Bits(1)(1)
        with Condition(is_step):
            is_max = current == maximum
            with Condition(~is_max):
                (state & self)[0] <= current + UInt(_UINT_WIDTH)(1)
            with Condition(is_max & wrap_flag):
                (state & self)[0] <= UInt(_UINT_WIDTH)(0)


class GridLogger(Module):
    """Consume the current indices and emit simulator logs."""

    def __init__(self) -> None:
        super().__init__(ports={
            'i': Port(UInt(_UINT_WIDTH)),
            'j': Port(UInt(_UINT_WIDTH)),
        })

    @module.combinational
    def build(self) -> None:
        i_val, j_val = self.pop_all_ports(True)
        log('[Driver] i={}, j={}', i_val, j_val)


class Driver(Module):
    """Coordinate the counters and terminate the simulation on the last pair."""

    def __init__(self, n: int, m: int) -> None:
        if n < 0 or m < 0:
            raise ValueError("n and m must be non-negative integers")
        super().__init__(ports={})
        self._n = n
        self._m = m

    @module.combinational
    def build(
        self,
        row_counter: Counter,
        col_counter: Counter,
        row_state: Array,
        col_state: Array,
        logger: GridLogger,
    ) -> None:
        log_row = RegArray(UInt(_UINT_WIDTH), 1)
        log_col = RegArray(UInt(_UINT_WIDTH), 1)
        started = RegArray(Bits(1), 1)

        current_row = log_row[0]
        current_col = log_col[0]
        started_flag = started[0]

        with Condition(started_flag):
            logger.async_called(i=current_row, j=current_col)

        (log_row & self)[0] <= row_state[0]
        (log_col & self)[0] <= col_state[0]
        log('[Driver-debug] row_state={}, col_state={}', row_state[0], col_state[0])
        (started & self)[0] <= Bits(1)(1)

        max_i = UInt(_UINT_WIDTH)(self._n)
        max_j = UInt(_UINT_WIDTH)(self._m)
        last_row_logged = current_row == max_i
        last_col_logged = current_col == max_j

        col_counter.async_called(step=Bits(1)(1))
        row_step = started_flag.select(last_col_logged, Bits(1)(0))
        row_counter.async_called(step=row_step)

        is_last_pair = started_flag & last_row_logged & last_col_logged
        with Condition(is_last_pair):
            finish()


def build_system(n: int, m: int) -> SysBuilder:
    """Construct the Assassyn system made of counters, logger, and driver."""

    sys = SysBuilder('easy_test2')
    with sys:
        row_state = RegArray(UInt(_UINT_WIDTH), 1)
        col_state = RegArray(UInt(_UINT_WIDTH), 1)

        row_counter = Counter(n, wrap=False)
        row_counter.build(row_state)

        col_counter = Counter(m, wrap=True)
        col_counter.build(col_state)

        logger = GridLogger()
        logger.build()

        driver = Driver(n, m)
        driver.build(row_counter, col_counter, row_state, col_state, logger)

    return sys


def simulate(
    n: int,
    m: int,
    *,
    show_full_log: bool = False,
    run_verilator: bool = False,
) -> str:
    """Elaborate and simulate the system, returning the primary simulator log."""

    sys = build_system(n, m)
    total_pairs = (n + 1) * (m + 1)

    cfg = config()
    cfg.update({
        'sim_threshold': total_pairs + 5,
        'idle_threshold': total_pairs + 5,
        'verilog': run_verilator and utils.has_verilator(),
    })

    simulator_manifest, verilator_root = elaborate(sys, **cfg)
    raw = utils.run_simulator(simulator_manifest)

    lines = raw.splitlines()
    if show_full_log:
        print('\n'.join(lines))
    else:
        filtered = [line for line in lines if '[Driver]' in line]
        print('\n'.join(filtered))

    if run_verilator and cfg['verilog'] and verilator_root is not None:
        utils.run_verilator(verilator_root)
    elif run_verilator and not utils.has_verilator():
        print('Verilator is not available; skipped Verilog co-simulation.')

    return raw


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run the Easy Test 2 grid logger')
    parser.add_argument('n', type=int, help='Upper bound for the i index (inclusive)')
    parser.add_argument('m', type=int, help='Upper bound for the j index (inclusive)')
    parser.add_argument('--full-log', action='store_true', help='Print the entire simulator output')
    parser.add_argument('--verilog', action='store_true', help='Also run the generated Verilog with Verilator when available')
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    simulate(args.n, args.m, show_full_log=args.full_log, run_verilator=args.verilog)


if __name__ == '__main__':  # pragma: no cover - CLI entry point
    main()
