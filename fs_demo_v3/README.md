# demov3 Tests Summary (Missing Writepages / Data Loss Bug)

This document synthesizes and delivers the test steps and technical details of the buggy `demov3` (Missing Writepages / Silent Data Loss) experiment.

Before starting the test, compile the user-space `mkfs` tool (included in the subdirectory):

```bash
gcc -Wall -o mkfs.demofs mkfs.demofs.c
```

---

## Part 1: Step-by-Step Test Sequence

### 1. Copying & Compiling the Buggy Driver on the VM

Compile the `demofsv3.ko` module:

```bash
make -C /lib/modules/$(uname -r)/build M=$PWD modules
```

### 2. Formatting the Virtual Disk Image

Create and format a 1MB loopback disk image `/tmp/disk_v3.img`:

```bash
dd if=/dev/zero of=/tmp/disk_v3.img bs=4096 count=256
./mkfs.demofs /tmp/disk_v3.img
```

### 3. Loading the Module & Mounting

Load the module and mount the disk image to `/tmp/test_mount_v3`:

```bash
sudo insmod demofsv3.ko
mkdir -p /tmp/test_mount_v3
sudo mount -o loop -t demofs_v3 /tmp/disk_v3.img /tmp/test_mount_v3
```

### 4. Writing Data and Checking File (Mounted)

Write some text to a file. While mounted, reading the file works because it reads directly from the dirty Page Cache still held in RAM:

```bash
echo 'Silent Data Loss Bug!' > /tmp/test_mount_v3/diskfile.txt
ls -la /tmp/test_mount_v3/diskfile.txt
# Output shows size 22 (correct)
cat /tmp/test_mount_v3/diskfile.txt
# Output shows: "Silent Data Loss Bug!" (reads successfully from Page Cache)
```

### 5. Triggering the Data Loss Bug (Unmount & Remount)

Unmount the directory (which should flush dirty buffers) and mount it back. Because `.writepages` is missing, the dirty Page Cache is discarded during unmount without ever being written to disk:

```bash
sudo umount /tmp/test_mount_v3
sudo mount -o loop -t demofs_v3 /tmp/disk_v3.img /tmp/test_mount_v3
```

### 6. Observing the Bug

List the files and check the size and content:

```bash
ls -la /tmp/test_mount_v3/diskfile.txt
# Output shows size 22 (the inode metadata was successfully written by write_inode)!
cat /tmp/test_mount_v3/diskfile.txt
# Output is completely empty/blank! (the data blocks contain only zeros)
```

---

## Part 2: Code Details: Buggy vs. Fixed

### The Buggy Code (`demofs_v3.c`)

The bug is triggered by omitting the `.writepages` callback in the `address_space_operations` structure. The function `demofs_writepages` exists but is not registered:

```c
/* BUGGY ADDR OPS: Missing .writepages callback! File data is never flushed, leading to data loss on remount. */
static const struct address_space_operations demofs_aops = {
    .read_folio     = demofs_read_folio,
    .write_begin    = demofs_write_begin,
    .write_end      = generic_write_end, /* Correct write_end: no unmount hang */
    /* .writepages  = demofs_writepages,   <--- MISSING WRITE_PAGES! (Silent Data Loss) */
    .dirty_folio    = block_dirty_folio,
};
```

### The Fixed Code (`demofs_v4.c`)

The fix is to register the `.writepages` callback, mapping it to our filesystem-specific helper `demofs_writepages` (which delegates to the block layer helper `mpage_writepages`):

```c
static const struct address_space_operations demofs_aops = {
    .read_folio     = demofs_read_folio,
    .write_begin    = demofs_write_begin,
    .write_end      = generic_write_end,
    .writepages     = demofs_writepages, /* FIXED: Correctly registered writepages callback! */
    .dirty_folio    = block_dirty_folio,
};
```

---

## Part 3: Why Missing `.writepages` Causes Silent Data Loss

When you write to a file, the VFS performs a series of steps to handle the write:
1. It requests a Page (Folio) from the Page Cache via `read_folio` or `write_begin`.
2. It copies the user-space data into the Page Cache.
3. It marks the Page Cache page as **dirty**.

At this stage, the data is **only** in RAM (Page Cache). It has not been written to the physical blocks of the loopback image file `/tmp/disk_v3.img`.

When an unmount, sync, or memory reclamation occurs:
1. The kernel attempts to flush the dirty page cache pages to disk.
2. It looks up the `.writepages` callback in the file's `address_space_operations`.
3. **The Block/Loss**: Since `.writepages` is NULL/missing, the kernel doesn't know how to flush these pages. It skips writing them and silently evicts/discards the pages from the Page Cache.
4. However, the metadata update (file size = 22) is processed separately by the inode/superblock layer using `write_inode`, which successfully writes the updated inode size to disk.
5. Consequently, upon remount, the inode metadata indicates a file size of 22, but the underlying disk blocks allocated to the file were never written with the data and still contain all zeros.
