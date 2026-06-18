#!/usr/bin/env python3
"""CO2 sensor logger for IO-DATA UD-CO2S.

Reads CO2/humidity/temperature from /dev/ttyACM0 (115200 baud) and writes
JSON to /tmp/co2/YYYY/MM/DD/HH/MM/SS/sensor.json and /tmp/co2/latest/sensor.json.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

DEVICE = os.environ.get("CO2_DEVICE", "/dev/ttyACM0")
BAUD_RATE = 115200
BASE_DIR = Path(os.environ.get("CO2_BASE_DIR", "/tmp/co2"))
POLL_INTERVAL = float(os.environ.get("CO2_POLL_INTERVAL", "5"))  # seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("co2-logger")


def parse_line(line: str) -> dict | None:
    """Parse 'CO2=573,HUM=38.0,TMP=30.4' into a dict."""
    try:
        parts = {}
        for token in line.strip().split(","):
            key, _, val = token.partition("=")
            parts[key.strip()] = val.strip()
        co2 = int(parts["CO2"])
        hum = float(parts["HUM"])
        tmp = float(parts["TMP"])
        return {"co2ppm": co2, "humidity": hum, "temperature": tmp}
    except (KeyError, ValueError):
        return None


def write_json(data: dict, ts: datetime) -> None:
    payload = json.dumps({"time": int(ts.timestamp()), "stat": data})

    # timestamped path
    ts_dir = (
        BASE_DIR
        / ts.strftime("%Y")
        / ts.strftime("%m")
        / ts.strftime("%d")
        / ts.strftime("%H")
        / ts.strftime("%M")
        / ts.strftime("%S")
    )
    ts_dir.mkdir(parents=True, exist_ok=True)
    (ts_dir / "sensor.json").write_text(payload)

    # latest (atomic write)
    latest_dir = BASE_DIR / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=latest_dir)
    try:
        os.write(tmp_fd, payload.encode())
        os.close(tmp_fd)
        shutil.move(tmp_path, latest_dir / "sensor.json")
    except Exception:
        os.close(tmp_fd)
        os.unlink(tmp_path)
        raise


def run_once(ser: serial.Serial) -> None:
    ser.write(b"STA\r\n")
    time.sleep(0.3)
    # drain the "OK STA" acknowledgement line
    ser.readline()

    deadline = time.monotonic() + POLL_INTERVAL
    data = None
    while time.monotonic() < deadline:
        raw = ser.readline().decode("ascii", errors="ignore").strip()
        if raw.startswith("CO2="):
            data = parse_line(raw)
            if data:
                break

    ser.write(b"STP\r\n")
    time.sleep(0.1)
    ser.reset_input_buffer()

    if data is None:
        logger.warning("No valid CO2 reading in this cycle")
        return

    ts = datetime.now(tz=timezone.utc)
    write_json(data, ts)
    logger.info("co2=%d hum=%.1f tmp=%.1f", data["co2ppm"], data["humidity"], data["temperature"])


def main() -> None:
    logger.info("Starting CO2 logger (device=%s, interval=%.0fs)", DEVICE, POLL_INTERVAL)
    while True:
        try:
            with serial.Serial(DEVICE, BAUD_RATE, timeout=10) as ser:
                logger.info("Serial port opened: %s", DEVICE)
                while True:
                    try:
                        run_once(ser)
                    except serial.SerialException:
                        raise
                    except Exception:
                        logger.exception("Error during sensor read")
                    time.sleep(POLL_INTERVAL)
        except serial.SerialException:
            logger.exception("Serial error — reconnecting in 10s")
            time.sleep(10)
        except KeyboardInterrupt:
            logger.info("Stopped")
            sys.exit(0)


if __name__ == "__main__":
    main()
