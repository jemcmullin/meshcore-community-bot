#!/usr/bin/env python3
"""
Terminal Area Forecast (TAF)-style weather command.

Produces a compact multi-period forecast string resembling an aviation TAF using
NOAA forecast periods obtained via geo lookup.  Each period is encoded as:

  LOCATION DDHHZ WIND VIS WX CLOUDS TEMP | NEXT_PERIOD ...

Example output:
  DENVER FM0600Z 27012KT 10SM SCT BKN 22C | TONIGHT 15C -RA | TOMORROW 28C SKC

Because mesh radio messages are length-constrained the output is deliberately
terse: one current period plus up to two look-ahead periods, space-separated.
"""

import re
from typing import Optional

from modules.commands.base_command import BaseCommand
from modules.models import MeshMessage

try:
    from modules.commands.wx_command import WxCommand
except Exception:
    WxCommand = None


class WxTafCommand(BaseCommand):
    """TAF-style multi-period forecast command.

    Usage: taf <zipcode|lat,lon|city>
    Keywords: taf, wxtaf, wx-taf, wxt
    """

    name = "taf"
    keywords = ["taf", "wxtaf", "wx-taf", "wxt"]
    description = "Produce a TAF-style compact multi-period weather forecast"
    category = "weather"

    short_description = "Get a TAF-style forecast for a location"
    usage = "taf <zipcode|lat,lon|city>"
    examples = ["taf 80202", "taf 39.7,-104.9", "taf Denver"]

    def __init__(self, bot):
        super().__init__(bot)
        self.enabled = self.get_config_value('WxTaf_Command', 'enabled', fallback=True, value_type='bool')
        self._wx = WxCommand(bot) if WxCommand is not None else None

    def can_execute(self, message: MeshMessage, skip_channel_check: bool = False) -> bool:
        if not self.enabled:
            return False
        return super().can_execute(message)

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_coordinates(self, text: str) -> Optional[tuple]:
        m = re.match(r'^\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*$', text)
        if not m:
            return None
        try:
            lat, lon = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return (lat, lon)
        except Exception:
            return None
        return None

    def _parse_speed_to_kts(self, value, use_high: bool = False) -> int:
        """Parse a speed value to knots.

        NOAA windSpeed fields often come as range strings like "12 to 18 mph".
        When *use_high* is True the upper bound is used (useful for gusts); otherwise
        the lower bound (sustained) is used.
        """
        if value is None:
            return 0
        try:
            if isinstance(value, (int, float)):
                return int(round(float(value) * 0.868976))  # mph → kts
            s = str(value).strip().lower()
            # Determine unit factor
            if 'kt' in s or 'knot' in s:
                factor = 1.0
            elif 'km/h' in s or 'kph' in s:
                factor = 0.539957
            elif 'm/s' in s or 'mps' in s:
                factor = 1.94384
            else:
                factor = 0.868976  # assume mph
            # Find all numeric values (handles "12 to 18 mph")
            nums = re.findall(r'\d+(?:\.\d+)?', s)
            if not nums:
                return 0
            v = float(nums[-1] if use_high else nums[0])
            return int(round(v * factor))
        except Exception:
            return 0

    def _parse_wind_dir_deg(self, value) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(round(float(value))) % 360
        except Exception:
            pass
        s = str(value).strip().upper()
        dirs = {
            'N': 0, 'NNE': 22, 'NE': 45, 'ENE': 67,
            'E': 90, 'ESE': 112, 'SE': 135, 'SSE': 157,
            'S': 180, 'SSW': 202, 'SW': 225, 'WSW': 247,
            'W': 270, 'WNW': 292, 'NW': 315, 'NNW': 337,
        }
        for token in s.replace('-', ' ').split():
            if token in dirs:
                return dirs[token]
        return None

    def _wind_field(self, direction, speed_raw, gust_raw=None) -> str:
        # Use lower bound of range strings for sustained speed, upper for gusts
        kts = self._parse_speed_to_kts(speed_raw, use_high=False)
        if kts == 0:
            return 'CALM'
        deg = self._parse_wind_dir_deg(direction)
        is_vrb = direction is None or 'variable' in str(direction).lower()
        dir_part = 'VRB' if is_vrb or deg is None else f"{deg:03d}"
        # If no explicit gust value, try using high end of speed range as gust
        gust_kts = self._parse_speed_to_kts(gust_raw)
        if gust_kts == 0 and speed_raw is not None:
            high_kts = self._parse_speed_to_kts(speed_raw, use_high=True)
            if high_kts > kts:
                gust_kts = high_kts
        if gust_kts > kts:
            return f"{dir_part}{kts:02d}G{gust_kts:02d}KT"
        return f"{dir_part}{kts:02d}KT"

    def _cloud_field(self, short_forecast: str) -> str:
        s = short_forecast.lower()
        if 'clear' in s or 'sunny' in s:
            return 'SKC'
        if 'few' in s or 'partly' in s:
            return 'SCT'
        if 'mostly cloudy' in s or 'mostly' in s:
            return 'BKN'
        if 'overcast' in s or 'cloudy' in s:
            return 'OVC'
        return ''

    def _wx_codes(self, s: str) -> str:
        if not s:
            return ''
        sl = s.lower()
        intensity = '+' if ('+' in sl or 'heavy' in sl) else ('-' if ('-' in sl or 'light' in sl) else '')
        codes = []
        if 'thunder' in sl:
            codes.append('TS')
        if 'rain' in sl:
            codes.append('RA')
        if 'drizzle' in sl:
            codes.append('DZ')
        if 'snow' in sl:
            codes.append('SN')
        if 'fog' in sl:
            codes.append('FG')
        if 'mist' in sl or ' br ' in sl:
            codes.append('BR')
        if 'haze' in sl:
            codes.append('HZ')
        if 'smoke' in sl:
            codes.append('FU')
        if 'dust' in sl or 'sand' in sl:
            codes.append('DU')
        return (intensity + ''.join(codes)) if codes else ''

    def _temp_c(self, value, unit: str) -> Optional[int]:
        try:
            v = float(value)
            u = (unit or 'F').strip().upper()
            if u.startswith('F'):
                return int(round((v - 32.0) * 5.0 / 9.0))
            if u.startswith('K'):
                return int(round(v - 273.15))
            return int(round(v))
        except Exception:
            return None

    def _station_name(self, points: dict, lat: float, lon: float, raw_text: str) -> str:
        try:
            props = points.get('properties', {}).get('relativeLocation', {}).get('properties', {})
            city = props.get('city') or props.get('name') or ''
            if isinstance(city, dict):
                city = city.get('name', '')
            if city:
                clean = re.sub(r'[^A-Za-z0-9]', '', city.split(',')[0]).upper()
                if clean:
                    return clean[:10]
        except Exception:
            pass
        m = re.search(r'\b(\d{5})\b', raw_text)
        if m:
            return m.group(1)
        return f"MESH{int(abs(lat * 100) % 1000):03d}"

    # ------------------------------------------------------------------
    # Period encoding
    # ------------------------------------------------------------------

    def _encode_period(self, period: dict, include_wind: bool = True) -> str:
        """Encode one NOAA forecast period into a compact TAF-like token string."""
        tokens = []

        # Period label (THIS AFTERNOON → AFTN, TONIGHT → NGT, weekday → MON etc.)
        name = (period.get('name') or '').upper()
        label_map = {
            'THIS AFTERNOON': 'AFTN',
            'THIS MORNING': 'MORN',
            'TONIGHT': 'NGT',
            'OVERNIGHT': 'OVNT',
            'TOMORROW': 'TMRW',
            'TOMORROW NIGHT': 'TMRW NGT',
            'MONDAY': 'MON', 'MONDAY NIGHT': 'MON NGT',
            'TUESDAY': 'TUE', 'TUESDAY NIGHT': 'TUE NGT',
            'WEDNESDAY': 'WED', 'WEDNESDAY NIGHT': 'WED NGT',
            'THURSDAY': 'THU', 'THURSDAY NIGHT': 'THU NGT',
            'FRIDAY': 'FRI', 'FRIDAY NIGHT': 'FRI NGT',
            'SATURDAY': 'SAT', 'SATURDAY NIGHT': 'SAT NGT',
            'SUNDAY': 'SUN', 'SUNDAY NIGHT': 'SUN NGT',
        }
        label = label_map.get(name, name[:6] if name else '')
        if label:
            tokens.append(label)

        if include_wind:
            wf = self._wind_field(
                period.get('windDirection'),
                period.get('windSpeed'),
                gust_raw=period.get('windGust'),
            )
            if wf and wf != 'CALM':
                tokens.append(wf)

        short = period.get('shortForecast', '')
        wx = self._wx_codes(short)
        cloud = self._cloud_field(short)
        if wx:
            tokens.append(wx)
        if cloud:
            tokens.append(cloud)

        t_val = self._temp_c(period.get('temperature'), period.get('temperatureUnit', 'F'))
        if t_val is not None:
            prefix = 'M' if t_val < 0 else ''
            tokens.append(f"{prefix}{abs(t_val):02d}C")

        # Precipitation probability
        pop = None
        try:
            pop = period.get('probabilityOfPrecipitation', {}).get('value')
        except Exception:
            pass
        if pop is not None and int(pop) > 0:
            tokens.append(f"PROB{int(pop)}")

        return ' '.join(tokens)

    # ------------------------------------------------------------------
    # Main execute
    # ------------------------------------------------------------------

    async def execute(self, message: MeshMessage) -> bool:
        content = message.content.strip()
        parts = content.split()

        lat = lon = None

        if len(parts) < 2:
            # Try companion location first
            if self._wx:
                loc = self._wx._get_companion_location(message)
                if loc:
                    lat, lon = loc
            # Fall back to default_city config
            if lat is None and self._wx:
                default_city = self.bot.config.get('Weather', 'default_city', fallback='').strip()
                if default_city:
                    try:
                        result = self._wx.city_to_lat_lon(default_city)
                        lat, lon = result[0], result[1]
                    except Exception:
                        pass
            # Fall back to bot location
            if lat is None and self._wx:
                bot_loc = self._wx._get_bot_location()
                if bot_loc:
                    lat, lon = bot_loc
            if lat is None:
                await self.send_response(message, 'No location provided')
                return False
        else:
            location_arg = ' '.join(parts[1:]).strip()
            coords = self._parse_coordinates(location_arg)
            if coords:
                lat, lon = coords
            elif re.match(r'^\d{5}$', location_arg):
                if not self._wx:
                    await self.send_response(message, 'Zipcode lookup unavailable')
                    return False
                try:
                    lat, lon = self._wx.zipcode_to_lat_lon(location_arg)
                    if lat is None or lon is None:
                        await self.send_response(message, f"Could not resolve zipcode {location_arg}")
                        return False
                except Exception as e:
                    self.logger.debug(f"Zipcode lookup failed: {e}")
                    await self.send_response(message, f"Could not resolve zipcode {location_arg}")
                    return False
            else:
                if not self._wx:
                    await self.send_response(message, 'City lookup unavailable')
                    return False
                try:
                    # city_to_lat_lon returns (lat, lon, address_info)
                    result = self._wx.city_to_lat_lon(location_arg)
                    lat, lon = result[0], result[1]
                    if lat is None or lon is None:
                        await self.send_response(message, f"Could not resolve location {location_arg}")
                        return False
                except Exception as e:
                    self.logger.debug(f"City lookup failed: {e}")
                    await self.send_response(message, f"Could not resolve location {location_arg}")
                    return False

        if not self._wx:
            await self.send_response(message, 'Weather provider unavailable')
            return False

        try:
            periods, points = self._wx.get_noaa_weather(lat, lon, return_periods=True)
        except Exception as e:
            self.logger.debug(f"get_noaa_weather failed: {e}")
            try:
                human = await self._wx.get_weather_for_location(f"{lat},{lon}", 'coordinates', message=message)
                await self.send_response(message, human)
                return True
            except Exception:
                await self.send_response(message, 'Error fetching weather data')
                return False

        if not periods:
            await self.send_response(message, 'No forecast data available')
            return False

        assert lat is not None and lon is not None
        station = self._station_name(points or {}, lat, lon, content)

        # Fetch observation data for the current period (real wind gusts, etc.)
        obs = {}
        try:
            obs = self._wx.get_observation_data(points) if points else {}
        except Exception:
            obs = {}

        # Current period
        current = periods[0]
        current_tokens = []

        # Wind: prefer observation gust over period windGust
        obs_gust_raw = obs.get('wind_gusts')  # mph string from observation
        wf = self._wind_field(
            current.get('windDirection'),
            current.get('windSpeed'),
            gust_raw=obs_gust_raw or current.get('windGust'),
        )
        short = current.get('shortForecast', '')
        wx = self._wx_codes(short)
        cloud = self._cloud_field(short)
        t_val = self._temp_c(current.get('temperature'), current.get('temperatureUnit', 'F'))

        if wf:
            current_tokens.append(wf)
        if wx:
            current_tokens.append(wx)
        if cloud:
            current_tokens.append(cloud)
        if t_val is not None:
            prefix = 'M' if t_val < 0 else ''
            current_tokens.append(f"{prefix}{abs(t_val):02d}C")

        # Precipitation probability for current period
        pop = None
        try:
            pop = current.get('probabilityOfPrecipitation', {}).get('value')
        except Exception:
            pass
        if pop is not None and int(pop) > 0:
            current_tokens.append(f"PROB{int(pop)}")

        taf_parts = [station]
        if current_tokens:
            taf_parts.append(' '.join(current_tokens))

        # Up to 3 look-ahead periods
        for period in periods[1:4]:
            encoded = self._encode_period(period, include_wind=True)
            if encoded:
                taf_parts.append(encoded)

        result = '\n'.join(taf_parts)

        await self.send_response(message, result)
        return True
