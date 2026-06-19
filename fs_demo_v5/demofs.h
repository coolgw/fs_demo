/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * On-disk structures and constants for demofs.
 * Copyright (C) 2026 Gemini CLI
 */

#ifndef _DEMOFS_H
#define _DEMOFS_H

#include <linux/types.h>

#define DEMOFS_MAGIC          0xdeeb00ff
#define DEMOFS_BLOCK_SIZE     4096

/* Disk layout block indexes */
#define DEMOFS_SUPER_BLOCK_NUM 0
#define DEMOFS_BLOCK_BITMAP    1
#define DEMOFS_INODE_BITMAP    2
#define DEMOFS_INODE_TABLE     3
#define DEMOFS_DATA_START      4

#define DEMOFS_MAX_INODES     32
#define DEMOFS_N_BLOCKS       12
#define DEMOFS_NAME_LEN       60

/* On-disk Superblock */
struct demofs_super_block {
	__le32 magic;
	__le32 block_size;
	__le32 blocks_count;
	__le32 inodes_count;
	__le32 free_blocks;
	__le32 free_inodes;
};

/* On-disk Inode */
struct demofs_inode {
	__le16 mode;
	__le16 uid;
	__le16 gid;
	__le16 links_count;
	__le32 size;
	__le32 blocks;
	__le32 block[DEMOFS_N_BLOCKS];
};

/* On-disk Directory Entry (64 bytes) */
struct demofs_dir_entry {
	__le32 inode;
	char name[DEMOFS_NAME_LEN];
};

#endif /* _DEMOFS_H */
