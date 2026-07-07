#!/usr/bin/env python3
"""
Community override of the `wx` command that emits a short, NWS-trim compact
forecast string (no emoji). Respects configured units and attempts to include
wind, visibility, humidity, PoP, gusts and pressure where available.

Format example:
City Tonight: Patchy smoke then partly cloudy. Wind S@8G25mph Vis 9mi 18%RH PoP20% P:1020mb | Tue: Areas of smoke H:93F L:88F
"""

import re
from typing import Optional

from modules.commands.base_command import BaseCommand
from modules.models import MeshMessage

# Import the upstream WxCommand to reuse its data-fetching helpers
try:
    from modules.commands.wx_command import WxCommand as UpstreamWxCommand
except Exception:
    UpstreamWxCommand = None


class WxCommand(BaseCommand):
    """Compact NWS-trim `wx` command (community override)."""

    name = "wx"
    keywords = ["wx", "weather", "wxa", "wxalert"]
    description = "Compact weather output (community override)"
    category = "weather"

    def __init__(self, bot):
        super().__init__(bot)
        self.enabled = self.get_config_value('Wx_Command', 'enabled', fallback=True, value_type='bool')
        self._wx = UpstreamWxCommand(bot) if UpstreamWxCommand is not None else None

    def can_execute(self, message: MeshMessage, skip_channel_check: bool = False) -> bool:
        if not self.enabled:
            return False
        return super().can_execute(message)

    def _deg_to_cardinal_8(self, deg: Optional[float]) -> Optional[str]:
        if deg is None:
            return None
        try:
            d = float(deg) % 360
        except Exception:
            return None
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        idx = int((d + 22.5) / 45) % 8
        return dirs[idx]

    def _text_to_cardinal(self, text: str) -> Optional[str]:
        if not text:
            return None
        t = text.strip().lower()
        mapping = {
            'north': 'N', 'n': 'N',
            'northeast': 'NE', 'ne': 'NE',
            'east': 'E', 'e': 'E',
            'southeast': 'SE', 'se': 'SE',
            'south': 'S', 's': 'S',
            'southwest': 'SW', 'sw': 'SW',
            'west': 'W', 'w': 'W',
            'northwest': 'NW', 'nw': 'NW'
        }
        return mapping.get(t) or mapping.get(t.replace(' ', ''))

    def _cardinal_from_wind(self, wind_dir_raw: Optional[str]) -> Optional[str]:
        if wind_dir_raw is None:
            return None
        # numeric degrees?
        try:
            return self._deg_to_cardinal_8(float(wind_dir_raw))
        except Exception:
            pass
        # textual
        return self._text_to_cardinal(wind_dir_raw)

    def _extract_first_number(self, s: Optional[str]) -> Optional[int]:
        if not s:
            return None
        m = re.search(r"(\d+)", s)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    def _convert_wind(self, mph: float, target_unit: str) -> int:
        try:
            if target_unit in ('kph', 'kmh'):
                return int(round(mph * 1.609344))
            if target_unit in ('kt', 'kts', 'knots'):
                return int(round(mph * 0.868976))
            # default mph
            return int(round(mph))
        except Exception:
            return int(round(mph))

    def _format_temp_unit(self, units_token: str) -> str:
        # units_token from NOAA typically 'F' or 'C'
        if not units_token:
            return 'F'
        return units_token.upper().replace('°', '')

    async def execute(self, message: MeshMessage) -> bool:
        if not self._wx:
            await self.send_response(message, 'Weather provider unavailable')
            return True

        content = message.content.strip()
        parts = content.split()

        # Location parsing (reuse upstream helpers when possible)
        using_companion_location = False
        if len(parts) < 2:
            # try companion / defaults like upstream
            companion = self._wx._get_companion_location(message)
            if companion:
                parts = [parts[0], f"{companion[0]},{companion[1]}"]
                using_companion_location = True
            else:
                # fall back to upstream behavior for default city / bot loc
                # delegate to upstream execute which handles defaults
                return await self._wx.execute(message)

        # allow same location parsing logic as upstream
        location_arg = ' '.join(parts[1:]).strip()

        # Determine coordinates
        lat = lon = None
        coords_match = re.match(r'^\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*$', location_arg)
        if coords_match:
            try:
                lat = float(coords_match.group(1))
                lon = float(coords_match.group(2))
            except Exception:
                lat = lon = None
        elif re.match(r'^\d{5}$', location_arg):
            lat, lon = self._wx.zipcode_to_lat_lon(location_arg)
        else:
            res = self._wx.city_to_lat_lon(location_arg)
            if isinstance(res, tuple) and len(res) >= 2:
                lat, lon = res[0], res[1]

        if lat is None or lon is None:
            await self.send_response(message, self.translate('commands.wx.no_location', default='No location provided'))
            return True

        # Fetch NOAA periods & points
        try:
            periods, points = self._wx.get_noaa_weather(lat, lon, return_periods=True)
        except Exception as e:
            self.logger.debug(f"get_noaa_weather failed: {e}")
            # fall back to upstream human readable
            human = await self._wx.get_weather_for_location(f"{lat},{lon}", 'coordinates', message=message)
            await self.send_response(message, human)
            return True

        if not periods:
            await self.send_response(message, 'No forecast data available')
            return True

        # Observation data
        obs = {}
        try:
            obs = self._wx.get_observation_data(points) if points else {}
        except Exception:
            obs = {}

        # Location display: prefer city name, omit state
        location_name = None
        try:
            rel = points.get('relativeLocation', {}) if points else None
            if rel:
                props = rel.get('properties', {})
                city = props.get('city', {}).get('name') if props else None
                if city:
                    location_name = city
        except Exception:
            location_name = None

        if not location_name:
            try:
                locstr = self._wx._coordinates_to_location_string(lat, lon) or ''
                if locstr:
                    # strip state if present
                    location_name = locstr.split(',')[0].strip()
            except Exception:
                location_name = None

        if not location_name:
            # fallback to zipcode numeric or lat/lon short
            location_name = f"{round(lat,2)},{round(lon,2)}"

        # Current / primary period
        current = periods[0]
        period_name_raw = (current.get('name') or '').lower()
        is_night = 'night' in period_name_raw or 'tonight' in period_name_raw
        period_label = 'Tonight' if is_night else self._wx._noaa_period_display_name(current)

        short_forecast = current.get('shortForecast') or ''
        detailed = current.get('detailedForecast') or ''

        # Build narrative (prefer shortForecast; abbreviate if very long)
        narrative = short_forecast.strip()
        if not narrative:
            # fallback to first sentence of detailed
            narrative = (detailed.split('.'))[0].strip()

        # Tokens
        tokens = []

        # WIND: prefer period wind, fallback to observation data where available
        wind_dir_raw = current.get('windDirection') or None
        wind_speed_raw = current.get('windSpeed') or ''

        # obs may not include wind speed/direction; try other keys
        if not wind_dir_raw:
            wind_dir_raw = obs.get('wind_direction') or obs.get('wind_dir')

        if not wind_speed_raw:
            wind_speed_raw = obs.get('wind_speed') or obs.get('wind_mph') or ''

        wind_dir_card = self._cardinal_from_wind(wind_dir_raw)
        wind_num = self._extract_first_number(wind_speed_raw)

        cfg_wind_unit = self.bot.config.get('Weather', 'wind_speed_unit', fallback='mph').lower()

        gust_val = None
        # obs gusts are mph from upstream get_observation_data
        if 'wind_gusts' in obs and obs.get('wind_gusts'):
            try:
                gust_val = int(obs.get('wind_gusts'))
            except Exception:
                gust_val = None
        else:
            # try extract from detailed text
            g = self._wx.extract_wind_gusts(detailed) if hasattr(self._wx, 'extract_wind_gusts') else None
            try:
                gust_val = int(g) if g else None
            except Exception:
                gust_val = None

        wind_field = ''
        if wind_dir_card and wind_num is not None:
            # assume NOAA wind numbers are in mph; convert to configured unit
            speed_converted = self._convert_wind(float(wind_num), cfg_wind_unit)
            gust_converted = None
            if gust_val is not None:
                gust_converted = self._convert_wind(float(gust_val), cfg_wind_unit)

            # Build string like S@8G25mph (no space, gust appended then unit)
            unit_suffix = cfg_wind_unit if cfg_wind_unit not in ('kph', 'kmh') else 'kph'
            wind_field = f"{wind_dir_card}@{speed_converted}"
            if gust_converted:
                wind_field += f"G{gust_converted}"
            wind_field += unit_suffix
            tokens.append(f"Wind {wind_field}")

        # Visibility
        vis = None
        if 'visibility' in obs and obs.get('visibility'):
            vis = obs.get('visibility')
        else:
            vis = self._wx.extract_visibility(detailed) if hasattr(self._wx, 'extract_visibility') else None
        if vis:
            tokens.append(f"Vis {vis}mi")

        # Humidity
        humidity = obs.get('humidity') or (self._wx.extract_humidity(detailed) if hasattr(self._wx, 'extract_humidity') else None)
        if humidity:
            tokens.append(f"{humidity}%RH")

        # Precip probability
        pop = None
        # NOAA JSON sometimes provides probabilityOfPrecipitation
        pop_val = current.get('probabilityOfPrecipitation', {})
        if isinstance(pop_val, dict):
            pop = pop_val.get('value')
        if not pop:
            p = self._wx.extract_precip_probability(detailed) if hasattr(self._wx, 'extract_precip_probability') else None
            try:
                pop = int(p) if p else None
            except Exception:
                pop = None
        if pop:
            tokens.append(f"Precip{int(pop)}%")

        # Pressure (use observation pressure in hPa -> mb)
        pressure = obs.get('pressure') or (self._wx.extract_pressure(detailed) if hasattr(self._wx, 'extract_pressure') else None)
        if pressure:
            # pressure is hPa (mb) already from get_observation_data
            tokens.append(f"P:{pressure}mb")

        # Tonight/Day summary: try to attach next period's high/low
        day_summary = ''
        # Prepare temperature token (show immediately after narrative)
        temp_token = None
        cur_temp = current.get('temperature')
        if cur_temp is not None:
            cur_unit = (current.get('temperatureUnit') or 'F').upper()
            cur_unit_token = cur_unit.replace('°', '')
            try:
                if is_night:
                    temp_token = f"T:{int(round(float(cur_temp)))}{cur_unit_token}"
                else:
                    temp_token = f"T:{int(round(float(cur_temp)))}{cur_unit_token}"
            except Exception:
                temp_token = None
        if len(periods) > 1:
            nextp = periods[1]
            # prefer extract_high_low from detailed forecast
            temp_unit = (nextp.get('temperatureUnit') or 'F').upper()
            units_token = self._format_temp_unit(temp_unit)
            hl = self._wx.extract_high_low(nextp.get('detailedForecast', ''), units_str=(units_token if units_token else 'F')) if hasattr(self._wx, 'extract_high_low') else ''
            # fallback: use temperature value
            if not hl:
                h = nextp.get('temperature')
                if h is not None:
                    hl = f"H:{int(round(h))}{units_token}"
            if hl:
                # keep day name short
                day_name = self._wx._noaa_period_display_name(nextp)
                day_summary = f"| {day_name}: {self._wx.abbreviate_noaa(nextp.get('shortForecast',''))} {hl}"

        # Assemble final message (insert temperature token immediately after narrative)
        head = f"{location_name} {period_label}: {narrative}."
        if temp_token:
            head = f"{head} {temp_token}"
        rest = ' '.join(tokens)
        if rest:
            out = f"{head} {rest}"
        else:
            out = head
        if day_summary:
            out = f"{out} {day_summary}"

        await self.send_response(message, out)
        return True
