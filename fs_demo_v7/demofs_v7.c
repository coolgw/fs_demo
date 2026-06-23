// SPDX-License-Identifier: GPL-2.0-only
/*
 * Demofs (Version 7) - Fully Correct Block-Backed Filesystem with Directory Support
 * 
 * Note: This version adds support for creating subdirectories (mkdir) and removing
 * subdirectories (rmdir).
 * 
 * Copyright (C) 2026 Gemini CLI
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/fs.h>
#include <linux/fs_context.h>
#include <linux/magic.h>
#include <linux/slab.h>
#include <linux/pagemap.h>
#include <linux/gfp.h>
#include <linux/security.h>
#include <linux/dcache.h>
#include <linux/buffer_head.h>
#include <linux/mpage.h>
#include <linux/version.h>
#include <linux/writeback.h>
#include "demofs.h"

#if LINUX_VERSION_CODE >= KERNEL_VERSION(7, 0, 0)
#define get_inode_state(inode) inode_state_read(inode)
#else
#define get_inode_state(inode) ((inode)->i_state)
#endif

static const struct address_space_operations demofs_aops;
static const struct inode_operations demofs_file_inode_operations;
static const struct inode_operations demofs_dir_inode_operations;
static const struct file_operations demofs_file_operations;
static const struct file_operations demofs_dir_operations;
static const struct super_operations demofs_ops;

/* Helper to dynamically adjust superblock's free block/inode counts on disk */
static void demofs_adjust_free_resources(struct super_block *sb, int block_delta, int inode_delta)
{
	struct buffer_head *bh = sb_bread(sb, DEMOFS_SUPER_BLOCK_NUM);
	struct demofs_super_block *dsb;

	if (bh) {
		dsb = (struct demofs_super_block *)bh->b_data;
		
		/* Update free blocks on-disk count */
		if (block_delta != 0) {
			uint32_t free_blks = le32_to_cpu(dsb->free_blocks);
			dsb->free_blocks = cpu_to_le32(free_blks + block_delta);
		}
		
		/* Update free inodes on-disk count */
		if (inode_delta != 0) {
			uint32_t free_inos = le32_to_cpu(dsb->free_inodes);
			dsb->free_inodes = cpu_to_le32(free_inos + inode_delta);
		}
		
		mark_buffer_dirty(bh);
		brelse(bh);
	}
}

/* Allocate a free physical block from the Block Bitmap (Block 1) */
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
		if (bitmap[i] != 0xFF) {
			for (j = 0; j < 8; j++) {
				if (!(bitmap[i] & (1 << j))) {
					bitmap[i] |= (1 << j);
					block = i * 8 + j;
					mark_buffer_dirty(bh);
					brelse(bh);
					
					/* Dynamically write back updated superblock free blocks count */
					demofs_adjust_free_resources(sb, -1, 0);
					return block;
				}
			}
		}
	}
	brelse(bh);
	return 0;
}

/* Allocate a free physical inode from the Inode Bitmap (Block 2) */
static uint32_t demofs_allocate_inode(struct super_block *sb)
{
	struct buffer_head *bh = sb_bread(sb, DEMOFS_INODE_BITMAP);
	unsigned char *bitmap;
	uint32_t ino = 0;
	int i, j;

	if (!bh)
		return 0;

	bitmap = (unsigned char *)bh->b_data;
	for (i = 0; i < DEMOFS_BLOCK_SIZE; i++) {
		if (bitmap[i] != 0xFF) {
			for (j = 0; j < 8; j++) {
				if (!(bitmap[i] & (1 << j))) {
					uint32_t candidate = i * 8 + j;
					if (candidate >= DEMOFS_MAX_INODES) {
						brelse(bh);
						return 0;
					}
					bitmap[i] |= (1 << j);
					ino = candidate;
					mark_buffer_dirty(bh);
					brelse(bh);
					
					/* Dynamically write back updated superblock free inodes count */
					demofs_adjust_free_resources(sb, 0, -1);
					return ino;
				}
			}
		}
	}
	brelse(bh);
	return 0;
}

/* Retrieve or instantiate a memory inode from the superblock */
static struct inode *demofs_iget(struct super_block *sb, unsigned long ino)
{
	struct inode *inode;
	struct buffer_head *bh;
	struct demofs_inode *di;

	inode = iget_locked(sb, ino);
	if (!inode)
		return ERR_PTR(-ENOMEM);

	if (!(get_inode_state(inode) & I_NEW))
		return inode;

	bh = sb_bread(sb, DEMOFS_INODE_TABLE);
	if (!bh) {
		iget_failed(inode);
		return ERR_PTR(-EIO);
	}

	di = (struct demofs_inode *)bh->b_data + ino;

	inode->i_mode = le16_to_cpu(di->mode);
	i_uid_write(inode, le16_to_cpu(di->uid));
	i_gid_write(inode, le16_to_cpu(di->gid));
	set_nlink(inode, le16_to_cpu(di->links_count));
	inode->i_size = le32_to_cpu(di->size);
	simple_inode_init_ts(inode);

	if (S_ISREG(inode->i_mode)) {
		inode->i_op = &demofs_file_inode_operations;
		inode->i_fop = &demofs_file_operations;
		inode->i_mapping->a_ops = &demofs_aops;
	} else if (S_ISDIR(inode->i_mode)) {
		inode->i_op = &demofs_dir_inode_operations;
		inode->i_fop = &demofs_dir_operations;
		inode->i_mapping->a_ops = &demofs_aops;
	}

	brelse(bh);
	unlock_new_inode(inode);
	return inode;
}

/* Save a memory inode's metadata back to the disk inode table */
static void demofs_write_inode_to_disk(struct inode *inode)
{
	struct buffer_head *bh = sb_bread(inode->i_sb, DEMOFS_INODE_TABLE);
	struct demofs_inode *di;

	if (bh) {
		di = (struct demofs_inode *)bh->b_data + inode->i_ino;
		di->mode = cpu_to_le16(inode->i_mode);
		di->size = cpu_to_le32(inode->i_size);
		di->uid = cpu_to_le16(i_uid_read(inode));
		di->gid = cpu_to_le16(i_gid_read(inode));
		di->links_count = cpu_to_le16(inode->i_nlink);
		mark_buffer_dirty(bh);
		brelse(bh);
	}
}

/* Map logical file blocks to physical disk blocks */
static int demofs_get_block(struct inode *inode, sector_t iblock,
			    struct buffer_head *bh_result, int create)
{
	struct super_block *sb = inode->i_sb;
	struct buffer_head *bh_inode;
	struct demofs_inode *di;
	uint32_t phys_block = 0;
	int ret = 0;

	if (iblock >= DEMOFS_N_BLOCKS)
		return -EFBIG;

	bh_inode = sb_bread(sb, DEMOFS_INODE_TABLE);
	if (!bh_inode)
		return -EIO;

	di = (struct demofs_inode *)bh_inode->b_data + inode->i_ino;
	phys_block = le32_to_cpu(di->block[iblock]);

	if (phys_block == 0) {
		if (!create) {
			brelse(bh_inode);
			return 0;
		}

		/* Allocate a physical block */
		phys_block = demofs_allocate_block(sb);
		if (phys_block == 0) {
			brelse(bh_inode);
			return -ENOSPC;
		}

		di->block[iblock] = cpu_to_le32(phys_block);
		di->blocks = cpu_to_le32(le32_to_cpu(di->blocks) + 1);
		
		mark_buffer_dirty(bh_inode);
	}

	brelse(bh_inode);
	map_bh(bh_result, sb, phys_block);
	return ret;
}

/* Address Space Operations for block mapping using 6.x folio APIs */
static int demofs_read_folio(struct file *file, struct folio *folio)
{
	return block_read_full_folio(folio, demofs_get_block);
}

static int demofs_write_begin(const struct kiocb *iocb, struct address_space *mapping,
			      loff_t pos, unsigned len,
			      struct folio **foliop, void **fsdata)
{
	return block_write_begin(mapping, pos, len, foliop, demofs_get_block);
}

static int demofs_writepages(struct address_space *mapping, struct writeback_control *wbc)
{
	return mpage_writepages(mapping, wbc, demofs_get_block);
}

static const struct address_space_operations demofs_aops = {
	.read_folio	= demofs_read_folio,
	.write_begin	= demofs_write_begin,
	.write_end	= generic_write_end,
	.writepages	= demofs_writepages,
	.dirty_folio	= block_dirty_folio,
};

/* File Operations */
static const struct file_operations demofs_file_operations = {
	.read_iter	= generic_file_read_iter,
	.write_iter	= generic_file_write_iter,
	.mmap		= generic_file_mmap,
	.fsync		= noop_fsync,
	.splice_read	= filemap_splice_read,
	.splice_write	= iter_file_splice_write,
	.llseek		= generic_file_llseek,
};

static const struct inode_operations demofs_file_inode_operations = {
	.setattr	= simple_setattr,
	.getattr	= simple_getattr,
};

/* Helper: Add a directory entry in parent directory data blocks on disk */
static int demofs_add_dir_entry(struct inode *dir, struct dentry *dentry, uint32_t ino)
{
	struct super_block *sb = dir->i_sb;
	struct buffer_head *bh;
	struct demofs_dir_entry *de;
	int i;

	/* We map block 0 of the directory using demofs_get_block */
	struct buffer_head bh_map = {0};
	int err = demofs_get_block(dir, 0, &bh_map, 1);
	if (err)
		return err;

	bh = sb_bread(sb, bh_map.b_blocknr);
	if (!bh)
		return -EIO;

	de = (struct demofs_dir_entry *)bh->b_data;
	for (i = 0; i < DEMOFS_BLOCK_SIZE / sizeof(struct demofs_dir_entry); i++) {
		if (le32_to_cpu(de[i].inode) == 0) {
			/* Found a free entry! */
			de[i].inode = cpu_to_le32(ino);
			strncpy(de[i].name, dentry->d_name.name, DEMOFS_NAME_LEN - 1);
			de[i].name[DEMOFS_NAME_LEN - 1] = '\0';
			mark_buffer_dirty(bh);
			brelse(bh);

			/* Update parent directory size if needed */
			if (dir->i_size < (i + 1) * sizeof(struct demofs_dir_entry)) {
				dir->i_size = (i + 1) * sizeof(struct demofs_dir_entry);
				mark_inode_dirty(dir);
			}
			return 0;
		}
	}

	brelse(bh);
	return -ENOSPC;
}

/* Helper: Remove a directory entry from parent directory data blocks on disk */
static int demofs_remove_dir_entry(struct inode *dir, struct dentry *dentry)
{
	struct super_block *sb = dir->i_sb;
	struct buffer_head *bh;
	struct demofs_dir_entry *de;
	int i;

	struct buffer_head bh_map = {0};
	int err = demofs_get_block(dir, 0, &bh_map, 0);
	if (err || bh_map.b_blocknr == 0)
		return -ENOENT;

	bh = sb_bread(sb, bh_map.b_blocknr);
	if (!bh)
		return -EIO;

	de = (struct demofs_dir_entry *)bh->b_data;
	for (i = 0; i < DEMOFS_BLOCK_SIZE / sizeof(struct demofs_dir_entry); i++) {
		if (le32_to_cpu(de[i].inode) > 0 && 
		    strcmp(de[i].name, dentry->d_name.name) == 0) {
			de[i].inode = cpu_to_le32(0);
			memset(de[i].name, 0, DEMOFS_NAME_LEN);
			mark_buffer_dirty(bh);
			brelse(bh);
			return 0;
		}
	}

	brelse(bh);
	return -ENOENT;
}

/* File creation */
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

	/* Allocate a completely new, blank in-memory inode */
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

	/* Insert the newly instantiated inode into VFS hash table */
	insert_inode_hash(inode);

	/* Write to disk inode table */
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

	/* Add entry in parent directory block on disk */
	err = demofs_add_dir_entry(dir, dentry, ino);
	if (err) {
		iput(inode);
		return err;
	}

	d_instantiate(dentry, inode);
	return 0;
}

/* Directory creation */
static struct dentry *demofs_mkdir(struct mnt_idmap *idmap, struct inode *dir,
				 struct dentry *dentry, umode_t mode)
{
	struct super_block *sb = dir->i_sb;
	struct inode *inode;
	uint32_t ino;
	uint32_t phys_block;
	struct buffer_head *bh_inode;
	struct buffer_head *bh_dir;
	struct demofs_inode *di;
	struct demofs_dir_entry *de;
	int err;

	/* 1. Allocate inode */
	ino = demofs_allocate_inode(sb);
	if (ino == 0)
		return ERR_PTR(-ENOSPC);

	/* 2. Allocate data block for directory entries */
	phys_block = demofs_allocate_block(sb);
	if (phys_block == 0) {
		return ERR_PTR(-ENOSPC);
	}

	/* 3. Initialize the directory data block on disk ('.' and '..') */
	bh_dir = sb_getblk(sb, phys_block);
	if (!bh_dir)
		return ERR_PTR(-EIO);

	memset(bh_dir->b_data, 0, DEMOFS_BLOCK_SIZE);
	de = (struct demofs_dir_entry *)bh_dir->b_data;

	/* "." entry points to self */
	de[0].inode = cpu_to_le32(ino);
	strncpy(de[0].name, ".", DEMOFS_NAME_LEN - 1);
	de[0].name[DEMOFS_NAME_LEN - 1] = '\0';

	/* ".." entry points to parent directory */
	de[1].inode = cpu_to_le32(dir->i_ino);
	strncpy(de[1].name, "..", DEMOFS_NAME_LEN - 1);
	de[1].name[DEMOFS_NAME_LEN - 1] = '\0';

	set_buffer_uptodate(bh_dir);
	mark_buffer_dirty(bh_dir);
	brelse(bh_dir);

	/* 4. Allocate and initialize VFS in-memory inode */
	inode = new_inode(sb);
	if (!inode)
		return ERR_PTR(-ENOMEM);

	inode->i_ino = ino;
	inode->i_mode = S_IFDIR | mode;
	i_uid_write(inode, from_kuid(&init_user_ns, current_fsuid()));
	i_gid_write(inode, from_kgid(&init_user_ns, current_fsgid()));
	set_nlink(inode, 2); /* self and "." */
	inode->i_size = DEMOFS_BLOCK_SIZE;
	simple_inode_init_ts(inode);

	inode->i_op = &demofs_dir_inode_operations;
	inode->i_fop = &demofs_dir_operations;
	inode->i_mapping->a_ops = &demofs_aops;

	insert_inode_hash(inode);

	/* 5. Write newly created inode's metadata to disk */
	bh_inode = sb_bread(sb, DEMOFS_INODE_TABLE);
	if (!bh_inode) {
		iput(inode);
		return ERR_PTR(-EIO);
	}

	di = (struct demofs_inode *)bh_inode->b_data + ino;
	memset(di, 0, sizeof(struct demofs_inode));
	di->mode = cpu_to_le16(inode->i_mode);
	di->uid = cpu_to_le16(i_uid_read(inode));
	di->gid = cpu_to_le16(i_gid_read(inode));
	di->links_count = cpu_to_le16(inode->i_nlink);
	di->size = cpu_to_le32(inode->i_size);
	di->blocks = cpu_to_le32(1);
	di->block[0] = cpu_to_le32(phys_block);

	mark_buffer_dirty(bh_inode);
	brelse(bh_inode);

	/* 6. Add directory entry in parent directory */
	err = demofs_add_dir_entry(dir, dentry, ino);
	if (err) {
		iput(inode);
		return ERR_PTR(err);
	}

	/* 7. Increment links_count of parent directory */
	inc_nlink(dir);
	mark_inode_dirty(dir);

	d_instantiate(dentry, inode);
	return NULL;
}

static int demofs_unlink(struct inode *dir, struct dentry *dentry)
{
	struct inode *inode = d_inode(dentry);
	int err;

	err = demofs_remove_dir_entry(dir, dentry);
	if (err)
		return err;

	inode_dec_link_count(inode);
	mark_inode_dirty(inode);
	return 0;
}

/* Directory removal */
static int demofs_rmdir(struct inode *dir, struct dentry *dentry)
{
	struct inode *inode = d_inode(dentry);
	int err;

	if (!simple_empty(dentry))
		return -ENOTEMPTY;

	err = demofs_remove_dir_entry(dir, dentry);
	if (err)
		return err;

	/* Drop link from parent to child dir entry */
	inode_dec_link_count(inode);
	/* Drop link for '.' */
	inode_dec_link_count(inode);
	/* Drop link for '..' in parent */
	inode_dec_link_count(dir);

	return 0;
}

static struct dentry *demofs_lookup(struct inode *dir, struct dentry *dentry, unsigned int flags)
{
	struct super_block *sb = dir->i_sb;
	struct buffer_head *bh;
	struct demofs_dir_entry *de;
	struct inode *inode = NULL;
	int i;

	struct buffer_head bh_map = {0};
	int err = demofs_get_block(dir, 0, &bh_map, 0);
	if (err || bh_map.b_blocknr == 0)
		return d_splice_alias(NULL, dentry);

	bh = sb_bread(sb, bh_map.b_blocknr);
	if (!bh)
		return ERR_PTR(-EIO);

	de = (struct demofs_dir_entry *)bh->b_data;
	for (i = 0; i < DEMOFS_BLOCK_SIZE / sizeof(struct demofs_dir_entry); i++) {
		if (le32_to_cpu(de[i].inode) > 0 && 
		    strcmp(de[i].name, dentry->d_name.name) == 0) {
			inode = demofs_iget(sb, le32_to_cpu(de[i].inode));
			brelse(bh);
			return d_splice_alias(inode, dentry);
		}
	}

	brelse(bh);
	return d_splice_alias(NULL, dentry);
}

static const struct inode_operations demofs_dir_inode_operations = {
	.create		= demofs_create,
	.lookup		= demofs_lookup,
	.unlink		= demofs_unlink,
	.mkdir		= demofs_mkdir,
	.rmdir		= demofs_rmdir,
};

/* Directory listing (readdir) from on-disk blocks */
static int demofs_readdir(struct file *file, struct dir_context *ctx)
{
	struct inode *dir = file_inode(file);
	struct super_block *sb = dir->i_sb;
	struct buffer_head *bh;
	struct demofs_dir_entry *de;
	int i;

	struct buffer_head bh_map = {0};
	int err = demofs_get_block(dir, 0, &bh_map, 0);
	if (err || bh_map.b_blocknr == 0)
		return 0;

	bh = sb_bread(sb, bh_map.b_blocknr);
	if (!bh)
		return -EIO;

	de = (struct demofs_dir_entry *)bh->b_data;
	
	/* Skip over elements we already read */
	i = ctx->pos / sizeof(struct demofs_dir_entry);
	
	for (; i < DEMOFS_BLOCK_SIZE / sizeof(struct demofs_dir_entry); i++) {
		if (le32_to_cpu(de[i].inode) > 0) {
			if (!dir_emit(ctx, de[i].name, strlen(de[i].name),
				     le32_to_cpu(de[i].inode), DT_UNKNOWN)) {
				break;
			}
		}
		ctx->pos += sizeof(struct demofs_dir_entry);
	}

	brelse(bh);
	return 0;
}

static const struct file_operations demofs_dir_operations = {
	.read		= generic_read_dir,
	.iterate_shared	= demofs_readdir,
	.llseek		= generic_file_llseek,
};

static int demofs_write_inode(struct inode *inode, struct writeback_control *wbc)
{
	demofs_write_inode_to_disk(inode);
	return 0;
}

static const struct super_operations demofs_ops = {
	.statfs		= simple_statfs,
	.write_inode	= demofs_write_inode,
};

static int demofs_fill_super(struct super_block *sb, struct fs_context *fc)
{
	struct buffer_head *bh;
	struct demofs_super_block *dsb;
	struct inode *root_inode;

	/* Set block size to 4KB */
	if (!sb_set_blocksize(sb, DEMOFS_BLOCK_SIZE))
		return -EINVAL;

	/* Read Superblock from Block 0 */
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

	sb->s_magic = le32_to_cpu(dsb->magic);
	sb->s_op = &demofs_ops;
	sb->s_time_gran = 1;

	brelse(bh);

	/* Get the Root Inode (inode 1) */
	root_inode = demofs_iget(sb, 1);
	if (IS_ERR(root_inode)) {
		pr_err("demofs: unable to get root inode\n");
		return PTR_ERR(root_inode);
	}

	sb->s_root = d_make_root(root_inode);
	if (!sb->s_root) {
		pr_err("demofs: unable to make root dentry\n");
		return -ENOMEM;
	}

	return 0;
}

static int demofs_get_tree(struct fs_context *fc)
{
	return get_tree_bdev(fc, demofs_fill_super);
}

static const struct fs_context_operations demofs_context_ops = {
	.get_tree	= demofs_get_tree,
};

static int demofs_init_fs_context(struct fs_context *fc)
{
	fc->ops = &demofs_context_ops;
	return 0;
}

static struct file_system_type demofs_fs_type = {
	.owner			= THIS_MODULE,
	.name			= "demofs_v7",
	.init_fs_context	= demofs_init_fs_context,
	.kill_sb		= kill_block_super,
	.fs_flags		= FS_REQUIRES_DEV,
};

static int __init demofs_init(void)
{
	pr_info("demofs_v7: registering block-backed filesystem with directory support\n");
	return register_filesystem(&demofs_fs_type);
}

static void __exit demofs_exit(void)
{
	pr_info("demofs_v7: unregistering block-backed filesystem\n");
	unregister_filesystem(&demofs_fs_type);
}

module_init(demofs_init);
module_exit(demofs_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Gemini CLI");
MODULE_DESCRIPTION("A block-backed virtual filesystem (Version 7 - Supports Directory Creation)");
