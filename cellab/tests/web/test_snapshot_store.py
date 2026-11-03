from datetime import datetime, timezone

try:
    from cellab.web.backend.snapshots import SnapshotStore, compress_state, decompress_state
except ModuleNotFoundError:
    from web.backend.snapshots import SnapshotStore, compress_state, decompress_state


def test_state_codec_round_trips_opaque_payload_with_versioned_compression_tag():
    payload = bytes(128)
    encoding, compressed = compress_state("dense-u8-v1", payload)
    assert encoding == "zlib:dense-u8-v1"
    assert len(compressed) < 128
    assert decompress_state(encoding, compressed) == ("dense-u8-v1", payload)


class FakeDatabase:
    def __init__(self):
        self.calls = []
        self.saved = None
        self.created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)

    def connect(self, _url):
        return FakeConnection(self)


class FakeConnection:
    def __init__(self, database):
        self.database = database

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return FakeCursor(self.database)


class FakeCursor:
    def __init__(self, database):
        self.database = database
        self.row = None
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.database.calls.append(normalized)
        if normalized.startswith("INSERT INTO snapshots"):
            self.database.saved = (41, *params, self.database.created_at)
            (
                snapshot_id, config_hash, _yaml, preset, display_name, width, height,
                counts, generation, _encoding, _blob, created_at,
            ) = self.database.saved
            self.row = (snapshot_id, config_hash, preset, display_name, width, height, counts, generation, created_at)
        elif "FROM snapshots WHERE snapshot_id" in normalized:
            saved = self.database.saved
            self.row = (saved[1], saved[2], saved[3], saved[5], saved[6], saved[7], saved[8], saved[9], saved[10])
        elif normalized.startswith("SELECT") and "FROM snapshots" in normalized:
            saved = self.database.saved
            self.rows = [(41, saved[1], saved[3], saved[4], saved[5], saved[6], saved[7], saved[8], saved[11])]

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


def test_snapshot_repository_saves_compressed_state_lists_metadata_and_restores_payload():
    database = FakeDatabase()
    store = SnapshotStore("postgresql://test", connect=database.connect)
    snapshot = {
        "config_hash": "abc123",
        "config_yaml": "version: 1\nname: board\n",
        "preset": "game_of_life",
        "width": 20,
        "height": 10,
        "counts": [190, 10],
        "generation": 5,
        "state_format": "dense-u8-v1",
        "state_blob": b"\x00\x01" * 50,
    }

    metadata = store.save(snapshot, "board")
    listed = store.list("abc123")
    restored = store.get(41)

    assert metadata["id"] == 41
    assert metadata["created_at"].startswith("2026-01-02T")
    assert listed == [metadata]
    assert restored == snapshot
    assert any("CREATE TABLE IF NOT EXISTS snapshots" in statement for statement in database.calls)
    assert any("USING btree (config_hash)" in statement for statement in database.calls)
