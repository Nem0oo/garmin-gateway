"""Builds the FIT test fixtures and the reference REST response.

Run from the repository root (inside Docker, see README):
    python tests/fixtures/make_fit.py

Outputs, all in tests/fixtures/ and committed:
- activity_power.zip: 60 records with power, temperature, heart rate, enhanced speed/altitude,
  distance, cadence + fractional cadence; one record without power, one without heart rate,
  and a 5 s pause, zipped like Garmin's download (one `<id>_ACTIVITY.fit` member).
- activity_no_power.zip: 30 records with plain speed/altitude, no power, no fractional cadence.
- series_expected.json: response of GET /api/v1/activities/21234567890/series for activity_power.zip.

Encoding uses the official SDK's Encoder (garmin-fit-sdk, already a runtime dependency).
Every value is chosen on the FIT field's resolution (speed in mm/s, distance in cm, altitude in
0.2 m steps, fractional cadence in 1/128), and the expected JSON is computed here from those raw
values, independently of garmin_gateway.garmin.fit.
"""
import datetime as dt
import json
import zipfile
from pathlib import Path

from garmin_fit_sdk import Encoder, Profile

HERE = Path(__file__).resolve().parent
ACTIVITY_ID = 21234567890
START = dt.datetime(2026, 11, 2, 7, 1, 12, tzinfo=dt.timezone.utc)
# Fixed member date so the zips are byte-for-byte reproducible.
ZIP_DATE = (2026, 11, 2, 8, 0, 0)

FILE_ID = Profile["mesg_num"]["FILE_ID"]
RECORD = Profile["mesg_num"]["RECORD"]


def _power_records() -> list[dict]:
    """Raw values per record (integers on the FIT field resolution)."""
    recs, distance_cm = [], 0
    for i in range(60):
        # 5 s pause between records 29 and 30.
        offset = i if i < 30 else i + 4
        speed_mm_s = 2800 + 10 * (i % 7)
        distance_cm += speed_mm_s // 10
        recs.append(
            {
                "t": START + dt.timedelta(seconds=offset),
                "speed_mm_s": speed_mm_s,
                "distance_cm": distance_cm,
                "altitude_raw": 3262 + i,  # (raw / 5) - 500 m: 152.4 m, then +0.2 m per record
                "hr": None if i == 7 else 120 + i % 15,
                "cadence": 85 + i % 3,  # cycles/min
                "fractional_raw": (i % 4) * 32,  # 0, 0.25, 0.5, 0.75 cycle
                "power": None if i == 3 else 200 + 3 * (i % 10),
                "temperature": 14 + i // 30,
            }
        )
    return recs


def _no_power_records() -> list[dict]:
    recs, distance_cm = [], 0
    for i in range(30):
        speed_mm_s = 3000 + 5 * i
        distance_cm += speed_mm_s // 10
        recs.append(
            {
                "t": START + dt.timedelta(seconds=i),
                "speed_mm_s": speed_mm_s,
                "distance_cm": distance_cm,
                "altitude_raw": 2600 + 2 * i,
                "hr": 130 + i % 5,
                "cadence": 88,
                "temperature": 9,
            }
        )
    return recs


def _file_id() -> dict:
    return {
        "mesg_num": FILE_ID,
        "type": "activity",
        "manufacturer": "garmin",
        "product": 1,
        "serial_number": 1234,
        "time_created": START,
    }


def _encode(records: list[dict], enhanced: bool) -> bytes:
    enc = Encoder()
    enc.write_mesg(_file_id())
    for r in records:
        mesg = {
            "mesg_num": RECORD,
            "timestamp": r["t"],
            "distance": r["distance_cm"] / 100,
            "cadence": r["cadence"],
            "temperature": r["temperature"],
        }
        speed = r["speed_mm_s"] / 1000
        altitude = r["altitude_raw"] / 5 - 500
        if enhanced:
            mesg.update(enhanced_speed=speed, enhanced_altitude=altitude, fractional_cadence=r["fractional_raw"] / 128)
        else:
            mesg.update(speed=speed, altitude=altitude)
        if r["hr"] is not None:
            mesg["heart_rate"] = r["hr"]
        if r.get("power") is not None:
            mesg["power"] = r["power"]
        enc.write_mesg(mesg)
    return enc.close()


def _zip(fit: bytes, path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo(f"{ACTIVITY_ID}_ACTIVITY.fit", date_time=ZIP_DATE), fit, compress_type=zipfile.ZIP_DEFLATED)


def _iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _expected(records: list[dict]) -> dict:
    """Contract rules applied to the raw values (rounding documented in garmin_gateway.garmin.fit)."""
    return {
        "activity_id": str(ACTIVITY_ID),
        "summary": {
            "duration_seconds": int((records[-1]["t"] - records[0]["t"]).total_seconds()),
            "start_time": _iso(records[0]["t"]),
            "end_time": _iso(records[-1]["t"]),
            "total_distance_km": round(records[-1]["distance_cm"] / 100 / 1000, 2),
        },
        "series": {
            "timestamps": [_iso(r["t"]) for r in records],
            "power_w": [r["power"] for r in records],
            "temperature_c": [r["temperature"] for r in records],
            "hr_bpm": [r["hr"] for r in records],
            "speed_kmh": [round(r["speed_mm_s"] * 3.6 / 1000, 2) for r in records],
            # Same operations as the decoder (cm -> m, then km) so ties round the same way.
            "distance_km": [round(r["distance_cm"] / 100 / 1000, 3) for r in records],
            "altitude_m": [round(r["altitude_raw"] / 5 - 500, 1) for r in records],
            "cadence": [round((r["cadence"] + r["fractional_raw"] / 128) * 2, 1) for r in records],
        },
    }


def main() -> None:
    power = _power_records()
    _zip(_encode(power, enhanced=True), HERE / "activity_power.zip")
    _zip(_encode(_no_power_records(), enhanced=False), HERE / "activity_no_power.zip")
    (HERE / "series_expected.json").write_text(json.dumps(_expected(power), indent=2) + "\n")


if __name__ == "__main__":
    main()
