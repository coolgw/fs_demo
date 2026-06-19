/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Userspace formatter for demofs (mkfs.demofs).
 * Copyright (C) 2026 Gemini CLI
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>

#define DEMOFS_MAGIC          0xdeeb00ff
#define DEMOFS_BLOCK_SIZE     4096
#define DEMOFS_MAX_INODES     32
#define DEMOFS_N_BLOCKS       12
#define DEMOFS_NAME_LEN       60

/* Endianness conversion helpers for portability */
#if __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
#define cpu_to_le32(x) ((uint32_t)(x))
#define cpu_to_le16(x) ((uint16_t)(x))
#else
#define cpu_to_le32(x) __builtin_bswap32(x)
#define cpu_to_le16(x) __builtin_bswap16(x)
#endif

struct demofs_super_block {
	uint32_t magic;
	uint32_t block_size;
	uint32_t blocks_count;
	uint32_t inodes_count;
	uint32_t free_blocks;
	uint32_t free_inodes;
};

struct demofs_inode {
	uint16_t mode;
	uint16_t uid;
	uint16_t gid;
	uint16_t links_count;
	uint32_t size;
	uint32_t blocks;
	uint32_t block[DEMOFS_N_BLOCKS];
};

struct demofs_dir_entry {
	uint32_t inode;
	char name[DEMOFS_NAME_LEN];
};

int main(int argc, char **argv)
{
	int fd;
	struct stat st;
	uint32_t num_blocks;
	uint32_t free_blocks;
	char buf[DEMOFS_BLOCK_SIZE];
	struct demofs_super_block *sb;
	struct demofs_inode *root_inode;
	struct demofs_dir_entry *de;

	if (argc < 2) {
		fprintf(stderr, "Usage: %s <device_or_file>\n", argv[0]);
		return 1;
	}

	fd = open(argv[1], O_RDWR);
	if (fd < 0) {
		perror("open");
		return 1;
	}

	if (fstat(fd, &st) < 0) {
		perror("fstat");
		close(fd);
		return 1;
	}

	num_blocks = st.st_size / DEMOFS_BLOCK_SIZE;
	if (num_blocks < 10) {
		fprintf(stderr, "Device too small. Minimum size is 10 blocks (40KB).\n");
		close(fd);
		return 1;
	}

	printf("Formatting %s with demofs:\n", argv[1]);
	printf("  Total size:   %lld bytes\n", (long long)st.st_size);
	printf("  Block size:   %d bytes\n", DEMOFS_BLOCK_SIZE);
	printf("  Blocks:       %u\n", num_blocks);

	/* 1. Superblock (Block 0) */
	memset(buf, 0, DEMOFS_BLOCK_SIZE);
	sb = (struct demofs_super_block *)buf;
	sb->magic = cpu_to_le32(DEMOFS_MAGIC);
	sb->block_size = cpu_to_le32(DEMOFS_BLOCK_SIZE);
	sb->blocks_count = cpu_to_le32(num_blocks);
	sb->inodes_count = cpu_to_le32(DEMOFS_MAX_INODES);
	
	/* 5 blocks used: SB(0), BlockBitmap(1), InodeBitmap(2), InodeTable(3), RootDirData(4) */
	free_blocks = num_blocks - 5;
	sb->free_blocks = cpu_to_le32(free_blocks);
	sb->free_inodes = cpu_to_le32(DEMOFS_MAX_INODES - 2); /* inode 0 is unused, inode 1 is root */

	if (write(fd, buf, DEMOFS_BLOCK_SIZE) != DEMOFS_BLOCK_SIZE) {
		perror("write superblock");
		close(fd);
		return 1;
	}

	/* 2. Block Bitmap (Block 1)
	 * Bits 0, 1, 2, 3, 4 are allocated. 0x1F is 0b00011111 */
	memset(buf, 0, DEMOFS_BLOCK_SIZE);
	buf[0] = 0x1F; 
	if (write(fd, buf, DEMOFS_BLOCK_SIZE) != DEMOFS_BLOCK_SIZE) {
		perror("write block bitmap");
		close(fd);
		return 1;
	}

	/* 3. Inode Bitmap (Block 2)
	 * Inode 0 (unused) and Inode 1 (root directory) are allocated. 0x03 is 0b00000011 */
	memset(buf, 0, DEMOFS_BLOCK_SIZE);
	buf[0] = 0x03;
	if (write(fd, buf, DEMOFS_BLOCK_SIZE) != DEMOFS_BLOCK_SIZE) {
		perror("write inode bitmap");
		close(fd);
		return 1;
	}

	/* 4. Inode Table (Block 3)
	 * Write root directory inode at index 1 */
	memset(buf, 0, DEMOFS_BLOCK_SIZE);
	root_inode = (struct demofs_inode *)(buf + sizeof(struct demofs_inode)); /* index 1 */
	root_inode->mode = cpu_to_le16(S_IFDIR | 0755);
	root_inode->uid = cpu_to_le16(0);
	root_inode->gid = cpu_to_le16(0);
	root_inode->links_count = cpu_to_le16(2);
	root_inode->size = cpu_to_le32(DEMOFS_BLOCK_SIZE);
	root_inode->blocks = cpu_to_le32(1);
	root_inode->block[0] = cpu_to_le32(4); /* Block 4 is RootDirData */

	if (write(fd, buf, DEMOFS_BLOCK_SIZE) != DEMOFS_BLOCK_SIZE) {
		perror("write inode table");
		close(fd);
		return 1;
	}

	/* 5. Root Directory Data (Block 4)
	 * Initialize "." and ".." directory entries */
	memset(buf, 0, DEMOFS_BLOCK_SIZE);
	
	de = (struct demofs_dir_entry *)buf;
	de->inode = cpu_to_le32(1);
	strcpy(de->name, ".");

	de = (struct demofs_dir_entry *)(buf + sizeof(struct demofs_dir_entry));
	de->inode = cpu_to_le32(1);
	strcpy(de->name, "..");

	if (write(fd, buf, DEMOFS_BLOCK_SIZE) != DEMOFS_BLOCK_SIZE) {
		perror("write root directory data");
		close(fd);
		return 1;
	}

	printf("Formatting complete successfully.\n");
	close(fd);
	return 0;
}
