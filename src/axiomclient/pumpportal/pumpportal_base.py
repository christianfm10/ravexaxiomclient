import websockets
import asyncio
import logging
import os
import json
from typing import Any, Awaitable, Callable
from aiogram import Bot

ROOM_NEW_TOKEN = "subscribeNewToken"
ROOM_MIGRATION = "subscribeMigration"
ROOM_ACCOUNT_TRADE = "subscribeAccountTrade"
ROOM_TOKEN_TRADE = "subscribeTokenTrade"

WS_PRIMARY_URL = "wss://pumpportal.fun/api/data"


class PumpPortalMonitor:
    SUBS_METHOD = "subscribeNewToken"
    UNSUBS_METHOD = f"un{SUBS_METHOD}"
    HEADERS = {}
    WS_PUMP_PORTAL = "wss://pumpportal.fun/api/data"

    def __init__(self, log_level: int = logging.INFO):
        self.ws_url = WS_PRIMARY_URL
        self.ws: Any = None  # Main WebSocket for regular channels
        self._callbacks: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}
        self._subs_mints: list[str] = []
        self._subs_accounts: list[str] = []
        # Setup logger for debugging and monitoring
        self.logger = logging.getLogger("PumpPortalMonitor")
        self.logger.setLevel(log_level)
        if not self.logger.handlers:
            self._setup_logging_handler(log_level)

        # Telegram bot for notifications
        self._telegram_bot: Bot | None = None
        self._telegram_chat_id: str | None = None
        self._setup_telegram()

        # Reconnection configuration
        self._max_reconnect_attempts = 5
        self._reconnect_delay_seconds = 5
        self._is_reconnecting = False
        #
        # Store subscriptions for reconnection
        self._active_subscriptions: dict[str, Any] = {}

    def _setup_logging_handler(self, log_level: int) -> None:
        """
        Configure logging handler for console output.

        Creates a StreamHandler with formatted output including timestamp,
        logger name, level, and message.

        ## Args:
        - `log_level` (int): Logging level to set for handler

        ## Side Effects:
        - Adds handler to logger
        - Sets formatter for consistent log formatting
        """

        handler = logging.StreamHandler()
        handler.setLevel(log_level)

        # Format: "2025-12-17 10:30:45 - AxiomTradeWebSocket - INFO - Connected"
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    async def start(self) -> None:
        """
        Start the WebSocket client and begin processing messages.

        Ensures connection is established and then enters the message handler loop.
        This method blocks until the WebSocket connection is closed.

        ## Process:
        1. Check if connection exists
        2. If not connected, attempt to connect
        3. Enter message handler loop (blocks here)
        4. Return when connection closes or error occurs

        ## Returns:
        - None (blocks until connection closes)

        ## Side Effects:
        - Establishes WebSocket connection if needed
        - Processes all incoming messages
        - Executes registered callbacks
        - Blocks the current task

        ## Usage:
        ```python
        # Setup client and subscriptions
        ws = AxiomTradeWebSocketClient(auth)
        await ws.connect()
        await ws.subscribe_new_tokens(callback)

        # Start processing messages (blocks)
        await ws.start()

        # Code here runs after connection closes
        print("WebSocket closed")
        ```

        ## Note:
        This is typically the last call in your async function as it blocks
        until the WebSocket closes. Consider running in a background task
        if you need concurrent operations.
        """
        # Ensure connection exists
        if not self.ws:
            if not await self.connect():
                self.logger.error("Cannot start: connection failed")
                return

        # Enter message processing loop (blocks here)
        await self._message_handler()

    async def close(self) -> None:
        """
        Close all WebSocket connections gracefully.

        Sends close frame to servers and cleans up connection resources.
        Closes both main WebSocket and Pulse WebSocket if connected.

        ## Side Effects:
        - Closes main WebSocket connection
        - Closes Pulse WebSocket connection
        - Sets self.ws and self.ws_pulse to None (implicitly via close)
        - Logs closure events
        - Stops message handler loops

        ## Example:
        ```python
        try:
            await ws.start()  # Blocks while processing
        finally:
            await ws.close()  # Always close all connections
        ```
        """
        # Close main WebSocket
        if self.ws:
            await self.ws.close()
            self.logger.info("✅ Main WebSocket connection closed")

    async def connect(self) -> bool:
        """
        Establish WebSocket connection to Axiom Trade server.

        Validates authentication, constructs headers with tokens, and
        establishes SSL/TLS WebSocket connection.

        ## Process:
        1. Validate authentication tokens are available
        2. Retrieve access and refresh tokens from auth_manager
        3. Build WebSocket headers with authentication cookies
        4. Attempt connection to WebSocket server
        5. Handle authentication errors (401) specifically

        ## Returns:
        - `bool`: True if connection successful, False otherwise

        ## Authentication:
        - Uses cookies in WebSocket handshake headers
        - Format: "auth-access-token=...; auth-refresh-token=..."
        - Tokens must be valid and non-expired

        ## Headers:
        - Origin: https://axiom.trade (required by server)
        - Cookie: Authentication tokens
        - User-Agent: Browser-like UA for compatibility
        - Cache-Control, Pragma: Prevent caching

        ## Error Handling:
        - Checks for HTTP 401 (authentication failure)
        - Logs detailed error information
        - Returns False on any connection error

        ## Side Effects:
        - Sets self.ws to connected WebSocket instance
        - Logs connection attempts and results

        ## Example:
        ```python
        if await ws.connect():
            print("Connected successfully")
        else:
            print("Connection failed")
        ```
        """
        HEADERS = {}

        try:
            # Attempt WebSocket connection with authentication
            self.logger.info(f"Attempting to connect to WebSocket: {self.ws_url}")
            self.ws = await websockets.connect(self.ws_url, additional_headers=HEADERS)
            self.logger.info("✅ Connected to WebSocket server")
            return True

        except Exception as e:
            # Check for authentication failure (HTTP 401)
            if "HTTP 401" in str(e) or "401" in str(e):
                self.logger.error(
                    "❌ WebSocket authentication failed - invalid or missing tokens"
                )
                self.logger.error(
                    "Please check that your tokens are valid and not expired"
                )
                self.logger.error(f"Error details: {e}")
            else:
                self.logger.error(f"❌ Failed to connect to WebSocket: {e}")

            return False

    async def _route_message(self, room: str, data: dict[str, Any]) -> None:
        """
        Route message to appropriate callback based on room name.

        Implements the routing logic that maps room names to registered callbacks.
        Handles both direct room matches and pattern-based matches (token rooms).

        ## Algorithm:
        1. Check for direct room matches (new_pairs, migrations)
        2. If not matched, check if room starts with token prefix (b-)
        3. Extract token address from room name
        4. Find token-specific callback
        5. Execute matched callback with data

        ## Args:
        - `room` (str): Room/channel name from message
        - `data` (dict): Complete message data including room field

        ## Routing Rules:
        - **new_pairs** → callback["new_pairs"]
        - **migrations** → callback["migrations"]
        - **b-{address}** → callback["token_mcap_{address}"]

        ## Side Effects:
        - Executes matched callback function
        - Logs warning if no callback found for room

        ## Example:
        ```python
        # Message: {"room": "b-0x123...", "market_cap": 1000000}
        # Extracts: token_address = "0x123..."
        # Calls: await callbacks["token_mcap_0x123..."](data)
        ```
        """
        # Handle direct room matches
        if room == ROOM_NEW_TOKEN and ROOM_NEW_TOKEN in self._callbacks:
            await self._callbacks[ROOM_NEW_TOKEN](data)
            return
        if room == ROOM_MIGRATION and ROOM_MIGRATION in self._callbacks:
            await self._callbacks[ROOM_MIGRATION](data)
            return
        if room == ROOM_TOKEN_TRADE and ROOM_TOKEN_TRADE in self._callbacks:
            await self._callbacks[ROOM_TOKEN_TRADE](data)
            return
        if room == ROOM_ACCOUNT_TRADE and ROOM_ACCOUNT_TRADE in self._callbacks:
            await self._callbacks[ROOM_ACCOUNT_TRADE](data)
            return

    async def _message_handler(self) -> None:
        """
        Handle incoming WebSocket messages in continuous loop.

        Receives messages from WebSocket, parses JSON, identifies the room/channel,
        and routes to the appropriate callback function.

        ## Process:
        1. Loop over incoming messages asynchronously
        2. Parse JSON message
        3. Extract room identifier
        4. Match room to registered callback
        5. Execute callback with message data

        ## Message Routing:
        - **new_pairs**: Direct room match
        - **migrations**: Direct room match
        - **b-{address}**: Token-specific room (extracts address from room name)

        ## Error Handling:
        - Catches JSON parsing errors (logs and continues)
        - Catches callback execution errors (logs and continues)
        - Handles WebSocket connection close gracefully
        - Logs all errors with context for debugging

        ## Design Decisions:
        - Runs in infinite loop until connection closes
        - Non-blocking: errors in one message don't stop processing
        - Flexible callback routing based on room naming patterns
        - Logs warnings for rooms without registered callbacks

        ## Side Effects:
        - Executes callbacks (may have their own side effects)
        - Logs message processing events
        - Blocks until WebSocket connection closes

        ## Example Message Flow:
        ```
        1. Receive: {"room": "new_pairs", "pair_address": "0x..."}
        2. Parse JSON
        3. Extract room: "new_pairs"
        4. Find callback: self._callbacks["new_pairs"]
        5. Execute: await callback(data)
        ```
        """
        if not self.ws:
            self.logger.error("Cannot handle messages: WebSocket not connected")
            return

        try:
            # Async iteration over WebSocket messages
            async for message in self.ws:
                try:
                    # Parse JSON message
                    data = json.loads(message)
                    if "message" in data:
                        self.logger.info(data["message"])
                    elif "name" in data:
                        room = ROOM_NEW_TOKEN
                        await self._route_message(room, data)
                    elif "mint" in data and data.get("mint", "") in self._subs_mints:
                        room = ROOM_TOKEN_TRADE
                        await self._route_message(room, data)
                    elif (
                        "traderPublicKey" in data
                        and data.get("traderPublicKey", "") in self._subs_accounts
                    ):
                        room = ROOM_ACCOUNT_TRADE
                        await self._route_message(room, data)
                    elif "txType" in data and data["txType"] == "migrate":
                        room = ROOM_MIGRATION
                        await self._route_message(room, data)
                    else:
                        self.logger.warning(f"No callback found for message: {data}")

                except json.JSONDecodeError as e:
                    self.logger.error(
                        f"Failed to parse WebSocket message as JSON: {message[:100]}..."
                    )
                    self.logger.debug(f"JSON decode error: {e}")

                except Exception as e:
                    self.logger.error(
                        f"Error handling WebSocket message: {e}", exc_info=True
                    )

        except websockets.exceptions.ConnectionClosed as e:
            self.logger.warning(
                f"⚠️ WebSocket connection closed: code={e.code} reason={e.reason}"
            )

            # Send Telegram notification about disconnection
            await self._send_telegram_notification(
                f"⚠️ <b>WebSocket PumpPortal Disconnected</b>\n"
                f"Connection: main\n"
                f"Code: {e.code}\n"
                f"Reason: {e.reason or 'Unknown'}\n"
                f"Status: Attempting reconnection..."
            )

            # Attempt to reconnect
            if await self._reconnect(connection_type="main"):
                self.logger.info(
                    "✅ Main WebSocket reconnected, resuming message handling"
                )
                # Recursively restart handler after successful reconnection
                await self._message_handler()
            else:
                self.logger.error("❌ Failed to reconnect main WebSocket")

        except Exception as e:
            self.logger.error(f"❌ WebSocket message handler error: {e}", exc_info=True)

    async def subscribe_method(
        self,
        method: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        keys: list = [],
    ):
        if not self.ws:
            if not await self.connect():
                return False
        # Register callback for migrations room
        self._callbacks[method] = callback
        try:
            # Send join message to server
            await self._send_message(method, keys)
            self.logger.info(f"✅ Subscribed to {method} monitor")
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to subscribe to {method} monitor: {e}")
            return False

    async def subscribe_new_token(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]]
    ):
        # Store subscription for reconnection
        self._active_subscriptions[ROOM_NEW_TOKEN] = {
            "type": "regular",
            "callback": callback,
        }
        await self.subscribe_method(ROOM_NEW_TOKEN, callback)

    async def subscribe_migration(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]]
    ):
        await self.subscribe_method(ROOM_MIGRATION, callback)

    async def subscribe_account_trade(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]], keys: list = []
    ):
        self._subs_accounts.extend(keys)
        await self.subscribe_method(ROOM_ACCOUNT_TRADE, callback, keys)

    async def subscribe_token_trade(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]], keys: list = []
    ):
        self._subs_mints.extend(keys)
        await self.subscribe_method(ROOM_TOKEN_TRADE, callback, keys)

    async def unsubscribe_method(self, method: str, keys: list = []) -> bool:
        # Build room name
        room_name = method

        # Remove callback from registry
        callback_key = method
        self._callbacks.pop(callback_key, None)

        try:
            # Send leave message to server
            await self._send_message(room_name, keys)
            self.logger.info(f"✅ Unsubscribed from {method} updates")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to unsubscribe from {method} updates: {e}")
            return False

    async def unsubscribe_token_trade(self, keys: list = []) -> bool:
        """
        Unsubscribe from token trade updates.

        Leaves the token trade room to stop receiving token trade updates.
        Useful for managing subscriptions and reducing message volume.

        ## Args:
        - `keys` (list, optional): List of keys to specify which token trades to unsubscribe from. Defaults to an empty list.

        ## Returns:
        - `bool`: True if unsubscribe successful, False otherwise

        ## Side Effects:
        - Sends "leave" action to WebSocket server
        - Removes callback from internal registry

        ## Example:
        ```python
        # Subscribe to token trade
        await ws.subscribe_token_trade(callback)

        # Later, unsubscribe when no longer needed
        await ws.unsubscribe_token_trade()
        ```
        """
        if await self.unsubscribe_method(ROOM_TOKEN_TRADE, keys):
            self._subs_mints = [k for k in self._subs_mints if k not in keys]
            return True
        return False

    async def unsubscribe_account_trade(self, keys: list = []) -> bool:
        """
        Unsubscribe from account trade updates.

        Leaves the account trade room to stop receiving account trade updates.
        Useful for managing subscriptions and reducing message volume.

        ## Args:
        - None

        ## Returns:
        - `bool`: True if unsubscribe successful, False otherwise

        ## Side Effects:
        - Sends "leave" action to WebSocket server
        - Removes callback from internal registry

        ## Example:
        ```python
        # Subscribe to account trade
        await ws.subscribe_account_trade(callback)

        # Later, unsubscribe when no longer needed
        await ws.unsubscribe_account_trade()
        ```
        """
        if await self.unsubscribe_method(ROOM_ACCOUNT_TRADE, keys):
            self._subs_accounts = [k for k in self._subs_accounts if k not in keys]
            return True
        return False
        # Build room name

    async def unsubscribe_migration(self) -> bool:
        """
        Unsubscribe from migration updates.

        Leaves the migrations room to stop receiving migration updates.
        Useful for managing subscriptions and reducing message volume.

        ## Args:
        - None

        ## Returns:
        - `bool`: True if unsubscribe successful, False otherwise

        ## Side Effects:
        - Sends "leave" action to WebSocket server
        - Removes callback from internal registry

        ## Example:
        ```python
        # Subscribe to migrations
        await ws.subscribe_migration(callback)

        # Later, unsubscribe when no longer needed
        await ws.unsubscribe_migration()
        ```
        """
        return await self.unsubscribe_method(ROOM_MIGRATION)
        # Build room name

    async def unsubscribe_new_token(self) -> bool:
        """
        Unsubscribe from market cap updates for a specific token.

        Leaves the token-specific room to stop receiving market cap updates.
        Useful for managing subscriptions and reducing message volume.

        ## Args:
        - `token_address` (str): Token contract address to unsubscribe from

        ## Returns:
        - `bool`: True if unsubscribe successful, False otherwise

        ## Side Effects:
        - Sends "leave" action to WebSocket server
        - Removes callback from internal registry

        ## Example:
        ```python
        # Subscribe to token
        await ws.subscribe_token_mcap(token, callback)

        # Later, unsubscribe when no longer needed
        await ws.unsubscribe_token_mcap(token)
        ```
        """
        return await self.unsubscribe_method(ROOM_NEW_TOKEN)
        # Build room name

    async def _send_message(self, method: str, keys: list = []) -> None:
        """
        Send join message to WebSocket server.

        ## Args:
        - `room` (str): Room name to join

        ## Message Format:
        ```json
        {"action": "join", "room": "room_name"}
        ```
        """
        if not self.ws:
            raise RuntimeError("WebSocket not connected")

        message = json.dumps({"method": method, "keys": keys})
        await self.ws.send(message)

    async def _send_telegram_notification(self, message: str) -> None:
        """Send notification via Telegram if bot is configured."""
        if self._telegram_bot and self._telegram_chat_id:
            try:
                await self._telegram_bot.send_message(
                    chat_id=self._telegram_chat_id, text=message, parse_mode="HTML"
                )
            except Exception as e:
                self.logger.error(f"Failed to send Telegram notification: {e}")

    def _setup_telegram(self) -> None:
        """Setup Telegram bot for notifications if credentials are available."""
        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

        if telegram_token and telegram_chat_id:
            try:
                self._telegram_bot = Bot(token=telegram_token)
                self._telegram_chat_id = telegram_chat_id
                self.logger.info("✅ Telegram notifications enabled")
            except Exception as e:
                self.logger.warning(f"Failed to setup Telegram bot: {e}")
        else:
            self.logger.info("ℹ️ Telegram notifications disabled (no credentials)")

    async def _reconnect(self, connection_type: str = "main") -> bool:
        """
        Attempt to reconnect to WebSocket with exponential backoff.

        ## Parameters
        - `connection_type`: Type of connection to reconnect ("main" or "pulse")

        ## Returns
        - `bool`: True if reconnection successful, False otherwise

        ## Reconnection Strategy
        1. Close existing connections
        2. Wait with exponential backoff
        3. Attempt to reconnect
        4. Restore all active subscriptions
        5. Retry up to max_reconnect_attempts

        ## Design Notes
        Uses exponential backoff to avoid overwhelming the server.
        Automatically restores all subscriptions that were active before disconnect.
        """
        if self._is_reconnecting:
            self.logger.debug("Reconnection already in progress")
            return False

        self._is_reconnecting = True

        try:
            for attempt in range(1, self._max_reconnect_attempts + 1):
                self.logger.info(
                    f"🔄 Reconnection attempt {attempt}/{self._max_reconnect_attempts} "
                    f"for {connection_type} WebSocket..."
                )

                # Wait with exponential backoff
                if attempt > 1:
                    delay = self._reconnect_delay_seconds * (2 ** (attempt - 2))
                    self.logger.info(f"Waiting {delay} seconds before retry...")
                    await asyncio.sleep(delay)

                try:
                    # Close existing connection if any
                    if connection_type == "main" and self.ws:
                        await self.ws.close()
                        self.ws = None

                    # Attempt reconnection
                    success = await self.connect()

                    if not success:
                        continue

                    # Restore subscriptions for main connection
                    if connection_type == "main":
                        self.logger.info("Restoring subscriptions...")
                        for room, sub_info in self._active_subscriptions.items():
                            if sub_info["type"] == "regular":
                                try:
                                    await self._send_message(room)
                                    self.logger.info(
                                        f"✅ Restored subscription: {room}"
                                    )
                                except Exception as e:
                                    self.logger.error(
                                        f"Failed to restore subscription {room}: {e}"
                                    )

                    self.logger.info(
                        f"✅ Successfully reconnected {connection_type} WebSocket"
                    )

                    # Send Telegram notification about successful reconnection
                    await self._send_telegram_notification(
                        f"✅ <b>WebSocket PumpPortal Reconnected</b>\n"
                        f"Connection: {connection_type}\n"
                        f"Status: Successfully restored after {attempt} attempt(s)"
                    )

                    return True

                except Exception as e:
                    self.logger.error(
                        f"Reconnection attempt {attempt} failed: {e}",
                        exc_info=(attempt == self._max_reconnect_attempts),
                    )

            self.logger.error(
                f"❌ Failed to reconnect after {self._max_reconnect_attempts} attempts"
            )
            return False

        finally:
            self._is_reconnecting = False
