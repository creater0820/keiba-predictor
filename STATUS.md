# STATUS — keiba_predictor 自律実装ログ

> 夜間ノンストップ実装の進捗記録。各タスク完了ごとに追記。

---

## 🌅 朝のチェックリスト（最後に更新）

> ※ 全タスク完了後にここを埋める。

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

**累計テスト: 66件 PASS（時点）**

---

## 既知の制限事項
- 脚質: 過去3走未満は「不明」扱い（confidence=走数）
- 血統: サンプル<30はベイズ平均で全体平均に収縮
- トラックバイアス: 予想日に結果が無ければ前日同会場へフォールバック、それも無ければ中立50
- combiner: スコア0〜100をlogitに使うため t=1.0 は比較的シャープ（UIで調整）
