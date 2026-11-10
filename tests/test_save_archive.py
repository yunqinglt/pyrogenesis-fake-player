import hashlib
import json
import zipfile

import pytest

from pyrogenesis_analysis.archive import SaveError, read_save


def make_save(tmp_path, metadata=None, simulation=b"state", extra=()):
    path = tmp_path / "sample.0adsave"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("metadata.json", metadata if isinstance(metadata, bytes) else
                         json.dumps(metadata or {"engine_serialization_version": "0.28.0"}))
        if simulation is not None:
            archive.writestr("simulation.dat", simulation)
        for name, value in extra:
            archive.writestr(name, value)
    return path


def test_archive_reads_without_modifying_or_extracting(tmp_path):
    path = make_save(tmp_path, extra=(("../../outside", b"never extract"),))
    original = path.read_bytes()
    result = read_save(path)
    assert result.simulation == b"state"
    assert result.sha256 == hashlib.sha256(original).hexdigest()
    assert result.metadata["engine_serialization_version"] == "0.28.0"
    assert path.read_bytes() == original
    assert set(tmp_path.iterdir()) == {path}


@pytest.mark.parametrize("metadata", [b"[]", b"{", b'{"engine_serialization_version":"0.28.0","x":NaN}',
    b'{"engine_serialization_version":"0.28.0","x":1e400}',
    b'{"engine_serialization_version":"0.28.0","x":1,"x":2}', b'{}'])
def test_rejects_invalid_metadata(tmp_path, metadata):
    with pytest.raises(SaveError):
        read_save(make_save(tmp_path, metadata))


def test_required_entries_must_be_unique(tmp_path):
    with pytest.warns(UserWarning):
        path = make_save(tmp_path, extra=(("simulation.dat", b"duplicate"),))
    with pytest.raises(SaveError, match="exactly one"):
        read_save(path)


def test_missing_simulation(tmp_path):
    with pytest.raises(SaveError, match="simulation.dat"):
        read_save(make_save(tmp_path, simulation=None))


def test_invalid_zip(tmp_path):
    path = tmp_path / "bad.0adsave"
    path.write_bytes(b"not a zip")
    with pytest.raises(SaveError, match="Cannot read"):
        read_save(path)


def test_bounded_decompression(tmp_path, monkeypatch):
    import pyrogenesis_analysis.archive as module
    monkeypatch.setattr(module, "MAX_SIMULATION_BYTES", 4)
    with pytest.raises(SaveError, match="size"):
        read_save(make_save(tmp_path, simulation=b"x" * 5))
