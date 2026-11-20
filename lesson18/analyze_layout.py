"""Document Intelligence のレイアウト モデルで、PDF を解析する（コマ18。教員が用意したもの）

使い方
  python lesson18/analyze_layout.py lesson18/pdf/kamome_shukuhaku.pdf      Azure の Document Intelligence で解析する
  python lesson18/analyze_layout.py --sample                               見本の解析結果を使う（Azure につながない）

結果を lesson18/out/<名前>.layout.json（解析結果）と <名前>.layout.md（Markdown）に保存する。
自分のサインイン（az login）で接続する。キーは使わない。エンドポイントは create-docintel.sh が保存したもの。

同じ内容のファイルを二度解析しないように、内容のハッシュで結果を保存しておく（lesson18/out/cache/）。
文書解析はページ数で課金される（F0 は無料だが、1回に解析されるのは先頭の2ページまで）。
取り込みのパイプラインでも、同じ文書を解析し直さない工夫が要る（コマ15の埋め込みキャッシュと同じ考え方）。
"""

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

from common import HERE, OUT, ROOT, layout_path

SAMPLE = HERE / "sample" / "kamome_shukuhaku.layout.json"


def analyze(pdf_path, endpoint):
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.ai.documentintelligence.models import AnalyzeDocumentRequest, DocumentContentFormat
    from azure.core.exceptions import HttpResponseError
    from azure.identity import AzureCliCredential

    client = DocumentIntelligenceClient(endpoint, AzureCliCredential())
    started = time.time()
    try:
        poller = client.begin_analyze_document(
            "prebuilt-layout",
            AnalyzeDocumentRequest(bytes_source=pdf_path.read_bytes()),
            output_content_format=DocumentContentFormat.MARKDOWN,
        )
        result = poller.result()  # 解析は非同期。終わるまで待つ（SDK が結果を問い合わせ続ける）
    except HttpResponseError as e:
        if e.status_code in (401, 403):
            sys.exit("401/403 になった。ロールの割り当てが行き渡るまで、数分待ってからもう一度実行する。")
        sys.exit(f"解析に失敗した（HTTP {e.status_code}）: {e.message}")
    print(f"解析にかかった時間: {time.time() - started:.1f} 秒")
    return result.as_dict()


def report(result):
    roles = Counter(p.get("role", "（本文）") for p in result.get("paragraphs", []))
    print(f"ページ: {len(result.get('pages', []))}、段落: {len(result.get('paragraphs', []))}、表: {len(result.get('tables', []))}")
    print("段落の役割: " + "、".join(f"{k} {v}" for k, v in roles.items()))
    for i, t in enumerate(result.get("tables", []), 1):
        print(f"表{i}: {t['rowCount']} 行 × {t['columnCount']} 列、セル {len(t['cells'])} 個")


def save(name, result):
    OUT.mkdir(exist_ok=True)
    layout_path(name).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    md = OUT / f"{name}.layout.md"
    md.write_text(result["content"], encoding="utf-8", newline="\n")
    print(f"{layout_path(name).relative_to(ROOT)} と {md.relative_to(ROOT)} に保存した。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", nargs="?")
    parser.add_argument("--sample", action="store_true", help="見本の解析結果を使う（Azure につながない）")
    args = parser.parse_args()

    if args.sample:
        result = json.loads(SAMPLE.read_text(encoding="utf-8"))
        print("見本の解析結果を使う（教員が作ったもの。実際の解析結果ではない）")
        report(result)
        save("kamome_shukuhaku", result)
        return
    if not args.pdf:
        sys.exit("使い方: python lesson18/analyze_layout.py <PDF のパス>  または  --sample")

    pdf_path = Path(args.pdf)
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    cached = OUT / "cache" / f"{digest}.json"
    if cached.exists():
        print("同じ内容のファイルの解析結果がある。キャッシュを使う（Document Intelligence を呼ばない）")
        result = json.loads(cached.read_text(encoding="utf-8"))
    else:
        endpoint_file = OUT / "endpoint.txt"
        if not endpoint_file.exists():
            sys.exit("lesson18/out/endpoint.txt がない。先に create-docintel.sh を実行する。")
        result = analyze(pdf_path, endpoint_file.read_text(encoding="utf-8").strip())
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    report(result)
    save(pdf_path.stem, result)


if __name__ == "__main__":
    main()
