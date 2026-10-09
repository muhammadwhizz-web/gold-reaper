"""
GOLD REAPER :: broker health + network resilience
=================================================
Two layers of "the bot never dies":

  retry_call(fn, ...)        exponential backoff 1s -> 2s -> 4s -> 8s -> 60s cap,
                             max 5 attempts, for ANY broker/network call
  HealthMonitor              background thread: pings the broker every 60s,
                             logs latency, alerts (Telegram/Discord/email via
                             core.notify) after repeated failures, and asks
                             the broker to reconnect

Every adapter's last_price() doubles as the liveness probe — cheap and real.
"""
from __future__ import annotations

import threading
import time
import traceback

from brokers.base import BrokerBase

DEFAULT_ATTEMPTS = 5
DEFAULT_BASE = 1.0
DEFAULT_CAP = 60.0


def retry_call(fn, *args, attempts: int = DEFAULT_ATTEMPTS,
               base: float = DEFAULT_BASE, cap: float = DEFAULT_CAP,
               exceptions: tuple = (Exception,),
               on_retry=None, **kwargs):
    """Call fn with exponential backoff. Returns fn result or raises last."""
    delay = base
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return fn(*args, **kwargs)
        except exceptions as e:  # noqa: PERF203
            last_exc = e
            if i < attempts - 1:
                sleep_s = min(cap, delay)
                if on_retry:
                    try:
                        on_retry(i + 1, e, sleep_s)
                    except Exception:  # noqa: BLE001
                        pass
                time.sleep(sleep_s)
                delay = min(cap, delay * 2)
    raise last_exc  # type: ignore[misc]


class HealthMonitor(threading.Thread):
    """Pings the broker every 60s. Alerts + reconnects on repeated failure.

    status snapshot keys: ok, consecutive_failures, last_latency_ms,
    last_ok_ts, broker (adapter name)
    """

    def __init__(self, broker: BrokerBase, cfg,
                 interval: float = 60.0, alert_after: int = 3,
                 log=None) -> None:
        super().__init__(daemon=True, name="broker-health")
        self.broker = broker
        self.cfg = cfg
        self.interval = interval
        self.alert_after = alert_after
        self.log = log
        self.stop_flag = threading.Event()
        self.ok = True
        self.consecutive_failures = 0
        self.last_latency_ms: float | None = None
        self.last_ok_ts: float = 0.0
        self._alerted = False

    # -------------------------------------------------------------- probe
    def _probe(self) -> float:
        t0 = time.perf_counter()
        px = self.broker.last_price()
        latency = (time.perf_counter() - t0) * 1000.0
        if not px or px <= 0:
            raise RuntimeError("probe returned no price")
        return latency

    def _reconnect(self) -> None:
        try:
            self.broker.disconnect()
        except Exception:  # noqa: BLE001
            pass
        try:
            ok = self.broker.connect()
            if self.log:
                self.log.info("health: reconnect %s -> %s",
                              type(self.broker).name, "ok" if ok else "failed")
        except Exception as e:  # noqa: BLE001
            if self.log:
                self.log.warning("health: reconnect error: %s", e)

    # -------------------------------------------------------------- loop
    def run(self) -> None:
        while not self.stop_flag.wait(self.interval):
            try:
                self.last_latency_ms = self._probe()
                self.ok = True
                self.consecutive_failures = 0
                self.last_ok_ts = time.time()
                self._alerted = False
                if self.log:
                    self.log.info("health: %s alive (%.0f ms)",
                                  type(self.broker).name, self.last_latency_ms)
            except Exception:  # noqa: BLE001
                self.consecutive_failures += 1
                self.ok = False
                if self.log:
                    self.log.warning(
                        "health: probe failed x%d (%s)",
                        self.consecutive_failures,
                        traceback.format_exc(limit=1).strip().splitlines()[-1])
                if self.consecutive_failures == 2:
                    self._reconnect()
                if self.consecutive_failures >= self.alert_after \
                        and not self._alerted:
                    self._alerted = True
                    self._alert()

    def _alert(self) -> None:
        try:
            from core.notify import notify
            notify("BROKER DOWN",
                   f"{type(self.broker).name} failed {self.consecutive_failures} "
                   f"health probes. Failover/reconnect in progress.")
        except Exception:  # noqa: BLE001
            pass

    def snapshot(self) -> dict:
        return {
            "ok": self.ok,
            "consecutive_failures": self.consecutive_failures,
            "last_latency_ms": (round(self.last_latency_ms, 1)
                                if self.last_latency_ms else None),
            "last_ok_age_s": (round(time.time() - self.last_ok_ts, 0)
                              if self.last_ok_ts else None),
            "broker": type(self.broker).name,
        }

    def stop(self) -> None:
        self.stop_flag.set()
