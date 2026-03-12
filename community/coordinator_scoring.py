import math
import logging
from .config import ScoringConfig

logger = logging.getLogger('CommunityBot')

class CoordinatorScoring:
	"""Implements delivery scoring and path metrics for coordination."""

	def __init__(self, scoring_config: ScoringConfig):
		self.config = scoring_config

	def compute_delivery_score(self, infrastructure, hop_score, path_bonus, path_freshness):
		"""Compute delivery score using weighted formula from config."""
		return (
			infrastructure * self.config.infrastructure_weight
			+ hop_score * self.config.hop_weight
			+ path_bonus * self.config.path_bonus_weight
			+ path_freshness * self.config.freshness_weight
		)

	def get_path_metrics(self, message, db_manager):
		"""Return (hop_score, infrastructure, path_bonus, path_freshness) for a message."""
		# Hop score
		hops = getattr(message, 'hops', None)
		hop_score = self.compute_hop_score(hops)

		# Path nodes
		path = getattr(message, 'path', None)
		path_nodes = self.parse_path_nodes(path)
		path_hex = ''.join(path_nodes).lower() if path_nodes else None

		# Infrastructure: fan-in per node from mesh_connections, direct path if hops==0
		infrastructure = self.compute_infrastructure_score(path_nodes, db_manager, hops)

		# Path bonus: exact sender+path match in observed_paths
		sender_prefix = getattr(message, 'sender_prefix', None)
		path_bonus = self.compute_path_bonus(sender_prefix, path_hex, db_manager)

		# Freshness: recency decay from observed_paths
		path_freshness = self.compute_path_freshness(sender_prefix, db_manager)

		return hop_score, infrastructure, path_bonus, path_freshness

	def parse_path_nodes(self, path):
		if not path:
			return []
		# Example: path is a hex string, split every 2 chars
		return [path[i:i+2] for i in range(0, len(path), 2)]

	def compute_hop_score(self, hops):
		if hops is None:
			return 0.5
		return 1 / (1 + hops)

	def compute_infrastructure_score(self, path_nodes, db_manager, hops=None):
		# Direct path: no hops, no infrastructure is best infrastructure
		if hops is not None and hops == 0:
			if path_nodes is not None and len(path_nodes) > 0:
				logger.warning("Message has 0 hops but path nodes exist, possible incorrect infra score")
			return 1.0
		# Not direct but no path info, assume average infrastructure
		if not path_nodes:
			return 0.5
		# Proceed to score infrastructure based on fan-in of nodes in path
		scores = []
		max_fan_in = 1
		for node in path_nodes:
			# Query fan-in for node
			query = "SELECT COUNT(DISTINCT from_prefix) AS fan_in FROM mesh_connections WHERE to_prefix = ?"
			result = db_manager.execute_query(query, (node,))
			fan_in = result[0]['fan_in'] if result and 'fan_in' in result[0] else 0
			max_fan_in = max(max_fan_in, fan_in)
			scores.append(fan_in)
		# Normalize scores
		norm_scores = [math.log1p(f) / math.log1p(max_fan_in) if max_fan_in > 0 else 0.5 for f in scores]
		# Harmonic mean
		if norm_scores:
			hm = len(norm_scores) / sum(1.0 / (s if s > 0 else 0.5) for s in norm_scores)
			return hm
		return 0.5

	def compute_path_bonus(self, sender_prefix, path_hex, db_manager):
		if not sender_prefix or not path_hex:
			return 0.0
		query = "SELECT 1 FROM observed_paths WHERE LOWER(from_prefix) = ? AND LOWER(path_hex) = ? AND packet_type = 'message' LIMIT 1"
		result = db_manager.execute_query(query, (sender_prefix.lower(), path_hex))
		return 1.0 if result else 0.0

	def compute_path_freshness(self, sender_prefix, db_manager):
		if not sender_prefix:
			return 0.5
		query = "SELECT last_seen FROM observed_paths WHERE LOWER(from_prefix) = ? AND packet_type = 'message' ORDER BY last_seen DESC LIMIT 1"
		result = db_manager.execute_query(query, (sender_prefix.lower(),))
		if result and 'last_seen' in result[0]:
			from datetime import datetime
			last_seen = result[0]['last_seen']
			now = datetime.now()
			try:
				last_seen_dt = datetime.fromisoformat(last_seen)
			except Exception:
				return 0.5
			age_hours = (now - last_seen_dt).total_seconds() / 3600.0
			return math.exp(-age_hours / 24.0)
		return 0.5
