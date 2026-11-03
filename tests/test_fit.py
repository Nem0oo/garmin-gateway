import datetime as dt
import io
import json
import zipfile
from pathlib import Path

import pytest
from garmin_fit_sdk import Encoder, Profile

from garmin_gateway.garmin import fit

FIXTURES = Path(__file__).parent / "fixtures"
SERIES_KEYS = {"timestamps", "power_w", "temperature_c", "hr_bpm", "speed_kmh", "distance_km", "altitude_m", "cadence"}
T0 = dt.datetime(2026, 11, 2, 7, 1, 12, tzinfo=dt.timezone.utc)


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _fit_without_records() -> bytes:
    enc = Encoder()
    enc.write_mesg({"mesg_num": Profile["mesg_num"]["FILE_ID"], "type": "activity", "manufacturer": "garmin", "time_created": T0})
    return enc.close()


# --- Fixture files ----------------------------------------------------------------------


def test_power_fixture_matches_reference_response():
    expected = json.loads(_fixture("series_expected.json"))
    result = fit.parse_activity_zip(_fixture("activity_power.zip"))
    assert result == {"summary": expected["summary"], "series": expected["series"]}


def test_every_series_has_one_value_per_record():
    series = fit.parse_activity_zip(_fixture("activity_power.zip"))["series"]
    assert set(series) == SERIES_KEYS
    assert len(series["timestamps"]) == 60
    assert all(len(v) == 60 for v in series.values())


def test_missing_value_in_a_record_is_null_in_place():
    series = fit.parse_activity_zip(_fixture("activity_power.zip"))["series"]
    assert series["power_w"][3] is None and series["power_w"][2] == 206
    assert series["hr_bpm"][7] is None and series["hr_bpm"][8] == 128


def test_series_absent_from_file_is_null_not_a_list():
    result = fit.parse_activity_zip(_fixture("activity_no_power.zip"))
    series = result["series"]
    assert series["power_w"] is None
    assert len(series["timestamps"]) == 30
    assert all(len(v) == 30 for k, v in series.items() if k != "power_w")
    assert series["temperature_c"] == [9] * 30
    assert series["cadence"] == [176] * 30  # 88 cycles/min, no fractional cadence


def test_summary_from_timestamps_and_last_distance():
    summary = fit.parse_activity_zip(_fixture("activity_power.zip"))["summary"]
    # 60 records, 1 s apart, plus one 5 s pause: 59 + 4 seconds.
    assert summary == {
        "duration_seconds": 63,
        "start_time": "2026-11-02T07:01:12Z",
        "end_time": "2026-11-02T07:02:15Z",
        "total_distance_km": 0.17,  # 169.74 m
    }


# --- Record conversion ------------------------------------------------------------------


def _rec(seconds: int, **fields) -> dict:
    return {"timestamp": T0 + dt.timedelta(seconds=seconds), **fields}


def test_units_and_rounding():
    records = [
        _rec(0, enhanced_speed=2.8613, enhanced_altitude=152.44, distance=1234.5678, cadence=85,
             fractional_cadence=0.5, power=212, temperature=14, heart_rate=128),
    ]
    series = fit.records_to_series(records)["series"]
    assert series["speed_kmh"] == [10.3]  # 2.8613 m/s * 3.6 = 10.30068
    assert series["altitude_m"] == [152.4]
    assert series["distance_km"] == [1.235]
    assert series["cadence"] == [171]  # (85 + 0.5) cycles/min * 2
    assert series["power_w"] == [212]
    assert series["temperature_c"] == [14]
    assert series["hr_bpm"] == [128]


def test_plain_speed_and_altitude_are_used_without_enhanced_fields():
    series = fit.records_to_series([_rec(0, speed=2.5, altitude=100.0)])["series"]
    assert series["speed_kmh"] == [9.0]
    assert series["altitude_m"] == [100.0]


def test_enhanced_fields_win_over_plain_ones():
    series = fit.records_to_series([_rec(0, speed=1.0, enhanced_speed=2.5, altitude=1.0, enhanced_altitude=100.0)])["series"]
    assert series["speed_kmh"] == [9.0]
    assert series["altitude_m"] == [100.0]


def test_timestamps_are_utc_with_z():
    paris = dt.timezone(dt.timedelta(hours=1))
    records = [{"timestamp": dt.datetime(2026, 11, 2, 8, 1, 12, tzinfo=paris)}, _rec(1)]
    result = fit.records_to_series(records)
    assert result["series"]["timestamps"] == ["2026-11-02T07:01:12Z", "2026-11-02T07:01:13Z"]
    assert result["summary"]["start_time"] == "2026-11-02T07:01:12Z"


def test_total_distance_is_last_known_distance():
    records = [_rec(0, distance=10.0), _rec(1, distance=4567.0), _rec(2)]
    result = fit.records_to_series(records)
    assert result["summary"]["total_distance_km"] == 4.57
    assert result["series"]["distance_km"] == [0.01, 4.567, None]


def test_no_distance_at_all_gives_null_total():
    result = fit.records_to_series([_rec(0), _rec(10)])
    assert result["summary"]["total_distance_km"] is None
    assert result["summary"]["duration_seconds"] == 10


def test_records_without_timestamp_are_skipped():
    result = fit.records_to_series([_rec(0, power=100), {"power": 999}, _rec(1, power=110)])
    assert result["series"]["power_w"] == [100, 110]


def test_no_record_is_an_error():
    with pytest.raises(fit.FitError, match="no records"):
        fit.records_to_series([{"power": 1}])


# --- Invalid files ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "data,message",
    [
        (b"not a zip", "not a zip"),
        (_zip({"readme.txt": b"hello"}), "no .fit file"),
        (_zip({"a_ACTIVITY.fit": b"garbage" * 10}), "not a valid FIT"),
        (_zip({"a_ACTIVITY.fit": _fit_without_records()}), "no records"),
    ],
)
def test_unusable_file_is_a_fit_error(data, message):
    with pytest.raises(fit.FitError, match=message):
        fit.parse_activity_zip(data)


def test_corrupted_fit_is_a_fit_error():
    raw = zipfile.ZipFile(io.BytesIO(_fixture("activity_power.zip"))).read("21234567890_ACTIVITY.fit")
    corrupted = raw[:40] + bytes([raw[40] ^ 0xFF]) + raw[41:]  # breaks the file CRC
    with pytest.raises(fit.FitError, match="not a valid FIT"):
        fit.parse_activity_zip(_zip({"a.fit": corrupted}))


def test_fit_member_is_found_whatever_its_case_and_position():
    raw = zipfile.ZipFile(io.BytesIO(_fixture("activity_power.zip"))).read("21234567890_ACTIVITY.fit")
    result = fit.parse_activity_zip(_zip({"notes.txt": b"x", "1_ACTIVITY.FIT": raw}))
    assert len(result["series"]["timestamps"]) == 60


def test_unzipped_size_is_capped():
    # Highly compressible member: small zip, large once inflated.
    data = _zip({"big.fit": b"\0" * 10_000})
    with pytest.raises(fit.FitTooLarge, match="limit 1000 bytes"):
        fit.parse_activity_zip(data, max_unzipped_bytes=1000)
