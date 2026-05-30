# STATUS — keiba_predictor 自律実装ログ

> 夜間ノンストップ実装の進捗記録。各タスク完了ごとに追記。

---

## おはよう Yasu さん。朝の手順（5 分）

### 即時確認
- アプリ: https://keiba-predictor-eah6al9bbkdldtbwmj2wk2.streamlit.app
- **並列化（3並列）でコールド取得が約 63〜73% 短縮**しました（東京1R: 121秒→45秒）。
  ベンチ詳細は下の「⚡ 高速化」表。
- UI: 推奨馬テーブルの上位3頭は**緑文字**になり、背景色ハイライトは廃止しました。
- すべて push 済み → Streamlit Cloud が自動再デプロイされています。

### Turso 永続キャッシュを有効化（任意・推奨）
有効化すると、クラウドでもキャッシュが消えず**ウォーム1秒運用**になります。

1. ターミナルで実行:
   ```bash
   cd /Users/yasuakinakamura/Documents/Claude/Projects/自動化で稼ぐ/keiba_predictor
   ./scripts/setup_turso.sh
   ```
2. ブラウザで Turso にサインイン（GitHub アカウント可）
3. 出力された `TURSO_DATABASE_URL` と `TURSO_AUTH_TOKEN` をコピー
4. https://share.streamlit.io/ → 該当アプリ → Settings → Secrets に貼り付け:
   ```toml
   TURSO_DATABASE_URL = "libsql://..."
   TURSO_AUTH_TOKEN = "ey..."
   ```
5. Save → アプリが自動再起動 → 以降は**ウォーム 1 秒運用**
   （ローカルでも `.env.local` に保存されるので `streamlit run app.py` で有効）

### Turso なしで運用する場合
何もしなくて OK。並列化の効果でコールド 1.5〜2 分（1レース18頭で）で動きます。
アプリ画面下部に現在のキャッシュ種別（ローカルSQLite / Turso）が表示されます。

### ⚠️ 万一クラウドのデプロイが失敗していたら
`requirements.txt` 末尾の `libsql-experimental` と `sqlalchemy-libsql` の2行が
原因の可能性。その2行を削除して push すれば復旧します（Turso 機能のみ無効化、
アプリ本体・並列化は正常動作）。

---

## 🚀 Streamlit Cloud デプロイ手順（手動 — Yasu が実施）

GitHub への push は完了済み（Private）: **https://github.com/creater0820/keiba-predictor**

最後の 1 ステップ（ブラウザ操作）だけ手動です:

1. https://share.streamlit.io にアクセス → GitHub アカウントでサインイン
2. 「Create app」→「Deploy a public app from GitHub」ではなく自分のリポジトリを選択
3. Repository: `creater0820/keiba-predictor`
4. Branch: `main`
5. **Main file path: `app.py`**（リポジトリ直下。ネストしていないのでこのままで OK）
6. App URL（任意）: `keiba-predictor` 等
7. 「Deploy!」をクリック → 数分待つと `https://<...>.streamlit.app` が発行される

### アクセス制限したい場合
- アプリ設定 → Sharing → 「Viewers」で Google アカウントを指定すると限定公開（無料枠あり）。

### ⚠️ クラウド動作時の注意
- キャッシュ（data/cache.db）は再起動で消えるため、毎回 netkeiba から再取得（1レース数分）。
- クラウド IP から netkeiba がブロックされる可能性あり。失敗時は `st.error` 表示 → ローカル実行推奨。

### 最新コミット（git log --oneline -5）
```
abf40e5 chore: prepare for streamlit cloud deploy
e9083fe feat: task9 betting tests + task10 pipeline/README/derby + encoding fix
fb35837 feat: task8 streamlit UI + task9 betting suggester + task10a pipeline
6cda891 feat: tasks 1-7 scraper/storage/analysis/combiner skeleton + tests
```
（デプロイ準備後のコミットは下記参照）

---

## 🌅 朝のチェックリスト

**全タスク完了 ✅（Task 1〜10 すべて実装・実走済み）**

| Task | 状態 | 概要 |
|---|:--:|---|
| 1-2 スケルトン+client | ✅ | robots/レート制限/HTTPキャッシュ |
| 3 race_list/race_card | ✅ | 一覧・出走表 |
| 4 storage | ✅ | 6テーブル+repo |
| 5 track_bias/pedigree | ✅ | バイアス算出・種牡馬成績 |
| 6 analysis 3スコア | ✅ | 純粋関数+脚質推定 |
| 7 combiner | ✅ | softmax確率化 |
| 8 Streamlit UI | ✅ | 3タブ、HTTP200起動確認 |
| 9 betting | ✅ | EV/ケリー or 確率ベース |
| 10A pipeline | ✅ | 統合 predict_race |
| 10B README | ✅ | データモデル・制限事項記載 |
| 10C ダービー実走 | ✅ | **Markdown出力済み** |

**生成物（朝、Yasu が読むもの）:**
- 📄 `/Users/yasuakinakamura/Documents/Claude/Projects/自動化で稼ぐ/predictions/derby_20260531.md`
  - 日本ダービー(東京11R 芝2400m 18頭) 確率ランキング+上位5頭理由+買い目
  - モデル本命: **6 コンジェスタス**（オッズ取得成功・EV連動の買い目あり）

**既知の問題と朝の推奨アクション（3行）:**
1. 勝率が尖りすぎ（本命94%）→ より現実的にするには UI で temperature を 2.5 前後に上げて再確認。
2. EV+983% 等は「モデルが過信気味」なだけで確実な利益ではない。買い目は参考程度に。
3. アプリ起動: `cd keiba_predictor && source .venv/bin/activate && streamlit run app.py`

---

## ⚡ 高速化（並列スクレイピング）— Before / After

`client.fetch_many(urls, max_concurrent=3)` を追加し、pipeline の各馬データ取得を
並列プリフェッチ化。逐次間隔も 1.5s→1.0s に短縮。

| ワークロード | Before（逐次） | After（3並列） | 効果 |
|---|---:|---:|---:|
| ダービー36URL（血統+過去走, bench_parallel.py） | 43.6秒 / 1.21s/req | **11.9秒 / 0.33s/req** | **73%短縮・3.7倍速** |
| 東京1R フル予想（run_one_race.py, 16頭） | 約121秒（旧逐次） | **45秒 / 74req** | **約63%短縮** |
| ウォーム（キャッシュ） | 0.6秒 | 1.1秒 | 実通信0 |

- レート制御: グローバル間隔 = 1.0s ÷ 同時接続数（3並列で実効 0.33s/req）。スレッドセーフな
  スロット予約方式。
- 安全装置: 429/503 連発→並列度を実質1に降格(60s クールダウン)→3連続で中断。
  1レース最大150リクエストのハードキャップ。robots.txt 尊重は維持。
- ログ: 並列取得時に `[client] parallel fetch: 3 workers, N urls` を出力（Cloud ログで確認可）。

## 🗄 Turso 永続キャッシュ（朝の作業で有効化）

- `src/storage/engine.py` 新設: `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` が
  両方そろえば Turso（`sqlite+libsql://`）、無ければローカル SQLite に自動フォールバック。
- 設定値は Streamlit secrets → 環境変数（`.env.local`）の順で読む。
- 依存追加（インストール確認済み・3.14 で wheel あり）:
  - `libsql-experimental==0.0.55` / `sqlalchemy-libsql==0.2.0`
  - ※ これらが無くても import 失敗時はローカル SQLite にフォールバック。
- スキーマ互換: モデルは `JSON` 型を使わず `Text + json.dumps` なので libSQL でそのまま動く。
- `scripts/setup_turso.sh`（実行権限付与済み）で CLI 導入〜接続情報出力〜`.env.local` 保存まで自動。
- テスト: `tests/test_turso_engine.py` 5件（接続文字列生成・フォールバックをモックで検証）。

## 完了タスク

### Task 1-2: スケルトン + scraper/client.py
- ディレクトリ構成、requirements.txt、config.py、.gitignore、.env.example、README 雛形
- `src/scraper/client.py`: robots.txt 尊重 / レート制限1.5s+ジッタ / 指数バックオフ / SQLite HTTPキャッシュ
- テスト: 5件（オフライン）

### Task 3: race_list.py + race_card.py
- 明日のレース一覧（Ajaxエンドポイント直叩き）、出走表（枠/馬番/horse_id/性齢/斤量/騎手/調教師）
- 過去日は result リンク形式に両対応
- テスト: +6件 / fixture 3点

### Task 4: storage/models.py + repo.py
- 6テーブル（races/horses/race_entries/track_bias_daily/pedigree_stats/scrape_log）SQLAlchemy 2.0
- upsert(merge)/get/is_fresh、has_pedigree_for_sire
- Alembic不使用・create_allのみ（スキーマ変更時は cache.db 再作成）
- テスト: +8件

### Task 5: track_bias.py + pedigree.py
- pedigree: 血統ID（父・母父）+ 種牡馬距離別成績（芝/ダ×5距離バケット）、sire_id単位キャッシュ
- track_bias: 当日先行→前日同会場→中立 のフォールバック、data_date/source メタ返却
- テスト: +6件 / fixture +4点
- **実走計測（1レース・フル取得）**: コールド 64req / 120.8秒（1.89秒/req）、ウォーム 0req / 0.60秒

### Task 6: analysis 3スコア + 脚質推定
- track_bias_score / pedigree_score / running_style_score（純粋関数、(score, confidence, breakdown)返却）
- running_style.py: 過去走の通過順位÷頭数で脚質推定、horses保存・30日キャッシュ
- テスト: +20件 / fixture +1点

### Task 7: combiner.py
- 重み正規化（w/sum, 全ゼロ→均等）、temperature max(0.1,t)ガード
- 総合信頼度=重み付き加重平均、低信頼の二重補正なし
- RaceProbabilities/HorseProbability dataclass、breakdownはDataFrame化容易
- テスト: +19件（エッジケース込み）

### Task 10-A: src/pipeline.py（先行実装）
- `predict_race(date, venue, race_no, weights, temperature, force_refresh, progress)` → RaceProbabilities
- race_list→race_card→各馬(血統+脚質)→track_bias→3スコア→combiner を一本化
- 各馬の取得失敗は中立フォールバックで続行（落とさない）
- meta にレース情報・バイアス出所・global_avg を格納（UI/Markdown用）

### Task 8: Streamlit UI 一式
- `app.py`（3タブ: 推奨馬/根拠の可視化/買い目提案、st.cache_data TTL12h、spinner、st.errorで非停止）
- `src/ui/sidebar.py`（日付=明日デフォルト/会場・レース選択/重み3スライダー/temperature/再取得ボタン/データソースcaption）
- `src/ui/results_table.py`（確率降順DF、上位3頭ハイライトStyler、信頼度バッジ🟢🟡🔴）
- `src/ui/factor_breakdown.py`（plotly積み上げ横棒、ホバーでbreakdown、上位5頭切替）
- `src/ui/betting_panel.py`
- **ヘッドレス起動確認: streamlit run app.py → HTTP 200・7秒継続稼働 ✅**

### Task 9: betting/suggester.py
- 純粋関数。オッズあり→EV>+10%単勝(1/4ケリー配分)+上位2頭馬連 / なし→上位3複勝+馬連
- BettingSuggestion/BetRow dataclass、金額100円単位
- テスト: +8件（EV計算/ゼロ予算/オッズ全欠損/極端確率 等）

### Task 10-B: README 完成
- セットアップ/起動/CLI予想/データモデル表/制限事項/免責 を整備

### Task 10-C: ダービー実走 ★最重要★
- `scripts/predict_derby.py`: 東京同日race_listから「優駿/ダービー」自動探索 → 11R 日本ダービー特定
- 実走: 18頭フル取得 **コールド 154秒**（race_list+shutuba+track_bias+18頭×(血統ped+種牡馬+脚質)）
- **オッズ取得成功**（api_get_jra_odds, 18頭分）→ EV連動の買い目を提案
- 出力: `../predictions/derby_20260531.md`

### 🐛 実走中に発見・修正したバグ（重要）
- **エンコーディング**: race.netkeiba.com の shutuba 等は `Content-Type: charset=`（空）を返し、
  requests が encoding='' を設定 → 馬名が文字化け。client.py を `not enc` でも apparent_encoding に
  フォールバックするよう修正。回帰テスト追加（test_empty_charset_falls_back_to_apparent_encoding）。
  ※スコア算出は馬番・ID（ASCII）ベースのため数値結果に影響なし。表示名のみ破損していた。

**累計テスト: 75件 PASS**

### リクエスト数・所要時間まとめ（実測）
- 1レース16頭フル: コールド 64req / 121秒、ウォーム 0req / 0.6秒
- ダービー18頭フル: コールド 154秒、再生成（shutubaのみ再取得）3秒

---

## 既知の制限事項
- 脚質: 過去3走未満は「不明」扱い（confidence=走数）
- 血統: サンプル<30はベイズ平均で全体平均に収縮
- トラックバイアス: 予想日に結果が無ければ前日同会場へフォールバック、それも無ければ中立50
- combiner: スコア0〜100をlogitに使うため t=1.0 は比較的シャープ（UIで調整）

---

## コミット履歴（git log --oneline -10）
```
3dbf8e8 feat(storage): Turso libSQL persistent cache with local SQLite fallback + setup script
d84f994 feat(scraper): parallel fetch_many (3 workers, ~73% faster cold) + rate-limit safety
0a8e862 feat(ui): green text for top-3 rank/name, remove background highlight
8e30faa docs: add streamlit cloud deploy steps to STATUS
abf40e5 chore: prepare for streamlit cloud deploy
e9083fe feat: task9 betting tests + task10 pipeline/README/derby + encoding fix
fb35837 feat: task8 streamlit UI + task9 betting suggester + task10a pipeline
6cda891 feat: tasks 1-7 scraper/storage/analysis/combiner skeleton + tests
```
