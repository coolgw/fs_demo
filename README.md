# FS Demo (demofs)

This repository contains a demonstration of a custom Linux Filesystem (`demofs`) across multiple stages of development and debugging.

## Filesystem Evolution & Test Suites

The `demofs` driver evolves through five stages, illustrating common kernel, VFS-interaction, and on-disk metadata bugs in block-device backed filesystems:

| Version | Bug Category | Symptom | Automated Test |
| :--- | :--- | :--- | :--- |
| [**`fs_demo_v1`**](./fs_demo_v1) | Folio Lock Leak | Unmount hangs, kworker and umount processes block in uninterruptible state `D`. | `./reproduce_v1_hang.sh` |
| [**`fs_demo_v2`**](./fs_demo_v2) | Transient Inode | After remounting, file size unexpectedly reads as `0` bytes / metadata is lost. | `./reproduce_v2_size0.sh` |
| [**`fs_demo_v3`**](./fs_demo_v3) | Missing `.writepages` | After remounting, file size is correct but contents are empty / data is lost. | `./reproduce_v3_loss.sh` |
| [**`fs_demo_v4`**](./fs_demo_v4) | Missing SB Writeback | Caching and writeback are correct, but superblock counters (free blocks/inodes) are never updated on disk. | `./verify_v4_correct.sh` |
| [**`fs_demo_v5`**](./fs_demo_v5) | Fully Corrected | Both metadata and data blocks persist perfectly, and superblock counters are dynamically kept in perfect sync. | `./verify_v5_correct.sh` |

---

## Running the Automated Reproduction & Verification Tests

Ensure you are running on a virtual machine (or development environment) with the target kernel headers installed, and run as `root` or with `sudo` capabilities.

To clone the repository and get started:

```bash
git clone https://github.com/coolgw/fs_demo.git
cd fs_demo
```

### 1. Version 1: Unmount Hang Bug (Folio Lock Leak)
To run the automated reproduction script for the buggy version `fs_demo_v1` (where folio locks are leaked in `write_end`):
```bash
cd fs_demo_v1/
./reproduce_v1_hang.sh
```

> ⚠️ **CRITICAL WARNING**: Running the `fs_demo_v1` test triggers an uninterruptible `D`-state kernel deadlock where the `umount` and writeback workqueue processes block indefinitely in uninterruptible sleep. This process state cannot be killed or forced, and **you will need to REBOOT your target VM to recover and clear the deadlocked processes** before running subsequent tests.

### 2. Version 2: Transient Inode Bug (Size 0 on Remount)
To run the automated reproduction script for `fs_demo_v2` (where `drop_inode` returns `1`, causing VFS to immediately evict the inode and discard dirty metadata before writeback):
```bash
cd fs_demo_v2/
./reproduce_v2_size0.sh
```

### 3. Version 3: Silent Data Loss Bug (Missing Writepages)
To run the automated reproduction script for `fs_demo_v3` (where `.writepages` callback is missing in `address_space_operations`, meaning page-cache dirty pages are never flushed on unmount):
```bash
cd fs_demo_v3/
./reproduce_v3_loss.sh
```

### 4. Version 4: Missing Superblock Write-back (Bad Metadata Example)
To run the verification script for `fs_demo_v4` to observe a metadata leak (where file write and size persistence works, but Block 0 superblock counts stay frozen at 251 blocks free instead of 250):
```bash
cd fs_demo_v4/
./verify_v4_correct.sh
```

### 5. Version 5: Fully Corrected and Synchronized Filesystem
To run the verification script for `fs_demo_v5` to prove that the fully synchronized driver successfully flushes both data blocks, bitmaps, and on-disk superblock counters ($256 - 6 = 250$ free blocks on disk) across mount boundaries:
```bash
cd fs_demo_v5/
./verify_v5_correct.sh
```
