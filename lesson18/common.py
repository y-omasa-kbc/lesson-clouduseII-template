"""コマ18の道具で共通に使うもの（教員が用意したもの）"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out"
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "evaluation"))


def simple_text(pdf_path):
    """単純抽出: PDF の中の文字の並びを、そのまま取り出す（pypdf）"""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages), len(reader.pages)


def layout_path(name):
    return OUT / f"{name}.layout.json"


def load_layout(name):
    path = layout_path(name)
    if not path.exists():
        sys.exit(f"{path.relative_to(ROOT)} がない。先に analyze_layout.py（または --sample）を実行する。")
    return json.loads(path.read_text(encoding="utf-8"))
