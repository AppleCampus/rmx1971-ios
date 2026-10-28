# firmware/

## stock-appended-dtbs.bin

The FDT blobs that stock Realme firmware appends to the kernel image, extracted
from this handset's own `boot.img` (2.2 MiB, four DTBs).

ABL on the RMX1971 does **not** take its base device tree from the boot header.
The stock boot cmdline carries `androidboot.dtb_idx=3`, and the bootloader
picks entry 3 out of the FDTs appended to the kernel. Without these blobs ABL
finds nothing to hand to the kernel and falls back to fastboot, so they are
appended to the shim image by `scripts/build-boot-image.py`.

They are Qualcomm kernel device trees, not Apple code. They still contain this
specific handset's board revisions, so regenerate them from your own phone
rather than reusing these if your variant differs:

```sh
fastboot getvar all                     # or TWRP: dd if=/dev/block/by-name/boot
python ../ios-sweet/tools/unpack_bootv2.py boot.img out/
# the appended FDTs start at the first 0xd00dfeed magic after the kernel
```

## Not in this directory, on purpose

- `d22ap.adt` — the iPhone X `/product` tree. Apple artifact, generated locally
  from the IPSW with `tools/remotezip.py` + `ipsw img4 im4p extract`, passed to
  `configs/rmx1971.adt.py --d22ap`. Never commit it.
- `ios16-root.img` — the iOS root filesystem (12 GiB). Built locally by
  ios-sweet's `tools/mkiosroot.sh` from an IPSW you already own.
