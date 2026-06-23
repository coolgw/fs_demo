# demov7 Verification Summary (Multi-Block File Support)

This document describes the design and verification steps of the `demov7` filesystem, which introduces full verification of files that span multiple blocks (specifically a 6000-byte file that requires two 4KB blocks to be stored).

Before starting verification, compile the user-space `mkfs` tool inside `fs_demo_v7`:

```bash
gcc -Wall -o mkfs.demofs mkfs.demofs.c
```

---

## Part 1: Design of Multi-Block File Support in demov7

In `demov7` (and the core layout template inherited from prior versions), files support multi-block allocation up to `DEMOFS_N_BLOCKS = 12` blocks (giving a maximum file size of 48KB):

### 1. Inode Allocation & Blocks Count
- When a file size exceeds 4KB (4096 bytes), the block layer maps logical file blocks to multiple physical disk blocks.
- The in-memory inode's metadata and disk inode table structure track:
  - **`size`**: Set dynamically to the actual write size (e.g. 6000 bytes).
  - **`blocks`**: Set to the number of physical blocks allocated to hold the file (e.g. `2` blocks).
  - **`block[N]`**: The direct block pointer array. For a 6000-byte file:
    - `block[0]` points to the first 4KB physical data block (e.g. Block 5).
    - `block[1]` points to the second 4KB physical data block (e.g. Block 6).

### 2. Physical Block Mapping (get_block)
The core kernel driver maps logical blocks dynamically:
- Page writes at logical block `0` call `demofs_get_block` with `iblock = 0`, which allocates and returns physical Block 5.
- Page writes at logical block `1` call `demofs_get_block` with `iblock = 1`, which allocates and returns physical Block 6.

Both blocks are marked as allocated in the Block Bitmap (Block 1).

---

## Part 2: Step-by-Step Verification Sequence

You can run our end-to-end automated verification test inside `fs_demo_v7`:

```bash
sudo ./verify_v7_correct.sh
```

This script performs the following sequence:

1. Compiles the user-space formatting utility.
2. Compiles the `demofsv7.ko` kernel module.
3. Formats a 1MB loopback disk image `/tmp/disk_v7.img`.
4. Inserts the `demofsv7.ko` kernel module.
5. Mounts the disk image at `/tmp/test_mount_v7`.
6. Creates a single file `/tmp/test_mount_v7/big_file.txt` of exactly **6000 bytes** composed entirely of 'A' characters.
7. Unmounts and remounts the filesystem to prove disk persistence.
8. Validates the existence, size (exactly 6000 bytes), and contents (exactly 6000 'A's) of the file, proving zero corruption.
9. Unmounts and runs the visual disk analyzer `analyze_disk.py` on `/tmp/disk_v7.img` to provide direct, visual mathematical proof of the multi-block layout on disk!
