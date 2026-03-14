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
		"""Return (hop_score, infrastructure, path_bonus, path_freshness) for a message.
		
		For Reference from `meshcore-bot/modules/message_handler.py`
		message = MeshMessage(
                content=message_content,  # Use the extracted message content
                sender_id=sender_id,
                sender_pubkey=sender_pubkey, #prefix if not in contacts
                channel=channel_name,
                timestamp=payload.get('sender_timestamp', 0),
                snr=snr,
                rssi=rssi,
                hops=hops,
                path=path_string,  # path extracted from RF data. csv string
                elapsed=_elapsed,
                is_dm=False # for channel
            )
		
		"""
		# Hop score
		hops = getattr(message, 'hops', None)
		hop_score = self.compute_hop_score(hops)

		# Path nodes, will be csv string or 'Direct', parse csv to list
		path = getattr(message, 'path', None)
		path_nodes = path.split(',') if path and path.lower() != 'direct' else []

		# Infrastructure: fan-in per node from mesh_connections, direct path if hops==0
		infrastructure = self.compute_infrastructure_score(path_nodes, db_manager, message)

		# Path bonus: exact sender+path match in observed_paths
		sender_prefix = getattr(message, 'sender_prefix', None)
		path_bonus = self.compute_path_bonus(sender_prefix, path_nodes, db_manager)

		# Freshness: recency decay from observed_paths
		sender_id = getattr(message, 'sender_id', None)
		path_freshness = self.compute_freshness(sender_prefix, sender_id, db_manager)

		return hop_score, infrastructure, path_bonus, path_freshness

	def compute_hop_score(self, hops):
		'''Reward proximity. Less hops, higher delivery potential.'''
		if hops is None:
			return 0.5
		return 1 / (1 + hops)

	def compute_infrastructure_score(self, path_prefixes, db_manager, message=None):
		'''Reward incoming paths on well connected infrastructure as a higher confidence 
		parallel of returning a message.
		'''
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

	def compute_path_bonus(self, sender_prefix, path_nodes, db_manager):
		'''Reward if this sender+path seen before in message_stats. 
		A lower confidence parallel of connectivity.
		'''
		if not sender_prefix or not path_nodes:
			return 0.0
		# Convert path_nodes to CSV string for direct comparison
		path_csv = ','.join(path_nodes).lower() if path_nodes else None
		query = "SELECT Count(id) FROM message_stats WHERE sender_id = ? AND LOWER(path) = ? LIMIT 2"
		result = db_manager.execute_query(query, (sender_prefix, path_csv))
		if len(result) > 1:
			return 1.0  # History more than this message
		return 0.0

	def compute_freshness(self, sender_prefix, sender_id, db_manager):
		'''Reward if this sender seen recently and frequently in message_stats.
		A lower confidence parallel of connectivity. Biased by active users so keep weight low.
		Freshness => 'Sender Recency' in this approach. Considered path based as alternative.
		'''
		relevance_time_window_hours = 24
		max_messages_considered = 5

		if not sender_prefix:
			return 0
		from datetime import datetime, timedelta
		now = datetime.now()
		
		def recency_calc(last_seen):
			try:
				last_seen_dt = datetime.fromisoformat(last_seen)
			except Exception:
				return 0
			age_hours = (now - last_seen_dt).total_seconds() / 3600.0
			return math.exp(-age_hours / 24.0)

		try:
			# Primary: check packet_stream for last message seen
			if not sender_id:
				raise Exception("No sender_id available")
			cutoff = now - timedelta(hours=relevance_time_window_hours)
			
			# Query up to max_messages recent messages from sender within window
			query = (
				"SELECT timestamp FROM message_stats WHERE sender_id = ? "
				"AND timestamp >= ? ORDER BY timestamp DESC LIMIT ?"
			)
			# Use integer timestamp for cutoff
			cutoff_ts = int(cutoff.timestamp())
			result = db_manager.execute_query(query, (sender_id, cutoff_ts, max_messages_considered))
			if not result:
				logger.warning(f"No recent messages sender_id {sender_id} with cutoff {cutoff_ts} and max {max_messages_considered} in message_stats")
				raise Exception("No result or message_stats not available")
			recency_scores = []
			for row in result:
				timestamp = row.get('timestamp')
				if timestamp:
					recency = recency_calc(timestamp)
					recency_scores.append(recency)
			if recency_scores:
				fresh_sum = sum(recency_scores) * 0.33
				# Cap at 1.0, recent rewarded, multiple rewarded but with diminishing returns
				# Compatible with fallback 
				return min(fresh_sum, 1.0)
			return 0
		except Exception:
			# Fallback: use complete_contact_tracking, likely an advert time 
			query_complete_contact_tracking = "SELECT last_heard FROM complete_contact_tracking WHERE public_key LIKE ? AND role = 'companion' ORDER BY last_heard DESC LIMIT 1"
			result = db_manager.execute_query(query_complete_contact_tracking, (f'{sender_prefix}%',))
			if result and 'last_seen' in result[0]:
				last_seen = result[0]['last_seen']
				return recency_calc(last_seen)
		return 0