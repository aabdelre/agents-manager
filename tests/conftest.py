import pytest

from chief.store import Store


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "state")
    result.create("chief", tmp_path)
    result.create("product", tmp_path, "Build a product plan")
    return result
