#!/usr/bin/env python3
"""
Community override of the wx command that formats output as an aviation-style METAR.

This command delegates weather retrieval to the existing `WxCommand` and builds a
compact METAR-like string from observation/point data where available. If required
data is missing it falls back to the regular human-friendly summary.

Example METAR-like output:
MESH123 061830Z 27012KT P6SM -RA 15/12 A3005
    - Station: MESH123 (community station identifier)
    - Time: 06th day 18:30Z
    - Wind: 270° at 12 kt
    - Visibility: P6SM (>= 6 statute miles)
    - Weather: light rain (-RA)
    - Temp/Dew: 15/12 (°C or °F depending on configured units)
    - Altimeter: 30.05 inHg (A3005)
"""

from datetime import datetime
import math
import re
from typing import Optional

from modules.commands.base_command import BaseCommand
from modules.models import MeshMessage

# Import the upstream WxCommand to reuse its data-fetching helpers
try:
    from modules.commands.wx_command import WxCommand
except Exception:
    WxCommand = None


class WxMetarCommand(BaseCommand):
    """METAR-like weather command.

    Usage: wxmetar <zipcode|lat,lon|city>
    Keywords: wxmetar, wx-metar, wxm
    """

    # METAR-specific command (does not override `wx`)
    name = "metar"
    keywords = ["metar", "wxmetar", "wx-metar", "wxm"]
    description = "Produce a METAR-like compact weather string"
    category = "weather"

    short_description = "Get METAR-like weather for a location"
    usage = "wxmetar <zipcode|lat,lon|city>"
    examples = ["wxmetar 98101", "wxmetar 47.6,-122.3"]

    def __init__(self, bot):
        super().__init__(bot)
        self.enabled = self.get_config_value('WxMetar_Command', 'enabled', fallback=True, value_type='bool')
        # Create a helper instance of the upstream WxCommand if available
        self._wx = WxCommand(bot) if WxCommand is not None else None

    def can_execute(self, message: MeshMessage, skip_channel_check: bool = False) -> bool:
        if not self.enabled:
            return False
        return super().can_execute(message)

    def get_help_text(self) -> str:
        return self.translate('commands.wxmetar.description') if self.bot.config.has_section('Keywords') else self.short_description

    def _parse_coordinates(self, text: str) -> Optional[tuple[float, float]]:
        m = re.match(r'^\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*$', text)
        if not m:
            return None
        try:
            lat = float(m.group(1))
            lon = float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return (lat, lon)
        except Exception:
            return None
        return None

    async def execute(self, message: MeshMessage) -> bool:
        content = message.content.strip()
        parts = content.split()

        # Expect at least the keyword + location
        if len(parts) < 2:
            # Try to use companion location if upstream helper available
            if self._wx:
                loc = self._wx._get_companion_location(message)
                if loc:
                    lat, lon = loc
                else:
                    await self.send_response(message, self.translate('commands.wx.no_location', default='No location provided'))
                    return False
            else:
                await self.send_response(message, 'No location provided')
                return False
        else:
            location_arg = ' '.join(parts[1:]).strip()
            coords = self._parse_coordinates(location_arg)
            if coords:
                lat, lon = coords
            elif re.match(r'^\d{5}$', location_arg):
                # zipcode
                if not self._wx:
                    await self.send_response(message, 'Zipcode lookup unavailable')
                    return False
                try:
                    lat, lon = self._wx.zipcode_to_lat_lon(location_arg)
                except Exception as e:
                    self.logger.debug(f"Zipcode lookup failed: {e}")
                    await self.send_response(message, f"Could not resolve zipcode {location_arg}")
                    return False
            else:
                # treat as city name
                if not self._wx:
                    await self.send_response(message, 'City lookup unavailable')
                    return False
                try:
                    lat, lon = self._wx.city_to_lat_lon(location_arg)
                except Exception as e:
                    self.logger.debug(f"City lookup failed: {e}")
                    await self.send_response(message, f"Could not resolve location {location_arg}")
                    return False

        # At this point we have lat/lon; fetch NOAA periods & points data
        if not self._wx:
            await self.send_response(message, 'Weather provider unavailable')
            return False

        try:
            periods, points = self._wx.get_noaa_weather(lat, lon, return_periods=True)
        except Exception as e:
            self.logger.debug(f"get_noaa_weather failed: {e}")
            # Fall back to human readable
            try:
                human = await self._wx.get_weather_for_location(f"{lat},{lon}", 'coordinates', message=message)
                await self.send_response(message, human)
                return True
            except Exception:
                await self.send_response(message, 'Error fetching weather data')
                return False

        # Try to get observation data if available
        obs = {}
        try:
            obs = self._wx.get_observation_data(points) if points else {}
        except Exception:
            obs = {}

        # Build a short, readable METAR-like output (no time, readable conditions)
        # Station identifier: prefer city name from points if present
        station = None
        try:
            rel = points.get('relativeLocation', {}) if points else None
            if rel:
                props = rel.get('properties', {})
                station = props.get('city', {}).get('name') if props else None
        except Exception:
            station = None
        if not station:
            try:
                station = self._wx._coordinates_to_location_string(lat, lon) if self._wx else None
            except Exception:
                station = None
        # Produce station display: prefer city name (no state), else zipcode if present, max 7 chars
        def make_station_display(city_name: Optional[str], message_text: str) -> str:
            # Prefer city name, strip state if present (split on comma)
            if city_name:
                try:
                    base = city_name.split(',')[0].strip()
                    # keep letters and digits only, remove spaces and punctuation
                    s = re.sub(r'[^A-Za-z0-9]', '', base).upper()
                    if s:
                        return s[:7]
                except Exception:
                    pass
            # Fallback: look for a 5-digit zipcode in the message text
            try:
                m = re.search(r'\b(\d{5})\b', message_text)
                if m:
                    return m.group(1)[:7]
            except Exception:
                pass
            # Final fallback: compact mesh id based on coords
            try:
                return f"MESH{int(abs(lat*100)%1000):03d}"[:7]
            except Exception:
                return 'MESH'

        station = make_station_display(station, content)

        # Wind: present as readable with METAR knot primary and configured unit in parens
        wind_dir = obs.get('wind_direction') or obs.get('wind_dir') or obs.get('wind_bearing')
        wind_speed = obs.get('wind_speed') or obs.get('wind_mph') or obs.get('wind_kph') or 0
        try:
            wind_speed = float(wind_speed)
        except Exception:
            wind_speed = 0.0
        # prefer knots for METAR primary display
        wind_kts = int(round(wind_speed * 0.868976)) if wind_speed else 0
        wind_dir_int = 0
        try:
            wind_dir_int = int(round(float(wind_dir))) if wind_dir is not None else 0
        except Exception:
            wind_dir_int = 0
        # configured wind unit (mph/kph/kt)
        cfg_wind_unit = self.bot.config.get('Weather', 'wind_speed_unit', fallback='mph').lower()
        wind_secondary = ''
        try:
            if cfg_wind_unit == 'mph':
                wind_secondary = f"{int(round(wind_kts / 0.868976)):d}mph" if wind_kts else ''
            elif cfg_wind_unit in ('kph', 'kmh'):
                wind_secondary = f"{int(round(wind_kts * 1.852)):d}kph" if wind_kts else ''
            elif cfg_wind_unit in ('kt', 'kts', 'knots'):
                wind_secondary = ''
        except Exception:
            wind_secondary = ''
        # METAR wind format: DDDSSKT or VRBSSKT; include gusts if present
        gust = obs.get('wind_gust') or obs.get('wind_gust_mph') or obs.get('wind_gust_kph')
        try:
            gust = int(round(float(gust))) if gust is not None else None
        except Exception:
            gust = None
        if wind_kts == 0:
            wind_field = '00000KT'
        else:
            dir_part = 'VRB' if wind_dir is None or str(wind_dir).lower() == 'variable' else f"{wind_dir_int:03d}"
            wind_field = f"{dir_part}{wind_kts:02d}KT"
            if gust and gust > 0:
                # convert gust to kt if input likely mph/kph
                try:
                    gust_kts = int(round(float(gust) * 0.868976))
                except Exception:
                    gust_kts = gust
                wind_field = f"{dir_part}{wind_kts:02d}G{gust_kts:02d}KT"

        # Temperature / Dew point: always display Celsius (append 'C')
        temp = obs.get('temperature') or obs.get('temp') or obs.get('air_temperature')
        dew = obs.get('dew_point') or obs.get('dewpoint')

        def to_celsius_int(val):
            try:
                v = float(val)
                return int(round((v - 32.0) * 5.0 / 9.0))
            except Exception:
                return None

        t_c = to_celsius_int(temp)
        d_c = to_celsius_int(dew)

        def metar_temp(v: Optional[int]) -> str:
            if v is None:
                return '//'  # unknown
            if v < 0:
                return f"M{abs(v):02d}"
            return f"{v:02d}"

        t_field = metar_temp(t_c)
        d_field = metar_temp(d_c)
        # Use Celsius and append unit only to the dew value (e.g., 18/03C)
        unit_letter = 'C'
        temp_dew_field = f"{t_field}/{d_field}{unit_letter}"

        # Visibility: produce SM (statute miles) as METAR uses SM in US
        vis = obs.get('visibility') or obs.get('visibility_mi') or obs.get('visibility_km')
        vis_out = ''
        try:
            if vis is not None:
                v = float(vis)
                # heuristics: if value > 10 assume km
                if v > 10:
                    v_mi = v * 0.621371
                else:
                    v_mi = v
                # round to nearest quarter mile
                frac = round(v_mi * 4) / 4.0
                if frac >= 6:
                    vis_out = '6SM'
                else:
                    # format fractions like 1/2, 1/4, etc.
                    whole = int(frac)
                    rem = frac - whole
                    frac_str = ''
                    if abs(rem - 0.75) < 0.01:
                        frac_str = '3/4'
                    elif abs(rem - 0.5) < 0.01:
                        frac_str = '1/2'
                    elif abs(rem - 0.25) < 0.01:
                        frac_str = '1/4'
                    if whole > 0 and frac_str:
                        vis_out = f"{whole} {frac_str}SM"
                    elif whole > 0:
                        vis_out = f"{whole}SM"
                    elif frac_str:
                        vis_out = f"{frac_str}SM"
                    else:
                        vis_out = f"{int(round(frac))}SM"
        except Exception:
            vis_out = ''

        # Human-readable weather condition -> METAR weather codes
        weather_text = obs.get('text') or obs.get('phenomena') or obs.get('weather') or ''
        if not weather_text and periods and len(periods) > 0:
            # fallback to shortForecast of first period
            try:
                weather_text = periods[0].get('shortForecast', '')
            except Exception:
                weather_text = ''
        # Build METAR weather code string
        def to_wx_codes(s: str) -> str:
            if not s:
                return ''
            s = s.lower()
            tokens = s.replace('-', ' ').replace('+', ' ').split()
            intensity = ''
            if '+' in s or 'heavy' in s:
                intensity = '+'
            elif '-' in s or 'light' in s:
                intensity = '-'
            codes = []
            if 'thunder' in s or 'ts' in s:
                codes.append('TS')
            if 'rain' in s or 'ra' in s:
                codes.append('RA')
            if 'drizzle' in s or 'dz' in s:
                codes.append('DZ')
            if 'snow' in s or 'sn' in s:
                codes.append('SN')
            if 'fog' in s or 'fg' in s:
                codes.append('FG')
            if 'mist' in s or 'br' in s:
                codes.append('BR')
            if 'haze' in s or 'hz' in s:
                codes.append('HZ')
            if 'smoke' in s or 'fu' in s:
                codes.append('FU')
            if 'sand' in s or 'sd' in s:
                codes.append('DU')
            if 'dust' in s:
                codes.append('DU')
            if 'squalls' in s or 'sq' in s:
                codes.append('SQ')
            if not codes:
                return ''
            return intensity + ''.join(codes)

        wx_codes = to_wx_codes(weather_text)

        # Pressure/Altimeter: prefer inHg (METAR Axxxx)
        alt = obs.get('pressure_in') or obs.get('altimeter') or obs.get('pressure')
        alt_field = ''
        try:
            if alt:
                alt_v = float(alt)
                if alt_v > 200:
                    # provided as hPa -> convert to inHg
                    alt_in = alt_v * 0.0295299830714
                else:
                    alt_in = float(alt_v)
                alt_field = f"A{int(round(alt_in * 100)):04d}"
        except Exception:
            alt_field = ''

        # Clouds: try to infer basic layer (SKC/FEW/SCT/BKN/OVC) from shortForecast
        cloud_layer = ''
        try:
            short = periods[0].get('shortForecast','').lower() if periods and len(periods) > 0 else ''
            if 'clear' in short or 'sunny' in short:
                cloud_layer = 'SKC'
            elif 'few' in short or 'partly' in short:
                cloud_layer = 'SCT'
            elif 'mostly' in short or 'intermittent clouds' in short:
                cloud_layer = 'BKN'
            elif 'overcast' in short or 'cloudy' in short:
                cloud_layer = 'OVC'
        except Exception:
            cloud_layer = ''

        parts_out = [station]
        if time_str:
            parts_out.append(time_str)
        if wind_field:
            parts_out.append(wind_field)
        if vis_out:
            parts_out.append(vis_out)
        if wx_codes:
            parts_out.append(wx_codes)
        if cloud_layer:
            parts_out.append(cloud_layer)
        # Temperature/dew in METAR style (unit only on last value)
        parts_out.append(temp_dew_field)
        if alt_field:
            parts_out.append(alt_field)

        metar = ' '.join([p for p in parts_out if p])

        # Remarks: include next-day items (short) when available
        try:
            remarks_parts = []
            if periods and len(periods) >= 3:
                next_day = periods[1]
                next_night = periods[2]
                name = (next_day.get('name') or 'Next')[:3].upper()
                h = next_day.get('temperature')
                l = next_night.get('temperature')
                if h is not None and l is not None:
                    try:
                        # convert to Celsius for RMK
                        hnum = to_celsius_int(h)
                        lnum = to_celsius_int(l)
                        if hnum is not None and lnum is not None:
                            remarks_parts.append(f"{name} H{hnum}C/L{lnum}C")
                    except Exception:
                        pass
                # include a short next-day weather code if we can
                try:
                    nf = next_day.get('shortForecast', '')
                    nf_code = to_wx_codes(nf)
                    if nf_code:
                        remarks_parts.append(nf_code)
                except Exception:
                    pass
            if remarks_parts:
                metar = f"{metar} RMK {' '.join(remarks_parts)}"
        except Exception:
            pass

        # If not enough data, fall back to human-friendly summary
        if (not obs or (t_c is None and d_c is None and wind_kts == 0 and not alt_field)):
            try:
                human = await self._wx.get_weather_for_location(f"{lat},{lon}", 'coordinates', message=message)
                await self.send_response(message, human)
                return True
            except Exception:
                await self.send_response(message, 'Insufficient data for METAR output')
                return False

        await self.send_response(message, metar)
        return True
