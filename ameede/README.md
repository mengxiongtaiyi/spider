# Ameede（Vector CNC-Laser）采集器

对应 V1.5 **任务二 / Craftseek Task 2**：从 [Ameede Vector CNC-Laser](https://www.ameede.com/amee/vector-cnc-laser/) 采集激光相关项目公开字段。

**字段 4（封面图）已确认：仅提取详情页中央高清图 URL，不下载图片。**  
本 PR **不包含** Instructables / FreeLaserFile / Robotime / Creative Fabrica。

## 字段映射（V1.5 → camelCase）

| V1.5 说明书 | CSV/Excel 字段 | 说明 |
| --- | --- | --- |
| 采集入口URL | `sourcePageUrl` | 分类列表页；分页保留实际 `/page/N/` URL |
| 项目详情页 URL | `detailUrl` | 项目详情；不含广告外链 |
| 项目名称 | `title` | 详情页完整标题（含项目编号） |
| 高清大图 URL | `coverUrl` | 详情页正文中央主图直链；**仅 URL，不落盘** |
| （历史列，可空） | `coverUrlRelative` | 早期试跑曾落盘；新采行为空，保留兼容 |

附加列（便于验收与区分入口，非 V1.5 强制四字段）：

- `projectCode`：从标题/URL 解析的编号（如 `E0025665`）
- `downloadUrl`：详情页上的 `/download/...` 入口（**不当作源站记录**，仅标注）
- `categories` / `tags`：详情页公开分类与标签
- `entryType`：`detail`（入库） / 跳过侧见 `ameede_skipped.csv`
- `crawlStatus` / `skipReason` / `seq`

### 入口区分规则

| 类型 | 判定 | 是否写入主表 |
| --- | --- | --- |
| 详情页 | `ameede.com` 项目 slug 页（`type-post`） | 是 |
| 下载入口 | `/download/{code}/`（WPDM `wpdmpro`） | 否（记入 skipped） |
| 广告/外链 | 非 ameede 域名或广告路径 | 否（记入 skipped） |

## 安装

```bash
cd ameede
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 运行

样例（约 5 条，列表→详情→`coverUrl`）：

```bash
cd ameede
python crawl_ameede.py --sample 5 -v
```

全量（约 1433 页 / ~25–26k，断点续跑；默认不下载图片）：

```bash
cd ameede
# 续跑（不要加 --fresh）
python crawl_ameede.py --min-delay 0.2 --max-delay 0.45 --xlsx-every-pages 50
# 或带 watchdog
bash watchdog_only.sh
```

常用参数：

- `--max-items N` / `--max-pages N` / `--start-page N`
- `--fresh` 清空 checkpoint 与输出后重跑
- `--download-images` 可选落盘（默认关闭，符合 Craftseek 字段 4）
- `--no-xlsx` 跳过 Excel

## 输出路径

均相对于 `ameede/` 目录：

| 路径 | 内容 |
| --- | --- |
| `data/ameede/ameede.csv` | 主数据 CSV |
| `data/ameede/ameede.xlsx` | 主数据 Excel |
| `data/ameede/ameede_skipped.csv` | 下载入口/广告等非源站 |
| `data/ameede/checkpoints/` | 断点 |
| `data/ameede/run_summary.json` | 运行摘要 |
| `data/ameede/images/` | 仅当显式 `--download-images` 时使用；历史试跑文件可保留 |

## 与 V1.5 / Craftseek 确认

1. **封面图**：仅 `coverUrl`（详情中央高清直链）；不下载。`coverUrlRelative` 对新行为空。
2. **广告 / 下载入口**：不当作源站记录。
3. **全量条数**：约 **1433** 页 × ~20 条；目标 ~25k–26k，以实际可列表量为准。
