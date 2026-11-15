from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

from app import database, main


@pytest.fixture
def isolated_database(monkeypatch) -> Iterator[None]:
    with TemporaryDirectory(prefix="seo-decay-test-") as directory:
        url = URL.create("sqlite", database=str(Path(directory) / "test.db"))
        test_engine = database.build_engine(url)
        monkeypatch.setattr(database, "engine", test_engine)
        monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=test_engine))
        try:
            yield
        finally:
            test_engine.dispose()


@pytest.fixture
def client(isolated_database, monkeypatch) -> Iterator[TestClient]:
    # Stage 1–3 tests build their own projects and datasets.
    monkeypatch.setattr(main, "seed_demo_projects_if_empty", lambda: None)
    with TestClient(main.app) as test_client:
        yield test_client
