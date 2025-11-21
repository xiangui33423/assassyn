# Radix sort with FSM refactoring
# Uses FSM module for cleaner state machine implementation
# 3 stage machine
# Stage 1 (Read): read data from memory and put them into a register array based on the radix
# Stage 2 (Prefix): prefix sum the radix
# Stage 3 (Write): write data to memory
import os

from assassyn.frontend import *
from assassyn.backend import *
from assassyn import utils
from assassyn.ir.module import fsm

# Resource base path
current_path = os.path.dirname(os.path.abspath(__file__))
resource_base = f"{current_path}/workload/"
print(f"resource_base: {resource_base}")
# Data width, length
data_width = 32
data_depth = sum(1 for _ in open(f"{resource_base}/numbers.data"))
addr_width = (data_depth * 2 + 1).bit_length()
print(f"data_width: {data_width}, data_depth: {data_depth}, addr_width: {addr_width}")


# MemUser module - 不变
class MemUser(Module):
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
        idx = (rdata >> offset_reg[0])[0:3]
        # Only read to radix_reg in stage 1 (read state)
        with Condition(SM_reg[0] == Bits(2)(1)):
            log(
                "Stage 1: Read rdata=({:08x}) from memory addr_reg[0]=({:08x})",
                rdata,
                addr_reg[0] - UInt(addr_width)(1),
            )
            radix_reg[idx] = radix_reg[idx] + UInt(width)(1)
        return rdata


# RadixReducer module - 不变
class RadixReducer(Module):
    def __init__(self, width):
        super().__init__(ports={})

    @module.combinational
    def build(self, radix_reg: RegArray, cycle_reg: RegArray):
        # Prefix sum
        with Condition(cycle_reg[0] < UInt(data_width)(16)):
            cycle_index = cycle_reg[0][0:3].bitcast(UInt(4))
            radix_reg[cycle_index] = (
                radix_reg[cycle_index] + radix_reg[cycle_index - UInt(4)(1)]
            )
            log(
                "Stage 2: radix_reg[{}]: {:08x}; cycle_index: {:04x};cycle_reg[0]: {:08x}",
                cycle_reg[0] - UInt(data_width)(1),
                radix_reg[cycle_reg[0] - UInt(data_width)(1)],
                cycle_index,
                cycle_reg[0],
            )
            cycle_reg[0] = cycle_reg[0] + UInt(data_width)(1)
        return


class MemImpl(Downstream):
    """MemImpl with FSM for Stage 3 (Write) operations."""

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

        # Stage 3: Write Data to Memory (only execute when main SM is in stage 3)
        with Condition(SM_reg[0] == Bits(2)(3)):
            # Define FSM transition conditions
            default = Bits(1)(1)
            not_stopped = stop_reg[0] == UInt(1)(0)
            is_stopped = stop_reg[0] == UInt(1)(1)
            reset_not_done = reset_cycle_reg[0] < UInt(4)(15)
            reset_done = reset_cycle_reg[0] == UInt(4)(15)

            # FSM transition table for MemImpl
            # States: init(0) -> read(1) -> write(2) -> (read or reset) -> reset(3) -> init
            memimpl_table = {
                "init": {default: "read"},
                "read": {default: "write"},
                "write": {not_stopped: "read", is_stopped: "reset"},
                "reset": {reset_done: "init", reset_not_done: "reset"},
            }

            # Define state-specific actions
            def init_action():
                """Initialize read/write addresses."""
                log(
                    "Stage 3-0: Initialization Cycle: Copy addr_reg[0]={:08x} to read_addr_reg[0]; mem_start={:08x}; mem_end={:08x}.",
                    addr_reg[0],
                    mem_start,
                    mem_end,
                )
                read_addr_reg[0] = addr_reg[0]
                write_addr_reg[0] = UInt(addr_width)(data_depth) - mem_start

            def read_action():
                """Read cycle: set up read from memory."""
                log("Stage 3-1: Reading from mem_addr ({}).", addr_reg[0])
                re[0] = Bits(1)(0)
                we[0] = Bits(1)(1)
                addr_reg[0] = write_addr_reg[0]
                with Condition(read_addr_reg[0] > mem_start.bitcast(UInt(addr_width))):
                    read_addr_reg[0] = read_addr_reg[0] - UInt(addr_width)(1)

            def write_action():
                """Write cycle: write data to memory and update radix."""
                log(
                    "Stage 3-2: Writing wdata ({:08x}) to mem_addr ({}); wdata <= rdata ({:08x}).",
                    wdata[0],
                    addr_reg[0],
                    rdata,
                )
                idx = (rdata >> offset_reg[0])[0:3]
                wdata[0] = rdata.bitcast(Bits(data_width))
                write_addr_reg[0] = (
                    radix_reg[idx][0 : (addr_width - 1)].bitcast(UInt(addr_width))
                    - UInt(addr_width)(1)
                    + UInt(addr_width)(data_depth)
                    - mem_start.bitcast(UInt(addr_width))
                )
                radix_reg[idx] = radix_reg[idx] - UInt(data_width)(1)

                # Check if we should stop
                with Condition(read_addr_reg[0] == mem_start.bitcast(UInt(addr_width))):
                    stop_reg[0] = UInt(1)(1)

                # Prepare for next iteration or stop
                with Condition(stop_reg[0] == UInt(1)(0)):  # Continue
                    addr_reg[0] = read_addr_reg[0]
                    re[0] = Bits(1)(1)
                    we[0] = Bits(1)(0)
                with Condition(stop_reg[0] == UInt(1)(1)):  # Stop
                    addr_reg[0] = (
                        radix_reg[idx][0 : (addr_width - 1)].bitcast(UInt(addr_width))
                        - UInt(addr_width)(1)
                        + UInt(addr_width)(data_depth)
                        - mem_start.bitcast(UInt(addr_width))
                    )

            def reset_action():
                """Reset all radix registers and state."""
                with Condition(reset_cycle_reg[0] == UInt(4)(0)):
                    log(
                        "Stage 3-3: Writing wdata ({:08x}) to mem_addr ({});",
                        wdata[0],
                        addr_reg[0],
                    )
                    re[0] = Bits(1)(0)
                    we[0] = Bits(1)(0)

                with Condition(reset_cycle_reg[0] <= UInt(4)(14)):
                    log(
                        "Stage 3-3: Reset radix_reg[{}] to {:08x}.",
                        reset_cycle_reg[0],
                        UInt(data_width)(0),
                    )
                    radix_reg[reset_cycle_reg[0]] = UInt(data_width)(0)
                    reset_cycle_reg[0] = reset_cycle_reg[0] + UInt(4)(1)

                with Condition(reset_cycle_reg[0] == UInt(4)(15)):
                    log(
                        "Stage 3-3: Reset radix_reg[{}] to {:08x}.",
                        reset_cycle_reg[0],
                        UInt(data_width)(0),
                    )
                    log(
                        "Stage 3-3: Reset other registers: reset_cycle_reg[0]=0; SM_MemImpl[0]=0; SM_reg[0]=0; read_addr_reg[0]=0; write_addr_reg[0]=data_depth; stop_reg[0]=0;"
                    )
                    radix_reg[reset_cycle_reg[0]] = UInt(data_width)(0)
                    reset_cycle_reg[0] = UInt(4)(0)
                    SM_reg[0] = Bits(2)(0)  # Return to reset state
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


# Driver module with FSM
class Driver(Module):
    """Driver module with FSM for main control logic."""

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
            init_file=f"{resource_base}/numbers.data",
        )
        numbers_mem.name = "numbers_mem"
        numbers_mem.build(we[0], re[0], addr_reg[0], wdata[0])

        # Connect SRAM output to MemUser input
        memory_user.async_called(rdata=numbers_mem.dout[0])

        mem_start = UInt(addr_width)(0) + (
            mem_pingpong_reg[0] * UInt(addr_width)(data_depth)
        )[0 : (addr_width - 1)].bitcast(UInt(addr_width))
        mem_end = mem_start + UInt(addr_width)(data_depth)

        # Outer loop: only run when offset < data_width
        with Condition(offset_reg[0] < UInt(data_width)(data_width)):
            # Define FSM transition conditions
            default = Bits(1)(1)
            read_not_done = read_cond
            read_done = ~read_cond
            prefix_not_done = cycle_reg[0] < UInt(data_width)(15)
            prefix_done = cycle_reg[0] == UInt(data_width)(15)

            # Main FSM transition table
            # States: reset(0) -> read(1) -> prefix(2) -> write(3) -> reset
            main_table = {
                "reset": {default: "read"},
                "read": {read_done: "prefix", read_not_done: "read"},
                "prefix": {prefix_done: "write", prefix_not_done: "prefix"},
                "write": {default: "write"},  # Transitions back to reset in MemImpl
            }

            # Define state-specific actions
            def reset_action():
                """Initialize for next radix digit."""
                log(
                    "Radix Sort: Bits {} - {} Completed!",
                    offset_reg[0],
                    offset_reg[0] + UInt(data_width)(4),
                )
                log(
                    "========================================================================"
                )
                offset_reg[0] = offset_reg[0] + UInt(data_width)(4)
                addr_reg[0] = UInt(addr_width)(0) + (
                    ~mem_pingpong_reg[0] * UInt(addr_width)(data_depth)
                )[0 : (addr_width - 1)].bitcast(UInt(addr_width))
                re[0] = Bits(1)(1)
                we[0] = Bits(1)(0)
                mem_pingpong_reg[0] = (~mem_pingpong_reg[0]).bitcast(UInt(1))

            def read_action():
                """Read data from memory into radix registers."""
                with Condition(addr_reg[0] < mem_end):
                    # SRAM is automatically accessed when conditions are met
                    addr_reg[0] = addr_reg[0] + UInt(addr_width)(1)

                with Condition(addr_reg[0] == (mem_end - UInt(addr_width)(1))):
                    re[0] = Bits(1)(0)

                with Condition(~read_cond):
                    cycle_reg[0] = UInt(data_width)(1)
                    addr_reg[0] = addr_reg[0] - UInt(addr_width)(1)

            def prefix_action():
                """Perform prefix sum on radix array."""
                radix_reducer.async_called()
                with Condition(cycle_reg[0] == UInt(data_width)(15)):
                    re[0] = Bits(1)(1)
                    we[0] = Bits(1)(0)

            def write_action():
                """Write sorted data back to memory."""
                # SRAM write is handled by MemImpl FSM
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


def build_system():
    sys = SysBuilder("radix_sort_fsm")
    with sys:
        # State machine uses 2 bits for 4 states (reset=0, read=1, prefix=2, write=3)
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

        sys.expose_on_top(radix_reg, kind="Output")

    conf = config(
        verilog=utils.has_verilator(),
        sim_threshold=100000,
        idle_threshold=10,
        resource_base="",
        fifo_depth=1,
    )

    simulator_path, verilog_path = elaborate(sys, **conf)
    return sys, simulator_path, verilog_path


if __name__ == "__main__":
    sys, simulator_path, verilog_path = build_system()
    print("System built successfully!")
    utils.run_simulator(simulator_path)
    print("Simulation check completed!")
    if utils.has_verilator():
        raw = utils.run_verilator(verilog_path)
