from assassyn.frontend import *
from assassyn.test import run_test

class Adder(Module):

    def __init__(self, record_ty):
        ports={
            'a': Port(record_ty),
            'b': Port(record_ty)
        }
        super().__init__(
            ports=ports,
        )

    @module.combinational
    def build(self):
        a, b = self.pop_all_ports(True)
        print(a.dtype, type(a.dtype))
        valid = a.is_odd & b.is_odd
        with Condition(valid):
            c = a.payload + b.payload
            log("Adder: {} + {} = {}", a.payload, b.payload, c)

class Driver(Module):

    def __init__(self):
            super().__init__(ports={})

    @module.combinational
    def build(self, adder: Adder, record_ty: Record):
        bundle = RegArray(record_ty, 1)

        value = bundle[0].payload

        is_odd = value[0:0]
        new_value = value + Int(32)(1)

        # `bundle` is a syntactical salt to create a new record.
        new_record = record_ty.bundle(is_odd=is_odd, payload=new_value)

        (bundle & self)[0] <= new_record

        adder.async_called(a = new_record, b = new_record)

def check_raw(raw):
    cnt = 0
    for i in raw.split('\n'):
        if 'Adder:' in i:
            line_toks = i.split()
            c = line_toks[-1]
            a = line_toks[-3]
            b = line_toks[-5]
            assert int(a) + int(b) == int(c), f'{a} + {b} != {c}'
            cnt += 1
    assert cnt == 99, f'cnt: {cnt} != 99'


def test_record():
    def top():
        record_ty = Record({
            (0, 0): ('is_odd', Bits),
            (1, 32): ('payload', Int),
        })

        adder = Adder(record_ty)
        adder.build()

        driver = Driver()
        driver.build(adder, record_ty)

    run_test(
        name='record',
        top=top,
        checker=check_raw,
        sim_threshold=200,
        idle_threshold=200,
        random=True
    )


if __name__ == '__main__':
    test_record()

