# demov1 Tests Summary

This document synthesizes and delivers the final test steps and outputs of the buggy `demov1` (Unmount Hang) experiment performed on openSUSE.

Before starting the test, compile the user-space `mkfs` tools:

```bash
gcc -Wall -o /tmp/demofs/mkfs.demofs /tmp/demofs/mkfs.demofs.c
```

---

## Part 1: Step-by-Step Test Sequence

### 1. Copying & Compiling the Buggy Driver on the VM

We copied `demofs_v1.c` (where the `folio_unlock` and `folio_put` calls are commented out inside `demofs_write_end`) and compiled the `demofsv1.ko` module:

```log
susetest:~ # make -C /lib/modules/7.0.7-1-default/build M=/tmp/demofs modules
make: Entering directory '/usr/src/linux-7.0.7-1-obj/x86_64/default'
make[1]: Entering directory '/tmp/demofs'
  CC [M]  demofs_v1.o
  LD [M]  demofsv1.o
  MODPOST Module.symvers
  CC [M]  demofsv1.mod.o
  LD [M]  demofsv1.ko
make[1]: Leaving directory '/tmp/demofs'
```

### 2. Formatting the Virtual Disk Image

Created and formatted a 1MB loopback disk image `/tmp/disk_v1.img` using our user-space formatting tool:

```log
susetest:~ # dd if=/dev/zero of=/tmp/disk_v1.img bs=4096 count=256
256+0 records in
256+0 records out
1048576 bytes (1.0 MB, 1.0 MiB) copied

susetest:~ # /tmp/demofs/mkfs.demofs /tmp/disk_v1.img
Formatting /tmp/disk_v1.img with demofs:
  Total size:   1048576 bytes
  Block size:   4096 bytes
  Blocks:       256
Formatting complete successfully.
```

### 3. Loading the Buggy Module & Mounting

Loaded the module and mounted the disk image to `/tmp/test_mount_v1`:

```bash
susetest:~ # insmod /tmp/demofs/demofsv1.ko
susetest:~ # mkdir -p /tmp/test_mount_v1
susetest:~ # mount -o loop -t demofs_v1 /tmp/disk_v1.img /tmp/test_mount_v1
```

### 4. Triggering the Folio Lock Leak

Wrote text to a file. This completed successfully but leaked the folio lock in the kernel's memory because `write_end` did not unlock it:

```log
susetest:~ # echo 'Deliberate Lock Leak!' > /tmp/test_mount_v1/diskfile.txt
susetest:~ # cat /tmp/test_mount_v1/diskfile.txt
Deliberate Lock Leak!
```

### 5. Triggering the Unmount Hang

Ran `umount` in the background and observed that it immediately entered the uninterruptible sleep state (state `D`):

```log
susetest:~ # umount /tmp/test_mount_v1 &
susetest:~ # sleep 2
susetest:~ # ps -aux | grep -E 'umount|test_mount_v1'
root        2627  0.0  0.1   7256  5092 ?        D    07:59   0:00 umount /tmp/test_mount_v1
```

### 6. Dumping Call Stacks via SysRq

Triggered the kernel's blocked-task detector to output the exact backtraces of the stuck threads to `dmesg`:

```bash
susetest:~ # echo w > /proc/sysrq-trigger
```

---

## Part 2: SysRq Call Stack Outputs (dmesg)

Here are the exact backtraces printed by SysRq, showing the two deadlocked threads:

### 1. The Blocked Writeback Thread (Holds `s_umount` Read-Lock)

The background kernel thread trying to flush pages is blocked waiting for the leaked folio lock:

```log
[   T2599] task:kworker/u32:3   state:D stack:0     pid:2282  tgid:2282  ppid:2
[   T2599] Workqueue: writeback wb_workfn (flush-7:0)
[   T2599] Call Trace:
[   T2599]  <TASK>
[   T2599]  __schedule+0x429/0x1740
[   T2599]  schedule+0x27/0xd0
[   T2599]  io_schedule+0x46/0x70
[   T2599]  folio_wait_bit_common+0x110/0x300      <--- BLOCKED WAITING FOR LEAKED FOLIO LOCK
[   T2599]  writeback_iter+0x2d1/0x2f0
[   T2599]  __mpage_writepages+0x6d/0x100
[   T2599]  do_writepages+0xd3/0x160
[   T2599]  __writeback_single_inode+0x42/0x340   <--- HOLDS s_umount READ LOCK
[   T2599]  writeback_sb_inodes+0x231/0x560
[   T2599]  wb_writeback+0x2c6/0x360
[   T2599]  wb_workfn+0x375/0x470
[   T2599]  process_one_work+0x19e/0x3a0
[   T2599]  worker_thread+0x1ba/0x330
[   T2599]  kthread+0xe3/0x120
[   T2599]  ret_from_fork+0x2bd/0x350
[   T2599]  ret_from_fork_asm+0x1a/0x30
[   T2599]  </TASK>
```

### 2. The Blocked `umount` Process (Waits for `s_umount` Write-Lock)

Your unmount thread is blocked waiting for the exclusive write-lock on the same superblock rwsem (`s_umount`) held by the kworker:

```log
[ T2056] task:umount          state:D stack:0     pid:2627  tgid:2627  ppid:1
[ T2056] Call Trace:
[ T2056]  <TASK>
[ T2056]  __schedule+0x429/0x1740
[ T2056]  schedule+0x27/0xd0
[ T2056]  schedule_preempt_disabled+0x15/0x30
[ T2056]  rwsem_down_write_slowpath+0x1df/0x740   <--- BLOCKED WAITING FOR s_umount WRITE-LOCK
[ T2056]  down_write+0x5a/0x60
[ T2056]  deactivate_super+0x3a/0x50              <--- REQUESTS EXCLUSIVE s_umount WRITE-LOCK
[ T2056]  cleanup_mnt+0xdc/0x140
[ T2056]  task_work_run+0x5d/0x90
[ T2056]  exit_to_user_mode_loop+0x139/0x4c0
[ T2056]  do_syscall_64+0x28d/0x1600
[ T2056]  entry_SYSCALL_64_after_hwframe+0x76/0x7e
[ T2056]  </TASK>
```

---

## Code Details: Buggy vs. Fixed

### The Buggy Code (`demofs_v1.c`)

Our custom `demofs_write_end` called `block_write_end()`. In modern kernels, `block_write_end()` commits write buffers but does not unlock or release the folio. The folio lock was leaked:

```c
static int demofs_write_end(const struct kiocb *iocb, struct address_space *mapping,
                loff_t pos, unsigned len, unsigned copied,
                struct folio *folio, void *fsdata)
{
    int ret;
    struct inode *inode = mapping->host;
    
    ret = block_write_end(pos, len, copied, folio);
    
    if (pos + ret > inode->i_size) {
        i_size_write(inode, pos + ret);
    }
    demofs_write_inode_to_disk(inode);

    /* BUG: folio_unlock(folio) and folio_put(folio) are missing here! */
    return ret; 
}
```

### The Fixed Code (Option A)

Adding the explicit unlock and reference put operations right before returning releases the lock and allows eviction to proceed cleanly:

```c
static int demofs_write_end(const struct kiocb *iocb, struct address_space *mapping,
                loff_t pos, unsigned len, unsigned copied,
                struct folio *folio, void *fsdata)
{
    int ret;
    struct inode *inode = mapping->host;
    
    ret = block_write_end(pos, len, copied, folio);
    
    if (pos + ret > inode->i_size) {
        i_size_write(inode, pos + ret);
    }
    demofs_write_inode_to_disk(inode);
    
    /* --- THE FIX (Option A) --- */
    folio_unlock(folio);  // Unlocks the PG_locked bit on the page
    folio_put(folio);     // Decrements the folio memory reference count
    /* ────────────────────────── */

    return ret;
}
```

---

## Part 3: Detailed Call Trace: From Write to Deadlock

### Path A: The Write Path (How the lock was leaked in the past)

```text
sys_write()
  └──► vfs_write()
         └──► generic_file_write_iter()
                └──► generic_perform_write()
                       ├──► [1. CALLS WRITE_BEGIN]
                       │      demofs_write_begin()
                       │        └──► block_write_begin()
                       │               └──► __filemap_get_folio() 
                       │                      └──► [Locks Folio: sets PG_locked = 1]
                       │
                       ├──► [2. COPIES DATA FROM USERSPACE]
                       │
                       └──► [3. CALLS WRITE_END]
                              demofs_write_end()
                                └──► block_write_end() 
                                       └──► [Exits without unlocking: LEAKS LOCK]
```

### Path B: The Writeback Path (Worker locks `s_umount` and hangs on the leaked folio)

```text
kworker (Workqueue: wb_workfn)
  └──► wb_writeback()
         └──► writeback_sb_inodes()
                │
                ├──► [1. ACQUIRES s_umount READ LOCK (Shared)]
                │
                └──► __writeback_single_inode()
                       └──► do_writepages()
                              └──► __mpage_writepages()
                                     └──► folio_wait_bit_common()
                                            └──► io_schedule() 
                                                   └──► [SLEEPS FOREVER waiting for folio lock]
```

### Path C: The Unmount Path (Hangs waiting for `s_umount` Write-Lock)

```text
sys_umount()
  └──► do_umount()
         └──► deactivate_super()
                └──► down_write(&sb->s_umount)
                       └──► rwsem_down_write_slowpath()
                              └──► schedule() 
                                     └──► [SLEEPS FOREVER waiting for kworker to release s_umount]
```

---

## Detailed Timeline

### Phase 1: The Writing Phase (Happened in the Past)

When you executed `echo 'Deliberate Lock Leak!' > diskfile.txt`, a temporary write process started:

1. It entered the kernel and ran `demofs_write_begin()`, which locked the memory page (folio).
2. It copied `'Deliberate Lock Leak!'` into the page buffers.
3. It ran `demofs_write_end()`, which wrote the file size to disk, but returned without unlocking the folio.
4. **Crucially**: The write system call completed, returned success to userspace, and the process finished and exited.

`demofs_write_end` has completely finished executing. However, it left behind a permanently locked folio (`PG_locked = 1`) sitting in the system's RAM page-cache.

### Phase 2: The Flush & Unmount Phase (Stuck in the Present)

When you ran `umount /tmp/test_mount_v1`, the kernel initiated unmounting:

#### The Victim 1: The Writeback Thread (`kworker`)
Before unmounting, the kernel must flush the dirty `'Deliberate Lock Leak!'` page cache to `/tmp/disk_v1.img`:
1. The kernel background thread `kworker` starts, acquires the `s_umount` read-lock, and enters `do_writepages` → `__mpage_writepages()`.
2. To write our file's dirty folio back to disk, the `kworker` must lock the folio before submitting it to the block layer.
3. It calls `folio_wait_bit_common()` to wait for the folio to be unlocked.
4. **The Block**: Because `demofs_write_end` (in Phase 1) left it locked and exited, the folio will never be unlocked. The `kworker` falls into uninterruptible sleep (state `D`), holding the `s_umount` read-lock forever.

#### The Victim 2: Your `umount` Process
1. Your `umount` process starts and calls `deactivate_super()`.
2. To safely unmount, it must acquire the `s_umount` write-lock.
3. **The Block**: It sees that the `kworker` is holding the read-lock on `s_umount`. It must wait. It blocks in state `D` waiting for the write-lock.

---

### Deadlock Diagram

```text
                                  s_umount (Single Lock)
                                   ┌──────────────────┐
  kworker holds Read Lock ────────►│   Active: READ   │
                                   └────────┬─────────┘
                                            │
                                            ▼ Blocked! (Cannot grant Write Lock while Read Lock is held)
                                   ┌──────────────────┐
  umount requests Write Lock ─────►│  Pending: WRITE  │
                                   └──────────────────┘
```
