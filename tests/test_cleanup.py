import os
import time

from app.cleanup import cleanup_old_audio


def _touch(path, age_seconds):
    path.write_bytes(b"x")
    t = time.time() - age_seconds
    os.utime(path, (t, t))


def test_removes_only_old_audio_and_tmp_files(tmp_path):
    _touch(tmp_path / "old.mp3", 1000)
    _touch(tmp_path / "stale.mp3.tmp", 1000)
    _touch(tmp_path / "new.mp3", 10)
    _touch(tmp_path / "notes.txt", 1000)  # not ours: never touched

    assert cleanup_old_audio(tmp_path, max_age_seconds=500) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new.mp3", "notes.txt"]
