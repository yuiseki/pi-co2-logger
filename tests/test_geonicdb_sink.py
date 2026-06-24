"""Tests for geonicdb_sink — building NGSIv2 payloads and throttled, non-fatal sending."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "logger"))

import geonicdb_sink  # noqa: E402


SAMPLE = {
    "co2ppm": 640,
    "temperature": 26.9,
    "humidity": 48.9,
    "raw": {"temperature": 31.4, "humidity": 37.7},
}
TS = datetime(2026, 6, 24, 7, 45, 16, tzinfo=timezone.utc)


class BuildPayloadTest(unittest.TestCase):
    def test_airqualityobserved_structure(self):
        payload = geonicdb_sink.build_payload(
            SAMPLE, TS, entity_id="urn:ngsi-ld:AirQualityObserved:co2-sensor-01"
        )
        self.assertEqual(payload["id"], "urn:ngsi-ld:AirQualityObserved:co2-sensor-01")
        self.assertEqual(payload["type"], "AirQualityObserved")
        self.assertEqual(payload["co2"], {"type": "Number", "value": 640})
        self.assertEqual(payload["temperature"], {"type": "Number", "value": 26.9})
        self.assertEqual(payload["relativeHumidity"], {"type": "Number", "value": 48.9})
        self.assertEqual(
            payload["dateObserved"],
            {"type": "DateTime", "value": "2026-06-24T07:45:16Z"},
        )

    def test_dateobserved_is_utc_zulu(self):
        payload = geonicdb_sink.build_payload(SAMPLE, TS, entity_id="x")
        self.assertTrue(payload["dateObserved"]["value"].endswith("Z"))


class SinkConfigTest(unittest.TestCase):
    def test_disabled_without_url_or_key(self):
        self.assertFalse(geonicdb_sink.GeonicDBSink(url="", api_key="k").enabled)
        self.assertFalse(geonicdb_sink.GeonicDBSink(url="https://x", api_key="").enabled)
        self.assertTrue(
            geonicdb_sink.GeonicDBSink(url="https://x", api_key="k").enabled
        )

    def test_from_env_disabled_when_unset(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(geonicdb_sink.GeonicDBSink.from_env().enabled)


class ThrottleAndSendTest(unittest.TestCase):
    def _sink(self, clock):
        return geonicdb_sink.GeonicDBSink(
            url="https://geonicdb.example",
            api_key="gdb_test",
            tenant="example-tenant",
            service_path="/devices/co2",
            entity_id="urn:ngsi-ld:AirQualityObserved:co2-sensor-01",
            interval=60,
            monotonic=clock,
        )

    def test_disabled_sink_does_not_send(self):
        sink = geonicdb_sink.GeonicDBSink(url="", api_key="")
        with mock.patch.object(geonicdb_sink, "_http_post") as post:
            self.assertFalse(sink.maybe_send(SAMPLE, TS))
            post.assert_not_called()

    def test_first_send_then_throttled(self):
        t = {"v": 1000.0}
        sink = self._sink(lambda: t["v"])
        with mock.patch.object(geonicdb_sink, "_http_post", return_value=201) as post:
            self.assertTrue(sink.maybe_send(SAMPLE, TS))  # first send
            t["v"] = 1030.0  # +30s, under 60s interval
            self.assertFalse(sink.maybe_send(SAMPLE, TS))  # throttled
            t["v"] = 1061.0  # +61s from first
            self.assertTrue(sink.maybe_send(SAMPLE, TS))  # interval elapsed
        self.assertEqual(post.call_count, 2)

    def test_network_error_is_swallowed(self):
        sink = self._sink(lambda: 0.0)
        with mock.patch.object(
            geonicdb_sink, "_http_post", side_effect=OSError("connection refused")
        ):
            # must not raise, and must report failure
            self.assertFalse(sink.maybe_send(SAMPLE, TS))

    def test_post_targets_upsert_url_with_headers(self):
        captured = {}

        def fake_post(url, data, headers, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["data"] = data
            return 201

        sink = self._sink(lambda: 0.0)
        with mock.patch.object(geonicdb_sink, "_http_post", side_effect=fake_post):
            self.assertTrue(sink.maybe_send(SAMPLE, TS))
        self.assertEqual(
            captured["url"], "https://geonicdb.example/v2/entities?options=upsert"
        )
        self.assertEqual(captured["headers"]["X-Api-Key"], "gdb_test")
        self.assertEqual(captured["headers"]["Fiware-Service"], "example-tenant")
        self.assertEqual(captured["headers"]["Fiware-ServicePath"], "/devices/co2")
        self.assertEqual(captured["headers"]["Content-Type"], "application/json")


class HeaderCasingTest(unittest.TestCase):
    """Guards the regression where urllib capitalized Fiware-ServicePath to
    Fiware-servicepath, which GeonicDB's case-sensitive policy lookup missed."""

    def test_http_post_preserves_exact_header_case(self):
        import http.server
        import threading

        received = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                # raw_requestline-derived header keys preserve on-the-wire case
                received["keys"] = list(self.headers.keys())
                self.send_response(204)
                self.end_headers()

            def log_message(self, *_args):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()
        port = srv.server_address[1]

        status = geonicdb_sink._http_post(
            f"http://127.0.0.1:{port}/v2/entities?options=upsert",
            b'{"id":"x","type":"AirQualityObserved"}',
            {
                "Content-Type": "application/json",
                "X-Api-Key": "gdb_test",
                "Fiware-Service": "example-tenant",
                "Fiware-ServicePath": "/devices/co2",
            },
            timeout=5,
        )
        t.join(timeout=5)
        srv.server_close()

        self.assertEqual(status, 204)
        # The exact canonical casing must survive (NOT "Fiware-servicepath").
        self.assertIn("Fiware-ServicePath", received["keys"])
        self.assertIn("X-Api-Key", received["keys"])
        self.assertNotIn("Fiware-servicepath", received["keys"])


if __name__ == "__main__":
    unittest.main()
