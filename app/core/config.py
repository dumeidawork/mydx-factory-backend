"""应用配置 - 从环境变量加载"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    app_name: str = "铭远ERP系统API"
    debug: bool = False

    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = "Dmd142357869."
    mysql_database: str = "mingyuan_erp"

    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # PDF 转换：docx → pdf（word=MS Word COM | libreoffice | auto）
    pdf_converter: str = "word"
    libreoffice_path: str = ""
    pdf_word_lock_timeout: int = 120
    # 质保书 PDF 输出：direct=AcroForm 填表 | word/libreoffice=Word 模板渲染后转换 | auto=填表失败回退
    qc_pdf_render_mode: str = "word"
    # 质保书 Word 输出压缩为单页（填模板后通过 MS Word 调整）
    qc_cert_single_page: bool = True
    # direct 模式填表引擎（当前仅支持 acroform）
    qc_pdf_fill_engine: str = "acroform"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
