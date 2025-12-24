"""
# Axiom Trade WebSocket Client

This module provides a WebSocket client for real-time data streaming from the Axiom Trade platform.
It handles authentication, connection management, and subscription to various data channels.

## Key Features:
- **Real-time Data Streaming**: Subscribe to live token updates, price changes, and migrations
- **Authenticated Connections**: Uses authentication tokens from AuthManager
- **Event-driven Architecture**: Callback-based message handling for different data types
- **Dual Connection Support**: Separate connections for regular channels and Pulse analytics
- **Multiple Subscriptions**: Support for multiple simultaneous data subscriptions
- **Binary Message Support**: Handles binary-encoded Pulse messages for efficient data transfer

## Supported Data Streams:
- **New Tokens**: Real-time notifications when new tokens are listed
- **Token Market Cap**: Live market cap updates for specific tokens
- **Token Migrations**: Notifications about token contract migrations
- **Pulse Analytics**: Advanced filtering and analytics with binary encoding

## Usage Examples:

### Basic Usage - New Tokens:
```python
from axiomclient.auth.auth_manager import AuthManager
from axiomclient.websocket._client import AxiomWebSocketClient

# Create authenticated session
auth = AuthManager(username="user@example.com", password="password")
auth.authenticate()

# Create WebSocket client
ws_client = AxiomWebSocketClient(auth)

# Define callback for new tokens
async def on_new_token(data):
    print(f"New token: {data['token_name']} at {data['pair_address']}")

# Subscribe and start listening
await ws_client.connect()
await ws_client.subscribe_new_tokens(on_new_token)
await ws_client.start()  # Runs message handler loop
```

### Token Market Cap Updates:
```python
async def on_mcap_update(data):
    print(f"Market cap: ${data['market_cap']:,.2f}")

token_address = "0x1234567890abcdef1234567890abcdef12345678"
await ws_client.subscribe_token_mcap(token_address, on_mcap_update)
```

### Pulse Advanced Analytics:
```python
# Pulse provides advanced filtering and real-time analytics
async def on_pulse_data(data):
    print(f"Pulse analytics: {data}")
    # Process filtered token data with advanced metrics

# Subscribe with default filters (from pulse_send_message.json)
await ws_client.subscribe_pulse(on_pulse_data)

# Or provide custom filter configuration
custom_filters = {
    "type": "userState",
    "state": {
        "tables": {"newPairs": True, "finalStretch": True},
        "filters": {
            "newPairs": {
                "marketCap": {"min": 100000, "max": 1000000},
                "protocols": {"pump": True},
                # ... more filters
            }
        }
    }
}
await ws_client.subscribe_pulse(on_pulse_data, custom_filters)
```

### Complete Example with Multiple Subscriptions:
```python
import asyncio
from axiomclient.auth.auth_manager import AuthManager
from axiomclient.websocket._client import AxiomWebSocketClient

async def main():
    # Authenticate
    auth = AuthManager(username="user@example.com", password="password")
    if not auth.authenticate():
        print("Authentication failed")
        return

    # Create WebSocket client
    ws = AxiomWebSocketClient(auth)

    # Define callbacks
    async def on_new_token(data):
        print(f"🆕 New token: {data['token_name']}")

    async def on_migration(data):
        print(f"🔄 Migration: {data['token_name']}")

    async def on_pulse(data):
        print(f"📊 Pulse data: {data}")

    # Connect and subscribe to multiple channels
    await ws.connect()
    await ws.subscribe_new_tokens(on_new_token)
    await ws.subscribe_migrations(on_migration)
    await ws.subscribe_pulse(on_pulse)

    try:
        # Start processing messages (blocks)
        await ws.start()
    finally:
        # Always close connections
        await ws.close()

# Run
asyncio.run(main())
```
"""

# Standard library imports
import asyncio
import base64
import json
import logging
import os
import msgpack
from typing import Any, Awaitable, Callable, Dict, Optional

# Third-party imports
# aiogram: Telegram bot library for sending notifications
from aiogram import Bot

# websockets: Modern asyncio-based WebSocket client library
# Provides async/await support for WebSocket connections with excellent performance
import websockets

import typing

if typing.TYPE_CHECKING:
    from axiomclient.auth.auth_manager import AuthManager


# WebSocket server URLs (cluster endpoints)
# Using cluster3 as primary - cluster-usc2 is alternative/backup
WS_PRIMARY_URL = "wss://cluster3.axiom.trade/"
# WS_PRIMARY_URL = "ws://localhost:8765"
WS_BACKUP_URL = "wss://cluster-usc2.axiom.trade/"
WS_PULSE_URL = "wss://pulse2.axiom.trade/ws"
# WS_PULSE_URL = "ws://localhost:8766"

# Room/channel names for WebSocket subscriptions
ROOM_NEW_PAIRS = "new_pairs"
ROOM_MIGRATIONS = "migrations"
ROOM_SOL_PRICE = "sol_price"
ROOM_TOKEN_PREFIX = "b-"  # Token-specific rooms use format: b-{token_address}


def _decode_message_content(content: bytes):
    """
    Decode WebSocket message content - try msgpack first, then UTF-8

    Args:
        content: Raw message content

    Returns:
        Decoded data (could be list, dict, or string)
    """
    if isinstance(content, bytes):
        try:
            # Try msgpack first (new format)
            decoded = msgpack.unpackb(content, raw=False)
            return decoded
        except Exception:
            try:
                # Fallback to UTF-8 (legacy format)
                return content.decode("utf-8")
            except Exception:
                # Last resort: base64
                return "base64:" + base64.b64encode(content).decode()
    return content


class AxiomWebSocketClient:
    """
    # Axiom Trade WebSocket Client

    Manages real-time WebSocket connections to Axiom Trade platform for live data streaming.
    Handles authentication, subscription management, and message routing to callbacks.

    ## Architecture:
    - **Event-driven**: Uses callbacks to handle different message types
    - **Async/await**: Built on asyncio for efficient concurrent operations
    - **Authenticated**: Requires valid AuthManager with tokens
    - **Room-based**: Subscribes to specific "rooms" (channels) for different data

    ## Data Channels:
    1. **new_pairs**: New token listings
    2. **migrations**: Token contract migrations
    3. **b-{address}**: Token-specific market cap updates

    ## Connection Lifecycle:
    1. Initialize with AuthManager
    2. Connect to WebSocket (validates authentication)
    3. Subscribe to desired data channels
    4. Start message handler loop
    5. Close connection when done

    ## Design Decisions:
    - Callback pattern allows flexible message handling without inheritance
    - Stores callbacks in dict keyed by room/channel name
    - Separates connection, subscription, and message handling concerns
    - Uses logging extensively for debugging and monitoring

    ## Error Handling:
    - Validates authentication before connecting
    - Handles connection failures gracefully
    - Catches JSON parsing errors
    - Logs all errors with context

    ## Example:
    ```python
    async def handle_new_token(data):
        token_address = data.get('pair_address')
        print(f"New token: {token_address}")

    ws = AxiomTradeWebSocketClient(auth_manager)
    await ws.connect()
    await ws.subscribe_new_tokens(handle_new_token)
    await ws.start()  # Blocks while processing messages
    ```
    """

    def __init__(
        self, auth_manager: AuthManager, log_level: int = logging.INFO
    ) -> None:
        """
        Initialize WebSocket client with authentication manager.

        ## Args:
        - `auth_manager` (AuthManager): Authenticated AuthManager instance with valid tokens
        - `log_level` (int): Logging level (default: logging.INFO)

        ## Raises:
        - `ValueError`: If auth_manager is None or invalid

        ## Side Effects:
        - Sets up logger with console handler
        - Initializes empty callback registry
        - Stores reference to auth_manager for token access
        """
        # WebSocket URL - using cluster3 as primary endpoint
        # Alternative: wss://cluster-usc2.axiom.trade/
        self.ws_url = WS_PRIMARY_URL
        self.ws_pulse_url = WS_PULSE_URL

        # WebSocket connections (None until connected)
        # Type is Any to accommodate different websockets versions
        self.ws: Any = None  # Main WebSocket for regular channels
        self.ws_pulse: Any = (
            None  # Separate WebSocket for Pulse channel (binary messages)
        )

        # Validate auth_manager is provided
        if not auth_manager:
            raise ValueError(
                "auth_manager is required and must be an authenticated AuthManager instance"
            )

        self.auth_manager = auth_manager

        # Setup logger for debugging and monitoring
        self.logger = logging.getLogger("AxiomTradeWebSocket")
        self.logger.setLevel(log_level)

        # Create console handler if none exists
        if not self.logger.handlers:
            self._setup_logging_handler(log_level)

        # Callback registry: maps room names to callback functions
        # Format: {"new_pairs": callback_fn, "token_mcap_0x123...": callback_fn}
        self._callbacks: Dict[str, Callable] = {}

        # Reconnection configuration
        self._max_reconnect_attempts = 5
        self._reconnect_delay_seconds = 5
        self._is_reconnecting = False

        # Store subscriptions for reconnection
        self._active_subscriptions: Dict[str, Any] = {}

        # Telegram bot for notifications
        self._telegram_bot: Optional[Bot] = None
        self._telegram_chat_id: Optional[str] = None
        self._setup_telegram()

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

    async def _send_telegram_notification(self, message: str) -> None:
        """Send notification via Telegram if bot is configured."""
        if self._telegram_bot and self._telegram_chat_id:
            try:
                await self._telegram_bot.send_message(
                    chat_id=self._telegram_chat_id, text=message, parse_mode="HTML"
                )
            except Exception as e:
                self.logger.error(f"Failed to send Telegram notification: {e}")

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
                    elif connection_type == "pulse" and self.ws_pulse:
                        await self.ws_pulse.close()
                        self.ws_pulse = None

                    # Attempt reconnection
                    if connection_type == "main":
                        success = await self.connect()
                    else:  # pulse
                        # Reconnect pulse using stored subscription info
                        pulse_sub = self._active_subscriptions.get("pulse")
                        if pulse_sub:
                            success = await self.subscribe_pulse(
                                pulse_sub["callback"], pulse_sub.get("user_state")
                            )
                        else:
                            success = False

                    if not success:
                        continue

                    # Restore subscriptions for main connection
                    if connection_type == "main":
                        self.logger.info("Restoring subscriptions...")
                        for room, sub_info in self._active_subscriptions.items():
                            if sub_info["type"] == "regular":
                                try:
                                    await self._send_join_message(room)
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
                        f"✅ <b>WebSocket Reconnected</b>\n"
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
        # Validate authentication before attempting connection
        if not self.auth_manager.ensure_valid_authentication():
            self.logger.error(
                "WebSocket authentication failed - unable to obtain valid tokens"
            )
            self.logger.error("Please login with valid email and password")
            return False

        # Retrieve tokens from auth manager
        tokens = self.auth_manager.get_tokens()
        if not tokens:
            self.logger.error("No authentication tokens available")
            return False

        # Build WebSocket handshake headers
        headers = self._build_connection_headers(tokens)

        self.logger.debug(f"Connecting to WebSocket with headers: {headers}")
        self.logger.debug(
            f"Using tokens: access_token length={len(tokens.access_token)}, "
            f"refresh_token length={len(tokens.refresh_token)}"
        )

        try:
            # Attempt WebSocket connection with authentication
            self.logger.info(f"Attempting to connect to WebSocket: {self.ws_url}")
            self.ws = await websockets.connect(self.ws_url, additional_headers=headers)
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

    def _build_connection_headers(self, tokens) -> Dict[str, str]:
        """
        Build HTTP headers for WebSocket connection handshake.

        Constructs headers required by Axiom Trade WebSocket server,
        including authentication cookies.

        ## Args:
        - `tokens` (AuthTokens): Authentication tokens from auth_manager

        ## Returns:
        - `dict`: Headers dictionary for WebSocket connection

        ## Header Explanations:
        - **Origin**: Required by server for CORS validation
        - **Cookie**: Authentication tokens in cookie format
        - **User-Agent**: Browser-like UA for server compatibility
        - **Cache-Control/Pragma**: Prevent caching of connection
        - **Accept-Language**: Language preferences
        """
        headers = {
            "Origin": "https://axiom.trade",
            "Cache-Control": "no-cache",
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
            "Pragma": "no-cache",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/135.0.0.0 Safari/537.36 OPR/120.0.0.0"
            ),
        }

        # Add authentication cookies
        cookie_header = (
            f"auth-access-token={tokens.access_token}; "
            f"auth-refresh-token={tokens.refresh_token}"
        )
        headers["Cookie"] = cookie_header

        return headers

    async def subscribe_new_tokens(
        self, callback: Callable[[Dict[str, Any]], Awaitable[None]]
    ) -> bool:
        """
        Subscribe to new token listing notifications.

        Joins the "new_pairs" room to receive real-time notifications when
        new tokens are listed on the Axiom Trade platform.

        ## Args:
        - `callback` (Callable): Async function called when new token data arrives
          - Signature: `async def callback(data: Dict[str, Any]) -> None`
          - Data contains token information (address, name, symbol, etc.)

        ## Returns:
        - `bool`: True if subscription successful, False otherwise

        ## Message Format:
        Callback receives dict with structure:
        ```python
        {
            "room": "new_pairs",
            "pair_address": "0x...",
            "token_name": "TokenName",
            "token_symbol": "TKN",
            # ... other token metadata
        }
        ```

        ## Side Effects:
        - Sends "join" action to WebSocket server
        - Registers callback in internal registry
        - Ensures connection exists before subscribing

        ## Example:
        ```python
        async def on_new_token(data):
            print(f"New token: {data['token_name']} ({data['token_symbol']})")
            print(f"Address: {data['pair_address']}")

        await ws.subscribe_new_tokens(on_new_token)
        ```
        """
        # Ensure connection is established
        if not self.ws:
            if not await self.connect():
                return False

        # Register callback for new_pairs room
        self._callbacks[ROOM_NEW_PAIRS] = callback

        # Store subscription for reconnection
        self._active_subscriptions[ROOM_NEW_PAIRS] = {
            "type": "regular",
            "callback": callback,
        }

        try:
            # Send join message to server
            await self._send_join_message(ROOM_NEW_PAIRS)
            self.logger.info("✅ Subscribed to new token updates")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to subscribe to new tokens: {e}")
            return False

    async def subscribe_token_mcap(
        self, token_address: str, callback: Callable[[Dict[str, Any]], None]
    ) -> bool:
        """
        Subscribe to market cap updates for a specific token.

        Joins a token-specific room to receive real-time market cap and
        price updates for the given token address.

        ## Args:
        - `token_address` (str): Token contract address (e.g., "0x1234...")
        - `callback` (Callable): Async function called when market cap data arrives
          - Signature: `async def callback(data: Dict[str, Any]) -> None`

        ## Returns:
        - `bool`: True if subscription successful, False otherwise

        ## Room Format:
        Token-specific rooms use format: `b-{token_address}`
        Example: "b-0x1234567890abcdef..."

        ## Message Format:
        Callback receives dict with structure:
        ```python
        {
            "room": "b-0x...",
            "market_cap": 1000000,
            "price": 0.001,
            "volume_24h": 50000,
            # ... other market data
        }
        ```

        ## Side Effects:
        - Sends "join" action to WebSocket server
        - Registers callback with token-specific key
        - Connection must exist before subscribing

        ## Example:
        ```python
        async def on_mcap_update(data):
            print(f"Market cap: ${data['market_cap']:,.2f}")
            print(f"Price: ${data['price']:.6f}")

        token = "0x1234567890abcdef1234567890abcdef12345678"
        await ws.subscribe_token_mcap(token, on_mcap_update)
        ```
        """
        # Build room name: b-{token_address}
        room_name = f"{ROOM_TOKEN_PREFIX}{token_address}"

        # Register callback with token-specific key
        callback_key = f"token_mcap_{token_address}"
        self._callbacks[callback_key] = callback

        try:
            # Send join message to server
            await self._send_join_message(room_name)
            self.logger.info(f"✅ Subscribed to token mcap updates for {token_address}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to subscribe to token mcap updates: {e}")
            return False

    async def subscribe_migrations(
        self, callback: Callable[[Dict[str, Any]], None]
    ) -> bool:
        """
        Subscribe to token migration notifications.

        Joins the "migrations" room to receive real-time notifications about
        token contract migrations (when a token moves to a new contract address).

        ## Args:
        - `callback` (Callable): Async function called when migration data arrives
          - Signature: `async def callback(data: Dict[str, Any]) -> None`

        ## Returns:
        - `bool`: True if subscription successful, False otherwise

        ## Message Format:
        Callback receives dict with structure:
        ```python
        {
            "room": "migrations",
            "old_address": "0xOLD...",
            "new_address": "0xNEW...",
            "token_name": "TokenName",
            "migration_time": 1234567890,
            # ... other migration metadata
        }
        ```

        ## Use Case:
        Token migrations occur when:
        - Security upgrades to contract code
        - Moving to new blockchain/L2
        - Fixing bugs in original contract
        - Rebranding or token consolidation

        ## Side Effects:
        - Sends "join" action to WebSocket server
        - Registers callback in internal registry
        - Ensures connection exists before subscribing

        ## Example:
        ```python
        async def on_migration(data):
            print(f"Migration detected!")
            print(f"Token: {data['token_name']}")
            print(f"Old: {data['old_address']}")
            print(f"New: {data['new_address']}")

        await ws.subscribe_migrations(on_migration)
        ```
        """
        # Ensure connection is established
        if not self.ws:
            if not await self.connect():
                return False

        # Register callback for migrations room
        self._callbacks[ROOM_MIGRATIONS] = callback

        try:
            # Send join message to server
            await self._send_join_message(ROOM_MIGRATIONS)
            self.logger.info("✅ Subscribed to token migrations")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to subscribe to token migrations: {e}")
            return False

    async def subscribe_sol_price(
        self, callback: Callable[[Dict[str, Any]], None]
    ) -> bool:
        # Ensure connection is established
        if not self.ws:
            if not await self.connect():
                return False

        # Register callback for migrations room
        self._callbacks[ROOM_SOL_PRICE] = callback

        try:
            # Send join message to server
            await self._send_join_message(ROOM_SOL_PRICE)
            self.logger.info("✅ Subscribed to token sol price")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to subscribe to token sol price: {e}")
            return False

    async def subscribe_pulse(
        self,
        callback: Callable[[list[Any]], Awaitable[None]],
        user_state: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Subscribe to Pulse data stream with real-time token analytics.

        Pulse is a specialized WebSocket channel that provides advanced filtering
        and analytics data. It uses a separate WebSocket connection and binary
        message encoding for efficient data transfer.

        ## Args:
        - `callback` (Callable): Async function called when Pulse data arrives
          - Signature: `async def callback(data: Dict[str, Any]) -> None`
        - `user_state` (dict, optional): Custom filter configuration for Pulse
          - If not provided, uses default configuration from pulse_send_message.json

        ## Returns:
        - `bool`: True if subscription successful, False otherwise

        ## Pulse Features:
        - Advanced filtering (age, volume, holders, social metrics, etc.)
        - Table views (newPairs, finalStretch, migrated)
        - Protocol filtering (pump, raydium, moonshot, etc.)
        - Keyword search and blacklisting
        - Real-time bonding curve metrics

        ## Message Format:
        Messages are binary-encoded and automatically decoded to JSON.
        Callback receives dict with token analytics data.

        ## Connection:
        Uses separate WebSocket (`wss://pulse2.axiom.trade/ws`) from main channels.
        This allows independent connection management and binary protocol handling.

        ## Side Effects:
        - Establishes separate WebSocket connection to Pulse server
        - Sends userState configuration message
        - Registers callback for Pulse messages
        - Starts background task for Pulse message handling

        ## Example:
        ```python
        async def on_pulse_data(data):
            print(f"Pulse data: {data}")
            # Process filtered token data with advanced metrics

        # Use default filters
        await ws.subscribe_pulse(on_pulse_data)

        # Or provide custom filter configuration
        custom_state = {
            "type": "userState",
            "state": {
                "tables": {"newPairs": True},
                "filters": {...}
            }
        }
        await ws.subscribe_pulse(on_pulse_data, custom_state)
        ```
        """
        # Validate authentication before attempting connection
        if not self.auth_manager.ensure_valid_authentication():
            self.logger.error(
                "Pulse authentication failed - unable to obtain valid tokens"
            )
            return False

        # Get tokens for authentication
        tokens = self.auth_manager.get_tokens()
        if not tokens:
            self.logger.error("No authentication tokens available for Pulse")
            return False

        # Build headers for Pulse WebSocket
        headers = self._build_connection_headers(tokens)

        try:
            # Connect to Pulse WebSocket server
            self.logger.info(f"Connecting to Pulse WebSocket: {self.ws_pulse_url}")
            self.ws_pulse = await websockets.connect(
                self.ws_pulse_url, additional_headers=headers
            )
            self.logger.info("✅ Connected to Pulse WebSocket server")

            # Load user state configuration
            if user_state is None:
                user_state = self._load_default_pulse_config()

            # Send userState message to configure filters
            await self.ws_pulse.send(json.dumps(user_state))
            self.logger.info("✅ Sent Pulse configuration")

            # Register callback for Pulse messages
            self._callbacks["pulse"] = callback

            # Store subscription for reconnection
            self._active_subscriptions["pulse"] = {
                "type": "pulse",
                "callback": callback,
                "user_state": user_state,
            }

            # Note: _pulse_message_handler() should be started manually
            # by the caller using TaskGroup for proper error handling

            self.logger.info("✅ Subscribed to Pulse data stream")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to subscribe to Pulse: {e}", exc_info=True)
            return False

    def _load_default_pulse_config(self) -> Dict[str, Any]:
        """
        Load default Pulse configuration from pulse_send_message.json.

        Reads the default filter configuration file that defines which
        tokens and metrics should be included in the Pulse stream.

        ## Returns:
        - `dict`: Default userState configuration for Pulse

        ## Configuration Structure:
        ```python
        {
            "type": "userState",
            "state": {
                "tables": {"newPairs": True, ...},
                "filters": {"newPairs": {...}, ...},
                "blacklist": {...},
                ...
            }
        }
        ```

        ## File Location:
        Looks for `pulse_send_message.json` in the same directory as this module.
        """
        import pathlib

        # Get path to pulse_send_message.json in same directory
        config_path = pathlib.Path(__file__).parent / "pulse_send_message.json"

        try:
            with open(config_path, "r") as f:
                config = json.load(f)
            self.logger.debug("Loaded default Pulse configuration")
            return config
        except Exception as e:
            self.logger.warning(
                f"Failed to load pulse_send_message.json, using minimal config: {e}"
            )
            # Fallback to minimal configuration
            return {
                "type": "userState",
                "state": {
                    "tables": {
                        "newPairs": True,
                        "finalStretch": True,
                        "migrated": True,
                    },
                    "filters": {"newPairs": {}, "finalStretch": {}, "migrated": {}},
                },
            }

    async def _pulse_message_handler(self) -> None:
        """
        Handle incoming Pulse WebSocket messages (binary format).

        Receives binary-encoded messages from Pulse WebSocket, decodes them,
        parses as JSON, and routes to the Pulse callback.

        ## Process:
        1. Loop over incoming binary messages
        2. Decode binary content to string
        3. Parse JSON from decoded string
        4. Execute Pulse callback with parsed data

        ## Message Encoding:
        Pulse uses binary encoding for efficiency. Messages are decoded
        using `_decode_message_content()` which handles:
        - UTF-8 binary data
        - Base64-encoded fallback for non-UTF-8 data

        ## Error Handling:
        - Catches decode errors (logs and continues)
        - Catches JSON parsing errors (logs and continues)
        - Handles WebSocket connection close gracefully
        - Non-blocking: errors don't stop message processing

        ## Design Note:
        Runs in parallel with main `_message_handler()` to allow
        simultaneous processing of both regular and Pulse channels.
        """
        if not self.ws_pulse:
            self.logger.error("Cannot handle Pulse messages: WebSocket not connected")
            return

        try:
            self.logger.info("Started Pulse message handler")

            # Async iteration over Pulse WebSocket messages
            async for message in self.ws_pulse:
                decoded_message = ""  # Initialize to prevent unbound variable
                try:
                    # Decode binary message to string
                    decoded_message = _decode_message_content(message)

                    # Route to Pulse callback if registered
                    if "pulse" in self._callbacks:
                        await self._callbacks["pulse"](decoded_message)
                    else:
                        self.logger.debug(
                            "Pulse message received but no callback registered"
                        )

                except json.JSONDecodeError as e:
                    self.logger.error(
                        f"Failed to parse Pulse message as JSON: {decoded_message[:100]}..."
                    )
                    self.logger.debug(f"JSON decode error: {e}")

                except Exception as e:
                    self.logger.error(
                        f"Error handling Pulse message: {e}", exc_info=True
                    )

        except websockets.exceptions.ConnectionClosed as e:
            self.logger.warning(
                f"⚠️ Pulse WebSocket connection closed: code={e.code} reason={e.reason}"
            )

            # Send Telegram notification about disconnection
            await self._send_telegram_notification(
                f"⚠️ <b>WebSocket Disconnected</b>\n"
                f"Connection: pulse\n"
                f"Code: {e.code}\n"
                f"Reason: {e.reason or 'Unknown'}\n"
                f"Status: Attempting reconnection..."
            )

            # Attempt to reconnect
            if await self._reconnect(connection_type="pulse"):
                self.logger.info(
                    "✅ Pulse WebSocket reconnected, resuming message handling"
                )
                # Recursively restart handler after successful reconnection
                await self._pulse_message_handler()
            else:
                self.logger.error("❌ Failed to reconnect Pulse WebSocket")

        except Exception as e:
            self.logger.error(f"❌ Pulse message handler error: {e}", exc_info=True)

    async def unsubscribe_token_mcap(self, token_address: str) -> bool:
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
        # Build room name
        room_name = f"{ROOM_TOKEN_PREFIX}{token_address}"

        # Remove callback from registry
        callback_key = f"token_mcap_{token_address}"
        self._callbacks.pop(callback_key, None)

        try:
            # Send leave message to server
            await self._send_leave_message(room_name)
            self.logger.info(
                f"✅ Unsubscribed from token mcap updates for {token_address}"
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to unsubscribe from token mcap updates: {e}")
            return False

    async def _send_join_message(self, room: str) -> None:
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

        message = json.dumps({"action": "join", "room": room})
        await self.ws.send(message)

    async def _send_leave_message(self, room: str) -> None:
        """
        Send leave message to WebSocket server.

        ## Args:
        - `room` (str): Room name to leave

        ## Message Format:
        ```json
        {"action": "leave", "room": "room_name"}
        ```
        """
        if not self.ws:
            raise RuntimeError("WebSocket not connected")

        message = json.dumps({"action": "leave", "room": room})
        await self.ws.send(message)

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
                    room = data.get("room", "")

                    # Route message to appropriate callback based on room
                    await self._route_message(room, data)

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
                f"⚠️ <b>WebSocket Disconnected</b>\n"
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

    async def _route_message(self, room: str, data: Dict[str, Any]) -> None:
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
        if room == ROOM_NEW_PAIRS and ROOM_NEW_PAIRS in self._callbacks:
            await self._callbacks[ROOM_NEW_PAIRS](data)
            return

        if room == ROOM_MIGRATIONS and ROOM_MIGRATIONS in self._callbacks:
            await self._callbacks[ROOM_MIGRATIONS](data)
            return
        if room == ROOM_SOL_PRICE and ROOM_SOL_PRICE in self._callbacks:
            await self._callbacks[ROOM_SOL_PRICE](data)
            return
        # Handle token-specific rooms (format: b-{token_address})
        if room.startswith(ROOM_TOKEN_PREFIX):
            # Extract token address from room name
            token_address = room[len(ROOM_TOKEN_PREFIX) :]
            callback_key = f"token_mcap_{token_address}"

            if callback_key in self._callbacks:
                await self._callbacks[callback_key](data)
                return
            else:
                self.logger.debug(f"No callback registered for room: {room}")
        else:
            self.logger.debug(f"Unhandled room type: {room}")

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

        # Close Pulse WebSocket
        if self.ws_pulse:
            await self.ws_pulse.close()
            self.logger.info("✅ Pulse WebSocket connection closed")
