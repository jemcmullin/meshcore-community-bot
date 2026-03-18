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
		path_csv = getattr(message, 'path', None)
		logger.debug(f"[SCORING] Extracted path_csv from message: {path_csv}")
		if path_csv and (path_csv.lower() == 'direct' or ',' in path_csv):
			logger.warning(f"[SCORING] Path parsing assumes path is a CSV string or 'Direct'. Verify message.path format. Got: {path_csv}")
		path_list = path_csv.split(',') if path_csv and path_csv.lower() != 'direct' else []

		# Infrastructure: fan-in per node from mesh_connections, direct path if hops==0
		logger.debug(f"[SCORING] Computing infrastructure score for path {path_list} with hops {hops}")
		infrastructure = self.compute_infrastructure_score(path_list, db_manager, message)

		# Path bonus: exact sender+path match in message_stats history
		sender_id = getattr(message, 'sender_id', None)
		logger.debug(f"[SCORING] Computing path bonus for sender_id {sender_id} and path {path_csv}")
		path_bonus = self.compute_path_bonus(sender_id, path_csv, db_manager)

		# Freshness: recency decay from message_stats history
		sender_pubkey = getattr(message, 'sender_pubkey', None)
		logger.debug(f"[SCORING] Computing freshness for sender_pubkey {sender_pubkey} and sender_id {sender_id}")
		freshness = self.compute_freshness(sender_pubkey, sender_id, db_manager)

		return hop_score, infrastructure, path_bonus, freshness

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

	def compute_path_bonus(self, sender_id, path_csv, db_manager):
		'''Reward if this sender+path seen before in message_stats. 
		A lower confidence parallel of connectivity.
		'''
		if not sender_id or not path_csv:
			return 0.0
		query = "SELECT COUNT(id) FROM message_stats WHERE sender_id = ? AND path = ? LIMIT 2"
		result = db_manager.execute_query(query, (sender_id, path_csv))
		logger.debug(f"Path bonus query result for sender_id {sender_id} and path_csv {path_csv}: {result}")
		if len(result) > 1:
			return 1.0  # History more than this message
		return 0.0

	def compute_freshness(self, sender_pubkey, sender_id, db_manager):
		'''Reward if this sender seen recently and frequently in message_stats.
		A lower confidence parallel of connectivity. Biased by active users so keep weight low.
		Freshness => 'Sender Recency' in this approach. Considered path based as alternative.
		'''
		relevance_time_window_hours = 24
		max_messages_considered = 5

		if not sender_pubkey:
			return 0
		from datetime import datetime, timedelta
		now = datetime.now()
		
		def recency_calc(now, timestamp:float):
			#timestamp is an int seconds
			try:
				timestamp_dt = datetime.fromtimestamp(timestamp)
			except Exception:
				return 0
			age_hours = (now - timestamp_dt).total_seconds() / 3600.0
			return math.exp(-age_hours / 24.0)

		try:
			# Primary: check packet_stream for last message seen
			if not sender_id:
				raise Exception("No sender_id available")
			cutoff = now - timedelta(hours=relevance_time_window_hours)
			
			# Query up to max_messages recent messages from sender within window grouped by 5-minute intervals to reduce 'rapid activity' bias.
			query = (
				"SELECT MAX(timestamp) as timestamp FROM message_stats WHERE sender_id = ? "
				"AND timestamp >= ? GROUP BY (timestamp / 300) ORDER BY timestamp DESC LIMIT ?"
			)
			# Use integer timestamp for cutoff
			cutoff_ts = int(cutoff.timestamp())
			logger.debug(f"Computing freshness: querying message_stats for sender_id {sender_id} with cutoff_ts {cutoff_ts} and max {max_messages_considered}")
			result = db_manager.execute_query(query, (sender_id, cutoff_ts, max_messages_considered))
			if not result:
				logger.warning(f"No recent messages sender_id {sender_id} with cutoff {cutoff_ts} and max {max_messages_considered} in message_stats")
				raise Exception("No result or message_stats not available")
			logger.debug(f"Freshness query result for sender_id {sender_id}: {result}")
			recency_scores = []
			for row in result:
				timestamp = row.get('timestamp')
				if timestamp:
					recency = recency_calc(now, float(timestamp))
					recency_scores.append(recency)
			if recency_scores:
				logger.debug(f"Recency scores for sender_id {sender_id}: {recency_scores}")
				fresh_sum = sum(recency_scores) * 0.33
				# Cap at 1.0, recent rewarded, multiple rewarded but with diminishing returns
				# Compatible with fallback 
				return min(fresh_sum, 1.0)
			return 0
		except Exception:
			# Fallback: use complete_contact_tracking, likely an advert time 
			logger.debug(f"Freshness fallback triggered for sender_pubkey {sender_pubkey}")
			query_complete_contact_tracking = "SELECT last_heard FROM complete_contact_tracking WHERE public_key LIKE ? AND role = 'companion' ORDER BY last_heard DESC LIMIT 1"
			result = db_manager.execute_query(query_complete_contact_tracking, (f'{sender_pubkey}%',))
			logger.debug(f"Freshness fallback query result for sender_pubkey {sender_pubkey}: {result}")
			if result and 'last_seen' in result[0]:
				last_seen = result[0]['last_seen']
				dt = datetime.strptime(last_seen, "%Y-%m-%d %H:%M:%S")
				last_heard_seconds = dt.timestamp() #to match message_stats
				return recency_calc(now, last_heard_seconds)
		return 0