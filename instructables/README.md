# Instructables（laser cut / laser engrave）采集器

对应 V1.5 **任务三 / Craftseek Task 2**：从 [Instructables Projects](https://www.instructables.com/projects) 按关键词检索并采集公开字段，输出 CSV/Excel。

默认 **Ameede 混合模式**：CSV 写入图片 URL（`coverUrl` / `imageUrls`），**不落盘图片**；需要本地文件时再加 `--download-images`。

本 PR **仅含 Instructables**，不含 Ameede / FreeLaserFile / Robotime / Creative Fabrica。

## 字段映射（V1.5 → camelCase）

| V1.5 说明书 | CSV/Excel 字段 | 说明 |
| --- | --- | --- |
| 采集入口 URL | `sourcePageUrl` | 搜索结果页完整链接，保留关键词与 `projects` 条件；分页带 `page=` |
| 项目详情页 URL | `detailUrl` | `https://www.instructables.com/{urlString}/` |
| 项目名称 | `title` | 详情完整标题 |
| 作者名称 | `author` | `By` 后作者名（`screenName`），不含分类文字 |
| 项目分类 | `categoryPath` | 层级路径，如 `Workshop > Laser Cutting` |
| 项目高清大图 URL | `coverUrl` + `imageUrls` | 主图与项目关联图直链（`\|` 分隔）；已排除广告域 |
| （可选）本地路径 | `coverUrlRelative` + `imagePaths` | 仅 `--download-images` 时填充 |
| 心形互动数 | `favoriteCount` | 列表/详情 favorites（心形） |

附加列：`viewCount`、`searchKeyword` / `searchKeywords`、`projectsScope`、`entryType`、`crawlStatus`、`skipReason`、`seq`。

### 去重与广告

- 双关键词 `laser cut`、`laser engrave` 按 **`detailUrl` 去重**（先到先得）。
- 默认 `projectsScope=featured`（`featureFlag:=true`），与说明书量级一致（约 7.5k + 1.1k ≈ 8.6k）。
- `sponsoredFlag` / `indexTags=external` / 非 Instructables CDN 图视为广告或无关，不入主表（见 `instructables_skipped.csv`）。

## 安装

```bash
cd instructables
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 运行

样例（约 5 条，元数据 + URL，不下载图）：

```bash
cd instructables
python crawl_instructables.py --sample 5 -v
```

全量（断点续跑，URL-only）：

```bash
python crawl_instructables.py --min-delay 0.8 --max-delay 1.6 \
  --snapshot-dir /path/to/docs/instructables-live
# 中断后再次执行即可 resume
python crawl_instructables.py --min-delay 0.8 --max-delay 1.6 \
  --snapshot-dir /path/to/docs/instructables-live
```

需要本地图片时：

```bash
python crawl_instructables.py --download-images --max-images-per-item 3
```

可选：`--projects-scope all` 扩大为非 featured（条数高于 V1.5 预估）。

## 输出路径

相对 `instructables/`：

| 路径 | 内容 |
| --- | --- |
| `data/instructables/instructables.csv` | 主表 |
| `data/instructables/instructables.xlsx` | Excel |
| `data/instructables/instructables_skipped.csv` | 广告等跳过行 |
| `data/instructables/PROGRESS.json` | 运行中进度快照 |
| `data/instructables/CRAWL_COMPLETE.json` | 完成后摘要 |
| `data/instructables/images/` | 本地图片（仅 `--download-images`） |
| `data/instructables/checkpoints/state.json` | 断点 |
| `data/instructables/run_summary.json` | 运行摘要 |

`--snapshot-dir` 会把 CSV/xlsx/`PROGRESS.json` 同步到交付目录（如 Project store `docs/instructables-live/`）。

## 与 V1.5 Instructables 表的已知差异 / 缺口

1. **图片**：说明书「仅提取 URL」；默认 URL-only（Ameede hybrid）。合同若要求落盘，用 `--download-images` 填 `coverUrlRelative` / `imagePaths`。
2. **多图字段**：说明书「主图及关联图」→ `coverUrl` + `imageUrls`（及可选本地路径）。
3. **搜索范围**：默认 **featured** 对齐说明书 ~8600；站点「全部」更多，可用 `--projects-scope all`。
4. **心形字段命名**：原站为 favorites，字段名用 `favoriteCount`（避免误标 likes）。
5. **右侧广告图**：按域名白名单排除；若站内推广卡片形态变化需补规则。
