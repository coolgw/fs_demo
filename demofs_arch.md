# Linux Filesystem Driver Architecture & Training Guide (demofs v4)

This document is a comprehensive, production-grade architectural specification and developer training guide for writing a custom block-backed Linux filesystem driver. It is based on **`demofs` (Version 4)**, which is a fully correct, persistent, block-device backed Linux filesystem.

Using `demofs` as our primary teaching vehicle, this guide explains how the Linux **Virtual Filesystem (VFS)** layer interacts with custom file drivers, how block-mapping converts file offsets to block device sectors, how the page-cache manages dirty pages, and how on-disk indexes are structured.

---

## Table of Contents
1. **The VFS Architecture Model**
2. **On-Disk Layout & Physical Representation**
3. **Core Driver Interfaces & Structures**
4. **The Mounting Pipeline & Modern Mount API**
5. **Inode & Physical Block Allocation**
6. **Block Mapping & File I/O Pipeline**
7. **The Address Space Operations & Folio Model**
8. **Directory Operations, Dentries & Indexing**
9. **Evolutionary Bugs & Lessons in Driver Robustness**
10. **Step-by-Step Guide to Writing Your First Filesystem**
11. **Binary-Level Disk Analysis & Manual Path Resolution**

---

## 1. The VFS Architecture Model

The Linux kernel uses the **Virtual Filesystem (VFS)** as an abstraction layer. The VFS defines a set of abstract interfaces and structures that all filesystem drivers must implement. This design enables userspace applications to use standard system calls (`open`, `read`, `write`, `stat`, `unlink`, etc.) uniformly, regardless of the underlying storage medium (EXT4, XFS, NFS, or our custom `demofs`).

```text
               +-------------------------------------------+
               |             Userspace App                 |
               |       (open, read, write, mkdir...)       |
               +---------------------|---------------------+
                                     | (Syscall Interface)
                                     v
               +-------------------------------------------+
               |               VFS Layer                   |
               +---------------------|---------------------+
                                     |
         +---------------------------+---------------------------+
         | (Page Cache)              | (Superblock/Inode Ops)    | (Path Resolution)
         v                           v                           v
+------------------+       +-------------------+       +------------------+
|  Address Space   |       |    Superblock     |       |      Dentry      |
|     (Folios)     |       |    Operations     |       |    Cache (dcache)|
+--------|---------+       +---------|---------+       +--------|---------+
         |                           |                          |
         +---------------------------+--------------------------+
                                     | (Custom Driver Implementation)
                                     v
                       +---------------------------+
                       |       demofs_v4.ko        |
                       +-------------|-------------+
                                     | (Block I/O Requests via buffer_head)
                                     v
                       +---------------------------+
                       |   Block Layer & Drivers   |
                       |     (/dev/loopX, etc.)    |
                       +---------------------------+
```

### The Four Crucial VFS Objects
1. **Superblock (`struct super_block`)**: Represents a mounted filesystem instance. It holds global metadata (block size, magic number, root directory reference) and points to `super_operations` for managing inodes and global states.
2. **Inode (`struct inode`)**: Represents a discrete file or directory object in memory. It contains file metadata (owner, permissions, size, modification times) but **does not** contain the filename or raw file contents. It points to `inode_operations` (metadata manipulations) and `file_operations` (I/O execution).
3. **Dentry (`struct dentry`)**: Represents a specific directory entry mapping a filename string to a unique inode number. Dentries are heavily cached (`dcache`) to accelerate path-resolution (e.g., resolving `/usr/bin/git`).
4. **File (`struct file`)**: Represents an active, open file instance created by a process. It keeps track of process-specific offsets (`f_pos`), access modes (`O_RDONLY`/`O_WRONLY`), and references the associated dentry and inode.

---

## 2. On-Disk Layout & Physical Representation

`demofs` is a block-backed filesystem. This means it formats and reads/writes blocks of a virtual or physical block device (such as `/dev/loop0`).

### On-Disk Block Grid
The filesystem is divided into static, equal-sized **Blocks** of **4096 bytes** (matching the default host page size).

```text
Block Offset:
 ┌───────────────┬─────────────────┬─────────────────┬─────────────────┬─────────────────┐
 │    Block 0    │     Block 1     │     Block 2     │     Block 3     │   Block 4~255   │
 ├───────────────┼─────────────────┼─────────────────┼─────────────────┼─────────────────┤
 │  Superblock   │  Block Bitmap   │  Inode Bitmap   │   Inode Table   │   Data Blocks   │
 │ (Global Meta) │ (Alloc Track)   │ (Alloc Track)   │ (32 Inode Slots)│ (Raw Contents)  │
 └───────────────┴─────────────────┴─────────────────┴─────────────────┴─────────────────┘
```

### On-Disk C Structures (defined in `demofs.h`)

#### A. The On-Disk Superblock
Occupies the very first block (`DEMOFS_SUPER_BLOCK_NUM = 0`). It identifies the device and describes the basic limits:
```c
struct demofs_super_block {
    __le32 magic;          /* DEMOFS_MAGIC = 0xdeeb00ff */
    __le32 block_size;     /* Block Size in bytes (4096) */
    __le32 blocks_count;   /* Total blocks on device */
    __le32 inodes_count;   /* Total inodes in table */
    __le32 free_blocks;    /* Number of unallocated blocks */
    __le32 free_inodes;    /* Number of unallocated inode slots */
};
```

#### B. The On-Disk Inode
`demofs` pre-allocates an **Inode Table** at Block 3. Each inode on disk occupies `sizeof(struct demofs_inode)` bytes, with a hard maximum of 32 inodes:
```c
#define DEMOFS_N_BLOCKS 12  /* Direct block addressing pointers */

struct demofs_inode {
    __le16 mode;                      /* File type & permissions (S_IFREG, S_IFDIR, etc.) */
    __le16 uid;                       /* Owner User ID */
    __le16 gid;                       /* Owner Group ID */
    __le16 links_count;               /* Number of directory links */
    __le32 size;                      /* File size in bytes */
    __le32 blocks;                    /* Number of 4KB blocks allocated to this file */
    __le32 block[DEMOFS_N_BLOCKS];    /* Physical block block numbers mapping offset 0 to 11 */
};
```

#### C. The On-Disk Directory Entry (Dentry)
A directory file's data blocks contain array entries of directory records mapping names to inode indexes. Each entry is **64 bytes** in size:
```c
#define DEMOFS_NAME_LEN 60

struct demofs_dir_entry {
    __le32 inode;                  /* Inode number slot mapped to name */
    char name[DEMOFS_NAME_LEN];    /* Null-terminated filename string */
};
```

---

## 3. Core Driver Interfaces & Structures

A filesystem driver interacts with the VFS by registering a `file_system_type` structure and exposing pointers to operation tables (`super_operations`, `inode_operations`, `file_operations`, `address_space_operations`).

Here is how these structures are declared and linked in `demofs_v4.c`:

```c
static const struct address_space_operations demofs_aops = {
    .read_folio  = demofs_read_folio,   /* Page cache read entry */
    .write_begin = demofs_write_begin,  /* Page cache write reservation */
    .write_end   = generic_write_end,   /* Releases dirty page cache folio */
    .writepages  = demofs_writepages,   /* Commits dirty memory folios to block layer */
    .dirty_folio = block_dirty_folio,   /* Marks cache pages as modified */
};

static const struct file_operations demofs_file_operations = {
    .read_iter    = generic_file_read_iter,   /* standard page cache reader */
    .write_iter   = generic_file_write_iter,  /* standard page cache writer */
    .mmap         = generic_file_mmap,        /* enables mmap memory mappings */
    .fsync        = noop_fsync,               /* flush barrier (noop for simplicity) */
    .splice_read  = filemap_splice_read,      /* optimal pipeline transfer */
    .splice_write = iter_file_splice_write,     /* optimal pipeline write */
    .llseek       = generic_file_llseek,      /* file offset seeker */
};

static const struct inode_operations demofs_file_inode_operations = {
    .setattr      = simple_setattr, /* sets size, permissions */
    .getattr      = simple_getattr, /* gets size, timestamps */
};

static const struct inode_operations demofs_dir_inode_operations = {
    .create       = demofs_create,  /* creates regular files */
    .lookup       = demofs_lookup,  /* maps filename to inode */
    .unlink       = demofs_unlink,  /* unlinks files (deletion) */
};

static const struct file_operations demofs_dir_operations = {
    .read           = generic_read_dir,     /* prevents reading directories as files */
    .iterate_shared = demofs_readdir,       /* drives ls / directory listings */
    .llseek         = generic_file_llseek,
};

static const struct super_operations demofs_ops = {
    .statfs       = simple_statfs,      /* tracks free space metrics */
    .write_inode  = demofs_write_inode, /* saves dirty inodes back to disk */
};
```

---

## 4. The Mounting Pipeline & Modern Mount API

When a user executes `mount -t demofs_v4 /dev/loop0 /mnt`, the kernel initiates mounting. Modern Linux kernels (starting with 5.x/6.x) use the **new mount API** based on **Filesystem Contexts (`struct fs_context`)**.

```text
mount() Syscall
  │
  ▼
demofs_init_fs_context()
  │
  ├──► Assigns context operations (.get_tree)
  │
  ▼
demofs_get_tree() 
  │
  ├──► Calls get_tree_bdev() (Block Device Mount Handler)
  │      │
  │      ▼
  └──► demofs_fill_super()  [THE MAIN CONSTRUCTOR]
         │
         ├──► sb_set_blocksize(sb, 4096)
         ├──► sb_bread(sb, 0) -> Reads physical superblock
         ├──► Validates magic (0xdeeb00ff)
         ├──► Instantiate root inode via demofs_iget(sb, 1)
         └──► Creates root dentry via d_make_root(root_inode)
```

### Mount API Implementation

The module registration serves as the entryway:
```c
static struct file_system_type demofs_fs_type = {
    .owner            = THIS_MODULE,
    .name             = "demofs_v4",
    .init_fs_context  = demofs_init_fs_context,  /* Mount context constructor */
    .kill_sb          = kill_block_super,         /* Clean up superblock on unmount */
    .fs_flags         = FS_REQUIRES_DEV,          /* Requires physical/loop block device */
};
```

#### Initialization & Context Setup
```c
static int demofs_init_fs_context(struct fs_context *fc)
{
    fc->ops = &demofs_context_ops; /* Context operation table link */
    return 0;
}

static const struct fs_context_operations demofs_context_ops = {
    .get_tree = demofs_get_tree, /* drives mount mapping */
};

static int demofs_get_tree(struct fs_context *fc)
{
    /* get_tree_bdev is a standard block-layer helper that opens the block device
     * and triggers our callback "demofs_fill_super" to initialize the instance. */
    return get_tree_bdev(fc, demofs_fill_super);
}
```

#### Constructing the Superblock Instance
```c
static int demofs_fill_super(struct super_block *sb, struct fs_context *fc)
{
    struct buffer_head *bh;
    struct demofs_super_block *dsb;
    struct inode *root_inode;

    /* Set the physical memory block size on the underlying device buffer cache */
    if (!sb_set_blocksize(sb, DEMOFS_BLOCK_SIZE))
        return -EINVAL;

    /* Read block 0 containing the physical superblock */
    bh = sb_bread(sb, DEMOFS_SUPER_BLOCK_NUM);
    if (!bh) {
        pr_err("demofs: unable to read superblock\n");
        return -EIO;
    }

    dsb = (struct demofs_super_block *)bh->b_data;
    if (le32_to_cpu(dsb->magic) != DEMOFS_MAGIC) {
        pr_err("demofs: invalid magic number: 0x%x\n", le32_to_cpu(dsb->magic));
        brelse(bh);
        return -EINVAL;
    }

    /* Assign superblock states */
    sb->s_magic = le32_to_cpu(dsb->magic);
    sb->s_op = &demofs_ops;
    sb->s_time_gran = 1; /* Nanosecond timestamp granularity */

    brelse(bh); /* Release physical block reference */

    /* Instantiate the root directory inode (Ino 1) */
    root_inode = demofs_iget(sb, 1);
    if (IS_ERR(root_inode)) {
        pr_err("demofs: unable to get root inode\n");
        return PTR_ERR(root_inode);
    }

    /* Wrap root inode in a VFS dentry and bind to superblock root */
    sb->s_root = d_make_root(root_inode);
    if (!sb->s_root) {
        pr_err("demofs: unable to make root dentry\n");
        return -ENOMEM;
    }

    return 0;
}
```

---

## 5. Inode & Physical Block Allocation

`demofs` uses simple on-disk bitmaps to allocate resources.

- **Block Bitmap (Block 1)**: Tracks allocations of blocks 0–255 using single-bit markers.
- **Inode Bitmap (Block 2)**: Tracks 32 inode table slots using single-bit markers.

### In-Memory Resource Allocation Mechanics
Below is the atomic block search-and-allocate subroutine. It reads the bitmap block, scans for the first bit with a value of `0` (unallocated), marks it as `1` (allocated), and flushes the bitmap back to disk.

```c
static uint32_t demofs_allocate_block(struct super_block *sb)
{
    struct buffer_head *bh = sb_bread(sb, DEMOFS_BLOCK_BITMAP);
    unsigned char *bitmap;
    uint32_t block = 0;
    int i, j;

    if (!bh)
        return 0;

    bitmap = (unsigned char *)bh->b_data;
    for (i = 0; i < DEMOFS_BLOCK_SIZE; i++) {
        if (bitmap[i] != 0xFF) { /* Byte has at least one free bit */
            for (j = 0; j < 8; j++) {
                if (!(bitmap[i] & (1 << j))) { /* Found free bit! */
                    bitmap[i] |= (1 << j);     /* Set bit to 1 */
                    block = i * 8 + j;
                    mark_buffer_dirty(bh);     /* Mark buffer cache dirty */
                    brelse(bh);
                    return block;              /* Return block index */
                }
            }
        }
    }
    brelse(bh);
    return 0; /* Device full */
}
```

*Note: Inode allocation (`demofs_allocate_inode`) follows an identical bitmap structure, capped by `DEMOFS_MAX_INODES`.*

---

## 6. Block Mapping & File I/O Pipeline

When reading or writing data from or to a file, the VFS handles page allocations, while the custom filesystem maps file-logical offsets (e.g., offset 8192, block 2 of the file) to physical disk blocks.

This translation is performed by the **`get_block`** function.

```text
Filesystem Write Request (e.g., write block 2 of a file)
  │
  ▼
demofs_get_block()
  │
  ├──► Check if requested logical block index < 12 (Direct Limit)
  ├──► sb_bread(sb, DEMOFS_INODE_TABLE)
  ├──► Locate physical inode structure
  ├──► Read physical disk block address: di->block[iblock]
  │
  ├──► CASE A: Block number is non-zero (Already Allocated)
  │      │
  │      └──► map_bh(bh_result, sb, phys_block) -> Direct mapping
  │
  └──► CASE B: Block number is zero & create=1 (Write/Allocation Required)
         │
         ├──► demofs_allocate_block(sb) -> Allocate new block
         ├──► di->block[iblock] = cpu_to_le32(allocated_block)
         ├──► di->blocks++ (increment block count)
         ├──► mark_buffer_dirty(bh_inode) -> Dirty inode table block
         └──► map_bh(bh_result, sb, allocated_block)
```

### The Block Mapping Core Function
```c
static int demofs_get_block(struct inode *inode, sector_t iblock,
                            struct buffer_head *bh_result, int create)
{
    struct super_block *sb = inode->i_sb;
    struct buffer_head *bh_inode;
    struct demofs_inode *di;
    uint32_t phys_block = 0;
    int ret = 0;

    if (iblock >= DEMOFS_N_BLOCKS)
        return -EFBIG; /* Enforce direct block count limit of 12 (Max file size 48KB) */

    /* Read Inode Table block containing on-disk metadata */
    bh_inode = sb_bread(sb, DEMOFS_INODE_TABLE);
    if (!bh_inode)
        return -EIO;

    di = (struct demofs_inode *)bh_inode->b_data + inode->i_ino;
    phys_block = le32_to_cpu(di->block[iblock]);

    if (phys_block == 0) {
        if (!create) { /* If create is 0, we treat unmapped blocks as holes */
            brelse(bh_inode);
            return 0;
        }

        /* Create mode: Allocate a physical block on-demand */
        phys_block = demofs_allocate_block(sb);
        if (phys_block == 0) {
            brelse(bh_inode);
            return -ENOSPC; /* No space left on device */
        }

        /* Write block assignment to disk inode cache */
        di->block[iblock] = cpu_to_le32(phys_block);
        di->blocks = cpu_to_le32(le32_to_cpu(di->blocks) + 1);
        
        mark_buffer_dirty(bh_inode); /* Ensure the physical inode table update is written */
    }

    brelse(bh_inode);
    
    /* map_bh updates the output bh_result structure with the physical block number,
     * signaling to the kernel block-subsystem how to read/write this sector */
    map_bh(bh_result, sb, phys_block);
    return ret;
}
```

---

## 7. The Address Space Operations & Folio Model

Modern Linux kernels (6.x and higher) manage memory pages using **Folios (`struct folio`)** instead of naked `struct page` structures. A folio is an active, structured region of memory cache belonging to a single file's address space.

```text
                   Page Cache Read Operation
                  ┌────────────────────────┐
                  │ generic_file_read_iter │
                  └───────────┬────────────┘
                              │ (Page cache lookup / allocating a Folio)
                              ▼
                  ┌────────────────────────┐
                  │   demofs_read_folio    │
                  └───────────┬────────────┘
                              │
                              ▼
                ┌────────────────────────────┐
                │ block_read_full_folio      │
                │ (Calls demofs_get_block)   │
                └────────────────────────────┘
```

The driver links its physical block-mapper (`demofs_get_block`) to these VFS folio subroutines.

### Address Space Operations Details

#### A. Read Pipeline (`read_folio`)
This callback is triggered when the kernel needs to read a block into the memory cache. The block layer's standard `block_read_full_folio` automatically queries our `demofs_get_block` mapper to translate physical sectors:
```c
static int demofs_read_folio(struct file *file, struct folio *folio)
{
    return block_read_full_folio(folio, demofs_get_block);
}
```

#### B. Write Reservation Pipeline (`write_begin`)
Triggered when standard system calls like `write` reserve a write cache buffer. It allocates or prepares the folio for modification, mapping physical blocks:
```c
static int demofs_write_begin(const struct kiocb *iocb, struct address_space *mapping,
                              loff_t pos, unsigned len,
                              struct folio **foliop, void **fsdata)
{
    return block_write_begin(mapping, pos, len, foliop, demofs_get_block);
}
```

#### C. Flushing Memory back to physical Disk (`writepages`)
This is where **`demofs_v3`** failed. When `sync`, `unmount`, or background reclaim processes execute, the system flushes memory folios back to physical disk sectors using `writepages`. 
```c
static int demofs_writepages(struct address_space *mapping, struct writeback_control *wbc)
{
    /* mpage_writepages aggregates multiple dirty cache pages and streams
     * block I/O requests down to disk using our custom block-mapping routine */
    return mpage_writepages(mapping, wbc, demofs_get_block);
}
```

---

## 8. Directory Operations, Dentries & Indexing

In `demofs`, directories are structured as files whose data blocks contain an array of `struct demofs_dir_entry` items.

### A. Directory Lookup (`demofs_lookup`)
When VFS resolves a path like `/testfile.txt` in a directory, it executes `lookup`. This function reads the directory's blocks, compares each entry's string name, and loads the corresponding inode:

```c
static struct dentry *demofs_lookup(struct inode *dir, struct dentry *dentry, unsigned int flags)
{
    struct super_block *sb = dir->i_sb;
    struct buffer_head *bh;
    struct demofs_dir_entry *de;
    struct inode *inode = NULL;
    int i;

    /* Find the physical block containing directory items (logical block index 0) */
    struct buffer_head bh_map = {0};
    int err = demofs_get_block(dir, 0, &bh_map, 0);
    if (err || bh_map.b_blocknr == 0)
        return d_splice_alias(NULL, dentry); /* Empty directory, resolve as negative dentry */

    bh = sb_bread(sb, bh_map.b_blocknr);
    if (!bh)
        return ERR_PTR(-EIO);

    de = (struct demofs_dir_entry *)bh->b_data;
    for (i = 0; i < DEMOFS_BLOCK_SIZE / sizeof(struct demofs_dir_entry); i++) {
        if (le32_to_cpu(de[i].inode) > 0 && 
            strcmp(de[i].name, dentry->d_name.name) == 0) {
            
            /* Match found! Load the memory inode via our iget routine */
            inode = demofs_iget(sb, le32_to_cpu(de[i].inode));
            brelse(bh);
            
            /* Bind the loaded inode to the VFS dentry structure */
            return d_splice_alias(inode, dentry);
        }
    }

    brelse(bh);
    return d_splice_alias(NULL, dentry); /* Return negative dentry if file not found */
}
```

### B. Directory Creation (`demofs_create`)
To create a new regular file within a directory:
1. Allocate an inode index from the physical bitmap (`demofs_allocate_inode`).
2. Instantiate a fresh VFS in-memory inode (`new_inode`).
3. Set the default values (permissions, times, block links) and assign file operations.
4. Insert it into the kernel's active hash table using `insert_inode_hash` (critical so future lookups can locate it).
5. Write the physical structure details directly to the disk's inode table block.
6. Write a new entry in the parent directory's block layout using `demofs_add_dir_entry`.
7. Link them using `d_instantiate`.

```c
static int demofs_create(struct mnt_idmap *idmap, struct inode *dir,
                         struct dentry *dentry, umode_t mode, bool excl)
{
    struct super_block *sb = dir->i_sb;
    struct inode *inode;
    uint32_t ino;
    struct buffer_head *bh_inode;
    struct demofs_inode *di;
    int err;

    ino = demofs_allocate_inode(sb);
    if (ino == 0)
        return -ENOSPC;

    inode = new_inode(sb);
    if (!inode)
        return -ENOMEM;

    inode->i_ino = ino;
    inode->i_mode = mode;
    i_uid_write(inode, from_kuid(&init_user_ns, current_fsuid()));
    i_gid_write(inode, from_kgid(&init_user_ns, current_fsgid()));
    set_nlink(inode, 1);
    inode->i_size = 0;
    simple_inode_init_ts(inode);

    inode->i_op = &demofs_file_inode_operations;
    inode->i_fop = &demofs_file_operations;
    inode->i_mapping->a_ops = &demofs_aops;

    insert_inode_hash(inode); /* Make the inode searchable in VFS cache */

    /* Commit metadata to physical disk inode block */
    bh_inode = sb_bread(sb, DEMOFS_INODE_TABLE);
    if (!bh_inode) {
        iput(inode);
        return -EIO;
    }

    di = (struct demofs_inode *)bh_inode->b_data + ino;
    memset(di, 0, sizeof(struct demofs_inode));
    di->mode = cpu_to_le16(inode->i_mode);
    di->uid = cpu_to_le16(i_uid_read(inode));
    di->gid = cpu_to_le16(i_gid_read(inode));
    di->links_count = cpu_to_le16(inode->i_nlink);
    di->size = cpu_to_le32(inode->i_size);
    di->blocks = cpu_to_le32(0);

    mark_buffer_dirty(bh_inode);
    brelse(bh_inode);

    /* Insert filename mapping into parent directory data block */
    err = demofs_add_dir_entry(dir, dentry, ino);
    if (err) {
        iput(inode);
        return err;
    }

    d_instantiate(dentry, inode); /* Associate VFS dentry with the new inode */
    return 0;
}
```

---

## 9. Evolutionary Bugs & Lessons in Driver Robustness

During the development of `demofs`, several bugs were introduced and fixed. These highlight the complexity of the VFS state model:

### Case 1: Unmount Hang (The folio Reference Leak)
- **Bug**: In version 1, `write_end` returned directly without calling `folio_unlock` and `folio_put`.
- **Consequence**: The modified cache page remained locked and flagged as busy. When the user ran `umount`, the writeback worker tried to acquire the page lock to flush it to disk, causing the thread to block indefinitely. The `umount` thread also blocked waiting for writeback to complete, resulting in a **deadlock**.
- **Fix**: Use `generic_write_end`, which automatically unlocks the folio and decrements its reference count.

### Case 2: Post-Remount Size 0 (The Transient Inode Bug)
- **Bug**: In version 2, `drop_inode` in `super_operations` was overridden to return `1`.
- **Consequence**: This forced the VFS to immediately evict the inode from cache as soon as the file descriptor was closed in userspace. The dirty flag on the inode was discarded before writeback could flush it, resulting in the file size resetting to `0` bytes on remount.
- **Fix**: Do not override `drop_inode` unnecessarily. Let the VFS manage the inode lifecycle. This ensures that dirty metadata is safely flushed to disk via `write_inode`.

### Case 3: Silent Data Loss (The Missing `.writepages` Bug)
- **Bug**: In version 3, `.writepages` was omitted from `address_space_operations`.
- **Consequence**: When writing data, the changes were stored in memory (Page Cache). However, during unmount or when `sync` was run, the kernel was unable to write the dirty pages to disk because there was no `.writepages` callback. The dirty pages were discarded, resulting in silent data loss.
- **Fix**: Register `.writepages` to point to a block-mapping writeback function like `mpage_writepages`.

### Case 4: On-Disk Metadata Leak (The Missing Superblock Write-back Bug)
- **Bug**: In version 4, while the driver resolved earlier unmount and page-cache caching bugs, it neglected to update and write back Block 0 (the on-disk Superblock structure) when a physical block or inode was allocated inside `demofs_allocate_block` and `demofs_allocate_inode`.
- **Consequence**: The raw bitmaps on disk recorded the allocations correctly (showing Block 5 and Inode 2 allocated). However, the on-disk superblock counters (`free_blocks` and `free_inodes`) remained frozen at their formatting values (`251` free blocks instead of the correct `250`). This resulted in a metadata desynchronization on disk, visible via hex analyses or diagnostics.
- **Fix (Implemented in Version 5)**: Introduce an on-disk metadata write-back helper function `demofs_adjust_free_resources` that reads the superblock block using `sb_bread`, decrements the free counts dynamically, marks the buffer dirty, and flushes it back to Block 0 upon allocations.

---

## 10. Step-by-Step Guide to Writing Your First Filesystem

Follow this step-by-step checklist to design, implement, and test your own block-backed filesystem driver:

### Step 1: Define Your Disk Layout
- Determine your block size (usually 4KB to match the system page size).
- Design your on-disk structures (superblock, inode slots, directory entry records).
- Maintain proper byte-alignment and use little-endian variables (`__le32`, `__le16`) to ensure portability across different CPU architectures.

### Step 2: Implement a Userspace Formatting Tool (`mkfs`)
- Write a userspace utility (like `mkfs.demofs.c`) that initializes your on-disk layouts.
- It should open a target file or block device, write the superblock magic number, clear resource bitmaps, pre-allocate the root inode (with directory permissions), and format empty blocks with zeros.

### Step 3: Implement Inode Management (`iget` and `write_inode`)
- Write a routine to load physical inodes from disk into VFS-managed memory inodes (`iget`).
- Link proper `inode_operations` and `file_operations` tables depending on the file type (regular file vs. directory).
- Implement a `write_inode` callback that saves updated memory inode metadata back to the on-disk inode table.

### Step 4: Implement a Block Mapping Function (`get_block`)
- Your block mapper is critical for file operations. It maps a logical file block index (`iblock`) to a physical block offset on the disk device.
- Handle physical block allocations when the `create` flag is set.

### Step 5: Connect to the Page Cache (Address Space Operations)
- Set up `address_space_operations` using standard block-layer helpers:
  - `.read_folio = block_read_full_folio`
  - `.write_begin = block_write_begin`
  - `.write_end = generic_write_end`
  - `.writepages = mpage_writepages`
  - `.dirty_folio = block_dirty_folio`
- These standard helpers rely on your custom `get_block` function to execute read and write operations.

### Step 6: Implement Directory and Metadata Operations
- Implement directory listings (`readdir`) by reading directory data blocks and emitting records using `dir_emit`.
- Implement path resolution (`lookup`) and file creation (`create`) / deletion (`unlink`) operations.

### Step 7: Define the Mount Entrypoints
- Declare your `file_system_type` structure.
- Define mounting callbacks using the modern `fs_context_operations` pipeline.
- Implement a module initialization function (`module_init`) that registers your driver using `register_filesystem`.

### Step 8: Build and Test Your Driver
- Create a Makefile that builds your driver as a kernel module against your current kernel version.
- Use loopback devices to format and test your filesystem safely inside a virtual machine (VM):
  ```bash
  # Create a virtual disk image
  dd if=/dev/zero of=disk.img bs=4096 count=256
  
  # Format using your mkfs tool
  ./mkfs.myfs disk.img
  
  # Load the module
  sudo insmod myfs.ko
  
  # Mount the image
  mkdir -p /mnt/test
  sudo mount -o loop -t myfs disk.img /mnt/test
  ```
- Use tools like `dmesg` to monitor kernel outputs, and test your driver under various I/O loads to ensure its stability.

---

## 11. Binary-Level Disk Analysis & Manual Path Resolution

To deeply understand a filesystem, you must be able to inspect a raw disk image and manually resolve files and directory paths. This section is a hands-on training module that details the on-disk binary layouts of `demofs` immediately after formatting and guides you through finding a specific file using raw hex analysis.

### A. Binary Structure of `disk.img` After `mkfs.demofs`

When `mkfs.demofs` is executed on a 1MB file (`disk.img`), the device size is $1048576$ bytes, which corresponds to exactly **256 blocks** (of 4KB each). The layout is structured as follows:

#### 1. Superblock (Block 0) — Offset `0x0000` to `0x0FFF` (0 to 4095 bytes)
The superblock holds the global parameters of the filesystem instance. In a hexadecimal representation (using little-endian format), the beginning of Block 0 looks like this:
- **`magic`**: `0xDEEB00FF` (Stored as `ff 00 eb de` at bytes 0-3)
- **`block_size`**: `4096` (`0x1000`, stored as `00 10 00 00` at bytes 4-7)
- **`blocks_count`**: `256` (`0x0100`, stored as `00 01 00 00` at bytes 8-11)
- **`inodes_count`**: `32` (`0x0020`, stored as `20 00 00 00` at bytes 12-15)
- **`free_blocks`**: `251` (Total 256 minus 5 metadata/root-data blocks; `0x00FB`, stored as `fb 00 00 00` at bytes 16-19)
- **`free_inodes`**: `30` (Max 32 minus Inode 0 [reserved] and Inode 1 [root directory]; `0x001E`, stored as `1e 00 00 00` at bytes 20-23)
- **Padding**: Bytes 24–4095 are filled with zeros.

#### 2. Block Bitmap (Block 1) — Offset `0x1000` to `0x1FFF` (4096 to 8191 bytes)
The block bitmap tracks the allocation state of physical blocks on disk. Since we have formatted the device and pre-allocated Block 0 (Superblock), Block 1 (Block Bitmap), Block 2 (Inode Bitmap), Block 3 (Inode Table), and Block 4 (Root Directory Data), the first **5 blocks** are occupied.
- **Allocation Mask**: The first byte of Block 1 contains `0x1F` (`0b00011111` in binary, representing allocated blocks 0, 1, 2, 3, and 4).
- **Disk representation**: `1f 00 00 00 ...` (rest of the 4096 bytes are `00`).

#### 3. Inode Bitmap (Block 2) — Offset `0x2000` to `0x2FFF` (8192 to 11263 bytes)
The inode bitmap tracks the allocation of the 32 pre-allocated inode slots in our inode table.
- Inode 0 is reserved/unused (bit 0 is set).
- Inode 1 is allocated to the root directory `/` (bit 1 is set).
- **Allocation Mask**: The first byte contains `0x03` (`0b00000011` in binary).
- **Disk representation**: `03 00 00 00 ...` (rest of the 4096 bytes are `00`).

#### 4. Inode Table (Block 3) — Offset `0x3000` to `0x3FFF` (12288 to 16383 bytes)
Each on-disk inode (`struct demofs_inode`) is **64 bytes** in size. The 4096-byte block comfortably stores our maximum of 32 inodes ($32 \times 64 = 2048$ bytes).
- **Inode 0 (Reserved)**: Bytes `0x3000` to `0x303F` (completely zeroed).
- **Inode 1 (Root Directory `/`)**: Bytes `0x3040` to `0x307F`:
  - **`mode`**: `S_IFDIR | 0755` = `0x41ED` (Stored as `ed 41` at bytes `0x3040`-`0x3041`)
  - **`uid`**: `0` (`00 00`)
  - **`gid`**: `0` (`00 00`)
  - **`links_count`**: `2` (`02 00` representing standard link count for a directory holding `.` and its own name entry)
  - **`size`**: `4096` (`00 10 00 00` at bytes `0x3048`-`0x304B`)
  - **`blocks`**: `1` (`01 00 00 00` at bytes `0x304C`-`0x304F`)
  - **`block[0]`**: Physical Block `4` (`04 00 00 00` at bytes `0x3050`-`0x3053`)
  - **`block[1..11]`**: All zeros (bytes `0x3054`-`0x307F`)

#### 5. Root Directory Data Block (Block 4) — Offset `0x4000` to `0x4FFF` (16384 to 20479 bytes)
Directories are formatted as files whose blocks contain array elements of `struct demofs_dir_entry`. Each entry is **64 bytes** (4-byte inode number + 60-byte filename string).
- **Entry 0 (`.`)**: Offset `0x4000` to `0x403F`:
  - `inode`: `1` (`01 00 00 00`)
  - `name`: `.` (`2e 00 ...` zero-filled)
- **Entry 1 (`..`)**: Offset `0x4040` to `0x407F`:
  - `inode`: `1` (`01 00 00 00`)
  - `name`: `..` (`2e 2e 00 ...` zero-filled)
- Entries 2–63**: Completely zero-filled.

---

### B. Under the Hood: Lifecycle of a Write Command

To understand how our disk volume gets populated, we must trace what occurs inside the Linux kernel and our `demofs_v5` driver when a user runs the following command:

```bash
echo -n "Demofs Version 5 is Perfectly Synchronized!" > /tmp/test_mount_v5/diskfile.txt
```

This simple userspace command triggers a series of low-level VFS and page-cache interactions:

#### Step 1: Inode and Path Creation (The `open` call with `O_CREAT`)
When the shell attempts to create `/tmp/test_mount_v5/diskfile.txt`:
1. The VFS enters path resolution and calls **`demofs_lookup`** on the root directory (`Inode 1`) for `"diskfile.txt"`. Since the file does not exist yet, the lookup returns `NULL`.
2. Because of the `O_CREAT` flag, the VFS invokes the root directory's **`.create`** callback, mapped to **`demofs_create`** in our driver:
   - **Inode Allocation**: `demofs_create` calls **`demofs_allocate_inode()`**, which reads Inode Bitmap (Block 2), allocates **Inode 2** (bit 2 is flipped from `0` to `1` changing the first bitmap byte from `0x03` to `0x07`), and writes the updated bitmap back.
   - **Superblock Update**: It calls **`demofs_adjust_free_resources(sb, 0, -1)`** to decrement the on-disk `free_inodes` count from `30` to `29`.
   - **Inode Instantiation**: It calls the kernel's `new_inode(sb)` helper to allocate a memory `struct inode` in RAM and hardcodes `inode->i_ino = 2`.
   - **On-Disk Metadata**: It writes the raw 64-byte `struct demofs_inode` structure for Inode 2 into the Inode Table block (Block 3) at offset `0x3080` (setting `mode` to `-rw-r--r--`, `size` to `0`, and block list `block[0..11]` to `0`).
   - **Directory Indexing**: It calls **`demofs_add_dir_entry`**, which reads Block 4 (Root directory block), locates Slot 2 (at offset `0x4080`), and records a new 64-byte `struct demofs_dir_entry` mapping `"diskfile.txt"` to Inode 2.
   - **Memory Registration**: It links the in-memory inode with the dentry cache via `d_instantiate` and returns the file descriptor back to userspace.

#### Step 2: Reserving Memory & Allocating Disk Space (The `write_begin` callback)
When the shell starts writing the 43 ASCII bytes of `"Demofs Version 5 is Perfectly Synchronized!"` into the file descriptor:
1. The VFS enters the standard page-cached writer pipeline (`generic_perform_write()`).
2. It calls **`demofs_aops.write_begin()`** for Logical Block offset `0` (`iblock = 0`).
3. `write_begin` calls the kernel standard buffer manager to allocate a fresh **Folio** in the Page Cache (RAM) and lock it.
4. It calls our **`demofs_get_block`** with `create = 1` (allocation-intent write):
   - `demofs_get_block` reads the Inode Table block (Block 3), extracts Inode 2's structure, and discovers `di->block[0]` is `0` (unallocated).
   - **Block Allocation**: It calls **`demofs_allocate_block`**, which reads the Block Bitmap (Block 1), allocates **Block 5** (bit 5 is flipped from `0` to `1` changing the bitmap byte from `0x1f` to `0x3f`), and flushes the bitmap back to disk.
   - **Superblock Update**: It calls **`demofs_adjust_free_resources(sb, -1, 0)`** to decrement the on-disk `free_blocks` count from `251` to `250`.
   - **Metadata Update**: It records `di->block[0] = 5` and increments `di->blocks = 1` inside Inode 2's structure, marking the Inode Table block as dirty.
   - **Buffer head mapping**: It calls `map_bh` to bind Physical Block 5 to the folio's buffer head.

#### Step 3: Copying Userspace Data to RAM
1. The kernel copies the 43 ASCII bytes `"Demofs Version 5 is Perfectly Synchronized!"` from user space directly into the locked Page Cache folio inside RAM.

#### Step 4: Finalizing In-Memory State (The `write_end` callback)
1. Once the memory copy is finished, the VFS invokes **`demofs_aops.write_end()`** (linked to standard `generic_write_end`):
   - It updates our in-memory inode size `inode->i_size` to `43` bytes and marks the in-memory inode structure as dirty in RAM.
   - It invokes **`block_dirty_folio()`** (the `.dirty_folio` callback), which marks the Page Cache folio as **dirty** (`PG_dirty = 1`), registering it to the address space's dirty list.
   - It unlocks the folio. At this point, the userspace `write()` syscall returns success. **The written data is currently 100% in RAM.**

#### Step 5: Committing the Page Cache to disk (The unmount / flush callback)
When our verification script calls `sudo umount /tmp/test_mount_v5` to safely unmount the directory:
1. The unmount system call initiates flushing of all active, dirty Page Cache folios belonging to our mount point.
2. It invokes our driver's writeback callback **`demofs_aops.writepages`**:
   - `demofs_writepages` delegates to `mpage_writepages(mapping, wbc, demofs_get_block)`.
   - The writepages engine locks our dirty folio, clears its `PG_dirty` flag, queries `demofs_get_block` (which immediately resolves Logical Block 0 $\rightarrow$ Physical Block 5), and submits an asynchronous Block I/O request (BIO) to copy the 43 bytes from RAM straight into **Block 5** of the physical loopback file.
3. Once the hardware write completions return, the loop mount is safely detached, leaving our `disk_v5.img` in a perfectly updated, persistent, and synchronized state!

---

### C. Walkthrough: Locating `/diskfile.txt` Manually on Disk

Suppose the `demofs_v5` driver has been loaded and mounted, and a user runs our synchronized test script:
```bash
echo -n "Demofs Version 5 is Perfectly Synchronized!" > /tmp/test_mount_v5/diskfile.txt
```
This write-back allocation creates a regular file. Now we want to inspect the binary `disk.img` using our visual analyzer `./analyze_disk.py` to trace this file's raw contents and locate its inode metadata.

```text
                               Manual Path Resolution Flow
                              ┌──────────────────────────┐
                              │    Resolve Root (Ino 1)  │
                              └─────────────┬────────────┘
                                            │
                                            ▼ Read Inode Table Block 3 (Offset 0x3000)
                              ┌──────────────────────────┐
                              │  Root Inode at 0x3040    │
                              │  Maps block[0] = Block 4 │
                              └─────────────┬────────────┘
                                            │
                                            ▼ Read Directory Data Block 4 (Offset 0x4000)
                              ┌──────────────────────────┐
                              │  Scan entries for matching│
                              │  "diskfile.txt"          │
                              │  Found entry: Inode = 2  │
                              └─────────────┬────────────┘
                                            │
                                            ▼ Read Inode Table Block 3 (Offset 0x3000)
                              ┌──────────────────────────┐
                              │  File Inode 2 at 0x3080  │
                              │  Maps block[0] = Block 5 │
                              └─────────────┬────────────┘
                                            │
                                            ▼ Read File Data Block 5 (Offset 0x5000)
                              ┌──────────────────────────┐
                              │  Extract raw ASCII bytes │
                              │"Demofs Version 5 is Per-"│
                              └──────────────────────────┘
```

Here is the exact step-by-step translation process, completely mapped to our analyzer's output:

#### Step 1: Read Root Inode to Locate Root Directory Data
1. All directory traversals begin at the root directory, which is hard-coded as **Inode 1**.
2. Calculate the byte offset of Inode 1 in the Inode Table:
   $$\text{Offset} = \text{Block 3 Offset} + (\text{Inode Number} \times 64 \text{ bytes}) = 12288 + (1 \times 64) = 12352 \text{ bytes} = \text{0x3040}$$
3. Look at the Block 3 hex dump at `0x3040`:
   `0x3040:  ed 41 00 00 00 00 02 00  00 10 00 00 01 00 00 00`
   `0x3050:  04 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00`
   - In the block pointers table (bytes `0x3050`-`0x3053`), we extract `block[0]` which reads **`04 00 00 00`** (little-endian for **`4`**). This tells us that the root directory entries are stored in physical **Block 4**.

#### Step 2: Read Root Directory Data (Block 4) to Find "diskfile.txt"
1. Calculate the byte offset of Block 4:
   $$\text{Offset} = 4 \times 4096 = 16384 \text{ bytes} = \text{0x4000}$$
2. Scan the 64-byte directory entry slots inside Block 4 at `0x4000`:
   - **Slot 0 (`0x4000` - `0x403F`)**: Inode `1`, Name `.`
   - **Slot 1 (`0x4040` - `0x407F`)**: Inode `1`, Name `..`
   - **Slot 2 (`0x4080` - `0x40BF`)**:
     `0x4080:  02 00 00 00 64 69 73 6b  66 69 6c 65 2e 74 78 74   |....diskfile.txt|`
     - `inode`: **`02 00 00 00`** $\rightarrow$ **Inode 2** (decimal).
     - `name`: `64 69 73 6b 66 69 6c 65 2e 74 78 74` $\rightarrow$ **`"diskfile.txt"`** in ASCII.
3. This match confirms that `/diskfile.txt` is dynamically bound to **Inode 2**.

#### Step 3: Read File Inode 2 to Locate Physical Data Blocks
1. Calculate the byte offset of Inode 2 in the Inode Table:
   $$\text{Offset} = 12288 + (2 \times 64) = 12416 \text{ bytes} = \text{0x3080}$$
2. Look at the Block 3 hex dump at Inode 2 (`0x3080` to `0x30BF`):
   `0x3080:  a4 81 00 00 00 00 01 00  2c 00 00 00 01 00 00 00`
   `0x3090:  05 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00`
3. Extract Inode 2's dynamic parameters:
   - **`mode`** (`0x3080`-`0x3081`): `a4 81` $\rightarrow$ `0x81a4` (Regular regular file `-rw-r--r--`).
   - **`size`** (`0x3088`-`0x308B`): `2c 00 00 00` $\rightarrow$ **`44` bytes** in decimal ($2\text{C} \text{ hex} = 44$).
   - **`blocks`** (`0x308C`-`0x308F`): `01 00 00 00` $\rightarrow$ `1` block allocated.
   - **`block[0]`** (`0x3090`-`0x3093`): **`05 00 00 00`** $\rightarrow$ **Physical Block `5`**.

#### Step 4: Extract File Contents from Block 5
1. Calculate the byte offset of physical Block 5:
   $$\text{Offset} = 5 \times 4096 = 20480 \text{ bytes} = \text{0x5000}$$
2. Read 44 bytes from `0x5000`. The raw hex is:
   `5000:  44 65 6d 6f 66 73 20 56  65 72 73 69 6f 6e 20 35   |Demofs Version 5|`
   `5010:  20 69 73 20 50 65 72 66  65 63 74 6c 79 20 53 79   | is Perfectly Sy|`
   `5020:  6e 63 68 72 6f 6e 69 7a  65 64 21                  |nchronized!|`
3. This translates directly to our written dynamic content:
   `Demofs Version 5 is Perfectly Synchronized!`

By utilizing this systematic binary mapping approach, developers can fully verify on-disk consistency, proving that the block bitmap allocations, path links, and physical blocks represent the identical, healthy data states.

---

### C. Interactive Visual Analysis Tool (`analyze_disk.py`)

To make analyzing disk states easier, the repository includes a custom, interactive Python tool: **`analyze_disk.py`**. 

This script parses a raw binary disk image and renders a highly visual breakdown directly in your terminal. It prints out:
1. **Superblock Parameters**: Magic validation, total block counts, and free inodes.
2. **Visual Block Grid (16x16 Matrix)**: Maps out the exact locations and boundaries of system metadata, directory records, and file block sectors.
3. **Inode Slot Grid**: Visualizes active inodes and their status.
4. **Decoded Inode Table**: Pretty-prints Unix permissions, file sizes, and block mappings for all active files.
5. **Interactive Directory Tree**: Traverses directories starting at root (`/`) and extracts inline file contents for review.

#### How to Run the Analyzer

Simply pass the formatted loopback image file as an argument:
```bash
./analyze_disk.py /tmp/disk_v4.img
```

#### Example Visual Output Mock-up
Here is what the terminal output looks like when running `./analyze_disk.py` on a freshly formatted disk with a single written file `diskfile.txt` (including the raw binary byte dump):

```text
======================================================================
      demofs Disk Image Visual Analyzer
======================================================================
Analyzing File:     /tmp/disk_v5.img
Total File Size:    1048576 bytes
Block Size:         4096 bytes
Calculated Blocks:  256

[0] Raw Disk Metadata Blocks Dump with Field Mapping (Hex View)

  Block 0 (Superblock) | Byte Offset 0x0000 (first 64 bytes):
  0x0000:  ff 00 eb de 00 10 00 00  00 01 00 00 20 00 00 00   |............ ...|
  0x0010:  fa 00 00 00 1d 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x0020:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x0030:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  Field Mapping Annotation:
    ├─ 0x0000-0x0003 [ff 00 eb de]: magic number (0xdeeb00ff)
    ├─ 0x0004-0x0007 [00 10 00 00]: block_size (4096 bytes)
    ├─ 0x0008-0x000b [00 01 00 00]: blocks_count (256 blocks)
    ├─ 0x000c-0x000f [20 00 00 00]: inodes_count (32 inodes)
    ├─ 0x0010-0x0013 [fa 00 00 00]: free_blocks (250 blocks)
    └─ 0x0014-0x0017 [1d 00 00 00]: free_inodes (29 inodes)

  Block 1 (Block Bitmap) | Byte Offset 0x1000 (first 16 bytes):
  0x1000:  3f 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |?...............|
  Field Mapping Annotation:
    └─ 0x1000           [3f]: block allocation mask (0b00111111 in binary)
                           -> Physical block(s) 0, 1, 2, 3, 4, 5 are currently allocated.

  Block 2 (Inode Bitmap) | Byte Offset 0x2000 (first 16 bytes):
  0x2000:  07 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  Field Mapping Annotation:
    └─ 0x2000           [07]: inode allocation mask (0b00000111 in binary)
                           -> Inode slot(s) 0 (Reserved), 1 (Root Dir), 2 are currently allocated.

  Block 3 (Inode Table) | Byte Offset 0x3000 (first 192 bytes):
  0x3000:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x3010:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x3020:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x3030:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x3040:  ed 41 00 00 00 00 02 00  00 10 00 00 01 00 00 00   |.A..............|
  0x3050:  04 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x3060:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x3070:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x3080:  a4 81 00 00 00 00 01 00  2c 00 00 00 01 00 00 00   |........,.......|
  0x3090:  05 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x30a0:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x30b0:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  Field Mapping Annotation (for Inode 1 [Root Directory] starting at 0x3040):
    ├─ 0x3040-0x3041 [ed 41]:       mode (0x41ed -> drwxr-xr-x directory)
    ├─ 0x3048-0x304b [00 10 00 00]: size (4096 bytes)
    ├─ 0x304c-0x304f [01 00 00 00]: blocks (1 blocks allocated)
    └─ 0x3050-0x3053 [04 00 00 00]: block[0] (Direct Pointer 0 -> Block 4 holds contents)
  Field Mapping Annotation (for Inode 2 starting at 0x3080):
    ├─ 0x3080-0x3081 [a4 81]:       mode (0x81a4 -> -rw-r--r-- regular file)
    ├─ 0x3088-0x308b [2c 00 00 00]: size (44 bytes)
    ├─ 0x308c-0x308f [01 00 00 00]: blocks (1 blocks allocated)
    └─ 0x3090-0x3093 [05 00 00 00]: block[0] (Direct Pointer 0 -> Block 5 holds file contents)

  Block 4 (Root Dir Data Block) | Byte Offset 0x4000 (first 192 bytes):
  0x4000:  01 00 00 00 2e 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x4010:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x4020:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x4030:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x4040:  01 00 00 00 2e 2e 00 00  00 00 00 00 00 00 00 00   |................|
  0x4050:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x4060:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x4070:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x4080:  02 00 00 00 64 69 73 6b  66 69 6c 65 2e 74 78 74   |....diskfile.txt|
  0x4090:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x40a0:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  0x40b0:  00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00   |................|
  Field Mapping Annotation:
    ├─ Entry 0 ('.' | Offset 0x4000-0x403f):
    │  ├─ 0x4000-0x4003 [01 00 00 00]: inode number (Inode 1)
    │  └─ 0x4004-0x4007 [2e 00 00 00...]: entry name (".")
    ├─ Entry 1 ('..' | Offset 0x4040-0x407f):
    │  ├─ 0x4040-0x4043 [01 00 00 00]: inode number (Inode 1)
    │  └─ 0x4044-0x4047 [2e 2e 00 00...]: entry name ("..")
    └─ Entry 2 ('diskfile.txt' | Offset 0x4080-0x40bf):
       ├─ 0x4080-0x4083 [02 00 00 00]: inode number (Inode 2)
       └─ 0x4084-0x4087 [64 69 73 6b...]: entry name ("diskfile.txt")

  Block 5 (File Data block) | Byte Offset 0x5000 (first 16 bytes):
  0x5000:  44 65 6d 6f 66 73 20 56  65 72 73 69 6f 6e 20 35   |Demofs Version 5|
  Field Mapping Annotation:
    └─ 0x5000-0x502b [44 65 6d 6f...]: raw file content data ('Demofs Version 5 is Perfectly Synchronized!' ASCII string)

----------------------------------------------------------------------

[1] Superblock Information (Block 0 | Offset 0x0000)
  Magic Number:     0xdeeb00ff (VALID)
  Block Size:       4096 bytes
  Blocks Count:     256
  Inodes Count:     32
  Free Blocks:      250
  Free Inodes:      29

[2] Block Allocation Grid (Block 1 | Offset 0x1000)
  Each character represents a 4KB block. Total blocks: 256
  Legend: ■ Metadata/Reserved  ■ Allocated Data  · Free Space
  ----------------------------------------
  Blocks 000-015: ■ ■ ■ ■ ■ ■ · · · · · · · · · ·
  Blocks 016-031: · · · · · · · · · · · · · · · ·
  Blocks 032-047: · · · · · · · · · · · · · · · ·
  ... (blocks 48 to 239 are free) ...
  Blocks 240-255: · · · · · · · · · · · · · · · ·

[3] Inode Allocation Bitmap (Block 2 | Offset 0x2000)
  Inode Slot Grid: 0 R 2 · · · · · · · · · · · · · · · · · · · · · · · · · · · · ·
  Active Inode IDs: [1, 2]

[4] Decoded Inode Table (Block 3 | Offset 0x3000)
  Inode 1 (Directory):
    Mode/Permissions: drwxr-xr-x (0o40755)
    Owner UID/GID:    0/0
    Links Count:      2
    File Size:        4096 bytes
    Allocated Blocks: 1 blocks
    Direct Blocks:    [4]

  Inode 2 (Regular File):
    Mode/Permissions: -rw-r--r-- (0o100644)
    Owner UID/GID:    0/0
    Links Count:      1
    File Size:        44 bytes
    Allocated Blocks: 1 blocks
    Direct Blocks:    [5]

[5] Visual Directory Hierarchy Decoding
  / (Root Directory ──► Inode 1)
  ├── . ──► Inode 1
  ├── .. ──► Inode 1
  └── diskfile.txt ──► Inode 2 (File, Size: 44B) [Content: 'Demofs Version 5 is Perfectly Synchronized!']
======================================================================
```

This diagnostic utility is an essential companion for driver development, making it incredibly fast to verify that your directory indexes, inode updates, and bitmap allocations behave exactly as intended at the byte level.


