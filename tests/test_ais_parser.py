from datetime import datetime, timezone

import pytest

from ais.parser import AisMessageError, parse_ais_message


def test_parse_official_position_report_shape():
    observation = parse_ais_message(
        {
            "Message": {
                "PositionReport": {
                    "Cog": 308,
                    "Latitude": 31.1234,
                    "Longitude": 121.9876,
                    "NavigationalStatus": 1,
                    "Sog": 0.4,
                    "TrueHeading": 235,
                    "UserID": 259000420,
                    "Valid": True,
                }
            },
            "MessageType": "PositionReport",
            "MetaData": {
                "MMSI": 259000420,
                "ShipName": "AUGUSTSON@@@@",
                "time_utc": "2026-07-26 08:22:32.318353 +0000 UTC",
            },
        },
        received_at=datetime(2026, 7, 26, 8, 23, tzinfo=timezone.utc),
    )

    assert observation is not None
    assert observation.mmsi == "259000420"
    assert observation.vessel_name == "AUGUSTSON"
    assert observation.latitude == 31.1234
    assert observation.longitude == 121.9876
    assert observation.speed_knots == 0.4
    assert observation.course_degrees == 308
    assert observation.heading_degrees == 235
    assert observation.navigational_status == "at_anchor"
    assert observation.observed_at == datetime(2026, 7, 26, 8, 22, 32, 318353, tzinfo=timezone.utc)
    assert observation.has_position


def test_parse_static_data_keeps_missing_fields_null():
    observation = parse_ais_message(
        {
            "MessageType": "ShipStaticData",
            "MetaData": {"MMSI": 257069200, "time_utc": "2026-07-26T08:00:00Z"},
            "Message": {
                "ShipStaticData": {
                    "UserID": 257069200,
                    "ImoNumber": 9353333,
                    "Name": "KV FARM@@@@",
                    "Type": 55,
                    "CallSign": "LBHF@@",
                    "MaximumStaticDraught": 4.5,
                    "Destination": "ROTTERDAM@@@@",
                }
            },
        }
    )

    assert observation is not None
    assert observation.imo == "9353333"
    assert observation.destination == "ROTTERDAM"
    assert observation.draught_m == 4.5
    assert observation.latitude is None
    assert observation.longitude is None
    assert observation.speed_knots is None
    assert observation.navigational_status is None
    assert not observation.has_position
    assert observation.has_static_data


def test_unsupported_message_is_ignored_and_invalid_mmsi_is_rejected():
    assert parse_ais_message({"MessageType": "SafetyBroadcastMessage"}) is None
    with pytest.raises(AisMessageError, match="MMSI"):
        parse_ais_message(
            {
                "MessageType": "PositionReport",
                "Message": {"PositionReport": {"UserID": 123, "Latitude": 1, "Longitude": 2}},
            }
        )
