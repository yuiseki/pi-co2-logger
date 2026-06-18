# pi-co2-logger

Raspberry Pi 上で IO-DATA UD-CO2S CO2センサーを読み取り、`co2.yuiseki.dev/sensor.json` として配信するシステム。

## アーキテクチャ

```
[IO-DATA UD-CO2S] --USB--> [pi4-s-1]
                                |
                       [systemd: co2-logger]
                                |
                    /tmp/co2/latest/sensor.json
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
