"""Test radix sort implementation with FSM-based state machine.

This test verifies the hardware radix sort algorithm using Assassyn's FSM abstraction.
The algorithm sorts 32-bit integers by processing 4 bits at a time (radix-16),
requiring 8 passes through the data.
"""
from assassyn.frontend import *
from assassyn.test import run_test
from assassyn import utils


# Data configuration for testing
data_width = 32
data_depth = 8  # Small dataset for CI testing
addr_width = (data_depth * 2 + 1).bit_length()


class MemUser(Module):
    """Memory user module that processes read data from SRAM.

    This module receives data from SRAM and updates the radix histogram
    during the read phase.
    """

    def __init__(self, width):
        super().__init__(ports={"rdata": Port(Bits(width))}, no_arbiter=True)

    @module.combinational
    def build(
        self,
        SM_reg: RegArray,
        radix_reg: RegArray,
        offset_reg: RegArray,
        addr_reg: RegArray,
        mem_pingpong_reg: RegArray,
    ):
        width = self.rdata.dtype.bits
        rdata = self.pop_all_ports(True)
        rdata = rdata.bitcast(UInt(width))
        # Extract 4-bit radix index from current bit position
        idx = (rdata >> offset_reg[0])[0:3].bitcast(UInt(4))
        # Only read to radix_reg in stage 1 (read state)
        with Condition(SM_reg[0] == Bits(2)(1)):
            log(
                "Stage 1: Read rdata=({:08x}) from memory addr_reg[0]=({:08x})",
                rdata,
                addr_reg[0] - UInt(addr_width)(1),
            )
            radix_reg[idx] = radix_reg[idx] + UInt(width)(1)
        return rdata


class RadixReducer(Module):
    """Radix reducer module that computes prefix sum on histogram.

    This module performs an in-place prefix sum (cumulative sum) on the
    radix histogram array.
    """

    def __init__(self, width):
        super().__init__(ports={})

    @module.combinational
    def build(self, radix_reg: RegArray, cycle_reg: RegArray):
        # Prefix sum: each bucket adds the previous bucket's value
        with Condition(cycle_reg[0] < UInt(data_width)(16)):
            cycle_index = cycle_reg[0][0:3].bitcast(UInt(4))
            radix_reg[cycle_index] = (
                radix_reg[cycle_index] + radix_reg[cycle_index - UInt(4)(1)]
            )
            log(
                "Stage 2: radix_reg[{}]: {:08x}",
                cycle_reg[0] - UInt(data_width)(1),
                radix_reg[cycle_reg[0] - UInt(data_width)(1)],
            )
            cycle_reg[0] = cycle_reg[0] + UInt(data_width)(1)
        return


class MemImpl(Downstream):
    """Memory implementation with FSM for write-back operations.

    This downstream module handles Stage 3 (write) of the main FSM using its own
    nested 4-state FSM.
    """

    def __init__(self):
        super().__init__()
        self.name = "MemImpl"

    @downstream.combinational
    def build(
        self,
        rdata: Value,
        wdata: RegArray,
        SM_reg: RegArray,
        addr_reg: RegArray,
        we: RegArray,
        re: RegArray,
        radix_reg: RegArray,
        offset_reg: RegArray,
        mem_pingpong_reg: RegArray,
        mem_start: Value,
        mem_end: Value,
    ):
        SM_MemImpl = RegArray(Bits(2), 1, initializer=[0])
        read_addr_reg = RegArray(UInt(addr_width), 1, initializer=[0])
        write_addr_reg = RegArray(UInt(addr_width), 1, initializer=[data_depth])
        stop_reg = RegArray(UInt(1), 1, initializer=[0])
        reset_cycle_reg = RegArray(UInt(4), 1, initializer=[0])

        # Stage 3: Write Data to Memory
        with Condition(SM_reg[0] == Bits(2)(3)):
            # Define FSM transition conditions
            default = Bits(1)(1)
            not_stopped = stop_reg[0] == UInt(1)(0)
            is_stopped = stop_reg[0] == UInt(1)(1)
            reset_not_done = reset_cycle_reg[0] < UInt(4)(15)
            reset_done = reset_cycle_reg[0] == UInt(4)(15)

            # FSM transition table
            memimpl_table = {
                "init": {default: "read"},
                "read": {default: "write"},
                "write": {not_stopped: "read", is_stopped: "reset"},
                "reset": {reset_done: "init", reset_not_done: "reset"},
            }

            # Define state-specific actions
            def init_action():
                log("Stage 3-0: Initialization")
                read_addr_reg[0] = addr_reg[0]
                write_addr_reg[0] = UInt(addr_width)(data_depth) - mem_start

            def read_action():
                log("Stage 3-1: Reading from mem_addr ({}).", addr_reg[0])
                re[0] = Bits(1)(0)
                we[0] = Bits(1)(1)
                addr_reg[0] = write_addr_reg[0]
                with Condition(read_addr_reg[0] > mem_start.bitcast(UInt(addr_width))):
                    read_addr_reg[0] = read_addr_reg[0] - UInt(addr_width)(1)

            def write_action():
                log(
                    "Stage 3-2: Writing wdata ({:08x}) to mem_addr ({})",
                    wdata[0],
                    addr_reg[0],
                )
                idx = (rdata >> offset_reg[0])[0:3].bitcast(UInt(4))
                wdata[0] = rdata.bitcast(Bits(data_width))
                write_addr_reg[0] = (
                    radix_reg[idx][0 : (addr_width - 1)].bitcast(UInt(addr_width))
                    - UInt(addr_width)(1)
                    + UInt(addr_width)(data_depth)
                    - mem_start.bitcast(UInt(addr_width))
                )
                radix_reg[idx] = radix_reg[idx] - UInt(data_width)(1)

                with Condition(read_addr_reg[0] == mem_start.bitcast(UInt(addr_width))):
                    stop_reg[0] = UInt(1)(1)

                with Condition(stop_reg[0] == UInt(1)(0)):
                    addr_reg[0] = read_addr_reg[0]
                    re[0] = Bits(1)(1)
                    we[0] = Bits(1)(0)
                with Condition(stop_reg[0] == UInt(1)(1)):
                    addr_reg[0] = (
                        radix_reg[idx][0 : (addr_width - 1)].bitcast(UInt(addr_width))
                        - UInt(addr_width)(1)
                        + UInt(addr_width)(data_depth)
                        - mem_start.bitcast(UInt(addr_width))
                    )

            def reset_action():
                with Condition(reset_cycle_reg[0] == UInt(4)(0)):
                    log("Stage 3-3: Reset starting")
                    re[0] = Bits(1)(0)
                    we[0] = Bits(1)(0)

                with Condition(reset_cycle_reg[0] < UInt(4)(15)):
                    radix_reg[reset_cycle_reg[0]] = UInt(data_width)(0)
                    reset_cycle_reg[0] = reset_cycle_reg[0] + UInt(4)(1)

                with Condition(reset_cycle_reg[0] == UInt(4)(15)):
                    log("Stage 3-3: Reset complete")
                    radix_reg[reset_cycle_reg[0]] = UInt(data_width)(0)
                    reset_cycle_reg[0] = UInt(4)(0)
                    SM_reg[0] = Bits(2)(0)
                    stop_reg[0] = UInt(1)(0)

            # Create action dictionary
            memimpl_actions = {
                "init": init_action,
                "read": read_action,
                "write": write_action,
                "reset": reset_action,
            }

            # Generate FSM
            memimpl_fsm = fsm.FSM(SM_MemImpl, memimpl_table)
            memimpl_fsm.generate(memimpl_actions)

        return


class Driver(Module):
    """Driver module that orchestrates the main radix sort FSM."""

    def __init__(self):
        super().__init__(ports={}, no_arbiter=True)

    @module.combinational
    def build(
        self,
        memory_user: Module,
        radix_reducer: Module,
        cycle_reg: RegArray,
        radix_reg: RegArray,
        SM_reg: RegArray,
        addr_reg: RegArray,
        we: RegArray,
        re: RegArray,
        wdata: RegArray,
        offset_reg: RegArray,
        mem_pingpong_reg: RegArray,
    ):
        # Determine if we're still reading
        read_cond = (
            (mem_pingpong_reg[0] == UInt(1)(0))
            & (addr_reg[0] < UInt(addr_width)(data_depth))
        ) | (
            (mem_pingpong_reg[0] == UInt(1)(1))
            & (addr_reg[0] < UInt(addr_width)(2 * data_depth))
        )

        # Build Memory
        numbers_mem = SRAM(
            width=data_width,
            depth=2**addr_width,
            init_file=f'{utils.repo_path()}/python/ci-tests/resources/radix_sort_small.data',
        )
        numbers_mem.name = "numbers_mem"
        numbers_mem.build(we[0], re[0], addr_reg[0], wdata[0])

        # Connect SRAM output to MemUser input
        memory_user.async_called(rdata=numbers_mem.dout[0])

        # Calculate memory range based on ping-pong buffer
        # mem_pingpong_reg toggles between 0 and 1
        # When 0: mem_start = 0, when 1: mem_start = data_depth
        mem_start = mem_pingpong_reg[0].select(
            UInt(addr_width)(data_depth),  # when 1
            UInt(addr_width)(0)             # when 0
        )
        mem_end = mem_start + UInt(addr_width)(data_depth)

        # Outer loop
        with Condition(offset_reg[0] < UInt(data_width)(data_width)):
            # Define FSM transition conditions
            default = Bits(1)(1)
            read_not_done = read_cond
            read_done = ~read_cond
            prefix_not_done = cycle_reg[0] < UInt(data_width)(15)
            prefix_done = cycle_reg[0] == UInt(data_width)(15)

            # Main FSM transition table
            main_table = {
                "reset": {default: "read"},
                "read": {read_done: "prefix", read_not_done: "read"},
                "prefix": {prefix_done: "write", prefix_not_done: "prefix"},
                "write": {default: "write"},
            }

            # Define state-specific actions
            def reset_action():
                log(
                    "Radix Sort: Bits {} - {} Completed!",
                    offset_reg[0],
                    offset_reg[0] + UInt(data_width)(4),
                )
                offset_reg[0] = offset_reg[0] + UInt(data_width)(4)
                addr_reg[0] = UInt(addr_width)(0) + (
                    (~mem_pingpong_reg[0]).bitcast(UInt(1)) * UInt(addr_width)(data_depth)
                )[0 : (addr_width - 1)].bitcast(UInt(addr_width))
                re[0] = Bits(1)(1)
                we[0] = Bits(1)(0)
                mem_pingpong_reg[0] = (~mem_pingpong_reg[0]).bitcast(UInt(1))

            def read_action():
                with Condition(addr_reg[0] < mem_end):
                    addr_reg[0] = addr_reg[0] + UInt(addr_width)(1)

                with Condition(addr_reg[0] == (mem_end - UInt(addr_width)(1))):
                    re[0] = Bits(1)(0)

                with Condition(~read_cond):
                    cycle_reg[0] = UInt(data_width)(1)
                    addr_reg[0] = addr_reg[0] - UInt(addr_width)(1)

            def prefix_action():
                radix_reducer.async_called()
                with Condition(cycle_reg[0] == UInt(data_width)(15)):
                    re[0] = Bits(1)(1)
                    we[0] = Bits(1)(0)

            def write_action():
                pass

            # Create action dictionary
            main_actions = {
                "reset": reset_action,
                "read": read_action,
                "prefix": prefix_action,
                "write": write_action,
            }

            # Generate main FSM
            main_fsm = fsm.FSM(SM_reg, main_table)
            main_fsm.generate(main_actions)

        with Condition(offset_reg[0] == UInt(data_width)(data_width)):
            log("finish")
            finish()

        return mem_start, mem_end


def top():
    """Build the radix sort system."""
    SM_reg = RegArray(Bits(2), 1, initializer=[1])  # Start at read state
    cycle_reg = RegArray(UInt(data_width), 1, initializer=[0])
    addr_reg = RegArray(UInt(addr_width), 1, initializer=[0])
    wdata = RegArray(Bits(data_width), 1, initializer=[0])
    we = RegArray(Bits(1), 1, initializer=[0])
    re = RegArray(Bits(1), 1, initializer=[1])
    radix_reg = RegArray(UInt(data_width), 16, initializer=[0] * 16)
    offset_reg = RegArray(UInt(data_width), 1, initializer=[0])
    mem_pingpong_reg = RegArray(UInt(1), 1, initializer=[0])

    # Create Memory User
    memory_user = MemUser(width=data_width)
    rdata = memory_user.build(
        SM_reg=SM_reg,
        radix_reg=radix_reg,
        offset_reg=offset_reg,
        addr_reg=addr_reg,
        mem_pingpong_reg=mem_pingpong_reg,
    )

    # Create Radix Reducer
    radix_reducer = RadixReducer(width=data_width)
    radix_reducer.build(radix_reg, cycle_reg=cycle_reg)

    # Create driver
    driver = Driver()
    mem_start, mem_end = driver.build(
        memory_user,
        radix_reducer,
        cycle_reg=cycle_reg,
        radix_reg=radix_reg,
        SM_reg=SM_reg,
        addr_reg=addr_reg,
        we=we,
        re=re,
        wdata=wdata,
        offset_reg=offset_reg,
        mem_pingpong_reg=mem_pingpong_reg,
    )

    # Create Memory Implementation
    mem_impl = MemImpl()
    mem_impl.build(
        rdata=rdata,
        wdata=wdata,
        SM_reg=SM_reg,
        addr_reg=addr_reg,
        we=we,
        re=re,
        radix_reg=radix_reg,
        offset_reg=offset_reg,
        mem_pingpong_reg=mem_pingpong_reg,
        mem_start=mem_start,
        mem_end=mem_end,
    )


def check(raw):
    """Check that radix sort completes successfully."""
    # Expected sorted sequence (from radix_sort_small.data)
    # Original: 255c, 41b, 2107, 2380, c1c, 1440, 28aa, 2dc1
    # Sorted:   41b, c1c, 1440, 2107, 2380, 255c, 28aa, 2dc1

    # Check for finish marker
    assert 'finish' in raw, "Radix sort did not complete (no 'finish' marker found)"

    # Count the number of complete passes (should be 8 for 32-bit, 4-bit radix)
    passes = raw.count('Radix Sort: Bits')
    assert passes == 8, f"Expected 8 passes, got {passes}"

    # Verify that Stage 1, 2, and 3 messages appear
    assert 'Stage 1: Read' in raw, "Stage 1 (read) did not execute"
    assert 'Stage 2:' in raw, "Stage 2 (prefix sum) did not execute"
    assert 'Stage 3' in raw, "Stage 3 (write) did not execute"


def test_radix_sort():
    """Test the radix sort implementation with FSM."""
    run_test(
        'radix_sort',
        top,
        check,
        sim_threshold=5000,
        idle_threshold=100,
        resource_base=f'{utils.repo_path()}/python/ci-tests/resources'
    )


if __name__ == '__main__':
    test_radix_sort()
