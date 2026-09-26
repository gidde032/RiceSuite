import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest

from transcribe import whisper


@pytest.fixture(autouse=True)
def reset_cached_model():
    whisper.dispose()
    yield
    whisper.dispose()


def _fake_backend(monkeypatch, model_class):
    backend = ModuleType("faster_whisper")
    backend.WhisperModel = model_class
    monkeypatch.setitem(sys.modules, "faster_whisper", backend)


def test_default_cpu_threads_uses_half_logical_cpus(monkeypatch):
    monkeypatch.delenv("RICECLIPPER_WHISPER_CPU_THREADS", raising=False)
    monkeypatch.setattr(whisper.os, "cpu_count", lambda: 8)

    assert whisper._default_cpu_threads() == 4


def test_model_setup_does_not_register_process_semaphore(monkeypatch):
    """Cleanup reviewer (HIGH): transcription setup must not create a tracked semaphore.

    Fix: use tqdm's thread-only lock instead of its multiprocessing lock.
    """
    import multiprocessing.resource_tracker as resource_tracker

    from tqdm import tqdm

    registrations = []
    monkeypatch.setattr(
        resource_tracker,
        "register",
        lambda name, resource_type: registrations.append((name, resource_type)),
    )
    monkeypatch.delattr(tqdm, "_lock", raising=False)

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

    _fake_backend(monkeypatch, FakeWhisperModel)
    whisper._model()
    tqdm.get_lock()

    assert not [
        registration for registration in registrations if registration[1] == "semaphore"
    ]
    assert isinstance(tqdm.get_lock(), type(threading.RLock()))


def test_cpu_thread_override_is_passed_to_backend(monkeypatch):
    calls = []

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

    _fake_backend(monkeypatch, FakeWhisperModel)
    monkeypatch.setenv("RICECLIPPER_WHISPER_CPU_THREADS", "7")

    whisper._model()

    assert calls == [
        (
            (whisper._MODEL_SIZE,),
            {
                "device": whisper._DEVICE,
                "compute_type": whisper._COMPUTE_TYPE,
                "cpu_threads": 7,
            },
        )
    ]


@pytest.mark.parametrize("override", ["not-an-int", "0", "-2"])
def test_invalid_cpu_thread_override_falls_back_to_default(monkeypatch, override):
    monkeypatch.setattr(whisper.os, "cpu_count", lambda: 6)
    monkeypatch.setenv("RICECLIPPER_WHISPER_CPU_THREADS", override)

    assert whisper._default_cpu_threads() == 3


def test_model_construction_is_single_flight(monkeypatch):
    construction_count = 0
    count_lock = threading.Lock()

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            nonlocal construction_count
            with count_lock:
                construction_count += 1
            time.sleep(0.02)

    _fake_backend(monkeypatch, FakeWhisperModel)
    barrier = threading.Barrier(4)
    models = []

    def load_model():
        barrier.wait()
        models.append(whisper._model())

    threads = [threading.Thread(target=load_model) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert construction_count == 1
    assert len(models) == 4
    assert len({id(model) for model in models}) == 1


def test_dispose_clears_cached_model_and_allows_reconstruction(monkeypatch):
    instances = []

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            instances.append(self)

    _fake_backend(monkeypatch, FakeWhisperModel)

    first = whisper._model()
    whisper.dispose()

    assert whisper._cached_model is None
    second = whisper._model()
    assert second is not first
    assert instances == [first, second]


@pytest.mark.smoke
def test_transcribe_preserves_word_timestamps_and_skips_blanks(monkeypatch):
    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, path, *, word_timestamps):
            assert path == "clip.mp4"
            assert word_timestamps is True
            return (
                [
                    SimpleNamespace(
                        words=[
                            SimpleNamespace(word=" hello ", start=0, end=0.4),
                            SimpleNamespace(word="  ", start=0.4, end=0.5),
                            SimpleNamespace(word=None, start=0.5, end=0.6),
                        ]
                    )
                ],
                SimpleNamespace(),
            )

    _fake_backend(monkeypatch, FakeWhisperModel)

    assert whisper.transcribe("clip.mp4") == [
        whisper.Word(text="hello", start=0.0, end=0.4)
    ]
