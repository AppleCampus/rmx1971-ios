# rmx1971-ios

[![board](https://github.com/AppleCampus/rmx1971-ios/actions/workflows/board.yml/badge.svg)](https://github.com/AppleCampus/rmx1971-ios/actions/workflows/board.yml)

**Apple's iOS 16 running on a Realme 5 Pro** (`RMX1971`, Qualcomm SDM712 / SM712, OPPO `19691`).

This is the board repository for the [ios-sweet](https://github.com/AppleCampus/ios-sweet)
port of Apple's open-source XNU kernel (xnu-8796, the iOS 16 generation). ios-sweet
holds the kernel, the patch series and the CI build; this repository holds
everything that is specific to *this* handset: the Apple Device Tree, the DRAM
window, the boot-args, the boot image packer and the evidence behind each
number.

| Repository | Contents |
| --- | --- |
| [ios-sweet](https://github.com/AppleCampus/ios-sweet) | XNU build, SWEET machine config, the kernel patch series, CI |
| **rmx1971-ios** (this) | Board definition, ADT generator, payload/boot image packing, bring-up records |

## State

Bring-up. The handset boots XNU, mounts an HFS+ root volume on its own UFS
driver, runs the real iPhone X iOS 16.7.16 userland, and reaches SpringBoard
with a working touchscreen and all three physical buttons. It is not a usable
phone: USB does not enumerate, brightness does nothing, and leaving an app can
stall the compositor for minutes.

| Area | State |
| --- | --- |
| Boot | Unlocked bootloader → shim → XNU, written with `fastboot flash boot` (this ABL has no `boot` command). Recovery, modem and persist are never touched |
| CPU | All 8 cores via PSCI (2× A75 + 6× A55), 16K pages, GICv3, architected timers |
| Memory | 2.01 GiB of the upper DDR bank by default; a 3.09 GiB window is built but not yet verified |
| Storage | Own UFS host driver at `0x1d84000`; root is a journaled HFS+ volume, read/write |
| Display | Kernel IOSurface + IOMobileFramebuffer; CoreAnimation composites on the CPU and frames are copied into the ABL framebuffer at `0x9c000000`. Presented as an iPhone X panel (1125×2436), squashed into 1080×2340 |
| GUI | `backboardd`, SpringBoard, lock screen, home screen, Control Center, notification centre, Settings |
| Touch | Himax HX83112A over GENI SPI (`0x880000`), 56-byte event frames, delivered as HID digitizer events |
| Buttons | Volume up/down on TLMM gpios 24/92, power over SPMI from the PM660 PON block (not a gpio), all as HID keys |
| Device identity | iPhone X (`iPhone10,3` / `D22AP`): gesture UI, no Home button |

### What does not work

- **USB.** DWC3 refuses the device role (`GCTL.PrtCapDir` reads `0b10`), and even
  after forcing it, endpoint 0 setup times out. No adb, no network gadget.
- **Brightness.** Real backlight control needs DSI commands; this path has no
  DSI channel, so the panel runs at whatever ABL left it at.
- **Compositor stalls.** Pulling down Control Center works, but going back to an
  app can leave the middle of the screen black for minutes. The vsync timer
  keeps firing while the swap counter stops, so the stall is in the compositing
  or copy path, not in the timer.
- **No GPU.** Everything is drawn by the CPU, flushed at 10 Hz by default
  (a full 1125×2436 frame is 10.9 MiB per copy).
- No Wi-Fi, Bluetooth, cellular, audio, camera, sensors, GPS or SEP.
- **Security is off** — permissive AMFI and sandbox, stub AppleKeyStore. Put
  nothing personal on it.

## Memory: why the kernel cannot use all 4 GiB

XNU on arm64 takes **one** contiguous physical range: `boot_args.physBase` and
`physSize`, fed from `/chosen`'s `dram-base`/`dram-size`. There is no
reserved-memory mechanism to hand it a fragmented map.

| | Base | Size | Usable |
| --- | --- | --- | --- |
| DDR itself | `0x80000000` | `0xFE2C0000` | 3.97 GiB |
| Low bank carve-outs | … | ends at `0xB8300000` | hyp/XBL/TZ/modem/adsp/cdsp/qseecom, `cont_splash` 35 MiB at `0x9c000000`, `kboot_uboot_logmem` |
| **Default window** | `0x1_00000000` | `0x7E2C0000` − 1 MiB panic ring | **1.97 GiB** |
| **Max window** | `0xB8400000` | `0xC5EC0000` − 1 MiB | **3.09 GiB** |

The upper bank alone is 2 GiB, so the 4 GiB handset can never give XNU 4 GiB
minus carve-outs without breaking contiguity. `0xB8400000` is the first byte
above the last reservation; the 3.09 GiB window is `--phys-base 0xb8400000`.

The 1 MiB panic ring is carved off the top and exposed as `/pram` +
`/chosen/embedded-panic-log-size`. Without it `check_for_panic_log()` leaves
`gPanicBase == 0` and the kernel hangs right after `kdp_core zlib memory`.

## Build

The kernel comes from ios-sweet's CI (`.github/workflows/xnu.yml`, macOS
runner). This repository turns that kernel into a flashable image and never
recompiles it: `physBase`/`memSize` are boot arguments, so a different DRAM
window is a different ADT, not a different kernel.

```sh
git clone https://github.com/AppleCampus/ios-sweet ../ios-sweet
# Actions → xnu → Run workflow, then take kernel.development.sweet from the
# xnu-kernel artifact

python scripts/check-board.py --sweet ../ios-sweet          # DRAM/series sanity
python scripts/build-boot-image.py --sweet ../ios-sweet \
    --kernel ../ios-sweet/xnu/out/kernel.development.sweet \
    --d22ap ../research/d22ap.adt -o out/boot.img            # 2.01 GiB
python scripts/build-boot-image.py ... --phys-base 0xb8400000 -o out/boot-3g.img
```

`d22ap.adt` is the iPhone X `/product` tree extracted from the IPSW; it is an
Apple artifact and is not in this repository (see `firmware/README.md`).
Without it MobileGestalt guesses the product and the UI draws for the wrong
panel.

This recipe reproduces the image currently on the handset: dropping in the m10
`kernel.development.sweet` and the extracted `d22ap.adt` yields the same
19,025,920-byte boot image.

### Flash

```sh
./scripts/flash-boot.sh out/boot.img          # writes /dev/block/sde10, then reboots
./scripts/flash-boot.sh out/boot.img --boot   # only where the ABL still has `boot`
```

### Where the numbers came from

Every address in `board.toml` was read off the handset, not copied from another
port. The live FDT was dumped through TWRP, the PON/SPMI and DWC3 registers
were read with a small MMIO probe, and the boot header values were taken from
`backup/boot.img`. The write-ups are in `docs/evidence/`:

| File | Subject |
| --- | --- |
| `2026-10-24-memory.md` | Live FDT, carve-outs, the 3.09 GiB ceiling |
| `2026-10-27-power-key.md` | PM660 PON over the SPMI arbiter (APID/PPID hunting) |
| `2026-10-27-usb-dwc3.md` | `PrtCapDir`, `DSBLCLKGTNG`, CSFTRST, ep0 timeouts |
| `2026-10-27-framebuffer.md` | Framebuffer path, vsync vs swap counters, the stall |

## Layout

| Path | Contents |
| --- | --- |
| `board.toml` | Board, identity, DRAM windows, ADT nodes, boot header values |
| `configs/rmx1971.adt.py` | Apple Device Tree generator (reads `board.toml`) |
| `configs/cmdline.txt` | Sweet boot-args: `cpus=8`, `rd=ufsroot0`, subsystem switches |
| `patches/xnu/series` | Board patches and where they live in ios-sweet |
| `patches/shim/` | Board patches to ios-sweet's `shim/`, applied before compiling |
| `scripts/build-boot-image.py` | ADT → payload → shim → boot.img |
| `scripts/check-board.py` | Window arithmetic, framebuffer fit, patch presence |
| `scripts/flash-boot.sh` | `fastboot flash boot` (default), `--boot` where supported |
| `firmware/` | Stock appended DTBs, required by ABL's `dtb_idx=3` |
| `docs/bringup.md` | What each subsystem needs, in the order it came up |
| `docs/evidence/` | Measurements and register dumps per subsystem |

## Legal

XNU is open source (APSL 2.0) and ios-sweet ships only its own patches. This
repository contains no Apple code and no Apple binaries: the iOS root
filesystem, the kernel cache and the `d22ap.adt` device tree are generated
locally from an IPSW you already own. Flashing overwrites the boot partition of
a device you own; the iOS userland it boots is Apple's and is not redistributed
here.
