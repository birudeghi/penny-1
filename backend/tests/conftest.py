import sys
from collections.abc import Callable, Iterator
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

import app.config
from app.config import Settings, get_settings
from app.main import app as penny_app
from app.openai_client import get_openai_client


@pytest.fixture(autouse=True)
def _clear_caches() -> Iterator[None]:
    """Settings and the OpenAI client are lru_cached process-wide.

    Clearing before AND after means no test inherits another's environment and
    no test leaks its own — so individual tests never have to think about it.
    """
    get_settings.cache_clear()
    get_openai_client.cache_clear()
    yield
    get_settings.cache_clear()
    get_openai_client.cache_clear()


@pytest.fixture
def settings_override(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Settings]:
    """Swap in a Settings built from kwargs, ignoring .env entirely.

    Modules bind `get_settings` at import time, so patching app.config alone would
    leave every importer still calling the real one; patch each binding too.
    """

    # Bound ONCE, before any patching, and reused across every call. `get_settings` read inside
    # `_override` would be re-read on each iteration, and this module's own binding is one of the
    # ones about to be patched — so the comparison target would silently become the lambda after
    # the first match and every module later in sys.modules would be skipped.
    real = get_settings
    # THE MODULES ARE DISCOVERED ONCE AND REMEMBERED, which is what makes a SECOND call work.
    # Re-running the `is real` scan on the second call finds nothing: every module it wants was
    # patched by the first call and no longer points at the real function. A test that overrides
    # twice — build a household against a volume, then ask what happens with no volume at all —
    # would therefore keep the FIRST settings everywhere except `app.config`, and quietly assert
    # against a configuration it thought it had replaced.
    targets: list[ModuleType] = []

    def _override(**kwargs: object) -> Settings:
        settings = Settings(_env_file=None, **kwargs)
        if not targets:
            targets.extend(
                module
                for module in list(sys.modules.values())
                if getattr(module, "get_settings", None) is real
            )
        monkeypatch.setattr(app.config, "get_settings", lambda: settings)
        for module in targets:
            monkeypatch.setattr(module, "get_settings", lambda: settings)
        return settings

    return _override


@pytest.fixture
def client() -> TestClient:
    return TestClient(penny_app)


@pytest.fixture
def db_url() -> str:
    """Real-Postgres tests opt in via env, mirroring the RUN_LIVE_OPENAI_TESTS pattern."""
    url = get_settings().test_database_url
    if not url:
        pytest.skip("set PENNY_TEST_DATABASE_URL to run DB tests")
    return url
