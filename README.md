# pi-co2-logger

Raspberry Pi 上で IO-DATA UD-CO2S CO2センサーを読み取り、`co2.yuiseki.dev/sensor.json` として配信するシステム。

## アーキテクチャ

```
[IO-DATA UD-CO2S] --USB--> [pi4-s-1]
                                |
                       [systemd: co2-logger] ----(任意/throttled)----> [GeonicDB]
                                |                                       NGSIv2 upsert
                    /tmp/co2/latest/sensor.json                   AirQualityObserved
                                |
                    [k3s Deployment: co2-server]
                                |
                    [Knative Ingress → Kourier]
                                |
                    http://co2.yuiseki.dev/sensor.json
```

## エンドポイント

```
GET /sensor.json
```

```json
{"time": 1781752684, "stat": {"co2ppm": 430, "humidity": 57.21, "temperature": 28.4}}
```

## コンポーネント

### `logger/` — systemd サービス (pi4-s-1)

`/dev/ttyACM0` を 115200 baud で開き、5秒ごとにセンサーデータを取得して JSON ファイルに書き込む。

- `/tmp/co2/YYYY/MM/DD/HH/MM/SS/sensor.json` — タイムスタンプ付きログ
- `/tmp/co2/latest/sensor.json` — 最新値（アトミック更新）

設定されていれば、`geonicdb_sink.py` 経由で GeonicDB (FIWARE Orion 互換 Context Broker) にも転送する（後述）。

### `geonicdb_sink.py` — GeonicDB 転送 (任意)

補正済みの読み取り値を NGSIv2 の `AirQualityObserved` エンティティに整形し、`POST /v2/entities?options=upsert` で送信する。標準ライブラリのみ（追加依存なし）。

- **throttle**: `GEONICDB_INTERVAL` 秒に最大1回だけ送信（デフォルト 60秒）
- **best-effort**: ネットワーク/HTTP エラーは握りつぶし、ロガー本体を止めない
- **fail-safe**: `GEONICDB_URL` / `GEONICDB_API_KEY` が未設定なら転送は無効（ローカルの sensor.json 配信はそのまま動作）

接続情報はすべて環境変数で注入し、リポジトリには一切ハードコードしない。`logger/geonicdb-co2.env.example` をコピーして使う:

```bash
# 配布先マシン上で（git には入れない）
mkdir -p ~/.secrets
cp logger/geonicdb-co2.env.example ~/.secrets/geonicdb-co2.env
chmod 600 ~/.secrets/geonicdb-co2.env
# GEONICDB_URL / GEONICDB_API_KEY / GEONICDB_TENANT を実値に編集
sudo systemctl restart co2-logger
```

systemd unit は `EnvironmentFile=-/home/<user>/.secrets/geonicdb-co2.env` でこのファイルを読み込む（`-` 付きなのでファイルが無くても起動する）。

### `server/` — HTTP サーバー (Docker / linux/arm64)

FastAPI による最小構成のサーバー。`/tmp/co2/latest/sensor.json` を読んで返す。

### `k8s/` — Kubernetes マニフェスト

| ファイル | 内容 |
|---|---|
| `deployment.yaml` | `nodeName: pi4-s-1` で固定、hostPath で `/tmp/co2/latest` をマウント |
| `service.yaml` | ClusterIP Service `co2` (port 80 → 8080) |
| `knative-ingress.yaml` | Knative 内部 Ingress → Kourier が `co2.yuiseki.dev` をルーティング |

## セットアップ

### 1. logger を pi4-s-1 にインストール

```bash
# pi4-s-1 上で実行
sudo git clone https://github.com/yuiseki/pi-co2-logger.git /opt/pi-co2-logger
sudo apt-get install -y python3-serial
sudo cp /opt/pi-co2-logger/logger/co2-logger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now co2-logger.service
```

### 2. Docker イメージをビルド (linux/arm64)

```bash
# ビルドマシン上で実行
cd server/
docker buildx build --platform linux/arm64 -t co2-server:0.1.1 --load .
docker save co2-server:0.1.1 -o /tmp/co2-server-0.1.1.tar

# pi4-s-1 に転送して containerd にインポート
scp /tmp/co2-server-0.1.1.tar pi4-s-1:/tmp/
ssh pi4-s-1 "sudo ctr -n k8s.io images import /tmp/co2-server-0.1.1.tar"
```

### 3. k3s にデプロイ

```bash
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/knative-ingress.yaml
```

## センサープロトコル

IO-DATA UD-CO2S のシリアル通信仕様:

| 項目 | 値 |
|---|---|
| デバイス | `/dev/ttyACM0` |
| ボーレート | 115200 baud |
| 開始コマンド | `STA\r\n` → `OK STA` |
| データ形式 | `CO2=573,HUM=38.0,TMP=30.4` |
| 停止コマンド | `STP\r\n` |

## 環境変数

| 変数 | デフォルト | 説明 |
|---|---|---|
| `CO2_DEVICE` | `/dev/ttyACM0` | シリアルデバイスパス |
| `CO2_BASE_DIR` | `/tmp/co2` | ログ出力先ディレクトリ |
| `CO2_POLL_INTERVAL` | `5` | 読取り間隔（秒） |
| `CO2_DATA_DIR` | `/data/latest` | サーバーが読むディレクトリ |
| `GEONICDB_URL` | （未設定） | GeonicDB のベース URL。未設定なら転送無効 |
| `GEONICDB_API_KEY` | （未設定） | `X-Api-Key`。未設定なら転送無効 |
| `GEONICDB_TENANT` | （空） | `Fiware-Service`（テナント名） |
| `GEONICDB_SERVICEPATH` | `/devices/co2` | `Fiware-ServicePath` |
| `GEONICDB_ENTITY_ID` | `urn:ngsi-ld:AirQualityObserved:co2-sensor-01` | 送信先エンティティ ID |
| `GEONICDB_INTERVAL` | `60` | GeonicDB への最小送信間隔（秒） |

## テスト

```bash
python3 -m unittest discover -s tests -v
```
