# STATUS — keiba_predictor 自律実装ログ

> 夜間ノンストップ実装の進捗記録。各タスク完了ごとに追記。

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
