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

## 起動

```bash
streamlit run app.py
```

ブラウザが開いたら、サイドバーで「開催日 → 会場 → レース」を選ぶと、3要素を
重み付け合成した各馬の勝率が表示されます。スライダー（重み・temperature）を
動かすと確率分布がリアルタイムに変わります。

> 初回はデータ取得のため数分かかることがあります（1レース約60〜90リクエスト）。
> 2回目以降はキャッシュ参照で即時です。

## CLI から 1 レースを予想

```bash
# ダービー（5/31 東京11R）の予想を Markdown 出力
python scripts/predict_derby.py
# → ../predictions/derby_20260531.md

# 任意のレースは src/pipeline.py の predict_race() を使う
```

## 開発の進捗

- [x] ディレクトリ・requirements.txt・config.py の雛形
- [x] scraper/client.py（rate limit / robots.txt / SQLite キャッシュ）
- [x] scraper/race_list.py + race_card.py
- [x] SQLite モデル + リポジトリ（storage/models.py + repo.py）
- [x] scraper/track_bias.py + pedigree.py
- [x] analysis 3 モジュール（track_bias_score / pedigree_score / running_style_score）＋ 脚質推定
- [x] combiner.py（重み正規化 → softmax で確率分布化）
- [x] app.py + UI（3タブ / サイドバー / 結果表 / plotly内訳 / 買い目）
- [x] betting/suggester.py（EV/ケリー or 確率ベース）
- [x] src/pipeline.py（統合パイプライン）
- [x] ダービー予想実走（predictions/derby_20260531.md）

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

## Streamlit Community Cloud へのデプロイ

このリポジトリは Streamlit Community Cloud でそのままデプロイできる構成です。

- リポジトリルートに `app.py` / `requirements.txt` / `runtime.txt`(python-3.11)
- 依存はバージョン固定済み。`playwright` は未使用のため除外しています。

### デプロイ（手動 1 ステップ）

GitHub に push 後、ブラウザで [share.streamlit.io](https://share.streamlit.io) から
接続します（詳細手順は `STATUS.md` 上部参照）。Main file path は `app.py`。

### GitHub への push（gh が無い場合のフォールバック）

```bash
git branch -M main
git remote add origin https://github.com/<USERNAME>/keiba-predictor.git
git push -u origin main
```

### Turso 永続キャッシュ（任意・推奨）

Streamlit Cloud はファイルシステムが再起動で消えるため、デフォルトでは毎回 netkeiba
から取り直しになります。これを避けたい場合、**Turso（libSQL のクラウド SQLite）** を
永続キャッシュとして使えます。

```bash
./scripts/setup_turso.sh   # Turso CLI 導入・ログイン・DB作成・接続情報出力
```

出力された `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` を:
- **クラウド**: Streamlit Cloud → アプリ → Settings → Secrets に貼り付け
- **ローカル**: 自動で `.env.local`（git 管理外）に保存される

`TURSO_DATABASE_URL` と `TURSO_AUTH_TOKEN` が両方そろったときだけ Turso を使い、
**未設定ならローカル SQLite で従来どおり動作**します（`src/storage/engine.py`）。

> Turso ドライバ（`sqlalchemy-libsql` / `libsql-experimental`）が無い環境でも、
> 自動でローカル SQLite にフォールバックします。

### ★ クラウド動作時の制限事項

- **キャッシュは永続化されません**。Streamlit Cloud はアプリ再起動でファイル
  システムがリセットされるため、`data/cache.db` は消え、**再起動のたびに netkeiba
  から取り直し**になります（1レースあたり数分・60〜90リクエスト）。
- **クラウド IP からの netkeiba アクセスはブロックされる可能性**があります
  （データセンター IP への制限・レート制限）。取得失敗時はアプリ内 `st.error`
  で表示されますが、その場合は**ローカル実行を推奨**します。
- 上記のため、Streamlit Cloud は「デモ・共有用」、本格利用はローカル iMac を想定。

## データモデル（SQLite テーブル）

| テーブル | 主キー | 内容 |
|---|---|---|
| `races` | race_id | レース基本情報（日付/会場/距離/馬場/状態） |
| `horses` | horse_id | 馬（性齢/父/母父/**推定脚質**/脚質信頼度） |
| `race_entries` | (race_id, horse_id) | 出走（枠/馬番/騎手/斤量） |
| `track_bias_daily` | (date, venue, surface) | 内外バイアス・ペースバイアス・算出元 |
| `pedigree_stats` | (sire_id, distance_bucket, surface) | 種牡馬の距離別勝率・標本数 |
| `scrape_log` | id | 取得履歴（監査用・追記専用） |
| `http_cache` | url | 生 HTML キャッシュ（client.py 所有） |

> 種牡馬（sire）の成績は `pedigree_stats` に sire_id 単位で蓄積します（父・母父とも
> 同テーブルに各 ID で格納）。一度取得したら再取得しません。

## 制限事項

予想ロジックは **トラックバイアス・血統・脚質の 3 要素のみ**です。以下は意図的な
単純化・近似です（v2 以降で改善余地）。

- **脚質**: 過去走の 1 コーナー通過順位÷頭数で推定。**過去3走未満は「不明」**扱い
  （confidence=走数）。不明馬は脚質スコア 50（中立）。
- **血統**: 父・母父の当該距離×馬場の勝率を使用。**標本 < 30 はベイズ平均**で
  全体平均に収縮（信頼度も低下）。距離変更・初ダート等のペナルティは簡易。
- **トラックバイアス**: 予想日に結果が無ければ**前日同会場へフォールバック**、
  それも無ければ中立(50)。内外は上位入線馬の枠、ペースは勝ち馬の上がり最速率で近似。
- **確率の校正**: スコア(0〜100)を softmax の logit に使うため、**temperature=1.0 では
  分布が尖りやすい**（上位馬の勝率が過大に出る）。UI で 2.0〜3.0 に上げると穏当に。
- **オッズ**: best-effort 取得。未確定・取得不可なら確率ベース提案にフォールバック。
- **調教・展開の細部・馬場の急変・騎手乗り替わり**などは未反映。

## 免責事項

- 本アプリは **個人利用・学習目的**。スクレイピング結果の再配布はしません。
- **投資判断は自己責任**です。買い目提案はあくまで参考情報です。
- netkeiba の規約変更時は速やかに対応・利用を停止します。
