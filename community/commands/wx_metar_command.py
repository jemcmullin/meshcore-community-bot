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

    # Internal toggle: keep METAR default in Celsius unless explicitly set to use bot config.
    USE_BOT_CONFIG_TEMP_UNIT = True

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

    def _station_name(self, points: dict, lat: float, lon: float, raw_text: str) -> str:
        try:
            props = points.get('properties', {}).get('relativeLocation', {}).get('properties', {})
            city = props.get('city') or props.get('name') or ''
            if isinstance(city, dict):
                city = city.get('name', '')
            if city:
                clean = re.sub(r'[^A-Za-z0-9]', '', city.split(',')[0])
                if clean:
                    return clean[:10]
        except Exception:
            pass
        m = re.search(r'\b(\d{5})\b', raw_text)
        if m:
            return m.group(1)
        return f"MESH{int(abs(lat * 100) % 1000):03d}"

    def _period_label(self, period: str) -> str:
        if not period:
            return ''
        name = period.upper()
        label_map = {
            'THIS AFTERNOON': 'AFTN',
            'THIS MORNING': 'MORN',
            'TONIGHT': 'NIGHT',
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
            return label
        return ''

    @staticmethod
    def _round_wind_dir_degrees(degrees: float) -> int:
        normalized = 360.0 if float(degrees) == 360.0 else float(degrees) % 360.0
        rounded = int(math.floor((normalized + 2.5) / 5.0) * 5)
        if rounded == 0 and normalized >= 357.5:
            return 360
        return min(rounded, 360)

    @classmethod
    def _parse_wind_dir_degrees(cls, value) -> Optional[int]:
        if value is None:
            return None
        try:
            return cls._round_wind_dir_degrees(float(value))
        except Exception:
            pass
        s = str(value).strip().upper()
        dirs = {
            'N': 360.0, 'NNE': 22.5, 'NE': 45.0, 'ENE': 67.5,
            'E': 90.0, 'ESE': 112.5, 'SE': 135.0, 'SSE': 157.5,
            'S': 180.0, 'SSW': 202.5, 'SW': 225.0, 'WSW': 247.5,
            'W': 270.0, 'WNW': 292.5, 'NW': 315.0, 'NNW': 337.5,
        }
        for token in s.replace('-', ' ').split():
            if token in dirs:
                return cls._round_wind_dir_degrees(dirs[token])
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
        station = self._station_name(points or {}, lat, lon, content)

        # Time string handling temporarily disabled
        # time_str = ''
        # try:
        #     ts = None
        #     if obs:
        #         ts = obs.get('timestamp')
        #     if not ts and periods and len(periods) > 0:
        #         ts = periods[0].get('startTime')
        #     if ts:
        #         if isinstance(ts, str) and ts.endswith('Z'):
        #             ts = ts.replace('Z', '+00:00')
        #         dt = datetime.fromisoformat(ts)
        #         time_str = dt.strftime('%d%H%MZ')
        # except Exception:
        #     time_str = ''

        # Wind: prefer station observation fields, then fall back to first forecast period.
        period0 = periods[0] if periods and len(periods) > 0 else {}
        wind_dir = (
            obs.get('wind_direction') or obs.get('wind_dir') or obs.get('wind_bearing')
            or period0.get('windDirection')
        )
        wind_speed_raw = (
            obs.get('wind_speed') or obs.get('wind_mph') or obs.get('wind_kph')
            or period0.get('windSpeed') or 0
        )

        def parse_speed_to_kts(value) -> int:
            if value is None:
                return 0
            try:
                # Bare numerics are treated as mph to match upstream observation parsing.
                if isinstance(value, (int, float)):
                    return int(round(float(value) * 0.868976))
                s = str(value).strip().lower()
                m = re.search(r'-?\d+(?:\.\d+)?', s)
                if not m:
                    return 0
                v = float(m.group(0))
                if 'kt' in s or 'knot' in s:
                    factor = 1.0
                elif 'km/h' in s or 'kph' in s or ('km' in s and '/h' in s):
                    factor = 0.539957
                elif 'm/s' in s or 'mps' in s:
                    factor = 1.94384
                else:
                    factor = 0.868976  # mph
                return int(round(v * factor))
            except Exception:
                return 0

        wind_kts = parse_speed_to_kts(wind_speed_raw)
        wind_dir_int = self._parse_wind_dir_degrees(wind_dir)

        # METAR wind format: DDDSSKT or VRBSSKT; include gusts if present.
        gust_raw = (
            obs.get('wind_gust') or obs.get('wind_gust_mph') or obs.get('wind_gust_kph')
            or obs.get('wind_gusts') or period0.get('windGust')
        )
        gust_kts = parse_speed_to_kts(gust_raw)
        if wind_kts == 0:
            wind_field = 'CALM'
        else:
            is_variable = wind_dir is None or 'variable' in str(wind_dir).lower() or 'vrb' in str(wind_dir).lower()
            dir_part = 'VRB' if is_variable or wind_dir_int is None else f"{wind_dir_int:03d}"
            wind_field = f"{dir_part}@{wind_kts:02d}kt"
            # Sanity check: gusts max 2.5× base wind or 55kt absolute (caps conversion errors from NOAA data format)
            max_realistic_gust = max(wind_kts + 40, int(wind_kts * 2.5))
            if gust_kts > wind_kts + 5 and gust_kts > 11 and gust_kts <= min(max_realistic_gust, 75):
                wind_field = f"{dir_part}@{wind_kts:02d}G{gust_kts:02d}kt"

        # Temperature / Dew point: preserve source units and omit missing values.
        temp = obs.get('temperature') or obs.get('temp') or obs.get('air_temperature')
        dew = obs.get('dew_point') or obs.get('dewpoint')

        # Temperature can be absent in observation data; fall back to period temperature.
        period_temp = None
        period_temp_unit = None
        try:
            if periods and len(periods) > 0:
                period_temp = periods[0].get('temperature')
                period_temp_unit = periods[0].get('temperatureUnit')
        except Exception:
            period_temp = None
            period_temp_unit = None

        def parse_temp_int(val):
            try:
                return int(round(float(val)))
            except Exception:
                return None

        def normalize_temp_unit(unit: Optional[str], fallback: str = 'F') -> str:
            if not unit:
                return fallback
            u = str(unit).strip().upper()
            if u.startswith('F'):
                return 'F'
            if u.startswith('C'):
                return 'C'
            if u.startswith('K'):
                return 'K'
            return fallback

        def convert_temp_value(value: Optional[int], from_unit: str, to_unit: str) -> Optional[int]:
            if value is None:
                return None
            fu = (from_unit or '').upper()
            tu = (to_unit or '').upper()
            if fu == tu:
                return value
            try:
                v = float(value)
                if fu == 'F' and tu == 'C':
                    return int(round((v - 32.0) * 5.0 / 9.0))
                if fu == 'C' and tu == 'F':
                    return int(round((v * 9.0 / 5.0) + 32.0))
                if fu == 'K' and tu == 'C':
                    return int(round(v - 273.15))
                if fu == 'C' and tu == 'K':
                    return int(round(v + 273.15))
                if fu == 'F' and tu == 'K':
                    c = (v - 32.0) * 5.0 / 9.0
                    return int(round(c + 273.15))
                if fu == 'K' and tu == 'F':
                    c = v - 273.15
                    return int(round((c * 9.0 / 5.0) + 32.0))
            except Exception:
                return value
            return value

        if self.USE_BOT_CONFIG_TEMP_UNIT:
            cfg_temp_unit = self.bot.config.get('Weather', 'temperature_unit', fallback='fahrenheit').lower()
            default_unit = 'C' if cfg_temp_unit.startswith('c') else 'F'
        else:
            default_unit = 'C'

        t_val = parse_temp_int(temp)
        if t_val is None:
            t_val = parse_temp_int(period_temp)

        d_val = parse_temp_int(dew)

        t_unit = normalize_temp_unit(
            obs.get('temperature_unit') or obs.get('temp_unit') or obs.get('air_temperature_unit') or period_temp_unit,
            fallback=default_unit,
        )
        d_unit = normalize_temp_unit(
            obs.get('dew_point_unit') or obs.get('dewpoint_unit') or obs.get('temperature_unit') or obs.get('temp_unit') or period_temp_unit,
            fallback=t_unit,
        )

        # Internal mode: always output Celsius values, converting numeric fields as needed.
        if not self.USE_BOT_CONFIG_TEMP_UNIT:
            t_val = convert_temp_value(t_val, t_unit, 'C')
            d_val = convert_temp_value(d_val, d_unit, 'C')
            t_unit = 'C'
            d_unit = 'C'

        def metar_temp(v: Optional[int]) -> str:
            if v is None:
                return ''
            if v < 0:
                return f"M{abs(v):02d}"
            return f"{v:02d}"

        t_field = metar_temp(t_val)
        d_field = metar_temp(d_val)

        if t_field and d_field:
            if t_unit == d_unit:
                temp_dew_field = f"{t_field}/{d_field}{t_unit}"
            else:
                temp_dew_field = f"{t_field}{t_unit}/{d_field}{d_unit}"
        elif t_field:
            temp_dew_field = f"{t_field}{t_unit}"
        elif d_field:
            temp_dew_field = f"DP{d_field}{d_unit}"
        else:
            temp_dew_field = ''

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
                # NOAA/NWS observation sources typically report visibility up to 10 SM.
                # Use 10SM as the explicit max
                if frac >= 10:
                    vis_out = '10SM'
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
        # time_str temporarily omitted from METAR output
        # if time_str:
        #     parts_out.append(time_str)
        if wind_field:
            parts_out.append(wind_field)
        if vis_out:
            parts_out.append(vis_out)
        if wx_codes:
            parts_out.append(wx_codes)
        if cloud_layer:
            parts_out.append(cloud_layer)
        if temp_dew_field:
            parts_out.append(temp_dew_field)
        if alt_field:
            parts_out.append(alt_field)

        metar = ' '.join([p for p in parts_out if p])

        # Remarks: include next-period items (short) when available
        try:
            remarks_parts = []
            if periods and len(periods) >= 2:
                # Try to find a sensible pair: prefer a daytime/nighttime pair or just the next available period
                period_to_use = None
                temp_code = None
                
                # Look for a period with both isDaytime and isTodaysDaytime fields, or just use next period
                for idx in range(1, min(4, len(periods))):  # Check next 2-3 periods
                    p = periods[idx]
                    if p and p.get('temperature') is not None:
                        period_to_use = p
                        break
                
                if period_to_use:
                    try:
                        ptemp = parse_temp_int(period_to_use.get('temperature'))
                        pname = self._period_label(period_to_use.get('name') or 'Next')
                        punit = normalize_temp_unit(period_to_use.get('temperatureUnit'), fallback=default_unit)

                        if not self.USE_BOT_CONFIG_TEMP_UNIT:
                            ptemp = convert_temp_value(ptemp, punit, 'C')
                            punit = 'C'
                        
                        if ptemp is not None:
                            # Just report this one period's temp; don't assume day/night pairing
                            remarks_parts.append(f"{pname} {ptemp}{punit}")
                        
                        # Include weather code if available
                        sf = period_to_use.get('shortForecast', '')
                        wx = to_wx_codes(sf)
                        if wx:
                            remarks_parts.append(wx)
                    except Exception:
                        pass
            
            if remarks_parts:
                metar = f"{metar} | {' '.join(remarks_parts)}"
        except Exception:
            pass

        # If not enough data, fall back to human-friendly summary
        if (not obs or (t_val is None and d_val is None and wind_kts == 0 and not alt_field)):
            try:
                human = await self._wx.get_weather_for_location(f"{lat},{lon}", 'coordinates', message=message)
                await self.send_response(message, human)
                return True
            except Exception:
                await self.send_response(message, 'Insufficient data for METAR output')
                return False

        await self.send_response(message, metar)
        return True
