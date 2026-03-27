"""Temporal worker — standalone process entry point.

Run with:
    python -m app.temporal.worker

Or via Docker Compose:
    command: uv run python -m app.temporal.worker

The worker polls the configured task queue and executes registered
workflows and activities. It runs independently from the FastAPI process.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from app.core.config import get_settings
from app.temporal.activities.onboarding import greet_user
from app.temporal.workflows.onboarding import OnboardingWorkflow

logger = logging.getLogger(__name__)


async def run_worker() -> None:
    """Connect to Temporal and run the worker until interrupted."""
    settings = get_settings()

    logger.info(
        "Connecting to Temporal at %s (namespace: %s)…",
        settings.TEMPORAL_HOST,
        settings.TEMPORAL_NAMESPACE,
    )
    client = await Client.connect(
        settings.TEMPORAL_HOST,
        namespace=settings.TEMPORAL_NAMESPACE,
    )

    worker = Worker(
        client,
        task_queue=settings.TEMPORAL_TASK_QUEUE,
        workflows=[OnboardingWorkflow],
        activities=[greet_user],
        # ThreadPoolExecutor for any sync activities added in the future
        activity_executor=ThreadPoolExecutor(5),
    )

    logger.info(
        "Worker started, listening on task queue: %s",
        settings.TEMPORAL_TASK_QUEUE,
    )
    await worker.run()


def main() -> None:
    """Entry point for ``python -m app.temporal.worker``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker shutting down…")


if __name__ == "__main__":
    main()
