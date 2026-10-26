# 任务三-YouTube-KOL（规范交付）

客户交付请用本目录。`data.csv` **仅含英文 lowerCamelCase 列**（无中文双列）。

## 字段

`seq`, `sourcePageUrl`, `detailUrl`, `title`, `description`, `coverUrl`, `author`, `authorAvatar`, `viewCount`, `likeCount`, `crawlStatus`, `skipReason`, `coverUrlRelative`, `authorAvatarRelative`

## 文件

| 路径 | 说明 |
|------|------|
| `data.csv` | 主表（UTF-8 BOM），2152 行 |
| `images/covers/` | 视频封面（1991） |
| `images/avatars/` | 作者头像（940） |
| `pack_images.sh` | 若需从备份重拷图片 |

## 统计

- 总行: 2152
- success: 2046 / failed: 60 / skipped: 46

原始中英双列草稿：`../需求3的任务2/`（内部备份，勿直接发给客户）。
