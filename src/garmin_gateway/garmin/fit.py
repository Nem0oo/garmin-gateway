"""Decoding of Garmin activity files (zip -> FIT -> per-record series).

Output shape (REST contract v1): {"summary": {...}, "series": {...}}, one value per FIT `record`
message in file order. A value missing from a record is None at its index; a series missing from
every record is None as a whole.

Units and rounding (well below the sensors' precision):
- speed_kmh: m/s * 3.6, 2 decimals (`enhanced_speed`, else `speed`)
- distance_km: cumulative m / 1000, 3 decimals (metre)
- altitude_m: 1 decimal (`enhanced_altitude`, else `altitude`)
- cadence: steps/min = (cadence + fractional_cadence) * 2, 1 decimal (FIT stores cycles/min)
- power_w, hr_bpm, temperature_c: integers as recorded
- summary.total_distance_km: last known cumulative distance, 2 decimals
"""
import datetime as dt
import io
import zipfile
from typing import Any, Callable

from garmin_fit_sdk import Decoder, Stream

# Inflated FIT size accepted from the zip (a zip bomb stops here). A multi-hour ride is a few MB.
MAX_UNZIPPED_BYTES = 128 * 1024 * 1024


class FitError(Exception):
    """The activity file holds no usable FIT data. Message is safe to return to clients."""


class FitTooLarge(FitError):
    """The FIT file inside the zip goes over the inflated size limit."""


def parse_activity_zip(data: bytes, max_unzipped_bytes: int = MAX_UNZIPPED_BYTES) -> dict[str, Any]:
    return records_to_series(_decode_records(_extract_fit(data, max_unzipped_bytes)))


def _extract_fit(data: bytes, max_bytes: int) -> bytes:
    """First member ending in .fit (any case), read with a cap on its inflated size."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise FitError("Activity file is not a zip archive") from exc
    with zf:
        member = next((i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".fit")), None)
        if member is None:
            raise FitError("Activity archive contains no .fit file")
        try:
            with zf.open(member) as f:
                content = f.read(max_bytes + 1)
        except (zipfile.BadZipFile, OSError, EOFError, NotImplementedError, RuntimeError) as exc:
            raise FitError("Activity archive is corrupted") from exc
    if len(content) > max_bytes:
        raise FitTooLarge(f"FIT file too large once unzipped (limit {max_bytes} bytes)")
    return content


def _decode_records(content: bytes) -> list[dict[str, Any]]:
    # is_fit/check_integrity move the stream position, so decoding uses a fresh decoder.
    checker = Decoder(Stream.from_byte_array(bytearray(content)))
    if not checker.is_fit() or not checker.check_integrity():
        raise FitError("Activity file is not a valid FIT file")
    messages, errors = Decoder(Stream.from_byte_array(bytearray(content))).read()
    if errors:
        raise FitError("Activity FIT file could not be decoded")
    return messages.get("record_mesgs") or []


def _first(*names: str) -> Callable[[dict], Any]:
    def get(rec: dict) -> Any:
        for name in names:
            value = rec.get(name)
            if value is not None:
                return value
        return None

    return get


def _number(get: Callable[[dict], Any], factor: float = 1.0, digits: int | None = None) -> Callable[[dict], Any]:
    def convert(rec: dict) -> Any:
        value = get(rec)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        if digits is None:
            return int(round(value * factor))
        return round(value * factor, digits)

    return convert


def _cadence(rec: dict) -> float | None:
    cycles = rec.get("cadence")
    if not isinstance(cycles, (int, float)):
        return None
    fractional = rec.get("fractional_cadence")
    if not isinstance(fractional, (int, float)):
        fractional = 0
    return round((cycles + fractional) * 2, 1)


_SERIES: dict[str, Callable[[dict], Any]] = {
    "power_w": _number(_first("power")),
    "temperature_c": _number(_first("temperature")),
    "hr_bpm": _number(_first("heart_rate")),
    "speed_kmh": _number(_first("enhanced_speed", "speed"), 3.6, 2),
    "distance_km": _number(_first("distance"), 1 / 1000, 3),
    "altitude_m": _number(_first("enhanced_altitude", "altitude"), 1, 1),
    "cadence": _cadence,
}


def _iso(t: dt.datetime) -> str:
    if t.tzinfo is None:
        # FIT timestamps are UTC; the SDK returns aware datetimes, this covers naive ones.
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def records_to_series(records: list[dict[str, Any]]) -> dict[str, Any]:
    # A record without timestamp cannot be placed on the time axis: it is left out.
    records = [r for r in records if isinstance(r.get("timestamp"), dt.datetime)]
    if not records:
        raise FitError("Activity FIT file has no records")

    series: dict[str, Any] = {"timestamps": [_iso(r["timestamp"]) for r in records]}
    for name, convert in _SERIES.items():
        values = [convert(r) for r in records]
        series[name] = values if any(v is not None for v in values) else None

    distances = [r["distance"] for r in records if isinstance(r.get("distance"), (int, float))]
    start, end = records[0]["timestamp"], records[-1]["timestamp"]
    summary = {
        "duration_seconds": int((end - start).total_seconds()),
        "start_time": series["timestamps"][0],
        "end_time": series["timestamps"][-1],
        "total_distance_km": round(distances[-1] / 1000, 2) if distances else None,
    }
    return {"summary": summary, "series": series}
