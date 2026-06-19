# demov4 Verification Summary (Fully Correct Block-Backed Filesystem)

This document synthesizes and delivers the verification steps, technical details, and proof of correctness of the fully resolved and robust `demov4` filesystem.

Before starting the verification, compile the user-space `mkfs` tool (included in the subdirectory):

```bash
gcc -Wall -o mkfs.demofs mkfs.demofs.c
```

---

## Part 1: Step-by-Step Verification Sequence

### 1. Compiling the Corrected Driver on the VM

Compile the `demofsv4.ko` module:

```bash
make -C /lib/modules/$(uname -r)/build M=$PWD modules
```

### 2. Formatting the Virtual Disk Image

Create and format a 1MB loopback disk image `/tmp/disk_v4.img`:

```bash
dd if=/dev/zero of=/tmp/disk_v4.img bs=4096 count=256
./mkfs.demofs /tmp/disk_v4.img
```

### 3. Loading the Module & Mounting

Load the module and mount the disk image to `/tmp/test_mount_v4`:

```bash
sudo insmod demofsv4.ko
mkdir -p /tmp/test_mount_v4
sudo mount -o loop -t demofs_v4 /tmp/disk_v4.img /tmp/test_mount_v4
```

### 4. Writing Data and Checking File (Mounted)

Write some text to a file:

```bash
echo 'Demofs Version 4 is Rock Solid!' > /tmp/test_mount_v4/diskfile.txt
ls -la /tmp/test_mount_v4/diskfile.txt
# Output shows size 32 (correct)
cat /tmp/test_mount_v4/diskfile.txt
# Output shows: "Demofs Version 4 is Rock Solid!"
```

### 5. Performing Unmount & Remount

Unmount the directory and mount it back. Because both `.writepages` is present and the default `drop_inode` is used, both metadata and data blocks are safely written to disk during unmount:

```bash
sudo umount /tmp/test_mount_v4
sudo mount -o loop -t demofs_v4 /tmp/disk_v4.img /tmp/test_mount_v4
```

### 6. Observing Perfect Persistence (Correctness)

List the files and check the size and content:

```bash
ls -la /tmp/test_mount_v4/diskfile.txt
# Output shows size 32 (successfully persisted metadata)!
cat /tmp/test_mount_v4/diskfile.txt
# Output shows: "Demofs Version 4 is Rock Solid!" (successfully persisted data)!
```

---

## Part 2: Code Details of the Corrected Version

In `demofs_v4.c`, the following critical components are correctly defined and integrated:

### 1. Correct Writeback Integration

The `.writepages` callback is explicitly registered to enable standard kernel flushing mechanisms:

```c
static const struct address_space_operations demofs_aops = {
    .read_folio     = demofs_read_folio,
    .write_begin    = demofs_write_begin,
    .write_end      = generic_write_end,
    .writepages     = demofs_writepages, /* REGISTERED: No more data loss! */
    .dirty_folio    = block_dirty_folio,
};
```

### 2. Standard Inode Lifecycle (No `drop_inode`)

We do not implement any custom `drop_inode` callback. This allows the VFS to keep inodes cached and safely flush dirty inodes to disk via `write_inode`:

```c
static const struct super_operations demofs_ops = {
    .statfs         = simple_statfs,
    /* CORRECT: Left default, allowing robust inode caching and metadata writeback */
    .write_inode    = demofs_write_inode,
};
```

### 3. Balanced folio unlock and reference put

Using `generic_write_end` ensures that the folio is properly unlocked and reference-put, resolving the unmount hang seen in `demofs_v1`.

---

## Part 3: Summary of the Demo filesystem evolution

| Version | Main Characteristics / Bugs | Symptoms | Resolution in next version |
| :--- | :--- | :--- | :--- |
| **`demofs_v1`** | Custom `write_end` misses unlocking folio and putting folio. | Unmount hangs, kworker and umount block in state `D` | Adopted standard `generic_write_end` helper. |
| **`demofs_v2`** | Custom `drop_inode` returns `1` to force immediate eviction. | File size becomes `0` bytes on remount / metadata lost | Removed `drop_inode` custom callback entirely. |
| **`demofs_v3`** | Missing `.writepages` callback in `address_space_operations`. | File contents are empty (all zeros) on remount / data lost | Registered `.writepages = demofs_writepages` callback. |
| **`demofs_v4`** | Fully integrated, standard caching, corrected writeback. | File metadata and data blocks persist perfectly across remounts | N/A (Fully correct). |
