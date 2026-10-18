"""Policy tests for the mood-map OpenGL default. No real GPU widget."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("PySide6")

import meridian.gl_viewport as gl


def _clear_gl_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MERIDIAN_NO_GL",
        "MERIDIAN_GL",
        "MERIDIAN_GL_PROBE",
        "QT_OPENGL",
        "LIBGL_ALWAYS_SOFTWARE",
        "MERIDIAN_GL_PROBE_CHILD",
    ):
        monkeypatch.delenv(name, raising=False)


def test_default_wants_hardware(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_gl_env(monkeypatch)
    assert gl.want_hardware_gl() is True
    assert gl.software_forced() is False


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_no_gl_forces_software(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _clear_gl_env(monkeypatch)
    monkeypatch.setenv("MERIDIAN_NO_GL", value)
    assert gl.software_forced() is True
    assert gl.want_hardware_gl() is False


def test_qt_opengl_software(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_gl_env(monkeypatch)
    monkeypatch.setenv("QT_OPENGL", "software")
    assert gl.software_forced() is True


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_libgl_always_software(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _clear_gl_env(monkeypatch)
    monkeypatch.setenv("LIBGL_ALWAYS_SOFTWARE", value)
    assert gl.software_forced() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "software"])
def test_meridian_gl_off_forces_software(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _clear_gl_env(monkeypatch)
    monkeypatch.setenv("MERIDIAN_GL", value)
    assert gl.software_forced() is True


@pytest.mark.parametrize("value", ["1", "desktop", "force", "gpu"])
def test_meridian_gl_on_still_wants_hardware(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _clear_gl_env(monkeypatch)
    monkeypatch.setenv("MERIDIAN_GL", value)
    assert gl.want_hardware_gl() is True


def test_explicit_software_wins_over_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_gl_env(monkeypatch)
    monkeypatch.setenv("MERIDIAN_GL", "desktop")
    monkeypatch.setenv("LIBGL_ALWAYS_SOFTWARE", "1")
    assert gl.software_forced() is True


def test_ensure_skips_probe_when_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_gl_env(monkeypatch)
    monkeypatch.setenv("MERIDIAN_NO_GL", "1")
    monkeypatch.setattr(gl, "probe_hardware_gl", lambda: pytest.fail("probe ran"))
    assert gl.ensure_hardware_gl() is False


def test_ensure_uses_ok_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_gl_env(monkeypatch)
    monkeypatch.setenv("MERIDIAN_GL_PROBE", "ok")
    monkeypatch.setattr(gl, "probe_hardware_gl", lambda: pytest.fail("probe ran"))
    assert gl.ensure_hardware_gl() is True


def test_ensure_failure_cache_sets_no_gl(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_gl_env(monkeypatch)
    monkeypatch.setenv("MERIDIAN_GL_PROBE", "fail")
    monkeypatch.setattr(gl, "probe_hardware_gl", lambda: pytest.fail("probe ran"))
    assert gl.ensure_hardware_gl() is False
    assert os.environ["MERIDIAN_NO_GL"] == "1"


def test_ensure_failure_marks_software(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_gl_env(monkeypatch)
    monkeypatch.setattr(gl, "probe_hardware_gl", lambda: False)
    assert gl.ensure_hardware_gl() is False
    assert os.environ["MERIDIAN_NO_GL"] == "1"
    assert os.environ["MERIDIAN_GL_PROBE"] == "fail"


def test_ensure_success_caches_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_gl_env(monkeypatch)
    monkeypatch.setattr(gl, "probe_hardware_gl", lambda: True)
    assert gl.ensure_hardware_gl() is True
    assert os.environ["MERIDIAN_GL_PROBE"] == "ok"
    assert os.environ.get("MERIDIAN_NO_GL", "") == ""


class _Result:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def test_probe_treats_signal_as_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gl.subprocess, "run", lambda *args, **kwargs: _Result(-6))
    assert gl.probe_hardware_gl() is False


def test_probe_timeout_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="gl", timeout=gl.PROBE_TIMEOUT_S)

    monkeypatch.setattr(gl.subprocess, "run", boom)
    assert gl.probe_hardware_gl() is False


def test_probe_ok_return(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gl.subprocess, "run", lambda *args, **kwargs: _Result(0))
    assert gl.probe_hardware_gl() is True


def test_probe_command_unfrozen(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _Result(0)

    monkeypatch.setattr(gl.subprocess, "run", fake_run)
    monkeypatch.setattr(gl, "_frozen", lambda: False)
    assert gl.probe_hardware_gl() is True
    assert captured["cmd"][0] == sys.executable
    assert captured["cmd"][1] == "-c"
    assert "child_probe_main" in captured["cmd"][2]
    assert captured["env"]["MERIDIAN_GL_PROBE_CHILD"] == "1"
    root = str(gl.Path(__file__).resolve().parent.parent)
    assert root in captured["env"]["PYTHONPATH"].split(os.pathsep)


def test_probe_command_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _Result(0)

    monkeypatch.setattr(gl.subprocess, "run", fake_run)
    monkeypatch.setattr(gl, "_frozen", lambda: True)
    assert gl.probe_hardware_gl() is True
    assert captured["cmd"] == [sys.executable]
    assert captured["env"]["MERIDIAN_GL_PROBE_CHILD"] == "1"
