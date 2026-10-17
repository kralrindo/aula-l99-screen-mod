"""Feed real weather (and clock / CPU load) to the AULA L99 screen on Windows.

The L99 screen already has a weather + CPU/GPU readout on its "Real-time Data"
page. AULA's own driver fills it from a Chinese weather site, which is why it
is blank or wrong outside China. This script fills the same fields from
Open-Meteo (free, no API key) for any city you choose.

How it talks to the keyboard: the same 64-byte HID feature-report "clock"
packet (opcode 0x28) the vendor driver sends on the keyboard's vendor
interface (VID 0C45, PID 800A, interface 3). Protocol credit: the
reverse-engineering in github.com/gavindi/Aula_L99_Linux (re_notes/
system_monitor_block.md); this is an independent Windows implementation.

Setup (once):   pip install hidapi psutil
Run:            python l99_weather.py --city "Istanbul"
Test first:     python l99_weather.py --city "Istanbul" --dry-run
Options:        --once (send one update and exit), --list (show HID devices)

Notes
 * Close AULA's driver while this runs, or it will overwrite the values.
 * USB cable only. The 2.4 GHz dongle uses a different, less-tested packet.
 * The screen shows temperatures as unsigned bytes: below 0 °C it would show
   garbage (e.g. -1 -> "55"), so negatives are sent as 0 for now.
 * The screen only knows 6 weather types: cloudy, clear, light snow, thunder,
   rain, heavy snow. Fog/drizzle etc. are mapped to the nearest one.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

VID, PID, IFACE = 0x0C45, 0x800A, 3
PACKET = 64

OP_BEGIN, OP_COMMIT, OP_RTC, OP_END = 0x18, 0x02, 0x28, 0xF0

COND_CLOUDY, COND_CLEAR, COND_LIGHT_SNOW, COND_THUNDER, COND_RAIN, COND_HEAVY_SNOW = range(6)
COND_NAMES = ["bulutlu", "açık", "hafif kar", "fırtına", "yağmur", "yoğun kar"]


# ---------------------------------------------------------------- weather ---
def wmo_to_l99(code: int) -> int:
    """Map an Open-Meteo WMO weather code onto the 6 icons the screen has."""
    if code in (0, 1):
        return COND_CLEAR
    if code in (95, 96, 99):
        return COND_THUNDER
    if code in (75, 86):
        return COND_HEAVY_SNOW
    if code in (71, 73, 77, 85):
        return COND_LIGHT_SNOW
    if 51 <= code <= 67 or 80 <= code <= 82:
        return COND_RAIN
    return COND_CLOUDY  # 2, 3 (cloudy) and 45, 48 (fog)


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as response:
        return json.load(response)


def find_city(name: str) -> tuple[float, float, str]:
    query = urllib.parse.urlencode({"name": name, "count": 1, "language": "tr"})
    results = get_json(f"https://geocoding-api.open-meteo.com/v1/search?{query}").get("results")
    if not results:
        sys.exit(f"City not found: {name}")
    r = results[0]
    return r["latitude"], r["longitude"], f"{r['name']}, {r.get('country', '')}"


def fetch_weather(lat: float, lon: float) -> dict:
    query = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1,
        "current": "temperature_2m,relative_humidity_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min",
    })
    data = get_json(f"https://api.open-meteo.com/v1/forecast?{query}")
    cur, day = data["current"], data["daily"]
    return {
        "temp": round(cur["temperature_2m"]),
        "high": round(day["temperature_2m_max"][0]),
        "low": round(day["temperature_2m_min"][0]),
        "humidity": round(cur["relative_humidity_2m"]),
        "condition": wmo_to_l99(int(cur["weather_code"])),
        "wmo": int(cur["weather_code"]),
    }


def screen_byte(value: int) -> int:
    """The panel shows the byte literally (unsigned), so clamp to 0..255."""
    return max(0, min(255, int(value)))


def cpu_load() -> int:
    try:
        import psutil
        return round(psutil.cpu_percent(interval=None))
    except ImportError:
        return 0


# ------------------------------------------------------------- the packet ---
def command(opcode: int, blocks: int = 0) -> bytes:
    p = bytearray(PACKET)
    p[0], p[1], p[8] = 0x04, opcode, blocks
    return bytes(p)


def rtc_block(now: datetime, w: dict | None, cpu: int) -> bytes:
    b = bytearray(PACKET)
    b[1] = 1                   # "view" index; any value >= 1 lands on the real-time page
    b[2] = 0x5A
    b[3] = now.year - 2000
    b[4], b[5] = now.month, now.day
    b[6], b[7], b[8] = now.hour, now.minute, now.second
    b[10] = now.isoweekday() % 7          # Sunday = 0
    b[13] = screen_byte(cpu)              # CPU load %
    # b[14] CPU temp, b[15] GPU load, b[16] GPU temp: left 0 (need a sensor library)
    if w:
        b[17] = screen_byte(w["temp"])
        b[18] = screen_byte(w["high"])
        b[19] = screen_byte(w["low"])
        b[20] = w["condition"]
        b[21] = screen_byte(w["humidity"])
    b[62], b[63] = 0xAA, 0x55
    return bytes(b)


# ------------------------------------------------------------------- USB ---
def open_keyboard():
    try:
        import hid
    except ImportError:
        sys.exit("Missing library: run  pip install hidapi")
    for info in hid.enumerate(VID, PID):
        if info.get("interface_number") == IFACE:
            dev = hid.device()
            dev.open_path(info["path"])
            return dev
    sys.exit("L99 not found on USB cable (0C45:800A interface 3). Is it plugged in by cable?")


def transact(dev, packet: bytes, expect_reply: bool) -> None:
    for attempt in range(20):
        dev.send_feature_report(bytes([0]) + packet)
        time.sleep(0.01)
        if not expect_reply:
            return
        reply = bytes(dev.get_feature_report(0, PACKET + 1))
        if reply[:1] == b"\x00" and reply[1:2] == b"\x04":
            reply = reply[1:]  # strip the report-id byte if hidapi returned it
        time.sleep(0.01)
        if len(reply) > 3 and reply[0] == 0x04 and reply[1] == packet[1] and reply[3] & 0x01:
            return
        time.sleep(0.05)
    print(f"warning: no acknowledgement for opcode 0x{packet[1]:02X}", file=sys.stderr)


def send_update(dev, block: bytes) -> None:
    transact(dev, command(OP_BEGIN), True)
    transact(dev, command(OP_RTC, 1), True)
    transact(dev, block, False)
    transact(dev, command(OP_COMMIT), True)
    transact(dev, command(OP_END), True)


# ------------------------------------------------------------------- main ---
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", default="Istanbul")
    ap.add_argument("--every", type=float, default=5, help="seconds between screen updates")
    ap.add_argument("--weather-minutes", type=float, default=15)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="fetch weather and print the packet; no USB")
    ap.add_argument("--list", action="store_true", help="list AULA HID interfaces and exit")
    args = ap.parse_args()

    if args.list:
        import hid
        for i in hid.enumerate(VID, PID):
            print(i["interface_number"], i["usage_page"], i["path"])
        return

    lat, lon, place = find_city(args.city)
    print(f"Konum: {place} ({lat:.2f}, {lon:.2f})")
    dev = None if args.dry_run else open_keyboard()

    weather, fetched = None, 0.0
    try:
        while True:
            if time.monotonic() - fetched > args.weather_minutes * 60 or weather is None:
                try:
                    weather = fetch_weather(lat, lon)
                    fetched = time.monotonic()
                    print(f"{datetime.now():%H:%M} hava: {weather['temp']}°C "
                          f"(en yüksek {weather['high']}, en düşük {weather['low']}), "
                          f"nem %{weather['humidity']}, {COND_NAMES[weather['condition']]} (WMO {weather['wmo']})")
                    if min(weather["temp"], weather["low"]) < 0:
                        print("  note: below-zero values are shown as 0 on the screen")
                except Exception as exc:  # keep the clock going if the network blips
                    print(f"weather fetch failed: {exc}", file=sys.stderr)
                    fetched = time.monotonic() - args.weather_minutes * 60 + 60  # retry in 1 min
            block = rtc_block(datetime.now(), weather, cpu_load())
            if args.dry_run:
                print("packet:", block.hex(" "))
                return
            send_update(dev, block)
            if args.once:
                print("sent.")
                return
            time.sleep(args.every)
    except KeyboardInterrupt:
        pass
    finally:
        if dev is not None:
            dev.close()


if __name__ == "__main__":
    main()
