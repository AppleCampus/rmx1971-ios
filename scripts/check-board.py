#!/usr/bin/env python3
"""Sanity-check the board definition before a build burns 20 minutes of CI.

    ./check-board.py [--sweet ../ios-sweet] [--image out/boot.img]

Checks the arithmetic that actually bricks a board if it is wrong: the DRAM
window must stay above the last Qualcomm carve-out and below the top of DDR,
the framebuffer must sit inside the low bank, and the boot image must still fit
the 64MiB boot partition. Also verifies that every patch listed in
patches/xnu/series exists in the ios-sweet checkout.
"""
import argparse
import os
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GiB = 2 ** 30


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sweet', default=os.environ.get('IOS_SWEET', os.path.join(ROOT, '..', 'ios-sweet')))
    ap.add_argument('--image', default=None, help='boot image to size-check')
    a = ap.parse_args()

    with open(os.path.join(ROOT, 'board.toml'), 'rb') as f:
        board = tomllib.load(f)
    mem, adt, disp, stor, bimg = (board[k] for k in ('memory', 'adt', 'display', 'storage', 'bootimg'))

    errors = []

    def note(text):
        print('  ' + text)

    def window(name, base, size):
        usable = size - mem['panic_log_size']
        note(f'{name:<10} {base:#012x} + {size:#010x}  -> {usable / GiB:.3f} GiB usable')
        if base + size > mem['ddr_top']:
            errors.append(f'{name}: window runs {base + size - mem["ddr_top"]:#x} past DDR top')
        if base < mem['low_carveout_end']:
            errors.append(f'{name}: base is below the last Qualcomm carve-out '
                          f'({mem["low_carveout_end"]:#x}) -- would collide with '
                          'cont_splash or kboot_uboot_logmem')
        if size <= mem['panic_log_size']:
            errors.append(f'{name}: window cannot hold the 1MiB panic ring')

    print('DRAM windows')
    window('default', mem['default_base'], mem['default_size'])
    window('max', mem['max_base'], mem['max_size'])
    if mem['max_base'] + mem['max_size'] != mem['ddr_top']:
        errors.append('max window does not end at the top of DDR')

    print('\nLow bank')
    note(f'DDR          {mem["ddr_base"]:#x} .. {mem["ddr_top"]:#x} '
                 f'({(mem["ddr_top"] - mem["ddr_base"]) / GiB:.2f} GiB)')
    note(f'carve-outs   .. {mem["low_carveout_end"]:#x}')
    note(f'framebuffer  {disp["fb_base"]:#x} + {disp["fb_size"]:#x} '
                 f'({disp["fb_size"] / 2**20:.0f} MiB, cont_splash)')
    if not (mem['ddr_base'] <= disp['fb_base'] < mem['low_carveout_end']):
        errors.append('framebuffer is not inside the low bank')
    if disp['fb_base'] + disp['fb_size'] > mem['low_carveout_end']:
        errors.append('framebuffer overlaps kboot_uboot_logmem')
    need = disp['canvas_width'] * disp['canvas_height'] * 4
    if need > disp['fb_size']:
        errors.append(f'canvas needs {need / 2**20:.1f} MiB, carve-out is only '
                      f'{disp["fb_size"] / 2**20:.1f} MiB')
    note(f'canvas       {disp["canvas_width"]}x{disp["canvas_height"]} '
                 f'= {need / 2**20:.1f} MiB, scaled to the {disp["panel_width"]}x'
                 f'{disp["panel_height"]} panel')

    print('\nSoC')
    for name in ('gicd', 'gicr'):
        base, size = adt[name], adt[name + '_size']
        note(f'{name:<12} {base:#x} + {size:#x}')
        if base < adt['soc_base']:
            errors.append(f'{name} is below soc_base, /arm-io ranges will not cover it')

    print('\nBoot image')
    if a.image and os.path.exists(a.image):
        n = os.path.getsize(a.image)
        note(f'{os.path.basename(a.image)}  {n} bytes = {n / bimg["page_size"]:.0f} pages')
        if n > stor['boot_size']:
            errors.append(f'image is {n} bytes, boot partition is only {stor["boot_size"]} bytes')
    else:
        note('not built yet')

    print('\nPatch series')
    series = os.path.join(ROOT, 'patches', 'xnu', 'series')
    missing = []
    with open(series) as f:
        for line in f:
            name = line.split('#', 1)[0].strip()
            if not name:
                continue
            p = os.path.join(a.sweet, 'xnu', 'patches', name)
            if os.path.exists(p):
                note(f'{name}  ok')
            else:
                missing.append(name)
    if missing:
        errors.append(f'{len(missing)} patch(es) missing from {a.sweet}/xnu/patches: '
                      + ', '.join(missing))

    print()
    if errors:
        for e in errors:
            print('ERROR ' + e, file=sys.stderr)
        return 1
    print('board ok')
    return 0


if __name__ == '__main__':
    sys.exit(main())
