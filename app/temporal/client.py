"""Temporal client singleton — used by the FastAPI application.

Usage:
    # During startup (in lifespan):
    await connect()

    # In route handlers / services:
    client = get_temporal_client()
    handle = await client.start_workflow(...)
"""

import logging

from temporalio.client import Client

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_client: Client | None = None


async def connect() -> Client:
    """Connect to the Temporal server and cache the client.

    Should be called once during FastAPI lifespan startup.
    """
    global _client
    settings = get_settings()

    _client = await Client.connect(
        settings.TEMPORAL_HOST,
        namespace=settings.TEMPORAL_NAMESPACE,
    )
    logger.info(
        "Connected to Temporal at %s (namespace: %s)",
        settings.TEMPORAL_HOST,
        settings.TEMPORAL_NAMESPACE,
    )
    return _client


def get_temporal_client() -> Client:
    """Return the cached Temporal client.

    Raises RuntimeError if ``connect()`` has not been called yet.
    """
    if _client is None:
        raise RuntimeError(
            "Temporal client is not connected. "
            "Ensure connect() is called during application startup."
        )
    return _client
