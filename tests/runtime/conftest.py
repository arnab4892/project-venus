"""Runtime turn tests reuse the ``make_ctx`` / ``new_session`` fixtures from the root conftest.

The reusable ``complete=`` seam class ``FakeLLM`` lives in ``tests.runtime._helpers`` so test
modules (in both tests/runtime and tests/agents) import it directly.
"""
