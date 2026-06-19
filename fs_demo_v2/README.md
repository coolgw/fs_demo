# demov2 Tests Summary (Transient Inode Bug)

This document synthesizes and delivers the test steps and technical details of the buggy `demov2` (Post-Remount Size 0 / Transient Inode) experiment.

Before starting the test, compile the user-space `mkfs` tool (included in the subdirectory):

```bash
gcc -Wall -o mkfs.demofs mkfs.demofs.c
```

---

## Part 1: Step-by-Step Test Sequence

### 1. Copying & Compiling the Buggy Driver on the VM

Compile the `demofsv2.ko` module:

```bash
make -C /lib/modules/$(uname -r)/build M=$PWD modules
```

### 2. Formatting the Virtual Disk Image

Create and format a 1MB loopback disk image `/tmp/disk_v2.img`:

```bash
dd if=/dev/zero of=/tmp/disk_v2.img bs=4096 count=256
./mkfs.demofs /tmp/disk_v2.img
```

### 3. Loading the Module & Mounting

Load the module and mount the disk image to `/tmp/test_mount_v2`:

```bash
sudo insmod demofsv2.ko
mkdir -p /tmp/test_mount_v2
sudo mount -o loop -t demofs_v2 /tmp/disk_v2.img /tmp/test_mount_v2
```

### 4. Writing Data and Checking Size (Mounted)

Write some text to a file. At this point, the file size will appear correct in the active VFS/page cache:

```bash
echo 'Transient Inode Bug!' > /tmp/test_mount_v2/diskfile.txt
ls -la /tmp/test_mount_v2/diskfile.txt
# Output shows size 21 (correct)
```

### 5. Triggering the Transient Inode Bug (Unmount & Remount)

Unmount the directory and mount it back. Because the inode was immediately evicted and its dirty metadata was discarded, the file size on disk is still 0:

```bash
sudo umount /tmp/test_mount_v2
sudo mount -o loop -t demofs_v2 /tmp/disk_v2.img /tmp/test_mount_v2
```

### 6. Observing the Bug

List the files and check the size:

```bash
ls -la /tmp/test_mount_v2/diskfile.txt
# Output shows size 0!
cat /tmp/test_mount_v2/diskfile.txt
# Output is completely empty!
```

---

## Part 2: Code Details: Buggy vs. Fixed

### The Buggy Code (`demofs_v2.c`)

The bug is triggered by implementing the `drop_inode` callback in the superblock operations to return `1`. This forces the VFS to immediately evict inodes when their reference count drops to 0, which happens as soon as the file descriptor is closed in user-space:

```c
static int demofs_drop_inode(struct inode *inode)
{
    return 1; /* BUG: Forces immediate inode eviction, discarding dirty state/size metadata before writeback! */
}

static const struct super_operations demofs_ops = {
    .statfs         = simple_statfs,
    .drop_inode     = demofs_drop_inode, /* BUGGY drop_inode callback */
    .write_inode    = demofs_write_inode,
};
```

### The Fixed Code (`demofs_v3.c` & `demofs_v4.c`)

The fix is simple: remove the custom `drop_inode` callback entirely, letting the VFS manage the inode lifecycle. This allows the VFS to keep the inode cached in memory so that its dirty metadata can be successfully written back to the disk block via `write_inode`:

```c
static const struct super_operations demofs_ops = {
    .statfs         = simple_statfs,
    /* FIXED: No custom drop_inode callback! Default caching behavior allows successful metadata writeback. */
    .write_inode    = demofs_write_inode,
};
```

---

## Part 3: Why Immediate Eviction Causes Metadata Loss

When you write to a file, the inode is modified in memory (e.g., `inode->i_size` is updated in `demofs_write_end`/`generic_write_end`), and the inode is marked as **dirty**. 

Under normal VFS caching:
1. The inode remains in the VFS inode cache even after user processes close the file.
2. Background writeback threads or an explicit `sync`/`umount` eventually flush the dirty inode to the disk using the filesystem's `write_inode` callback.
3. Once clean, the inode can be safely reclaimed later.

Under immediate eviction (`drop_inode` returns `1`):
1. As soon as the file is closed (reference count drops to 0), the VFS immediately evicts the inode.
2. In the eviction process, the dirty state of the inode is discarded without being written to disk first.
3. Consequently, the disk superblock/inode blocks never receive the updated size metadata. When you remount, the inode is re-read from disk, showing its stale (initial) size of `0` bytes.
