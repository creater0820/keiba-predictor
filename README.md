# keiba_predictor

netkeiba の公開情報をもとに、翌日のレースの推奨馬を **確率分布（勝率%）** で算出する Streamlit Web アプリ。

予想ロジックは以下の 3 要素のみ：

1. **トラックバイアス** — その日の馬場傾向（内/外、前/後、芝/ダート別）
2. **血統** — 父・母父の距離・コース・馬場適性
3. **脚質** — 逃げ/先行/差し/追込 と展開予測の適合度

3 要素のスコアを UI で重み付けし、softmax で各馬の勝率を百分率出力する。

---

## セットアップ

```bash
cd keiba_predictor

# 仮想環境（任意）
python3 -m venv .venv
source .venv/bin/activate

# 依存インストール
pip install -r requirements.txt

# 環境変数ファイルを用意
cp .env.example .env
```

## 起動（MVP 完成後）

```bash
streamlit run app.py
```

## 開発の進捗

- [x] ディレクトリ・requirements.txt・config.py の雛形
- [x] scraper/client.py（rate limit / robots.txt / SQLite キャッシュ）
- [x] scraper/race_list.py + race_card.py
- [x] SQLite モデル + リポジトリ（storage/models.py + repo.py）
- [x] scraper/track_bias.py + pedigree.py
- [x] analysis 3 モジュール（track_bias_score / pedigree_score / running_style_score）＋ 脚質推定
- [x] combiner.py（重み正規化 → softmax で確率分布化）
- [ ] app.py + UI
- [ ] betting/suggester.py

---

## データベース（SQLite: data/cache.db）

スキーマ管理ツール（Alembic）は使いません。`repo.init_db()` の `create_all`
だけで全テーブルを用意します。

> **スキーマを変更したら `data/cache.db` を削除して作り直してください。**
> （マイグレーションは行いません。再取得でキャッシュは復元されます。）
>
> ```bash
> rm -f data/cache.db   # 次回起動時に自動で再生成
> ```

### キャッシュの役割分担（重複しない設計）

| 仕組み | 所有 | 役割 |
|---|---|---|
| `http_cache` テーブル | `client.py` | HTTP バイト層のキャッシュ。URL 単位で HTML を保持し、`fetch()` が毎回参照して再ダウンロードを防ぐ |
| 各テーブルの `fetched_at` | `storage` | 解析済みデータの鮮度判定（再パース／再保存の要否） |
| `scrape_log` テーブル | `storage` | **追記専用の監査ログ**。キャッシュ判定には使わない。最終取得表示・デバッグ・透明性のための履歴のみ |

`pedigree_stats` はオンデマンド取得＋キャッシュ方式。未取得（初回ゼロ件）でも
`get_pedigree_stat()` が `None` を返し、分析側が中立スコアにフォールバックします。
種牡馬ページは **sire_id 単位でキャッシュ**し、`pedigree_stats` に保存後は再取得しません。

### 1 レースの処理時間・リクエスト数の概算

16 頭立て 1 レースのフル取得（トラックバイアス＋全頭の父・母父成績）を実測した目安：

| 状態 | リクエスト数 | 所要時間 |
|---|---|---|
| **コールド**（初回・実取得） | 約 60〜65 回 | **約 2 分**（1.5 秒間隔＋ジッタで平均 ≈ 1.9 秒/req） |
| **ウォーム**（2 回目・全キャッシュ） | 0 回 | 1 秒未満 |

内訳の目安：出走表 1 + 開催日 1 + トラックバイアス（前日結果）約 14 + 血統
（16 頭分の血統ページ 16 ＋ 父・母父の種牡馬ページ最大 32）。種牡馬・URL は
キャッシュされるため、同日 2 回目以降は再アクセスしません。計測の再現は
`python scripts/run_one_race.py`。

> レース数が増えるほどリクエストも増えるため、**初回取得は時間に余裕を持って**
> 実行してください（マナーとして 1.5 秒間隔を守るため短縮はしません）。

## スクレイピングのマナー（重要）

本アプリは netkeiba への負荷を抑えるため、以下を必ず守ります。

- `robots.txt` を尊重（許可された URL のみ取得）
- リクエスト間隔は最低 **1.5 秒**（+ ランダムなゆらぎ）
- `User-Agent` にアプリ名と連絡先メールを明記
- 同一 URL は当日キャッシュがあれば再取得しない（SQLite）
- 失敗時は最大 3 回まで指数バックオフでリトライ
- **公開ページのみ**取得（ログイン必須ページには触れない）

## 免責事項

- 本アプリは **個人利用・学習目的**。スクレイピング結果の再配布はしません。
- **投資判断は自己責任**です。買い目提案はあくまで参考情報です。
- netkeiba の規約変更時は速やかに対応・利用を停止します。
