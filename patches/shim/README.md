# patches/shim

Patches to ios-sweet's `shim/` — the AArch64 code that ABL jumps into before
XNU exists. They are board code, so they live here, and
`scripts/build-boot-image.py` copies `ios-sweet/shim/` into `out/build/shim/`
and applies everything in this directory before compiling.

## 0001-rmx1971-imem-and-freestanding.patch

Three changes, each of which was required for this board to boot at all:

1. **`IMEM_RESTART_REASON` → `0x146BF65C`.** Upstream hardcodes the SM7150
   address of `restart_reason@65c`. On SM712 the IMEM block is at
   `0x146BF000`, so the shim was printing a restart reason read from an
   unrelated register. Read from the live FDT.

2. **Panic-kind strings as a `char[4][8]` instead of a `switch`.** A
   `switch`/if-chain over calls with identical arguments gets lowered to a
   `.rodata` table of *absolute* pointers. The shim runs wherever ABL loaded it
   with no relocation pass, so absolute pointers break it — and the build's
   ABS64 check (correctly) refuses the object. Raw bytes indexed at runtime
   cannot be tabled.

3. **A local `strcmp`.** Clang's `-ffreestanding` implies `-fno-builtin`, so
   `strcmp` stays an external call, and there is no libc. GCC inlines the
   constant-argument uses, which is why the same source built with the upstream
   toolchain; with the NDK's clang it does not.

The banner string change is cosmetic and rides along.
