# Instructables（laser cut / laser engrave）采集器

对应 V1.5 **任务三 / Craftseek Task 2**：从 [Instructables Projects](https://www.instructables.com/projects) 按关键词检索并采集公开字段，**下载封面图 + 作者头像到本地**，输出 CSV/Excel。

本 PR **仅含 Instructables**，不含 Ameede / FreeLaserFile / Robotime / Creative Fabrica。

## 字段映射（对齐既有 Craftseek 交付）

| 字段 | 说明 |
| --- | --- |
| `sourcePageUrl` | 搜索结果页完整链接 |
| `detailUrl` | `https://www.instructables.com/{urlString}/` |
| `title` / `author` | 标题与作者 `screenName` |
| `authorAvatar` | 作者头像 URL（详情 `author.downloadUrl`） |
| `authorAvatarRelative` | 本地头像路径，如 `images/avatars/...` |
| `categoryPath` | 如 `Workshop > Laser Cutting` |
| `coverUrl` / `coverUrlRelative` | 封面 URL / 本地路径 `images/covers/...` |
| `imageUrls` / `imagePaths` | 关联图 URL（`\|` 分隔）；本地默认仅封面 |
| `favoriteCount` | 心形 favorites |

附加列：`viewCount`、`searchKeyword` / `searchKeywords`、`projectsScope`、`entryType`、`crawlStatus`、`skipReason`、`seq`。

### 去重与广告

- 双关键词 `laser cut`、`laser engrave` 按 **`detailUrl` 去重**。
- 默认 `projectsScope=featured`（约 7.5k + 1.1k ≈ 8.6k）。
- `sponsoredFlag` / `indexTags=external` / 非 Instructables CDN 图跳过。

## 安装与运行

```bash
cd instructables
pip install -r requirements.txt
python crawl_instructables.py --sample 5 -v
python crawl_instructables.py --min-delay 0.5 --max-delay 1.0 \
  --snapshot-dir /path/to/docs/instructables-live
```

补下已有 CSV 的封面/头像（可与续跑并行，带文件锁）：

```bash
python backfill_images.py --snapshot-dir /path/to/docs/instructables-live
```

`--no-images` 可关下载；`--max-images-per-item N`（N>1）额外落盘画廊图。

## 输出

| 路径 | 内容 |
| --- | --- |
| `data/instructables/instructables.csv` / `.xlsx` | 主表 |
| `data/instructables/images/covers/` | 封面 |
| `data/instructables/images/avatars/` | 作者头像 |
| `data/instructables/PROGRESS.json` | 进度 |
| `data/instructables/checkpoints/state.json` | 断点 |
