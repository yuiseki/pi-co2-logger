#!/usr/bin/env python3
"""Optional sink that forwards CO2 readings to a GeonicDB (FIWARE Orion-compatible)
Context Broker as NGSIv2 ``AirQualityObserved`` entities.

All connection details (endpoint, API key, tenant, service path) are supplied via
environment variables — nothing is hardcoded. When the endpoint or API key is
missing the sink is disabled and the logger keeps working as before.

Sending is:
- **throttled** to at most one upsert per ``GEONICDB_INTERVAL`` seconds, and
- **best-effort**: any network/HTTP error is logged and swallowed so it never
  interrupts the serial logging loop.

Environment variables:
    GEONICDB_URL          Base URL of the broker (e.g. https://broker.example)
    GEONICDB_API_KEY      X-Api-Key value
    GEONICDB_TENANT       Fiware-Service (tenant) name
    GEONICDB_SERVICEPATH  Fiware-ServicePath (default: /devices/co2)
    GEONICDB_ENTITY_ID    NGSI entity id (default: urn:ngsi-ld:AirQualityObserved:co2-sensor-01)
    GEONICDB_INTERVAL     Minimum seconds between upserts (default: 60)
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import time
import urllib.parse
from datetime import datetime
from typing import Callable

logger = logging.getLogger("co2-logger.geonicdb")

USER_AGENT = "pi-co2-logger"

DEFAULT_SERVICE_PATH = "/devices/co2"
DEFAULT_ENTITY_ID = "urn:ngsi-ld:AirQualityObserved:co2-sensor-01"
DEFAULT_INTERVAL = 60.0


def build_payload(stat: dict, ts: datetime, *, entity_id: str) -> dict:
    """Build an NGSIv2 AirQualityObserved entity from a corrected reading.

    ``stat`` follows the logger's internal shape:
    ``{"co2ppm": int, "temperature": float, "humidity": float, ...}``.
    """
    return {
        "id": entity_id,
        "type": "AirQualityObserved",
        "co2": {"type": "Number", "value": stat["co2ppm"]},
        "temperature": {"type": "Number", "value": stat["temperature"]},
        "relativeHumidity": {"type": "Number", "value": stat["humidity"]},
        "dateObserved": {
            "type": "DateTime",
            "value": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }


def _http_post(url: str, data: bytes, headers: dict, timeout: float) -> int:
    """POST ``data`` and return the HTTP status code. Raises on transport errors.

    Uses ``http.client`` rather than ``urllib`` on purpose: urllib's
    ``Request.add_header`` runs ``key.capitalize()`` on every header name, which
    turns ``Fiware-ServicePath`` into ``Fiware-servicepath``. GeonicDB's policy
    engine looks the header up by exact case (``Fiware-ServicePath`` or the
    all-lowercase ``fiware-servicepath``), so the mangled name is missed and the
    request is denied. ``putheader`` sends names verbatim.
    """
    parts = urllib.parse.urlsplit(url)
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    if parts.scheme == "https":
        conn = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=timeout)
    else:
        conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    try:
        conn.putrequest("POST", path, skip_host=False, skip_accept_encoding=True)
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.putheader("Content-Length", str(len(data)))
        conn.endheaders()
        conn.send(data)
        return conn.getresponse().status
    finally:
        conn.close()


class GeonicDBSink:
    """Throttled, best-effort forwarder of readings to GeonicDB."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        tenant: str = "",
        service_path: str = DEFAULT_SERVICE_PATH,
        entity_id: str = DEFAULT_ENTITY_ID,
        interval: float = DEFAULT_INTERVAL,
        timeout: float = 10.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.tenant = tenant
        self.service_path = service_path
        self.entity_id = entity_id
        self.interval = interval
        self.timeout = timeout
        self._monotonic = monotonic
        self._last_sent: float | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.api_key)

    @classmethod
    def from_env(cls) -> "GeonicDBSink":
        return cls(
            url=os.environ.get("GEONICDB_URL", ""),
            api_key=os.environ.get("GEONICDB_API_KEY", ""),
            tenant=os.environ.get("GEONICDB_TENANT", ""),
            service_path=os.environ.get("GEONICDB_SERVICEPATH", DEFAULT_SERVICE_PATH),
            entity_id=os.environ.get("GEONICDB_ENTITY_ID", DEFAULT_ENTITY_ID),
            interval=float(os.environ.get("GEONICDB_INTERVAL", DEFAULT_INTERVAL)),
        )

    def maybe_send(self, stat: dict, ts: datetime) -> bool:
        """Upsert the reading if enabled and the throttle interval has elapsed.

        Returns True only when an upsert was accepted (HTTP 2xx). Never raises.
        """
        if not self.enabled:
            return False

        now = self._monotonic()
        if self._last_sent is not None and (now - self._last_sent) < self.interval:
            return False

        payload = build_payload(stat, ts, entity_id=self.entity_id)
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "X-Api-Key": self.api_key,
            "Fiware-Service": self.tenant,
            "Fiware-ServicePath": self.service_path,
        }
        url = f"{self.url}/v2/entities?options=upsert"
        try:
            status = _http_post(
                url, json.dumps(payload).encode(), headers, self.timeout
            )
        except (http.client.HTTPException, OSError) as exc:
            logger.warning("GeonicDB upsert failed (network): %s", exc)
            return False

        # Only advance the throttle clock on a successful send so transient
        # failures are retried on the next cycle rather than silently skipped.
        if 200 <= status < 300:
            self._last_sent = now
            logger.info("GeonicDB upsert ok (%d) co2=%d", status, stat["co2ppm"])
            return True

        logger.warning("GeonicDB upsert rejected: HTTP %d", status)
        return False
