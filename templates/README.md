# 模板目录说明

本目录为后端运行时使用的模板根目录（`get_templates_dir()` 优先读取此处）。

请将客户正式模板放入本目录（文件名需与下列一致）：

- `质保书模板.docx`（法国 A105 兜底模板）
- `dalian_quality_certificate.docx`（大连质保书，亦可命名为 `大连质保书模板.docx`）
- `装箱单模板.xlsx`
- **大连西门子发货唛头**：`10-DL-mark/dalian_siemens_shipping_mark.xlsx`（或 `大连西门子唛头模板.xlsx`）
- **大连质检质保书（多材质）**：`10-DL-qc/word/{A105|A105_UTMTPT|304|316}/quality_certificate.docx`
- **法国质检质保书（多材质）**：`11-FR-qc/word/{A105|A105_UTMTPT|304|316}/quality_certificate.docx`
- **PDF 版（AcroForm 直填，仅 `QC_PDF_RENDER_MODE=direct` 时使用）**：`{10-DL-qc|11-FR-qc}/pdf/{材质}/quality_certificate.pdf`

## 质保书 PDF 输出（默认：桌面客户端 Word→PDF）

当用户在 **Electron 桌面客户端** 选择 **PDF** 时：

1. 服务器生成 Word（`.docx`）并返回（与选 Word 相同接口）
2. 客户端在指定目录暂存 docx → 本机 **Microsoft Word**（优先）或 **WPS** 转 PDF → 删除临时 docx

**客户端要求**：Windows 桌面版 + 本机 Word 或 WPS（首次需打开一次完成激活）。**服务器无需**为 PDF 安装 Word。

服务器端直转 PDF（`output_format=pdf`）仍保留作备选，见 [`docs/ph1/03-4-4-服务器Word转PDF部署说明.md`](../../docs/ph1/03-4-4-服务器Word转PDF部署说明.md) 第一节备选方案。

## 质保书 PDF 输出（服务器端，备选）

1. 在服务器系统临时目录生成已填占位符的 `.docx`
2. 调用 `PDF_CONVERTER=word`，通过 **Microsoft Word COM** 转为 PDF
3. 将 PDF 返回客户端；临时 docx/pdf 在下载后删除

**服务器要求**：Windows x64、已安装 Microsoft Word、后端以 **RDP 登录的专用账户** 运行（见 [`docs/ph1/03-4-4-服务器Word转PDF部署说明.md`](../../docs/ph1/03-4-4-服务器Word转PDF部署说明.md)）；uvicorn 须 **`--workers 1`**。推荐 `scripts/start-backend.ps1` + 登录自启，**断开 RDP 请勿注销**。

Word 输出仍使用 `word/{材质}/quality_certificate.docx`。客户端无需安装 Word/WPS/LibreOffice。

填模板后默认调用 MS Word 将质保书**压缩为单页**（`.env` 中 `QC_CERT_SINGLE_PAGE=true`），Word 与 PDF 输出均生效。

### 可选模式

| `QC_PDF_RENDER_MODE` | 行为 |
|----------------------|------|
| `word` | Word 模板渲染 → MS Word 转 PDF（**推荐**） |
| `libreoffice` | Word 模板渲染 → LibreOffice 转 PDF |
| `direct` | 仅 AcroForm 填表（不经过 Word 渲染） |
| `auto` | 优先 AcroForm 填表，失败时回退 Word/LibreOffice 转换 |

| `PDF_CONVERTER` | 行为 |
|-----------------|------|
| `word` | MS Word COM（仅 Windows） |
| `libreoffice` | LibreOffice headless |
| `auto` | Windows 上先 Word，失败再 LibreOffice |

部署细节见 [`docs/ph1/03-4-4-服务器Word转PDF部署说明.md`](../../docs/ph1/03-4-4-服务器Word转PDF部署说明.md)。

### AcroForm 直填（`direct` 模式，归档）

1. 系统读取 `pdf/{材质}/quality_certificate.pdf`（须含 AcroForm Text Field，域名与 Word 占位符键一致、无花括号）
2. 用 PyMuPDF 填充表单域（映射规则与 Word 相同），默认 flatten 为静态 PDF

#### 模板制作（脚本辅助）

1. 从现有含 `{{}}` 的 PDF 导出域坐标：

   `python backend/scripts/extract_pdf_field_layout.py`

   输出：`backend/resources/qc_pdf_field_layout/*.json`

2. 批量注入 Text Field（预览默认写入 `storage/_acroform_build/`）：

   `python backend/scripts/build_qc_acroform_templates.py`

   确认后可部署到正式模板：

   `python backend/scripts/build_qc_acroform_templates.py --deploy`

   可用 `--dry-run` 生成域矩形 overlay 供核对。

3. AcroForm 门禁检查：

   `python backend/scripts/inspect_pdf_templates.py`

4. 8 套全覆盖填表测试：

   `python backend/scripts/test_qc_full_coverage.py`

可在 Acrobat/Foxit 中对 JSON 坐标或生成结果做目视微调后重新跑门禁。

法国唛头由程序动态生成，无需 `唛头模板.docx`。`文件读入合同模板.xlsx` 仅作业务参考，不参与生成。

当前系统在模板不存在时会自动使用程序内置模板生成文件，确保流程可继续执行。
