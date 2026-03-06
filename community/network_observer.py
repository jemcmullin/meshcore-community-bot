"""Passive observer that learns repeater node significance from observed traffic.

For each channel message, observes which repeater nodes appear in the path
and whether each was the last hop before reaching this bot.

- A node that is always the last hop is likely a co-located/private feeder
  (low significance — it only feeds this bot, not shared infrastructure).
- A node that appears at varying positions is genuine shared infrastructure
  (high significance — many bots may share it).

Stores daily aggregates in `repeater_daily_stats` (rolling window).
Provides:
  observe_path(path_nodes)          — called for each channel message
  get_node_significance(node_id)    → float 0-1
  compute_path_significance(nodes)  → float | None
"""

import configparser
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("CommunityBot")

_DEFAULT_WINDOW_DAYS = 7
_DEFAULT_MIN_OBS = 10
_DEFAULT_CLEANUP_INTERVAL = 86400
_DEFAULT_SUMMARY_INTERVAL = 3600

# Save to DB every N path observations to reduce write load
_SAVE_THROTTLE = 10


class NetworkObserver:
    """Learns repeater significance from passive path observation."""

    def __init__(self, db_manager):
        self.db_manager = db_manager

        # In-memory accumulators: {node_id: {"total": int, "last_hop": int}}
        self._node_counts: dict[str, dict] = {}
        self._obs_since_save: int = 0

        self._load_observer_config()
        self._init_tables()
        self._load_from_db()

        self._last_cleanup = time.time()
        self._last_summary = time.time()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _load_observer_config(self):
        """Load config from scoring_observer_config.ini + OBSERVER_* env vars."""
        config = configparser.ConfigParser()
        cfg_path = Path(__file__).parent / "scoring_observer_config.ini"
        config.read(str(cfg_path))

        def _get(key, default):
            env_key = f"OBSERVER_{key.upper()}"
            if env_key in os.environ:
                return os.environ[env_key]
            return config.get("NetworkObserver", key, fallback=str(default))

        self.window_days = int(_get("window_days", _DEFAULT_WINDOW_DAYS))
        self.min_observations = int(_get("min_observations", _DEFAULT_MIN_OBS))
        self.cleanup_interval_seconds = int(
            _get("cleanup_interval_seconds", _DEFAULT_CLEANUP_INTERVAL)
        )
        self.summary_interval_seconds = int(
            _get("summary_interval_seconds", _DEFAULT_SUMMARY_INTERVAL)
        )
        logger.info(
            f"NetworkObserver config: window={self.window_days}d "
            f"min_obs={self.min_observations} "
            f"cleanup={self.cleanup_interval_seconds}s "
            f"summary={self.summary_interval_seconds}s"
        )

    # ------------------------------------------------------------------
    # DB schema
    # ------------------------------------------------------------------

    def _init_tables(self):
        """Create tables if absent. Uses execute_query to bypass whitelist."""
        self.db_manager.execute_query(
            """
            CREATE TABLE IF NOT EXISTS repeater_daily_stats (
                node_id     TEXT NOT NULL,
                date_bucket TEXT NOT NULL,
                total_seen  INTEGER NOT NULL DEFAULT 0,
                last_hop    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (node_id, date_bucket)
            )
            """
        )
        self.db_manager.execute_query(
            """
            CREATE TABLE IF NOT EXISTS network_observer_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        try:
            self.db_manager.execute_query(
                "CREATE INDEX IF NOT EXISTS idx_rds_date ON repeater_daily_stats(date_bucket)"
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def _load_from_db(self):
        """Load rolling-window aggregates into in-memory counts."""
        cutoff = self._cutoff_date()
        try:
            rows = self.db_manager.execute_query(
                """
                SELECT node_id, SUM(total_seen), SUM(last_hop)
                FROM repeater_daily_stats
                WHERE date_bucket >= ?
                GROUP BY node_id
                """,
                (cutoff,),
                fetch=True,
            )
            if rows:
                for node_id, total, last_hop in rows:
                    self._node_counts[node_id] = {
                        "total": total or 0,
                        "last_hop": last_hop or 0,
                    }
            logger.info(
                f"NetworkObserver loaded {len(self._node_counts)} repeater nodes "
                f"from DB (window={self.window_days}d, cutoff={cutoff})"
            )
            for node_id, counts in self._node_counts.items():
                sig = self.get_node_significance(node_id)
                role = "private feeder" if sig < 0.3 else ("shared infra" if sig > 0.7 else "mixed")
                logger.debug(
                    f"  Loaded {node_id}: total={counts['total']} "
                    f"last_hop={counts['last_hop']} sig={sig:.2f} ({role})"
                )
        except Exception as e:
            logger.warning(f"NetworkObserver: failed to load from DB: {e}")

    def _flush_to_db(self):
        """Persist today's in-memory counts to DB."""
        today = self._today()
        try:
            for node_id, counts in self._node_counts.items():
                self.db_manager.execute_query(
                    """
                    INSERT INTO repeater_daily_stats (node_id, date_bucket, total_seen, last_hop)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(node_id, date_bucket) DO UPDATE SET
                        total_seen = excluded.total_seen,
                        last_hop   = excluded.last_hop
                    """,
                    (node_id, today, counts["total"], counts["last_hop"]),
                )
            logger.debug(f"NetworkObserver flushed {len(self._node_counts)} nodes to DB ({today})")
        except Exception as e:
            logger.warning(f"NetworkObserver: failed to flush to DB: {e}")

    def _cleanup_old_rows(self):
        """Delete daily stats older than window_days."""
        cutoff = self._cutoff_date()
        try:
            self.db_manager.execute_query(
                "DELETE FROM repeater_daily_stats WHERE date_bucket < ?",
                (cutoff,),
            )
            logger.debug(f"NetworkObserver cleanup: removed rows older than {cutoff}")
        except Exception as e:
            logger.warning(f"NetworkObserver: cleanup failed: {e}")

    # ------------------------------------------------------------------
    # Core observation
    # ------------------------------------------------------------------

    def observe_path(self, path_nodes: list[str]):
        """Record a path observation.

        path_nodes — list of node IDs in path order, last element is
                     the hop immediately before this bot (last_hop node).
        """
        if not path_nodes:
            return

        last_node = path_nodes[-1]

        logger.info(
            f"NetworkObserver path: nodes={path_nodes} last_hop={last_node} "
            f"(obs_since_save={self._obs_since_save + 1}/{_SAVE_THROTTLE})"
        )

        for node_id in path_nodes:
            prev_total = self._node_counts.get(node_id, {}).get("total", 0)
            if node_id not in self._node_counts:
                self._node_counts[node_id] = {"total": 0, "last_hop": 0}
            self._node_counts[node_id]["total"] += 1
            if node_id == last_node:
                self._node_counts[node_id]["last_hop"] += 1

            # Log when a node first crosses the minimum observation threshold
            new_total = self._node_counts[node_id]["total"]
            if prev_total < self.min_observations <= new_total:
                sig = self.get_node_significance(node_id)
                role = "private feeder" if sig < 0.3 else ("shared infra" if sig > 0.7 else "mixed")
                logger.info(
                    f"Repeater {node_id} reached min_obs threshold: "
                    f"significance={sig:.2f} ({role}) "
                    f"total={new_total} last_hop={self._node_counts[node_id]['last_hop']}"
                )
            else:
                sig = self.get_node_significance(node_id)
                role = "private feeder" if sig < 0.3 else ("shared infra" if sig > 0.7 else "mixed")
                logger.info(
                    f"  {node_id}: total={self._node_counts[node_id]['total']} "
                    f"last_hop={self._node_counts[node_id]['last_hop']} "
                    f"sig={sig:.2f} ({role})"
                )

        self._obs_since_save += 1
        if self._obs_since_save >= _SAVE_THROTTLE:
            self._flush_to_db()
            self._obs_since_save = 0

        now = time.time()
        if now - self._last_cleanup > self.cleanup_interval_seconds:
            self._cleanup_old_rows()
            self._last_cleanup = now

        if (
            self.summary_interval_seconds > 0
            and now - self._last_summary > self.summary_interval_seconds
        ):
            self._log_summary()
            self._last_summary = now

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def get_node_significance(self, node_id: str) -> float:
        """Return significance of a single node (0=private feeder, 1=shared infra).

        Returns 0.5 if insufficient data.
        """
        counts = self._node_counts.get(node_id)
        if not counts or counts["total"] < self.min_observations:
            return 0.5  # uncertain

        total = counts["total"]
        last_hop = counts["last_hop"]
        # Fraction of appearances where node was NOT the last hop
        return 1.0 - (last_hop / total)

    def compute_path_significance(self, path_nodes: list[str]) -> Optional[float]:
        """Mean significance across all nodes in path.

        Returns None if path is empty.
        """
        if not path_nodes:
            return None
        sigs = [self.get_node_significance(n) for n in path_nodes]
        result = sum(sigs) / len(sigs)
        logger.debug(
            f"Path significance: {result:.2f} "
            f"nodes={path_nodes} "
            f"per-node={[f'{n}:{s:.2f}' for n, s in zip(path_nodes, sigs)]}"
        )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _today() -> str:
        from datetime import date
        return date.today().isoformat()

    def _cutoff_date(self) -> str:
        from datetime import date, timedelta
        return (date.today() - timedelta(days=self.window_days)).isoformat()

    def _log_summary(self):
        total_nodes = len(self._node_counts)
        if total_nodes == 0:
            return
        high_sig = sum(
            1
            for n in self._node_counts
            if self.get_node_significance(n) >= 0.7
        )
        logger.info(
            f"NetworkObserver: {total_nodes} nodes observed, "
            f"{high_sig} high-significance (shared infra)"
        )
