from assassyn.frontend import *
from assassyn.backend import elaborate
from assassyn import utils
import assassyn

class Adder(Module):

    def __init__(self):
        super().__init__(
            ports={
                'a': Port(Int(32)),
                'b': Port(Int(32)),
            },
        )

    @module.combinational
    def build(self):
        a, b = self.pop_all_ports(True)
        c = a + b
        log("Adder: {} + {} = {}", a, b, c)

class Driver(Module):

    def __init__(self):
        super().__init__(ports={})

    @module.combinational
    def build(self, adder: Adder):
        # The code below is equivalent
        # cnt = RegArray(Int(32), 0)
        # v = cnt[0]
        # (cnt & self)[0] <= v + Int(32)(1)
        # NOTE: cnt[0]'s new value is NOT visible until next cycle.
        # cond = v < Int(32)(100)
        # with Condition(cond):
        #     adder.async_called(a = v, b = v)
        cnt = RegArray(Int(32), 1)
        (cnt & self)[0] <= cnt[0] + Int(32)(1)
        cond = cnt[0] < Int(32)(100)
        with Condition(cond):
            adder.async_called(a = cnt[0], b = cnt[0])

def check_raw(raw):
    cnt = 0
    # print(raw)
    for i in raw.split('\n'):
        if 'Adder:' in i:
            line_toks = i.split()
            c = line_toks[-1]
            a = line_toks[-3]
            b = line_toks[-5]
            # print(a,b,c)
            assert int(a) + int(b) == int(c)
            cnt += 1
    # print(cnt)
    assert cnt == 100, f'cnt: {cnt} != 100'


def test_async_call():
    # NOTE: The name of the system should be unique within all the testcases,
    # because we currently have no locks to exclusively own a folder, under the
    # context of multi-thread testing.
    sys = SysBuilder('async_call')
    with sys:
        adder = Adder()
        adder.build()

        driver = Driver()
        call = driver.build(adder)

    print(sys)

    config = assassyn.backend.config(
            verilog=utils.has_verilator(),
            sim_threshold=200,
            idle_threshold=200,
            random=True)

    simulator_path, verilator_path = elaborate(sys, **config)
    print("simulator")
    raw = utils.run_simulator(simulator_path)
    check_raw(raw)
    
    if verilator_path:
        print("verialtor")
        raw = utils.run_verilator(verilator_path)
        print(raw)
        check_raw(raw)


if __name__ == '__main__':
    test_async_call()
