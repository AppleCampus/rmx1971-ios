#!/usr/bin/env python3
"""Build a flashable RMX1971 boot image: ADT + SWEETXNU payload + shim.

    ./build-boot-image.py --sweet ../ios-sweet \
        --kernel ../ios-sweet/xnu/out/kernel.development.sweet \
        --d22ap ../research/d22ap.adt -o out/boot-gz.img

Steps, in order:
  1. configs/rmx1971.adt.py generates the Apple Device Tree from board.toml
  2. ios-sweet tools/mkpayload.py resolves the kernel's chained fixups and
     packs kernel + ADT + boot-args into the SWEETXNU payload
  3. the shim is compiled (aarch64-linux-gnu-gcc, or the NDK's clang) and
     padded to _end so .bss and the stacks exist as real zeroed bytes
  4. the stock FDTs are appended after the kernel, because ABL selects its base
     DTB with androidboot.dtb_idx=3 instead of taking one from the boot header
  5. a header-v1 boot.img mirroring the stock header is written

The kernel is never recompiled here: physBase/memSize are boot arguments, and
mkpayload re-resolves fixups against whatever the ADT says, so changing the
DRAM window only means re-running this script with a different --phys-base.
"""
import argparse
import glob
import gzip
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def sh(cmd, **kw):
    print('+', ' '.join(os.path.basename(c) if os.sep in c else c for c in cmd), flush=True)
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        sys.exit(f'error: {cmd[0]} exited {r.returncode}')
    return r


def find_toolchain(args):
    """aarch64-linux-gnu-* if present, else the NDK's llvm (clang/lld are
    freestanding-safe for the shim and the only option on Windows)."""
    if args.cc:
        base = os.path.dirname(args.cc)
        def sib(name, suffix=''):
            p = os.path.join(base, name + suffix)
            return p if os.path.exists(p) else name + suffix
        return dict(cc=args.cc, objcopy=sib('llvm-objcopy', '.exe' if os.name == 'nt' else ''),
                    nm=sib('llvm-nm', '.exe' if os.name == 'nt' else ''),
                    readelf=sib('llvm-readelf', '.exe' if os.name == 'nt' else ''))
    for prefix in ('aarch64-linux-gnu-', ''):
        cc = prefix + 'gcc'
        if subprocess.run(['which', cc], capture_output=True).returncode == 0:
            return dict(cc=cc, objcopy=prefix + 'objcopy', nm=prefix + 'nm',
                        readelf=prefix + 'readelf')
    ndk = os.environ.get('ANDROID_NDK') or os.environ.get('ANDROID_NDK_ROOT')
    if not ndk:
        sys.exit('error: no aarch64-linux-gnu-gcc and ANDROID_NDK is unset')
    host = {'nt': 'windows-x86_64', 'posix': 'linux-x86_64'}[os.name]
    binp = os.path.join(ndk, 'toolchains', 'llvm', 'prebuilt', host, 'bin')
    tail = '.exe' if os.name == 'nt' else ''
    return dict(cc=os.path.join(binp, 'clang' + tail),
                objcopy=os.path.join(binp, 'llvm-objcopy' + tail),
                nm=os.path.join(binp, 'llvm-nm' + tail),
                readelf=os.path.join(binp, 'llvm-readelf' + tail))


CFLAGS = ['-O2', '-ffreestanding', '-nostdlib', '-fno-pic', '-mgeneral-regs-only',
          '-mstrict-align', '-fno-stack-protector', '-Wall', '-Wextra']
SOURCES = ['start.S', 'shim.c', 'console.c', 'mmu.c', 'fdt.c', 'loader.c', 'ufs.c']


def prepare_shim(sweet, outdir, patches_dir):
    """Copy ios-sweet's shim and apply the board's shim patches. The shim is
    board code -- restart-reason address, freestanding libc -- so it cannot come
    from upstream unmodified."""
    dst = os.path.join(outdir, 'shim')
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(os.path.join(sweet, 'shim')):
        if name.endswith(('.c', '.h', '.S', '.ld')):
            shutil.copy2(os.path.join(sweet, 'shim', name), os.path.join(dst, name))
    for p in sorted(glob.glob(os.path.join(patches_dir, '*.patch'))):
        print('applying', os.path.basename(p))
        r = subprocess.run(['git', 'apply', '-p1', p], cwd=dst, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f'error: {os.path.basename(p)} did not apply://n{r.stderr}')
    return dst


def build_shim(shimdir, outdir, tc, target):
    objs = []
    for src in SOURCES:
        obj = os.path.join(outdir, os.path.splitext(src)[0] + '.o')
        sh([tc['cc'], *target, *CFLAGS, '-c', os.path.join(shimdir, src), '-o', obj])
        objs.append(obj)
    # Every C object must be position-independent enough to run wherever ABL
    # put us; start.o is the one allowed exception (it stores the image size).
    for obj in objs:
        if obj.endswith('start.o'):
            continue
        r = subprocess.run([tc['readelf'], '-r', obj], capture_output=True, text=True)
        bad = [l for l in r.stdout.splitlines() if 'ABS64' in l or 'R_AARCH64_ABS' in l]
        if bad:
            sys.exit(f'error: {len(bad)} absolute relocations in {os.path.basename(obj)}')
    elf = os.path.join(outdir, 'shim.elf')
    sh([tc['cc'], *target, *CFLAGS, '-static', '-fuse-ld=lld' if 'clang' in tc['cc'] else '-fuse-ld=bfd',
        '-T', os.path.join(shimdir, 'shim.ld'), '-Wl,--build-id=none', '-Wl,-N', *objs, '-o', elf])
    return elf


def elf_to_image(elf, outdir, tc):
    image = os.path.join(outdir, 'Image')
    sh([tc['objcopy'], '-O', 'binary', elf, image])
    r = subprocess.run([tc['nm'], elf], capture_output=True, text=True)
    end = next((int(p[0], 16) for p in (l.split() for l in r.stdout.splitlines())
                if len(p) == 3 and p[2] == '_end'), None)
    if end is None:
        sys.exit('error: _end symbol not found in the shim')
    data = open(image, 'rb').read()
    if len(data) > end:
        sys.exit(f'error: Image {len(data):#x} larger than _end {end:#x}')
    data = data.ljust(end, b'\0')       # .bss and the stacks must exist
    open(image, 'wb').write(data)
    print(f'shim Image: {len(data)} bytes')
    return image


def pack_bootimg(board, image, payload, dtbs_path, outpath, gz):
    b = board['bootimg']
    kdata = open(image, 'rb').read()
    assert b'\xd0\x0d\xfe\xed' not in kdata, 'shim Image accidentally contains FDT magic'
    dtbs = open(dtbs_path, 'rb').read()
    assert dtbs[:4] == b'\xd0\x0d\xfe\xed', f'{dtbs_path} is not an FDT blob'
    kdata = (gzip.compress(kdata, 9, mtime=0) if gz else kdata) + dtbs
    rdata = open(payload, 'rb').read()
    page = b['page_size']

    hdr = bytearray(page)
    struct.pack_into('<8s', hdr, 0, b'ANDROID!')
    struct.pack_into('<10I', hdr, 8, len(kdata), b['kernel_addr'],
                     len(rdata), b['ramdisk_addr'], 0, 0,
                     b['tags_addr'], page, b['header_version'], b['os_version'])
    cmd = b['cmdline'].encode()[:511]
    hdr[64:64 + len(cmd)] = cmd                      # cmdline[512], name[16] stays empty
    sha = hashlib.sha1(bytes(hdr[:576]) + kdata + rdata + bytes(hdr[704:1728]))
    struct.pack_into('<32I', hdr, 576, *struct.unpack('<5I', sha.digest()), *([0] * 27))

    pad = lambda n: (n + page - 1) // page * page
    img = bytes(hdr) + kdata.ljust(pad(len(kdata)), b'\0') + rdata.ljust(pad(len(rdata)), b'\0')
    os.makedirs(os.path.dirname(os.path.abspath(outpath)), exist_ok=True)
    open(outpath, 'wb').write(img)
    print(f'boot image: {outpath} ({len(img)} bytes, kernel {len(kdata)} gz={gz})')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--sweet', default=os.environ.get('IOS_SWEET', os.path.join(ROOT, '..', 'ios-sweet')))
    ap.add_argument('--kernel', required=True, help='kernel.development.sweet from the xnu build')
    ap.add_argument('-o', '--out', default=os.path.join(ROOT, 'out', 'boot.img'))
    ap.add_argument('--d22ap', default=None, help='d22ap.adt for the /product node (optional)')
    ap.add_argument('--cmdline', default=os.path.join(ROOT, 'configs', 'cmdline.txt'),
                    help='sweet boot-args (NOT the Android boot.img cmdline)')
    ap.add_argument('--dtbs', default=os.path.join(ROOT, 'firmware', 'stock-appended-dtbs.bin'))
    ap.add_argument('--phys-base', type=lambda s: int(s, 0), default=None,
                    help='0x100000000 = upper bank (2.01GiB); 0xb8400000 = 3.09GiB')
    ap.add_argument('--mem-size', type=lambda s: int(s, 0), default=None)
    ap.add_argument('--gzip-kernel', action='store_true', default=True)
    ap.add_argument('--no-gzip-kernel', dest='gzip_kernel', action='store_false')
    ap.add_argument('--cc', default=None, help='aarch64 compiler to use instead of autodetect')
    a = ap.parse_args()

    a.sweet = os.path.abspath(a.sweet)
    if not os.path.isdir(os.path.join(a.sweet, 'tools')):
        sys.exit(f'error: {a.sweet} is not an ios-sweet checkout')
    with open(os.path.join(ROOT, 'board.toml'), 'rb') as f:
        board = tomllib.load(f)

    build = os.path.join(ROOT, 'out', 'build')
    os.makedirs(build, exist_ok=True)
    adt_path = os.path.join(build, 'rmx1971.adt')
    payload = os.path.join(build, 'payload.bin')

    adt_cmd = [sys.executable, os.path.join(ROOT, 'configs', 'rmx1971.adt.py'),
               '-o', adt_path, '--sweet', a.sweet]
    if a.d22ap:
        adt_cmd += ['--d22ap', a.d22ap]
    if a.phys_base is not None:
        adt_cmd += ['--phys-base', hex(a.phys_base)]
    if a.mem_size is not None:
        adt_cmd += ['--mem-size', hex(a.mem_size)]
    sh(adt_cmd)

    sh([sys.executable, os.path.join(a.sweet, 'tools', 'mkpayload.py'),
        os.path.abspath(a.kernel), adt_path, payload,
        '--cmdline', open(a.cmdline).read().strip()])

    tc = find_toolchain(a)
    target = ['--target=aarch64-linux-gnu'] if 'clang' in os.path.basename(tc['cc']) else []
    shimdir = prepare_shim(a.sweet, build, os.path.join(ROOT, 'patches', 'shim'))
    image = elf_to_image(build_shim(shimdir, build, tc, target), build, tc)
    pack_bootimg(board, image, payload, a.dtbs, a.out, a.gzip_kernel)


if __name__ == '__main__':
    main()
