#!/usr/bin/env python3
"""Generate the Apple Device Tree that the SWEET xnu boots with on the RMX1971.

    ./rmx1971.adt.py -o out/rmx1971.adt --sweet ../ios-sweet [--d22ap ...]

Only what the early boot path reads is emitted; every MMIO address comes from
the live FDT pulled off the handset (see docs/evidence/2026-10-24-memory.md).
Values live in ../board.toml so the board file stays the single source of truth.

The one input that must not be committed is the /product node of the real
D22AP tree: it is an Apple artifact. Extract it from the iPhone X IPSW once and
pass it with --d22ap; without it, MobileGestalt guesses and the UI misdraws.
"""
import argparse
import hashlib
import os
import struct
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, '..', 'board.toml')

u32 = lambda *v: struct.pack('<%dI' % len(v), *v)
u64 = lambda *v: struct.pack('<%dQ' % len(v), *v)
cstr = lambda s: s.encode() + b'\0'


def load_board(path=BOARD, phys_base=None, mem_size=None):
    with open(path, 'rb') as f:
        b = tomllib.load(f)
    mem = b['memory']
    base = phys_base if phys_base is not None else mem['default_base']
    # No explicit size -> take everything above the base, which is how
    # SWEET_PHYS_BASE=0xb8400000 produces the 3.09GiB window.
    size = mem_size if mem_size is not None else mem['default_size']
    if phys_base is not None and mem_size is None:
        size = mem['ddr_top'] - base
    if base + size > mem['ddr_top']:
        raise SystemExit(f'ADT DRAM {base:#x}+{size:#x} runs past the top of DDR '
                         f'({mem["ddr_top"]:#x})')
    if base < mem['low_carveout_end']:
        raise SystemExit(f'ADT DRAM base {base:#x} sits below the last Qualcomm '
                         f'carve-out ({mem["low_carveout_end"]:#x})')
    return b, base, size


def d22ap_product(path):
    """/product properties of the real D22AP tree, without the syscfg
    placeholders iBoot would fill in (flag bit set) and the node bookkeeping."""
    from adt import parse
    if not path or not os.path.exists(path):
        print(f'warning: {path or "(no --d22ap)"} missing, no /product node',
              file=sys.stderr)
        return None
    root, _ = parse(open(path, 'rb').read())
    for c in root.children:
        if dict((k, v) for k, f, v in c.props).get('name') == b'product\0':
            return [(k, v) for k, f, v in c.props if not f and k not in ('name', 'AAPL,phandle')]
    raise SystemExit(f'{path}: no /product node')


def node(name, props=(), children=()):
    from adt import Node
    n = Node()
    n.props = [('name', 0, cstr(name))] + [(k, 0, v) for k, v in props]
    n.children = list(children)
    return n


QEMU = dict(phys_base=0xb0000000, mem_size=0x4d000000, gicd=0x08000000,
            gicr=0x080a0000, timebase=62500000, vtimer=27)


def build(board, phys_base, mem_size, product_props, qemu=False):
    mem = board['memory']
    adt_b = board['adt']
    ident = board['identity']
    disp = board['display']
    stor = board['storage']

    if qemu:
        phys_base, mem_size = QEMU['phys_base'], QEMU['mem_size']
        gicd, gicr = QEMU['gicd'], QEMU['gicr']
        timebase, vtimer = QEMU['timebase'], QEMU['vtimer']
    else:
        gicd, gicr = adt_b['gicd'], adt_b['gicr']
        timebase, vtimer = adt_b['timebase_frequency'], adt_b['virtual_timer_intid']

    # The panic ring is carved off the TOP of the window: without /pram and the
    # matching embedded-panic-log-size, check_for_panic_log() leaves
    # gPanicBase == 0, debug_log_init() bails twice and the kernel hangs right
    # after "kdp_core zlib memory".
    panic_size = mem['panic_log_size']
    panic_base = phys_base + mem_size - panic_size
    mem_size -= panic_size

    phandle = iter(range(1, 1000))
    ph = lambda: ('AAPL,phandle', u32(next(phandle)))

    chosen = node('chosen', [
        ph(),
        ('dram-base', u64(phys_base)),
        ('dram-size', u64(mem_size)),
        ('random-seed', os.urandom(64)),
        ('debug-enabled', u32(1)),
        ('embedded-panic-log-size', u32(panic_size)),
        ('firmware-version', cstr('rmx1971-shim')),
        ('system-firmware-version', cstr('rmx1971-shim')),
        ('boot-manifest-hash', hashlib.sha384(b'rmx1971 boot manifest').digest()),
        ('crypto-hash-method', cstr('sha2-384')),
        ('display-scale', u32(3)),          # 1125x2436 @3x = 375x812 points
        ('unique-chip-id', struct.pack('<Q', ident['ecid'])),
        ('chip-id', u32(ident['chip_id'])),
        ('certificate-production-status', u32(0)),
        ('effective-production-status-ap', u32(0)),
        ('certificate-security-mode', u32(1)),
        ('effective-security-mode-sep', u32(1)),
    ], [node('memory-map', [ph()]),
        node('asmb', [ph(), ('lp-sip0', u64(0x6f)), ('lp-sip1', u32(1))])])

    # SDM712: cores 0-1 are Kryo 360 Gold (A75), 2-7 Silver (A55); MPIDR = core<<8.
    # 'qcom,kryo470' is the SweetCPU IONameMatch key: it must stay kryo470 or the
    # CPU driver never binds and the kernel waits forever in ml_wait_max_cpus.
    cpu_nodes = []
    for core in range(1 if qemu else 8):
        cpu_nodes.append(node(f'cpu{core}', [
            ph(),
            ('device_type', cstr('cpu')),
            ('compatible', cstr('qcom,kryo470') + cstr('ARM,v8')),
            ('reg', u32(core << 8)),
            ('cpu-id', u32(core)),
            ('cluster-id', u32(0)),
            ('cluster-type', u32(0)),
            ('die-cluster-id', u32(0)),
            ('cluster-core-id', u32(core)),
            ('l2-cache-id', u32(core)),
            ('l3-cache-id', u32(0)),
            ('state', cstr('running' if core == 0 else 'stopped')),
            ('timebase-frequency', u32(timebase)),
            ('clock-frequency', u32(2304000000 if core < 2 else 1704000000)),
            ('bus-frequency', u32(100000000)),
            ('memory-frequency', u32(1866000000)),
            ('peripheral-frequency', u32(timebase)),
            ('fixed-frequency', u32(timebase)),
        ]))
    cpus = node('cpus', [ph(), ('#address-cells', u32(1)),
                         ('#size-cells', u32(0))], cpu_nodes)

    gic = node('gic', [
        ph(),
        ('compatible', cstr('arm,gic-v3')),
        ('interrupt-controller', b''),
        ('virtual-timer-intid', u32(vtimer)),
        ('reg', u64(gicd - adt_b['soc_base'], adt_b['gicd_size'],
                    gicr - adt_b['soc_base'], adt_b['gicr_size'])),
    ])
    arm_io = node('arm-io', [
        ph(),
        ('device_type', cstr('sweet-io')),
        ('compatible', cstr('sweet-io') + cstr('arm-io')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
        ('ranges', u64(0, adt_b['soc_base'], 0x100000000 - adt_b['soc_base'])),
    ], [gic,
        node('wdt', [ph(), ('no-pmu', u32(1))]),
        node('disp0', [
            ph(),
            ('device_type', cstr('display-subsystem')),
            ('compatible', cstr('sweet,display')),
            ('canvas-width', u32(disp['canvas_width'])),
            ('canvas-height', u32(disp['canvas_height'])),
        ])])

    fbcon = node('sweet-fbcon', [
        ph(),
        ('reg', u64(disp['fb_base'], disp['fb_size'])),
        ('width', u32(disp['panel_width'])),
        ('height', u32(disp['panel_height'])),
        ('stride', u32(disp['panel_width'] * 4)),
    ])
    ufs = node('sweet-ufs', [
        ph(),
        ('compatible', cstr('qcom,ufshc')),
        ('reg', u64(stor['ufs_base'], stor['ufs_size'])),
    ])
    buttons = node('buttons', [
        ph(),
        ('button-names', cstr('volup') + cstr('voldown') + cstr('hold')),
        ('home-button-type', u32(2)),
        ('opposed-power-vol-buttons', u32(1)),
    ])
    pram = node('pram', [ph(), ('reg', u64(panic_base) + u64(panic_size))])
    product = [node('product', [ph()] + product_props)] if product_props else []

    return node('device-tree', [
        ph(),
        ('compatible', b''.join(cstr(c) for c in ident['compatible'])),
        ('model', cstr(ident['model'])),
        ('target-type', cstr(ident['target_type'])),
        ('target-sub-type', cstr(ident['target_sub_type'])),
        ('manufacturer', cstr('Apple Inc.')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
    ], [chosen, node('defaults', [ph(), ('no-effaceable-storage', u32(1))]),
        cpus, arm_io, fbcon, buttons, pram] + product
        + ([] if qemu else [ufs]))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('-o', '--out', default='out/rmx1971.adt')
    ap.add_argument('--sweet', default=os.environ.get('IOS_SWEET', '../ios-sweet'),
                    help='ios-sweet checkout (for tools/adt.py)')
    ap.add_argument('--d22ap', default=None,
                    help='DeviceTree.d22ap.im4p extracted to .adt, for the /product node')
    ap.add_argument('--board', default=BOARD)
    ap.add_argument('--phys-base', type=lambda s: int(s, 0), default=None)
    ap.add_argument('--mem-size', type=lambda s: int(s, 0), default=None)
    ap.add_argument('--qemu', action='store_true')
    ap.add_argument('--dump', action='store_true')
    a = ap.parse_args()

    tools = os.path.join(os.path.abspath(a.sweet), 'tools')
    if not os.path.isdir(tools):
        raise SystemExit(f'{tools} not found: pass --sweet /path/to/ios-sweet')
    sys.path.insert(0, tools)

    board, base, size = load_board(a.board, a.phys_base, a.mem_size)
    root = build(board, base, size, d22ap_product(a.d22ap), qemu=a.qemu)

    import adt
    blob = adt.serialize(root)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, 'wb').write(blob)
    if a.dump:
        adt.dump(adt.parse(blob)[0])
    print(f'{a.out}: {len(blob)} bytes, dram {base:#x} + {size - board["memory"]["panic_log_size"]:#x} '
          f'({(size - board["memory"]["panic_log_size"]) / 2**30:.2f}GiB)', file=sys.stderr)


if __name__ == '__main__':
    main()
