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
		infrastructure = self.compute_infrastructure_score(path_nodes, db_manager, message)

		# Path bonus: exact sender+path match in observed_paths
		sender_prefix = getattr(message, 'sender_prefix', None)
		path_bonus = self.compute_path_bonus(sender_prefix, path_hex, db_manager)

		# Freshness: recency decay from observed_paths
		sender_id = getattr(message, 'sender_id', None)
		path_freshness = self.compute_freshness(sender_prefix, sender_id, db_manager)

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

	def compute_infrastructure_score(self, path_prefixes, db_manager, message=None):
		# Direct path: no hops, score based on SNR/RSSI
		hops = getattr(message, 'hops', None)
		if hops is not None and hops == 0:
			snr = getattr(message, 'snr', None)
			rssi = getattr(message, 'rssi', None)
			# Normalize SNR (assume -15 to +15 dB typical range)
			snr_score = 0.5
			if snr is not None:
				snr_score = min(max((snr + 15) / 30.0, 0.0), 1.0)
			# Normalize RSSI (assume -120 to -30 dBm typical range)
			rssi_score = 0.5
			if rssi is not None:
				rssi_score = min(max((rssi + 120) / 90.0, 0.0), 1.0)
			# Blend SNR/RSSI (weight SNR 70%, RSSI 30%)
			infra_score = snr_score * 0.7 + rssi_score * 0.3
			return infra_score

		# Not direct but no path info, assume average infrastructure
		if not path_prefixes:
			return 0.5

		# Score infrastructure based on fan-in of nodes in path
		# The logic below became more complex to support transition from 2-byte to 4-byte (and longer) prefixes.
		# It deduplicates ambiguous prefix/public key matches to prevent inflated infrastructure scores
		# when prefixes overlap or multiple public keys share a prefix. This ensures each node is counted
		# only once in almost all cases, regardless of prefix length or DB schema.
		scores = []
		max_fan_in = 1
		for node_prefix in path_prefixes:
			
			# Fetch all relevant rows
			query = """
				SELECT from_prefix, from_public_key
				FROM mesh_connections
				WHERE (
					(to_public_key IS NOT NULL AND to_public_key LIKE ?)
					OR (to_public_key IS NULL AND to_prefix = ?)
				)
			"""
			like_pattern = f'{node_prefix}%'  # Match public keys starting with node
			rows = db_manager.execute_query(query, (like_pattern, node_prefix))

			public_keys = set()
			prefixes = []

			for row in rows:
				public_key = row.get('from_public_key')
				prefix = row.get('from_prefix')
				if public_key:
					public_keys.add(public_key)
				elif prefix:
					prefixes.append(prefix)


			unique_ids = set(public_keys)
			for prefix in prefixes:
				matches = [pk for pk in public_keys if pk.startswith(prefix)]
				if len(matches) == 1:
					unique_ids.add(matches[0])  # count as the node
				elif len(matches) == 0:
					unique_ids.add(prefix)      # count as unique prefix node
				# else: len(matches) > 1, ambiguous, ignore

			fan_in = len(unique_ids)
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

	def compute_freshness(self, sender_prefix, sender_id, db_manager):
		if not sender_prefix:
			return 0
		from datetime import datetime, timedelta
		now = datetime.now()
		
		def freshness_calc(last_seen):
			try:
				last_seen_dt = datetime.fromisoformat(last_seen)
			except Exception:
				return 0
			age_hours = (now - last_seen_dt).total_seconds() / 3600.0
			return math.exp(-age_hours / 24.0)

		try:
			# Primary: check packet_stream for last message seen
			if not sender_id:
				raise Exception("No sender_id available for packet_stream query")
			cutoff = now - timedelta(hours=24)
			max_messages = 5
			# Query up to max_messages recent messages from sender within window
			query = (
				"SELECT timestamp FROM packet_stream WHERE data LIKE ? "
				"AND timestamp >= ? ORDER BY timestamp DESC LIMIT ?"
			)
			result = db_manager.execute_query(query, (f'%\"user\": \"{sender_id}\"%', cutoff.isoformat(), max_messages))
			if not result:
				raise Exception("No result or packet_stream not available")
			freshness_scores = []
			for row in result:
				timestamp = row.get('timestamp')
				if timestamp:
					freshness = freshness_calc(timestamp)
					freshness_scores.append(freshness)
			if freshness_scores:
				fresh_sum = sum(freshness_scores)
				# Cap at 1.0, recent rewarded, multiple rewarded but with diminishing returns
				# Compatible with fallback 
				return min(fresh_sum, 1.0)
			return 0
		except Exception:
			# Fallback: use complete_contact_tracking as before but likely an advert time 
			query_complete_contact_tracking = "SELECT last_heard FROM complete_contact_tracking WHERE public_key LIKE ? AND role = 'companion' ORDER BY last_heard DESC LIMIT 1"
			result = db_manager.execute_query(query_complete_contact_tracking, (f'{sender_prefix}%',))
			if result and 'last_seen' in result[0]:
				last_seen = result[0]['last_seen']
				return freshness_calc(last_seen)
		return 0

	# Preferred method not viable until message observed_paths also implemented. Currently only adverts.
	# def compute_path_freshness(self, sender_prefix, db_manager):
	# 	if not sender_prefix:
	# 		return 0.5
	# 	query = "SELECT last_seen FROM observed_paths WHERE LOWER(from_prefix) = ? AND packet_type = 'message' ORDER BY last_seen DESC LIMIT 1"
	# 	result = db_manager.execute_query(query, (sender_prefix.lower(),))
	# 	if result and 'last_seen' in result[0]:
	# 		from datetime import datetime
	# 		last_seen = result[0]['last_seen']
	# 		now = datetime.now()
	# 		try:
	# 			last_seen_dt = datetime.fromisoformat(last_seen)
	# 		except Exception:
	# 			return 0.5
	# 		age_hours = (now - last_seen_dt).total_seconds() / 3600.0
	# 		return math.exp(-age_hours / 24.0)
	# 	return 0.5