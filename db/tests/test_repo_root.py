from pathlib import Path

import pytest

from db import repo_root as module


def test_resolves_the_repo_root():
    root = module.repo_root()
    assert (root / "contracts").is_dir()
    assert (root / "db" / "repo_root.py").is_file()


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    assert module.repo_root() == tmp_path.resolve()


def test_out_of_range_indexes_are_filtered_not_raised(monkeypatch):
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.setattr(module, "_CANDIDATE_INDEXES", (1, 999))
    assert (module.repo_root() / "contracts").is_dir()


def test_raises_a_clear_error_when_root_is_not_found(monkeypatch):
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.setattr(module, "_MARKER", "no_existe_esta_carpeta")
    with pytest.raises(RuntimeError, match="raíz del repo"):
        module.repo_root()


def test_returns_a_path():
    assert isinstance(module.repo_root(), Path)
