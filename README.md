# Mingyuan ERP — Backend API / 銘遠 ERP — バックエンド API / 铭远 ERP — 后端 API

**Languages / 言語 / 语言：** [English](#english) · [日本語](#日本語) · [中文](#中文)

---

<a id="english"></a>

## English

FastAPI backend for the [Mingyuan Digital ERP System](../README.md). It exposes REST APIs for orders, production, QC, packing, shipping, and finance, and generates Word / Excel / PDF documents such as quality certificates, packing lists, and shipping marks.

| Item | Description |
|------|-------------|
| Framework | FastAPI 0.109 + Pydantic 2 + Uvicorn |
| Language | Python 3.11+ |
| Database | MySQL 8.0+ (raw SQL, no ORM) |
| API prefix | `/api/v1` |
| Interactive docs | [`http://localhost:8000/docs`](http://localhost:8000/docs) after startup |

### API Modules

| Module | Path | Responsibility |
|--------|------|----------------|
| `workflow` | `/api/v1/workflow` | Contracts, production archiving, materials, QC, packing, shipping (main flow) |
| `heat_treatment` | `/api/v1/heat-treatment` | Heat treatment tests, chemical composition, result summary |
| `finance` | `/api/v1/finance` | Test fees, payments / reconciliation (partially reserved) |
| `documents` | `/api/v1/documents` | Document preview and export (MVP) |
| `operations` | `/api/v1/operations` | Early MVP work-order endpoints |
| `auth` | `/api/v1/auth` | Login and user info (JWT placeholder) |

Health check: `GET /health` (includes database connectivity)

### Directory Layout

```
backend/
├── app/
│   ├── main.py              # App entry, CORS, global exceptions, API audit logging
│   ├── api/v1/              # REST routes
│   ├── core/                # Config, database, i18n, file logging
│   ├── services/            # Template rendering, placeholder mapping, PDF handling
│   └── domain/              # Document field specifications
├── scripts/                 # DB init, migrations, QC test scripts
├── templates/               # Runtime templates (QC certs, packing lists, marks, etc.)
├── resources/               # Fonts, PDF field layouts, static assets
├── storage/                 # Runtime logs and generated files (local; commit as needed)
├── requirements.txt
├── Dockerfile
└── .env.example             # Sample env vars (copy to .env and fill in)
```

See [`templates/README.md`](templates/README.md) for template details.

### Quick Start

**1. Install dependencies**

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

On Windows you can also run `scripts/install-deps.ps1`.

**2. Configure environment variables**

```bash
cp .env.example .env   # Windows: copy .env.example .env
```

Edit `.env` and set at least MySQL connection settings and `SECRET_KEY` (use a long random string in production). See [`.env.example`](.env.example) for all options.

> `.env` is listed in `.gitignore`. Do not commit real passwords or secrets.

**3. Initialize the database**

```bash
mysql -u root -p < scripts/init_db.sql
python scripts/apply_db_updates.py    # Incremental schema
python scripts/migrate_test_fee.py    # Test fee tables
```

**4. Start the server**

Development (recommended):

```bash
# From monorepo root
npm run dev:backend

# Or from this directory
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Windows production (includes Word COM → PDF):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1
```

Default URL: `http://localhost:8000`.

### Docker

Start MySQL + backend + frontend from the monorepo root:

```bash
docker-compose up -d
```

Build the backend image only:

```bash
docker build -t mingyuan-erp-backend .
docker run -p 8000:8000 --env-file .env mingyuan-erp-backend
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | MySQL connection |
| `SECRET_KEY` / `ALGORITHM` / `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT (for future login integration) |
| `DEBUG` | Debug mode; set to `false` in production |
| `CORS_ALLOW_ORIGINS` | Extra CORS origins, comma-separated |
| `PDF_CONVERTER` | `word` / `libreoffice` / `auto` — docx to PDF |
| `QC_PDF_RENDER_MODE` | `word` / `libreoffice` / `direct` / `auto` — QC certificate PDF strategy |
| `QC_CERT_SINGLE_PAGE` | Compress filled template to a single-page Word doc |

### Useful Scripts

| Script | Purpose |
|--------|---------|
| `scripts/init_db.sql` | Full schema bootstrap |
| `scripts/apply_db_updates.py` | Incremental DDL |
| `scripts/check_schema.py` | Validate schema |
| `scripts/test_qc_full_coverage.py` | 8-template QC placeholder coverage test |
| `scripts/test_all_templates.py` | Template rendering smoke test |
| `scripts/start-backend.ps1` / `stop-backend.ps1` | Windows production start/stop |

### Logs & Audit

Runtime logs are written to `storage/logs/` (`*.log` is git-ignored):

| File | Content |
|------|---------|
| `api_*.log` | API request log |
| `order_detail_change_*.log` | Order detail changes |
| `qty_change_*.log` | QC delivery quantity edits |
| `heat_trial_change_*.log` | Heat treatment trial changes |

### Related Docs

- Project overview: [../README.md](../README.md)
- Phase-1 architecture & design: `docs/ph1/`
- Database design: `docs/011-1-数据库与后端逻辑设计.md`
- Server Word → PDF deployment: `docs/ph1/03-4-4-服务器Word转PDF部署说明.md`

### License

Same as the [Mingyuan ERP monorepo](../README.md). Internal project — do not redistribute without an explicit license.

[↑ Back to top](#mingyuan-erp--backend-api--銘遠-erp--バックエンド-api--铭远-erp--后端-api)

---

<a id="日本語"></a>

## 日本語

[銘遠デジタル管理システム（ERP）](../README.md) の FastAPI バックエンドです。受注・生産・品質検査・梱包・出荷・財務向け REST API を提供し、品質保証書・梱包リスト・マーク等の Word / Excel / PDF 文書を生成します。

| 項目 | 説明 |
|------|------|
| フレームワーク | FastAPI 0.109 + Pydantic 2 + Uvicorn |
| 言語 | Python 3.11+ |
| データベース | MySQL 8.0+（ネイティブ SQL、ORM なし） |
| API プレフィックス | `/api/v1` |
| 対話型ドキュメント | 起動後 [`http://localhost:8000/docs`](http://localhost:8000/docs) |

### 機能モジュール

| モジュール | パス | 役割 |
|------------|------|------|
| `workflow` | `/api/v1/workflow` | 契約・受注、製品登録、資材、QC、梱包、出荷（メインフロー） |
| `heat_treatment` | `/api/v1/heat-treatment` | 熱処理試験、化学成分、結果集計 |
| `finance` | `/api/v1/finance` | 試験費用、支払・照合（一部予約） |
| `documents` | `/api/v1/documents` | 帳票プレビュー・エクスポート（MVP） |
| `operations` | `/api/v1/operations` | 初期 MVP 作業指示 API |
| `auth` | `/api/v1/auth` | ログイン・ユーザー情報（JWT プレースホルダ） |

ヘルスチェック：`GET /health`（DB 接続状態を含む）

### ディレクトリ構成

```
backend/
├── app/
│   ├── main.py              # エントリ、CORS、例外処理、API 監査ログ
│   ├── api/v1/              # REST ルート
│   ├── core/                # 設定、DB、多言語、ファイルログ
│   ├── services/            # テンプレート描画、プレースホルダ映射、PDF 処理
│   └── domain/              # 帳票フィールド仕様
├── scripts/                 # DB 初期化、マイグレーション、QC テスト
├── templates/               # 実行時テンプレート（保証書、梱包リスト、マーク等）
├── resources/               # フォント、PDF フィールドレイアウト等
├── storage/                 # 実行時ログ・生成ファイル（ローカル、必要に応じてコミット）
├── requirements.txt
├── Dockerfile
└── .env.example             # 環境変数サンプル（.env にコピーして設定）
```

テンプレート詳細は [`templates/README.md`](templates/README.md) を参照。

### クイックスタート

**1. 依存関係のインストール**

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Windows では `scripts/install-deps.ps1` も利用可能です。

**2. 環境変数の設定**

```bash
cp .env.example .env   # Windows: copy .env.example .env
```

`.env` を編集し、最低限 MySQL 接続と `SECRET_KEY`（本番は十分長いランダム文字列）を設定してください。全項目は [`.env.example`](.env.example) を参照。

> `.env` は `.gitignore` 対象です。パスワードや秘密鍵をコミットしないでください。

**3. データベース初期化**

```bash
mysql -u root -p < scripts/init_db.sql
python scripts/apply_db_updates.py    # 増分スキーマ
python scripts/migrate_test_fee.py    # 試験費用テーブル
```

**4. サーバー起動**

開発モード（推奨）：

```bash
# monorepo ルートから
npm run dev:backend

# または本ディレクトリから
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Windows 本番（Word COM による PDF 変換を含む）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1
```

デフォルト：`http://localhost:8000`

### Docker

monorepo ルートから MySQL + バックエンド + フロントエンドを一括起動：

```bash
docker-compose up -d
```

バックエンドイメージのみビルド：

```bash
docker build -t mingyuan-erp-backend .
docker run -p 8000:8000 --env-file .env mingyuan-erp-backend
```

### 環境変数（概要）

| 変数 | 説明 |
|------|------|
| `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | MySQL 接続 |
| `SECRET_KEY` / `ALGORITHM` / `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT（将来のログイン連携用） |
| `DEBUG` | デバッグモード；本番は `false` |
| `CORS_ALLOW_ORIGINS` | 追加 CORS オリジン（カンマ区切り） |
| `PDF_CONVERTER` | `word` / `libreoffice` / `auto` — docx → PDF |
| `QC_PDF_RENDER_MODE` | `word` / `libreoffice` / `direct` / `auto` — 保証書 PDF 戦略 |
| `QC_CERT_SINGLE_PAGE` | テンプレート填充後に 1 ページ Word に圧縮するか |

### よく使うスクリプト

| スクリプト | 用途 |
|------------|------|
| `scripts/init_db.sql` | 全量テーブル作成 |
| `scripts/apply_db_updates.py` | 増分 DDL |
| `scripts/check_schema.py` | スキーマ検証 |
| `scripts/test_qc_full_coverage.py` | 8 種保証書プレースホルダカバレッジテスト |
| `scripts/test_all_templates.py` | テンプレート描画スモークテスト |
| `scripts/start-backend.ps1` / `stop-backend.ps1` | Windows 本番起動・停止 |

### ログと監査

実行時ログは `storage/logs/` に出力（`*.log` は git 除外）：

| ファイル | 内容 |
|----------|------|
| `api_*.log` | API リクエスト記録 |
| `order_detail_change_*.log` | 受注明細変更 |
| `qty_change_*.log` | 保証書納入数量の変更 |
| `heat_trial_change_*.log` | 熱処理試験変更 |

### 関連ドキュメント

- プロジェクト概要：[../README.md](../README.md)
- 第 1 期アーキテクチャ・設計：`docs/ph1/`
- DB 設計：`docs/011-1-数据库与后端逻辑设计.md`
- サーバー Word → PDF デプロイ：`docs/ph1/03-4-4-服务器Word转PDF部署说明.md`

### ライセンス

[銘遠 ERP monorepo](../README.md) と同一。社内プロジェクトのため、明示的なライセンス声明がない限り外部配布しないでください。

[↑ Back to top](#mingyuan-erp--backend-api--銘遠-erp--バックエンド-api--铭远-erp--后端-api)

---

<a id="中文"></a>

## 中文

[铭远数字化管理系统](../README.md) 的 FastAPI 后端，提供订单、生产、质检、装箱、发货、财务等 REST 接口，并负责质保书、装箱单、唛头等单据的 Word / Excel / PDF 生成。

| 项目 | 说明 |
|------|------|
| 框架 | FastAPI 0.109 + Pydantic 2 + Uvicorn |
| 语言 | Python 3.11+ |
| 数据库 | MySQL 8.0+（原生 SQL，无 ORM） |
| API 前缀 | `/api/v1` |
| 交互文档 | 启动后访问 [`http://localhost:8000/docs`](http://localhost:8000/docs) |

### 功能模块

| 路由模块 | 路径 | 职责 |
|----------|------|------|
| `workflow` | `/api/v1/workflow` | 合同订单、产品建档、物料、质检、装箱、发货（主流程） |
| `heat_treatment` | `/api/v1/heat-treatment` | 热处理试验、化学成分、结果汇总 |
| `finance` | `/api/v1/finance` | 试验费用、付款/对账（部分预留） |
| `documents` | `/api/v1/documents` | 单据预览与导出（MVP） |
| `operations` | `/api/v1/operations` | 早期 MVP 工单接口 |
| `auth` | `/api/v1/auth` | 登录与用户信息（JWT 占位） |

健康检查：`GET /health`（含数据库连通性）

### 目录结构

```
backend/
├── app/
│   ├── main.py              # 应用入口、CORS、全局异常与 API 审计日志
│   ├── api/v1/              # REST 路由
│   ├── core/                # 配置、数据库、多语言、文件日志
│   ├── services/            # 模板渲染、占位符映射、PDF 处理
│   └── domain/              # 单据字段规范
├── scripts/                 # 数据库初始化、迁移与 QC 测试脚本
├── templates/               # 运行时模板（质保书、装箱单、唛头等）
├── resources/               # 字体、PDF 域布局等静态资源
├── storage/                 # 运行时日志与生成文件（本地数据，按需提交）
├── requirements.txt
├── Dockerfile
└── .env.example             # 环境变量示例（复制为 .env 后填写）
```

模板说明见 [`templates/README.md`](templates/README.md)。

### 快速开始

**1. 安装依赖**

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Windows 也可运行 `scripts/install-deps.ps1`。

**2. 配置环境变量**

```bash
cp .env.example .env   # Windows: copy .env.example .env
```

编辑 `.env`，至少配置 MySQL 连接与 `SECRET_KEY`（生产环境请使用足够长的随机字符串）。完整项说明见 [`.env.example`](.env.example)。

> `.env` 已在仓库 `.gitignore` 中忽略，请勿提交真实密码或密钥。

**3. 初始化数据库**

```bash
mysql -u root -p < scripts/init_db.sql
python scripts/apply_db_updates.py    # 增量表结构
python scripts/migrate_test_fee.py    # 试验费用表
```

**4. 启动服务**

开发模式（推荐）：

```bash
# 在 monorepo 根目录
npm run dev:backend

# 或在本目录
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Windows 生产环境（含 Word COM 转 PDF）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1
```

默认监听 `http://localhost:8000`。

### Docker

在 monorepo 根目录使用 Compose 一键启动 MySQL + 后端 + 前端：

```bash
docker-compose up -d
```

单独构建后端镜像：

```bash
docker build -t mingyuan-erp-backend .
docker run -p 8000:8000 --env-file .env mingyuan-erp-backend
```

### 环境变量摘要

| 变量 | 说明 |
|------|------|
| `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | MySQL 连接 |
| `SECRET_KEY` / `ALGORITHM` / `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT（登录接入后使用） |
| `DEBUG` | 调试模式；生产环境设为 `false` |
| `CORS_ALLOW_ORIGINS` | 额外 CORS 源，逗号分隔 |
| `PDF_CONVERTER` | `word` / `libreoffice` / `auto` — docx 转 PDF |
| `QC_PDF_RENDER_MODE` | `word` / `libreoffice` / `direct` / `auto` — 质保书 PDF 策略 |
| `QC_CERT_SINGLE_PAGE` | 填模板后是否压缩为单页 Word |

### 常用脚本

| 脚本 | 用途 |
|------|------|
| `scripts/init_db.sql` | 全量建表 |
| `scripts/apply_db_updates.py` | 增量 DDL |
| `scripts/check_schema.py` | 校验表结构 |
| `scripts/test_qc_full_coverage.py` | 八套质保书占位符覆盖率测试 |
| `scripts/test_all_templates.py` | 模板渲染冒烟测试 |
| `scripts/start-backend.ps1` / `stop-backend.ps1` | Windows 生产启停 |

### 日志与审计

运行时日志写入 `storage/logs/`（`*.log` 已被 git 忽略）：

| 文件 | 内容 |
|------|------|
| `api_*.log` | API 请求记录 |
| `order_detail_change_*.log` | 订单明细变更 |
| `qty_change_*.log` | 质保书交付数量修改 |
| `heat_trial_change_*.log` | 热处理试验变更 |

### 相关文档

- 项目总览与业务说明：[../README.md](../README.md)
- 一期架构与设计：`docs/ph1/`
- 数据库设计：`docs/011-1-数据库与后端逻辑设计.md`
- 服务器 Word 转 PDF 部署：`docs/ph1/03-4-4-服务器Word转PDF部署说明.md`

### 许可证

与 [铭远数字化 ERP monorepo](../README.md) 保持一致；内部项目，未单独声明开源协议前请勿对外分发。

[↑ Back to top](#mingyuan-erp--backend-api--銘遠-erp--バックエンド-api--铭远-erp--后端-api)
