# SNS投稿パイプライン v1（Threads下書き）

`topics.csv` のテーマから、Threads投稿の下書きを3案ずつ作り、`drafts/` に日付ごとのMarkdownとJSONで保存します。

- **投稿はしません。** ネットへの自動送信機能もありません（投稿は別の仕組みで本人の承認を経て行う）。
- **APIキーもMeta開発者登録も不要です。** 下書きの文章は Claude Code 自身が書き、`pipeline.py`（Python標準ライブラリのみ）が読み込み・型チェック・保存を担当します。

## 使い方

1. `topics.csv` にテーマを書く（Excelで編集してCSV保存してもOK）

   ```csv
   account,theme,memo
   oem,小ロットで始めたいときに最初に考えること,問い合わせで一番多い相談
   juken,直前期に新しい参考書に手を出すべきか,不安で買い足す生徒が多い時期
   ```
   - account は `oem` か `juken`
2. このフォルダ（`sns-pipeline`）で Claude Code を開き、次を入力

   ```
   /sns-drafts
   ```
3. `drafts/2026-11-25.md`（読む用）と `drafts/2026-11-25.json`（取り込み用）ができる

同じ日に2回実行すると、同じ日のファイルに追記されます（同じテーマ・同じ案番号は上書き）。

## 自動チェックの内容

| 判定 | 内容 |
|---|---|
| ✗ 保存しない | 500文字超え（絵文字はThreadsの仕様どおりバイト数で計算）／書き出し・伏線が本文の先頭・末尾にない／`private_terms.txt` の語を含む |
| ⚠ 要確認（保存はする） | `ng_words.txt` の効能表現／手順・番号付きリストなど「どうやるか」寄りの表現／URL・ハッシュタグ |

自動チェックは補助です。特定につながる具体性（製品の種類・経緯など）は機械では判定しきれないので、承認前に必ず人の目で確認してください。

### 固有名詞リスト（推奨）
`private_terms.example.txt` を `private_terms.txt` にコピーし、実在の人名・学校名・塾名・会社名・取引先名などを1行1語で書くと、含まれた下書きは保存されなくなります。`private_terms.txt` はGitに上がりません。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `topics.csv` | 入力（account, theme, memo） |
| `rules.md` | 下書きの型。ここを直すと生成ルールが変わる |
| `ng_words.txt` | 効能表現のNG語リスト（追加・削除自由） |
| `pipeline.py` | `brief`（指示文表示）/ `save`（検査して保存）/ `check`（再検査） |
| `.claude/commands/sns-drafts.md` | `/sns-drafts` コマンドの中身 |
| `drafts/` | 出力先 |

## JSONの形（イロ側キュー取り込み用）

```json
{"date": "2026-11-25", "drafts": [{
  "id": "2026-11-25-oem-1a2b3c-1", "account": "oem", "theme": "...", "memo": "...",
  "variant": 1, "hook": "書き出し", "text": "投稿本文", "foreshadow": "伏線",
  "payoff_note": "回収予定（投稿には出ない）", "char_count": 312,
  "status": "draft", "warnings": [], "created_at": "..."}]}
```

## 受け渡し
`drafts/` の `.md` と `.json` を、Google Driveの「note作業（Claude×Muse）」フォルダに置いてイロ側のキューへ取り込みます。

## v2で足すもの（未実装）
- 週次レポート：表示回数・スキ・フォロワー数などを貼り付けると、要約と「伸びた投稿の共通点」を文章化する
