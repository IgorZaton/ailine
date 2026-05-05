"""MLflow ``run_context_provider`` plugin: deterministic AIline correlation tag.

When ``ailine track`` launches a child process it sets the
``AILINE_CORRELATION_ID`` environment variable to a unique UUID. MLflow
auto-discovers this :class:`RunContextProvider` via the
``mlflow.run_context_provider`` entry point declared in ``pyproject.toml``,
so any ``mlflow.start_run()`` call inside the child automatically carries
the tag::

    ailine.correlation_id = <uuid>

The session loop polls MLflow for that tag and updates the lineage row's
``mlflow_run`` column the moment a matching run appears - no client code
changes required.

Outside an AIline-launched process (env var unset) the provider is a
no-op, so it is safe to keep installed in any environment.
"""

from __future__ import annotations

import os

from mlflow.tracking.context.abstract_context import RunContextProvider


CORRELATION_ENV = "AILINE_CORRELATION_ID"
CORRELATION_TAG = "ailine.correlation_id"


class AilineRunContextProvider(RunContextProvider):
    """Tag every run started under ``ailine track`` with the correlation id."""

    def in_context(self) -> bool:
        return bool(os.environ.get(CORRELATION_ENV))

    def tags(self) -> dict:
        cid = os.environ.get(CORRELATION_ENV)
        if not cid:
            return {}
        return {CORRELATION_TAG: cid}
