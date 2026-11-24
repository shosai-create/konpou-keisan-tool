#!/usr/bin/env python3
"""SNS投稿パイプライン v1（Threads下書き作成）

投稿の実行・ネットへの送信は一切しません。標準ライブラリだけで動きます。

使い方:
  python3 pipeline.py brief  [--topics topics.csv]        下書き作成用の指示文を表示
  python3 pipeline.py save   <生成JSON> [--date YYYY-MM-DD] 下書きを検査して drafts/ に保存
  python3 pipeline.py check  [drafts/YYYY-MM-DD.json]       保存済み下書きを再検査

下書き本文は Claude Code が rules.md に従って作ります（/sns-drafts コマンド）。
このスクリプトは「読み込み・型のチェック・保存」を担当します。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DRAFTS_DIR = BASE / "drafts"
ACCOUNTS = {"oem", "juken"}
MAX_CHARS = 500
VARIANTS_PER_THEME = 3

# 「どうやるか」に踏み込んでいそうな表現（警告のみ。最終判断は人）
HOWTO_PATTERNS = [
    r"手順", r"ステップ", r"チェックリスト", r"判断基準", r"やり方",
    r"^\s*[0-9０-９]+[.．)）、]", r"^\s*[①-⑳]", r"^\s*[・\-*]\s",
    r"STEP", r"まず.+次に", r"第[一二三1-3]に",
]
URL_PATTERN = re.compile(r"https?://|www\.", re.I)
HASHTAG_PATTERN = re.compile(r"(^|\s)[#＃]\S")


def threads_len(text: str) -> int:
    """Threadsの文字数。Meta公式ドキュメントでは絵文字はUTF-8のバイト数で数えるため、
    絵文字（と結合用の不可視文字）だけバイト数、それ以外は1文字=1で数える。"""
    n = 0
    for c in text:
        cp = ord(c)
        if cp >= 0x1F000 or 0x2600 <= cp <= 0x27BF or cp in (0x200D, 0xFE0F) or 0x2B00 <= cp <= 0x2BFF:
            n += len(c.encode("utf-8"))
        else:
            n += 1
    return n


# ---------- 入力 ----------
def load_word_list(path: Path) -> list[str]:
    if not path.exists():
        return []
    words = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            words.append(line)
    return words


def load_topics(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"エラー: {path} が見つかりません")
    # utf-8-sig: Excelで保存したBOM付きCSVにも対応
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        need = {"account", "theme", "memo"}
        if not reader.fieldnames or not need <= {c.strip() for c in reader.fieldnames}:
            sys.exit(f"エラー: {path.name} の列は account, theme, memo が必要です（今: {reader.fieldnames}）")
        topics, errors = [], []
        for i, row in enumerate(reader, start=2):
            row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            if not any(row.values()):
                continue
            acc = row["account"].lower()
            if acc not in ACCOUNTS:
                errors.append(f"{i}行目: account は oem か juken（今: {row['account']!r}）")
                continue
            if not row["theme"]:
                errors.append(f"{i}行目: theme が空です")
                continue
            topics.append({"account": acc, "theme": row["theme"], "memo": row["memo"]})
    if errors:
        sys.exit("エラー:\n  " + "\n  ".join(errors))
    if not topics:
        sys.exit(f"エラー: {path.name} にテーマがありません")
    return topics


# ---------- 検査 ----------
def check_draft(d: dict, ng_words: list[str], private_terms: list[str]) -> tuple[list[str], list[str]]:
    """(errors, warnings) を返す。errors は保存不可、warnings は要目視。"""
    errors, warnings = [], []
    text = (d.get("text") or "").strip()
    hook = (d.get("hook") or "").strip()
    fs = (d.get("foreshadow") or "").strip()

    if d.get("account") not in ACCOUNTS:
        errors.append("account が oem/juken ではない")
    if not text:
        errors.append("本文(text)が空")
    if threads_len(text) > MAX_CHARS:
        errors.append(f"{threads_len(text)}文字（上限{MAX_CHARS}。絵文字はバイト数で計算）")
    if not hook:
        errors.append("書き出し(hook)が未記入")
    elif not text.startswith(hook):
        errors.append("本文が書き出し(hook)で始まっていない")
    if not fs:
        errors.append("伏線(foreshadow)が未記入")
    elif not text.endswith(fs):
        errors.append("本文の最後が伏線(foreshadow)になっていない")

    for w in private_terms:
        if w in text:
            errors.append(f"固有名詞リストの語「{w}」を含む")
    for w in ng_words:
        if w.lower() in text.lower():
            warnings.append(f"効能表現の疑い「{w}」")
    for line in text.splitlines():
        for p in HOWTO_PATTERNS:
            if re.search(p, line):
                warnings.append(f"「どうやるか」寄りの可能性: {line.strip()[:30]}")
                break
    if URL_PATTERN.search(text):
        warnings.append("URLを含む")
    if HASHTAG_PATTERN.search(text):
        warnings.append("ハッシュタグを含む")
    return errors, warnings


# ---------- 出力 ----------
def render_markdown(date: str, drafts: list[dict]) -> str:
    out = [f"# Threads下書き {date}", "",
           "> status は全て draft。投稿は本人の承認を経て別の仕組みで行う。", ""]
    groups: dict[tuple, list[dict]] = {}
    for d in drafts:
        groups.setdefault((d["account"], d["theme"]), []).append(d)
    for (acc, theme), items in groups.items():
        out += [f"## [{acc}] {theme}", ""]
        if items[0].get("memo"):
            out += [f"メモ: {items[0]['memo']}", ""]
        for d in sorted(items, key=lambda x: x["variant"]):
            out += [f"### 案{d['variant']}（{d['char_count']}文字・{d['status']}）　id: `{d['id']}`", ""]
            out += ["```text", d["text"], "```", ""]
            out += [f"- 伏線: {d['foreshadow']}"]
            if d.get("payoff_note"):
                out += [f"- 回収予定: {d['payoff_note']}"]
            for w in d.get("warnings", []):
                out += [f"- ⚠ 要確認: {w}"]
            out += [""]
    return "\n".join(out)


def write_outputs(date: str, drafts: list[dict]) -> tuple[Path, Path]:
    DRAFTS_DIR.mkdir(exist_ok=True)
    jp, mp = DRAFTS_DIR / f"{date}.json", DRAFTS_DIR / f"{date}.md"
    payload = {"date": date, "drafts": drafts}
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mp.write_text(render_markdown(date, drafts), encoding="utf-8")
    return jp, mp


# ---------- コマンド ----------
def cmd_brief(args) -> None:
    topics = load_topics(Path(args.topics))
    rules = (BASE / "rules.md").read_text(encoding="utf-8")
    print(rules.strip())
    print("\n# 今回のテーマ\n")
    for i, t in enumerate(topics, 1):
        print(f"{i}. [{t['account']}] {t['theme']}" + (f"（メモ: {t['memo']}）" if t["memo"] else ""))
    print(f"""
# 出力形式（JSON。テーマごとに{VARIANTS_PER_THEME}案）
{{"drafts": [
  {{"account": "oem", "theme": "<topics.csvのthemeそのまま>", "memo": "<memoそのまま>", "variant": 1,
    "hook": "<書き出しの一文。textの先頭と完全一致>",
    "text": "<投稿本文全体。{MAX_CHARS}文字以内>",
    "foreshadow": "<最後の一文。textの末尾と完全一致>",
    "payoff_note": "<この伏線をどこで回収する想定か（投稿には出ない社内メモ）>"}}
]}}""")


def cmd_save(args) -> None:
    src = Path(args.input)
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"エラー: {src} を読めません: {e}")
    items = data["drafts"] if isinstance(data, dict) else data
    date = args.date or dt.date.today().isoformat()
    ng = load_word_list(BASE / "ng_words.txt")
    private = load_word_list(BASE / "private_terms.txt")

    # 同じ日のファイルがあれば追記（同じ account+theme+variant は上書き）
    existing = []
    jp = DRAFTS_DIR / f"{date}.json"
    if jp.exists():
        existing = json.loads(jp.read_text(encoding="utf-8"))["drafts"]

    new, failed = [], []
    for d in items:
        d = {k: (v.strip() if isinstance(v, str) else v) for k, v in d.items()}
        d["account"] = str(d.get("account", "")).lower()
        errors, warnings = check_draft(d, ng, private)
        label = f"[{d.get('account')}] {d.get('theme')} 案{d.get('variant')}"
        if errors:
            failed.append((label, errors))
            continue
        new.append({
            "id": f"{date}-{d['account']}-{hashlib.sha1(d['theme'].encode()).hexdigest()[:6]}-{d['variant']}",
            "date": date,
            "account": d["account"],
            "theme": d["theme"],
            "memo": d.get("memo", ""),
            "variant": int(d["variant"]),
            "hook": d["hook"],
            "text": d["text"],
            "foreshadow": d["foreshadow"],
            "payoff_note": d.get("payoff_note", ""),
            "char_count": threads_len(d["text"]),
            "status": "draft",
            "warnings": warnings,
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        })

    if failed:
        print("保存できなかった下書き（型違反）:", file=sys.stderr)
        for label, errs in failed:
            print(f"  ✗ {label}: " + " / ".join(errs), file=sys.stderr)
        print("→ 直してから、もう一度 save してください。何も保存していません。", file=sys.stderr)
        sys.exit(1)

    # テーマごとの案数チェック
    counts: dict[tuple, int] = {}
    for d in new:
        counts[(d["account"], d["theme"])] = counts.get((d["account"], d["theme"]), 0) + 1
    for (acc, theme), n in counts.items():
        if n != VARIANTS_PER_THEME:
            print(f"  ⚠ [{acc}] {theme}: {n}案（想定は{VARIANTS_PER_THEME}案）")

    keys = {(d["account"], d["theme"], d["variant"]) for d in new}
    merged = [d for d in existing if (d["account"], d["theme"], d["variant"]) not in keys] + new
    jp, mp = write_outputs(date, merged)
    print(f"保存しました: {len(new)}件 → {jp.relative_to(BASE)} / {mp.relative_to(BASE)}")
    for d in new:
        mark = "⚠" if d["warnings"] else "✓"
        print(f"  {mark} [{d['account']}] {d['theme']} 案{d['variant']}（{d['char_count']}文字）"
              + ("  " + " / ".join(d["warnings"]) if d["warnings"] else ""))


def cmd_check(args) -> None:
    if args.file:
        path = Path(args.file)
    else:
        files = sorted(DRAFTS_DIR.glob("*.json"))
        if not files:
            sys.exit("drafts/ に下書きがありません")
        path = files[-1]
    data = json.loads(path.read_text(encoding="utf-8"))
    ng = load_word_list(BASE / "ng_words.txt")
    private = load_word_list(BASE / "private_terms.txt")
    bad = 0
    for d in data["drafts"]:
        errors, warnings = check_draft(d, ng, private)
        mark = "✗" if errors else ("⚠" if warnings else "✓")
        bad += bool(errors)
        print(f"{mark} [{d['account']}] {d['theme']} 案{d['variant']}（{threads_len(d['text'])}文字）"
              + ("  " + " / ".join(errors + warnings) if errors or warnings else ""))
    sys.exit(1 if bad else 0)


def main() -> None:
    p = argparse.ArgumentParser(description="SNS投稿パイプライン v1（下書きのみ・投稿はしない）")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("brief", help="下書き作成用の指示文を表示")
    b.add_argument("--topics", default=str(BASE / "topics.csv"))
    b.set_defaults(func=cmd_brief)
    s = sub.add_parser("save", help="生成JSONを検査して drafts/ に保存")
    s.add_argument("input")
    s.add_argument("--date", help="ファイル名の日付（既定: 今日）")
    s.set_defaults(func=cmd_save)
    c = sub.add_parser("check", help="保存済み下書きを再検査")
    c.add_argument("file", nargs="?")
    c.set_defaults(func=cmd_check)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
