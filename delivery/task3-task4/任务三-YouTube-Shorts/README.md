# 任务三-YouTube-Shorts（规范交付）

客户交付请用本目录。`data.csv` **仅含英文 lowerCamelCase 列**（无中文双列）。

## 字段

`seq`, `sourcePageUrl`, `detailUrl`, `title`, `description`, `coverUrl`, `author`, `authorAvatar`, `viewCount`, `likeCount`, `crawlStatus`, `skipReason`, `coverUrlRelative`, `authorAvatarRelative`

## 文件

| 路径 | 说明 |
|------|------|
| `data.csv` | 主表（UTF-8 BOM），549 行 |
| `images/covers/` | 视频封面（528） |
| `images/avatars/` | 作者头像（238） |
| `pack_images.sh` | 若需从备份重拷图片 |

## 统计

- 总行: 549
- success: 533 / failed: 12 / skipped: 4

原始中英双列草稿：`../需求3的任务2-Shorts/`（内部备份，勿直接发给客户）。
