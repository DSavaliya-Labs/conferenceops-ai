from conferenceops.models import Base, Profile, ProfilePreferences

PRIVATE = {"needs_step_free", "wants_captions", "reduced_motion", "excluded_levels"}


def test_private_preferences_are_never_columns_of_the_public_profile():
    # PRIV-1: public DTOs, search documents and telemetry are built from `profiles`; preferences live apart.
    assert PRIVATE.isdisjoint(Profile.__table__.columns.keys())
    assert set(ProfilePreferences.__table__.columns.keys()) >= PRIVATE


def test_event_times_are_timezone_aware_columns():
    # TIME-1: instants are stored with a time zone, never as naive local times.
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if column.type.__class__.__name__ == "DateTime":
                assert column.type.timezone, f"{table.name}.{column.name} must be timezone-aware"


def test_conferences_record_what_their_data_supports():
    columns = set(Base.metadata.tables["conferences"].columns.keys())
    assert {"kind", "source", "clock_mode", "is_fictional", "has_transcripts", "timezone"} <= columns
