# Robotime Online 采集器

对应 V1.5 **任务五 / Craftseek Task 4**：从 [Robotime Online All Products](https://www.robotimeonline.com/collections/all) 采集商品公开字段，并**落盘高清图**。

本 PR **仅含 Robotime**，不含 Ameede / Instructables / FreeLaserFile / Creative Fabrica。

Shopify 店铺：优先 `/collections/all/products.json`；可选 HTML 补齐最终 URL / JSON-LD 品牌与库存语义。

## 字段映射（V1.5 → camelCase）

| V1.5 说明书 | CSV/Excel 字段 | 说明 |
| --- | --- | --- |
| Online 来源 URL | `sourceUrl` | Robotime Online 商品详情链接 |
| 实际源站 URL | `actualSourceUrl` | 跟随跳转后的最终 URL；无外跳时与 `sourceUrl` 相同 |
| 商品名称 | `title` | 完整标题（含型号） |
| 设计师/品牌 | `designer` + `brand` | **仅**页面明确展示的设计师写入 `designer`；Shopify `vendor`/JSON-LD brand 写入 `brand`，**不把品牌推断为设计师** |
| 价格/销售状态 | `price` / `currency` / `compareAtPrice` / `availabilityStatus` | 售价、币种、划线价、`inStock`/`soldOut`/`preorder` |
| 源站高清大图 | `coverUrl` + `imageUrls` | 主图与详情图 URL |
| （合同增补）本地路径 | `coverUrlRelative` + `imagePaths` | 如 `images/covers/...`（全量默认只落盘封面） |
| 作者头像 | — | 站点无 per-product author/avatar；`images/avatars/` 预留为空 |

附加列：`handle`、`productId`、`productType`、`sku`、`variantCount`、`tags`、`collectionSourcePageUrl`、`entryType`、`crawlStatus`、`skipReason`、`seq`。

### 变体

同一商品的颜色/规格等 **不拆行**；`variantCount` 记录变体数，价格取「第一个 available 变体，否则第一个变体」。

## 安装

```bash
cd robotime
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 运行

样例（仓库内已提交 `sample_delivery/` 5 条 + covers）：

```bash
cd robotime
python crawl_robotime.py --sample 5 -v
```

全量（断点续跑 + 可选 live 镜像）：

```bash
python crawl_robotime.py --fresh --expected-total 662 \
  --snapshot-dir /path/to/docs/robotime-live \
  --min-delay 0.4 --max-delay 1.0
python crawl_robotime.py --expected-total 662 \
  --snapshot-dir /path/to/docs/robotime-live \
  --min-delay 0.4 --max-delay 1.0   # resume
```

## 输出路径

相对 `robotime/`：

| 路径 | 内容 |
| --- | --- |
| `data/robotime/robotime.csv` | 主表 |
| `data/robotime/robotime.xlsx` | Excel |
| `data/robotime/images/covers/` | 本地封面（默认） |
| `data/robotime/images/avatars/` | 预留空（站点无作者头像） |
| `data/robotime/checkpoints/state.json` | 断点 |
| `data/robotime/PROGRESS.json` | 进度 |
| `data/robotime/CRAWL_COMPLETE.json` | 完成标记 |
| `data/robotime/IMAGE_DOWNLOAD_FAILURES.json` | 图片失败原因 |
| `data/robotime/run_summary.json` | 摘要 |

## 与 V1.5 Robotime 表的已知差异 / 缺口

1. **图片落盘**：说明书「仅提取 URL」；合同要求下载 → 增加本地路径字段（默认封面；`--max-images-per-item` 可扩 gallery）。
2. **条数**：说明书估 ~386；现网 `collections/all` **662** 商品 handle（按商品一行；变体不拆行）。
3. **设计师 / 头像**：多数商品仅有 brand/vendor（如 ROKR），`designer` 常为空；无 per-product author avatar。
4. **价格快照**：多变体商品只保留一行价格快照（优先 available 变体），不做变体展开。
5. **Live 镜像**：`--snapshot-dir` 同步 CSV/xlsx/images/PROGRESS；store I/O 失败非致命。
