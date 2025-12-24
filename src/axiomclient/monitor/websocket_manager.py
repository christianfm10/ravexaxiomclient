"""
WebSocket client management for the Axiom Monitor.
"""

import asyncio
import logging

from axiomclient.auth.auth_manager import AuthManager
from axiomclient.websocket._client import AxiomWebSocketClient
from axiomclient.monitor.message_handlers import (
    handle_new_token_message,
    dispatch_pulse_message,
)

logger = logging.getLogger(__name__)


async def run_websocket_monitoring() -> None:
    """
    Initialize and run WebSocket client for monitoring Axiom Trade streams.

    ## Setup Steps
    1. Authenticate with Axiom (reuse saved tokens if available)
    2. Create WebSocket client
    3. Establish main connection
    4. Subscribe to new tokens and pulse streams
    5. Start message handler tasks

    ## Task Management
    Uses TaskGroup to manage multiple concurrent tasks:
    - Main WebSocket message handler
    - Pulse WebSocket message handler (if connection exists)

    TaskGroup ensures proper error propagation and graceful shutdown.

    ## Error Handling
    Keyboard interrupts are caught and logged for clean shutdown.
    Other exceptions propagate up to main() for handling.
    """
    # Authenticate with Axiom Trade
    auth_manager = AuthManager(use_saved_tokens=True)

    if not auth_manager.is_authenticated():
        logger.info("Authenticating with Axiom Trade...")
        auth_manager.authenticate()
    else:
        logger.info("Using saved authentication tokens")

    logger.info("Authentication successful")

    # Initialize WebSocket client
    ws_client = AxiomWebSocketClient(auth_manager=auth_manager)

    # Connect to main WebSocket
    await ws_client.connect()
    logger.info("Main WebSocket connection established")

    # Subscribe to data streams
    await ws_client.subscribe_new_tokens(handle_new_token_message)
    await ws_client.subscribe_pulse(dispatch_pulse_message)

    logger.info("WebSocket subscriptions configured")
    logger.info("Starting message handlers...")

    # Run message handlers in TaskGroup for proper lifecycle management
    try:
        async with asyncio.TaskGroup() as tg:
            # Main WebSocket message handler
            tg.create_task(ws_client._message_handler())

            # Pulse WebSocket message handler (if connected)
            if ws_client.ws_pulse:
                tg.create_task(ws_client._pulse_message_handler())

    except* KeyboardInterrupt:
        logger.info("WebSocket monitoring interrupted")
