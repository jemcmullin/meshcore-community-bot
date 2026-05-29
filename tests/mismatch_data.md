# List of example message requests for token troubleshooting. Firmware is always truth.

## 1

### Request

```
Stored recent RF data with routing info: {'timestamp': 1779989617.7001173, 'packet_prefix': '2ec615447373574685ebb5f3ca85e8b1', 'pubkey_prefix': None, 'snr': 11.5, 'rssi': -58, 'raw_hex': '2ec615447373574685ebb5f3ca85e8b11d9f4e1ccfcb9ed6ee308ed91d2e5687ea84e77ac1d8c1045863aa9f000829', 'payload': '15447373574685ebb5f3ca85e8b11d9f4e1ccfcb9ed6ee308ed91d2e5687ea84e77ac1d8c1045863aa9f000829', 'payload_length': 45, 'routing_info': {'path_length': 4, 'path_len_byte': 68, 'path_byte_length': 8, 'bytes_per_hop': 2, 'path_hex': '7373574685ebb5f3', 'path_nodes': ['7373', '5746', '85EB', 'B5F3'], 'route_type': 'FLOOD', 'payload_length': 45, 'payload_type': 'GRP_TXT', 'packet_hash': 'A4EC15BE300281E9'}, 'packet_hash': 'A4EC15BE300281E9', 'route_type_int': 1, 'transport_code1': None, 'payload_type_int': 5, 'scope_payload_hex': 'ca85e8b11d9f4e1ccfcb9ed6ee308ed91d2e5687ea84e77ac1d8c1045863aa9f000829'}
```

```
Channel message payload: {'type': 'CHAN', 'SNR': 11.5, 'channel_idx': 1, 'path_hash_mode': 1, 'path_len': 4, 'txt_type': 0, 'sender_timestamp': 1779989614, 'text': '🐻MEGABEAR 730F: T'}
```

### Firmware Hash

[4ec4]

### Python Hash

[8ad3]

#### Python Debug Output

```
hash compute: fp=0x37055a7761118ad3 channel_kind=1 channel_name_proc=b'bot' sender_name_proc=b'\xf0\x9f\x90\xbbMEGABEAR 730F' sender_key_prefix_padded=b'\x00\x00\x00\x00\x00\x00' sender_timestamp=1779989614 normalized_text=b'T' normalized_text_lower=b't'
```

```
input: channel_kind=1 channel_name='#bot' sender_name='🐻MEGABEAR 730F' sender_key_prefix=000000000000 sender_key_source=firmware_channel_zero sender_timestamp=1779989614 text='T' text_len=1 path_hash_count=4
```

## 2

### Request

```
RF data with routing info: {'timestamp': 1779991338.021989, 'packet_prefix': '33c515434c7be2b7b5f3cae07414ba8a', 'pubkey_prefix': None, 'snr': 12.75, 'rssi': -59, 'raw_hex': '33c515434c7be2b7b5f3cae07414ba8a95f0bf95746e09e41ea3a82768', 'payload': '15434c7be2b7b5f3cae07414ba8a95f0bf95746e09e41ea3a82768', 'payload_length': 27, 'routing_info': {'path_length': 3, 'path_len_byte': 67, 'path_byte_length': 6, 'bytes_per_hop': 2, 'path_hex': '4c7be2b7b5f3', 'path_nodes': ['4C7B', 'E2B7', 'B5F3'], 'route_type': 'FLOOD', 'payload_length': 27, 'payload_type': 'GRP_TXT', 'packet_hash': '5E632B86902CF968'}, 'packet_hash': '5E632B86902CF968', 'route_type_int': 1, 'transport_code1': None, 'payload_type_int': 5, 'scope_payload_hex': 'cae07414ba8a95f0bf95746e09e41ea3a82768'}
```

```
Channel message payload: {'type': 'CHAN', 'SNR': 12.75, 'channel_idx': 1, 'path_hash_mode': 1, 'path_len': 3, 'txt_type': 0, 'sender_timestamp': 1779991334, 'text': 'ZWatt01: T'}
```

### Firmware Hash

[a86f]

### Python Hash

[104f]

#### Python Debug Output

```
hash compute: fp=0x280ce26e18c1104f channel_kind=1 channel_name_proc=b'bot' sender_name_proc=b'ZWatt01' sender_key_prefix_padded=b'\x00\x00\x00\x00\x00\x00' sender_timestamp=1779991334 normalized_text=b'T' normalized_text_lower=b't'
```

```
input: channel_kind=1 channel_name='#bot' sender_name='ZWatt01' sender_key_prefix=000000000000 sender_key_source=firmware_channel_zero sender_timestamp=1779991334 text='T' text_len=1 path_hash_count=3
```

## 3

### Request

```
RF data with routing info: {'timestamp': 1780000519.2444534, 'packet_prefix': '2fc615052774fd18b5ca13cd1822498c', 'pubkey_prefix': None, 'snr': 11.75, 'rssi': -58, 'raw_hex': '2fc615052774fd18b5ca13cd1822498c9816baddc575ea2fafcc8597007cba1311cef3d88aa352196e31faea', 'payload': '15052774fd18b5ca13cd1822498c9816baddc575ea2fafcc8597007cba1311cef3d88aa352196e31faea', 'payload_length': 42, 'routing_info': {'path_length': 5, 'path_len_byte': 5, 'path_byte_length': 5, 'bytes_per_hop': 1, 'path_hex': '2774fd18b5', 'path_nodes': ['27', '74', 'FD', '18', 'B5'], 'route_type': 'FLOOD', 'payload_length': 42, 'payload_type': 'GRP_TXT', 'packet_hash': 'E52A352E94CD3BB0'}, 'packet_hash': 'E52A352E94CD3BB0', 'route_type_int': 1, 'transport_code1': None, 'payload_type_int': 5, 'scope_payload_hex': 'ca13cd1822498c9816baddc575ea2fafcc8597007cba1311cef3d88aa352196e31faea'}
```

```
Channel message payload: {'type': 'CHAN', 'SNR': 11.75, 'channel_idx': 1, 'path_hash_mode': 0, 'path_len': 5, 'txt_type': 0, 'sender_timestamp': 1780000516, 'text': 'Nix Mobile 3: T'}
```

### Firmware Hash

[b2d6]

### Python Hash

[e57e]

#### Python Debug Output

```
hash compute: fp=0xa467519857cce57e channel_kind=1 channel_name_proc=b'bot' sender_name_proc=b'Nix Mobile 3' sender_key_prefix_padded=b'\x00\x00\x00\x00\x00\x00' sender_timestamp=1780000516 normalized_text=b'T' normalized_text_lower=b't'
```

```
input: channel_kind=1 channel_name='#bot' sender_name='Nix Mobile 3' sender_key_prefix=000000000000 sender_key_source=firmware_channel_zero sender_timestamp=1780000516 text='T' text_len=1 path_hash_count=5
```

## 4

### Request

```
RF data with routing info: {'timestamp': 1780006350.0899172, 'packet_prefix': '31d51540ca37894c5d63ac823e9b730b', 'pubkey_prefix': None, 'snr': 12.25, 'rssi': -43, 'raw_hex': '31d51540ca37894c5d63ac823e9b730b41a896a37562284b2b1b4a2d948318d95b07a992058049', 'payload': '1540ca37894c5d63ac823e9b730b41a896a37562284b2b1b4a2d948318d95b07a992058049', 'payload_length': 37, 'routing_info': {'path_length': 0, 'path_len_byte': 64, 'path_byte_length': 0, 'bytes_per_hop': 2, 'path_hex': '', 'path_nodes': [], 'route_type': 'FLOOD', 'payload_length': 37, 'payload_type': 'GRP_TXT', 'packet_hash': '71A739C97AE12429'}, 'packet_hash': '71A739C97AE12429', 'route_type_int': 1, 'transport_code1': None, 'payload_type_int': 5, 'scope_payload_hex': 'ca37894c5d63ac823e9b730b41a896a37562284b2b1b4a2d948318d95b07a992058049'}
```

```
Channel message payload: {'type': 'CHAN', 'SNR': 12.25, 'channel_idx': 1, 'path_hash_mode': 1, 'path_len': 0, 'txt_type': 0, 'sender_timestamp': 1780006349, 'text': '🏃Runr 01: T'}
```

### Firmware Hash

[1a8b]

### Python Hash

[52b3]

#### Python Debug Output

```
hash compute: fp=0xc6c769fe922352b3 channel_kind=2 channel_name_proc=b'bot' sender_name_proc=b'\xf0\x9f\x8f\x83Runr 01' sender_key_prefix_padded=b'\x00\x00\x00\x00\x00\x00' sender_timestamp=1780006349 normalized_text=b'T' normalized_text_lower=b't'
```

```
input: channel_kind=2 channel_name='#bot' sender_name='🏃Runr 01' sender_key_prefix=000000000000 sender_key_source=firmware_channel_zero sender_timestamp=1780006349 text='T' text_len=1 path_hash_count=0
```
