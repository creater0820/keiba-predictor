#!/usr/bin/env bash
# Turso 永続キャッシュのセットアップ（朝に Yasu が 1 回実行）。
# 実行: ./scripts/setup_turso.sh
set -euo pipefail

cd "$(dirname "$0")/.."

# 1. Turso CLI インストール
if ! command -v turso &> /dev/null; then
  echo "Installing Turso CLI..."
  brew install tursodatabase/tap/turso
fi

# 2. ログイン（ブラウザが開く）
turso auth login

# 3. DB 作成（nrt=東京リージョン。既にあれば無視）
turso db create keiba-predictor --location nrt || true

# 4. 接続情報取得
URL=$(turso db show --url keiba-predictor)
TOKEN=$(turso db tokens create keiba-predictor)

echo ""
echo "=========================================="
echo "Turso セットアップ完了。以下を Streamlit Cloud Secrets に貼り付けてください:"
echo "=========================================="
echo ""
echo "TURSO_DATABASE_URL = \"$URL\""
echo "TURSO_AUTH_TOKEN = \"$TOKEN\""
echo ""
echo "貼り付け先: https://share.streamlit.io/ → アプリ → Settings → Secrets"
echo ""

# 5. ローカル .env.local にも保存（git 管理外）
cat > .env.local << EOF
TURSO_DATABASE_URL=$URL
TURSO_AUTH_TOKEN=$TOKEN
EOF
echo "ローカル用に .env.local に保存しました（git 管理外）"
echo "ローカルでも 'streamlit run app.py' で Turso が使われます。"
