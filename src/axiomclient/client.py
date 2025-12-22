"""# Axiom Trade Client

Main client interface for interacting with the Axiom Trade platform.
Provides unified access to authentication, REST API endpoints, and WebSocket connections.

## Features

- **Automatic Authentication**: Handles login, token refresh, and secure token storage
- **REST API Access**: Methods for token info, portfolio, and other API endpoints
- **WebSocket Streaming**: Real-time market data through integrated WebSocket client
- **Token Management**: Automatic token refresh and secure encrypted storage
- **Error Handling**: Robust error handling with automatic retry on authentication failures

## Basic Usage

### Initialize with Credentials

```python
from axiomclient import AxiomTradeClient

client = AxiomTradeClient(
    username="user@example.com",
    password="your_password",
    use_saved_tokens=True  # Enable automatic token storage
)

# Login (tokens will be saved automatically)
result = client.login()
if result["success"]:
    print("Login successful!")
```

### Use Saved Tokens

```python
# Initialize without credentials - will load saved tokens
client = AxiomTradeClient(use_saved_tokens=True)

if client.is_authenticated():
    print("Using saved tokens")
else:
    # Need to login
    client.login(email="user@example.com", password="password")
```

### Access REST API

```python
# Get token information
token_info = client.get_token_info("token_address_here")
print(f"Token: {token_info['name']}")

# Get user portfolio
portfolio = client.get_user_portfolio()
print(f"Holdings: {portfolio}")

# Get token by pair address
pair_info = client.get_token_info_by_pair("pair_address_here")
```

### WebSocket Streaming

```python
import asyncio

async def main():
    client = AxiomTradeClient(username="user@example.com", password="password")

    # Connect to WebSocket
    await client.ws.connect()

    # Subscribe to new tokens
    await client.ws.subscribe_new_tokens(
        callback=lambda data: print(f"New token: {data}")
    )

    # Subscribe to token migrations
    await client.ws.subscribe_migrations(
        callback=lambda data: print(f"Migration: {data}")
    )

    # Subscribe to market cap updates
    await client.ws.subscribe_token_mcap(
        tokens=["token1", "token2"],
        callback=lambda data: print(f"Market cap: {data}")
    )

    # Subscribe to Pulse (binary protocol)
    await client.ws.subscribe_pulse(
        callback=lambda data: print(f"Pulse data: {data}")
    )

    # Keep running
    await asyncio.sleep(3600)

    # Clean up
    await client.ws.close()

asyncio.run(main())
```

### Manual Token Management

```python
# Set tokens directly
client.set_tokens(
    access_token="your_access_token",
    refresh_token="your_refresh_token"
)

# Get current tokens
tokens = client.get_tokens()
print(f"Access token: {tokens['access_token']}")
print(f"Expires at: {tokens['expires_at']}")
print(f"Is expired: {tokens['is_expired']}")

# Get detailed token info
info = client.get_token_info_detailed()
print(f"Token valid for: {info['time_until_expiry']} seconds")

# Manual token refresh
if client.refresh_access_token():
    print("Token refreshed successfully")

# Ensure valid authentication (auto-refresh if needed)
if client.ensure_authenticated():
    print("Authentication is valid")
```

### Logout and Clear Data

```python
# Logout and clear all tokens
client.logout()

# Just clear saved tokens from storage
client.clear_saved_tokens()

# Check if saved tokens exist
if client.has_saved_tokens():
    print("Saved tokens available")
```

## Error Handling

All API methods automatically ensure authentication before making requests:

```python
try:
    token_info = client.get_token_info("token_address")
except ValueError as e:
    # Authentication failed
    print(f"Auth error: {e}")
except Exception as e:
    # API request failed
    print(f"API error: {e}")
```

## Configuration

### Custom Storage Directory

```python
client = AxiomTradeClient(
    username="user@example.com",
    password="password",
    storage_dir="/custom/path/to/tokens",
    use_saved_tokens=True
)
```

### Disable Token Storage

```python
client = AxiomTradeClient(
    username="user@example.com",
    password="password",
    use_saved_tokens=False  # Tokens won't be saved
)
```

### Custom Logging Level

```python
import logging

client = AxiomTradeClient(
    username="user@example.com",
    password="password",
    log_level=logging.DEBUG  # Enable debug logging
)
```

## Properties

- `access_token`: Current access token (read-only)
- `refresh_token`: Current refresh token (read-only)
- `ws`: WebSocket client instance
- `auth_manager`: Authentication manager instance
- `auth`: Alias for auth_manager (backward compatibility)

## Thread Safety

The client is not thread-safe. Create separate instances for multi-threaded applications.
For async operations, use the WebSocket client methods with proper asyncio handling.
"""

import logging
from typing import Any, Dict, Optional

import httpx

from axiomclient.websocket._client import AxiomWebSocketClient
from .auth.auth_manager import AuthManager

# API endpoint constants
API_BASE_URL_V6 = "https://api6.axiom.trade"
API_BASE_URL_V10 = "https://api10.axiom.trade"

# HTTP headers for API requests
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Origin": "https://axiom.trade",
    "Connection": "keep-alive",
    "Referer": "https://axiom.trade/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}


class AxiomClient:
    """# Axiom Trade Client

    Main client for interacting with Axiom Trade API and WebSocket streams.
    Provides unified access to authentication, REST endpoints, and real-time data.

    ## Attributes

    - `auth_manager` (AuthManager): Handles authentication and token management
    - `auth` (AuthManager): Alias for auth_manager (backward compatibility)
    - `ws` (AxiomWebSocketClient): WebSocket client for real-time data streams
    - `logger` (logging.Logger): Logger instance for this client
    - `base_headers` (Dict[str, str]): Default HTTP headers for API requests

    ## Properties

    - `access_token`: Current access token (None if not authenticated)
    - `refresh_token`: Current refresh token (None if not authenticated)

    ## Authentication Flow

    1. Initialize with credentials or saved tokens
    2. Call `login()` or let methods auto-authenticate via `ensure_authenticated()`
    3. Tokens are automatically refreshed when needed
    4. Tokens can be saved securely to disk for future use

    ## Example

    ```python
    # Basic usage
    client = AxiomTradeClient(
        username="user@example.com",
        password="password",
        use_saved_tokens=True
    )

    # Login
    result = client.login()

    # Access REST API
    portfolio = client.get_user_portfolio()

    # Use WebSocket
    await client.ws.connect()
    await client.ws.subscribe_new_tokens(callback=handler)
    ```
    """

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        auth_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        storage_dir: Optional[str] = None,
        use_saved_tokens: bool = True,
        log_level: int = logging.INFO,
    ) -> None:
        """## Initialize Axiom Trade Client

        Creates a new client instance with authentication management and WebSocket support.

        ### Parameters

        - `username` (Optional[str]): Email address for automatic login. If provided with
          password, enables automatic authentication. Can be omitted if using saved tokens
          or setting tokens manually.

        - `password` (Optional[str]): Password for automatic login. Required if username
          is provided for authentication.

        - `auth_token` (Optional[str]): Existing access token to use instead of logging in.
          Useful when tokens are managed externally. Should be provided with refresh_token.

        - `refresh_token` (Optional[str]): Existing refresh token for automatic token renewal.
          Should be provided with auth_token.

        - `storage_dir` (Optional[str]): Custom directory for storing encrypted tokens.
          Defaults to `~/.axiom_tokens/` if not specified. Tokens are encrypted using
          Fernet encryption for security.

        - `use_saved_tokens` (bool): Enable automatic loading and saving of tokens to disk.
          When True, tokens are loaded from storage on initialization and saved after
          successful authentication. Defaults to True.

        - `log_level` (int): Logging level for the client and WebSocket client.
          Defaults to logging.INFO. Use logging.DEBUG for verbose output.

        ### Raises

        - `ValueError`: If invalid parameters are provided (e.g., username without password)

        ### Example

        ```python
        # With credentials (will auto-login)
        client = AxiomTradeClient(
            username="user@example.com",
            password="password"
        )

        # With saved tokens
        client = AxiomTradeClient(use_saved_tokens=True)

        # With manual tokens
        client = AxiomTradeClient(
            auth_token="access_token_here",
            refresh_token="refresh_token_here",
            use_saved_tokens=False
        )

        # With custom storage and logging
        client = AxiomTradeClient(
            username="user@example.com",
            password="password",
            storage_dir="/custom/path",
            log_level=logging.DEBUG
        )
        ```
        """
        # Initialize authentication manager
        self.auth_manager = AuthManager(
            username=username,
            password=password,
            auth_token=auth_token,
            refresh_token=refresh_token,
            storage_dir=storage_dir,
            use_saved_tokens=use_saved_tokens,
        )

        # Backward compatibility alias
        self.auth = self.auth_manager

        # Setup logging
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(log_level)

        # Default headers for API requests
        self.base_headers = DEFAULT_HEADERS.copy()

        # Initialize WebSocket client
        self.ws = AxiomWebSocketClient(
            auth_manager=self.auth_manager, log_level=log_level
        )

    @property
    def access_token(self) -> Optional[str]:
        """## Access Token Property

        Get the current access token used for API authentication.

        ### Returns

        - `Optional[str]`: Current access token, or None if not authenticated

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        token = client.access_token
        if token:
            print(f"Authenticated with token: {token[:20]}...")
        ```
        """
        return (
            self.auth_manager.tokens.access_token if self.auth_manager.tokens else None
        )

    @property
    def refresh_token(self) -> Optional[str]:
        """## Refresh Token Property

        Get the current refresh token used for obtaining new access tokens.

        ### Returns

        - `Optional[str]`: Current refresh token, or None if not authenticated

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        refresh = client.refresh_token
        if refresh:
            # Save for later use
            save_token_to_config(refresh)
        ```
        """
        return (
            self.auth_manager.tokens.refresh_token if self.auth_manager.tokens else None
        )

    def login(
        self, email: Optional[str] = None, password: Optional[str] = None
    ) -> Dict[str, Any]:
        """## Login to Axiom Trade

        Authenticate with email and password. Performs the full OTP-based authentication
        flow if needed. Tokens are automatically saved if `use_saved_tokens=True`.

        ### Parameters

        - `email` (Optional[str]): Email address for login. Falls back to username
          provided in constructor if not specified.

        - `password` (Optional[str]): Password for login. Falls back to password
          provided in constructor if not specified.

        ### Returns

        - `Dict[str, Any]`: Login result containing:
            - `success` (bool): True if login succeeded, False otherwise
            - `access_token` (str): JWT access token (on success)
            - `refresh_token` (str): JWT refresh token (on success)
            - `expires_at` (float): Token expiration timestamp (on success)
            - `message` (str): Human-readable status message

        ### Raises

        - `ValueError`: If email and password are not provided and not available from constructor

        ### Example

        ```python
        # Login with credentials
        client = AxiomTradeClient()
        result = client.login(email="user@example.com", password="password")

        if result["success"]:
            print(f"Login successful! Token expires at {result['expires_at']}")
        else:
            print(f"Login failed: {result['message']}")

        # Login using constructor credentials
        client = AxiomTradeClient(username="user@example.com", password="password")
        result = client.login()  # Uses stored credentials
        ```
        """
        # Use provided credentials or fall back to constructor values
        email = email or self.auth_manager.username
        password = password or self.auth_manager.password

        if not email or not password:
            raise ValueError(
                "Email and password are required for login. "
                "Provide them in login() call or constructor."
            )

        # Update auth manager credentials
        self.auth_manager.username = email
        self.auth_manager.password = password

        # Perform authentication
        success = self.auth_manager.authenticate()

        if success and self.auth_manager.tokens:
            self.logger.info("Login successful for %s", email)
            return {
                "success": True,
                "access_token": self.auth_manager.tokens.access_token,
                "refresh_token": self.auth_manager.tokens.refresh_token,
                "expires_at": self.auth_manager.tokens.expires_at,
                "message": "Login successful",
            }
        else:
            self.logger.warning("Login failed for %s", email)
            return {"success": False, "message": "Login failed"}

    def set_tokens(self, access_token: str, refresh_token: str) -> None:
        """## Set Authentication Tokens

        Manually set access and refresh tokens without performing login.
        Useful when tokens are obtained from external sources or saved configurations.

        ### Parameters

        - `access_token` (str): JWT access token for API authentication
        - `refresh_token` (str): JWT refresh token for obtaining new access tokens

        ### Example

        ```python
        client = AxiomTradeClient(use_saved_tokens=False)

        # Set tokens from external source
        client.set_tokens(
            access_token="eyJhbGc...",
            refresh_token="eyJhbGc..."
        )

        # Now can use API
        portfolio = client.get_user_portfolio()
        ```
        """
        self.auth_manager._set_tokens(access_token, refresh_token)
        self.logger.debug("Tokens set manually")

    def get_tokens(self) -> Dict[str, Any]:
        """## Get Current Tokens

        Retrieve information about current authentication tokens.

        ### Returns

        - `Dict[str, Any]`: Token information containing:
            - `access_token` (Optional[str]): Current access token or None
            - `refresh_token` (Optional[str]): Current refresh token or None
            - `expires_at` (Optional[float]): Expiration timestamp or None
            - `is_expired` (bool): Whether tokens are expired

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        tokens = client.get_tokens()
        print(f"Access token: {tokens['access_token'][:20]}...")
        print(f"Expires at: {tokens['expires_at']}")
        print(f"Is expired: {tokens['is_expired']}")

        # Check expiration
        if tokens['is_expired']:
            client.refresh_access_token()
        ```
        """
        tokens = self.auth_manager.tokens
        return {
            "access_token": tokens.access_token if tokens else None,
            "refresh_token": tokens.refresh_token if tokens else None,
            "expires_at": tokens.expires_at if tokens else None,
            "is_expired": tokens.is_expired if tokens else True,
        }

    def is_authenticated(self) -> bool:
        """## Check Authentication Status

        Check if the client has valid (non-expired) authentication tokens.

        ### Returns

        - `bool`: True if authenticated with valid tokens, False otherwise

        ### Example

        ```python
        client = AxiomTradeClient(use_saved_tokens=True)

        if client.is_authenticated():
            print("Ready to use API")
        else:
            print("Need to login")
            client.login(email="user@example.com", password="password")
        ```
        """
        return self.auth_manager.is_authenticated()

    def refresh_access_token(self) -> bool:
        """## Refresh Access Token

        Manually refresh the access token using the stored refresh token.
        This is usually called automatically by `ensure_authenticated()`.

        ### Returns

        - `bool`: True if refresh was successful, False otherwise

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        # Later, when token expires
        if not client.is_authenticated():
            if client.refresh_access_token():
                print("Token refreshed successfully")
            else:
                print("Refresh failed, need to login again")
                client.login()
        ```
        """
        success = self.auth_manager.refresh_tokens()
        if success:
            self.logger.debug("Access token refreshed successfully")
        else:
            self.logger.warning("Failed to refresh access token")
        return success

    def ensure_authenticated(self) -> bool:
        """## Ensure Valid Authentication

        Ensure the client has valid authentication tokens. Automatically refreshes
        expired tokens or re-authenticates if refresh fails.

        This method is called automatically by API methods before making requests.

        ### Returns

        - `bool`: True if valid authentication is available, False otherwise

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")

        # Ensure authentication before making requests
        if client.ensure_authenticated():
            # Safe to use API
            portfolio = client.get_user_portfolio()
        else:
            print("Authentication failed")

        # API methods call this automatically
        token_info = client.get_token_info("token_address")  # Auto-authenticates
        ```
        """
        return self.auth_manager.ensure_valid_authentication()

    def logout(self) -> None:
        """## Logout

        Clear all authentication data including in-memory tokens and saved tokens
        from secure storage. After logout, you'll need to login again to use the API.

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        # Use API...

        # Logout when done
        client.logout()

        # Now must login again
        print(client.is_authenticated())  # False
        ```
        """
        self.auth_manager.logout()
        self.logger.info("Logged out successfully")

    def clear_saved_tokens(self) -> bool:
        """## Clear Saved Tokens

        Remove saved tokens from secure storage without affecting in-memory tokens.
        The client will remain authenticated until tokens expire.

        ### Returns

        - `bool`: True if tokens were cleared successfully, False otherwise

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        # Clear saved tokens but keep session active
        if client.clear_saved_tokens():
            print("Saved tokens cleared")

        # Still authenticated in memory
        print(client.is_authenticated())  # True

        # But won't load saved tokens on next run
        ```
        """
        success = self.auth_manager.clear_saved_tokens()
        if success:
            self.logger.debug("Saved tokens cleared from storage")
        return success

    def has_saved_tokens(self) -> bool:
        """## Check for Saved Tokens

        Check if encrypted tokens exist in secure storage.

        ### Returns

        - `bool`: True if saved tokens exist, False otherwise

        ### Example

        ```python
        client = AxiomTradeClient(use_saved_tokens=True)

        if client.has_saved_tokens():
            print("Found saved tokens, loading...")
            # Tokens loaded automatically on init
        else:
            print("No saved tokens, need to login")
            client.login(email="user@example.com", password="password")
        ```
        """
        return self.auth_manager.has_saved_tokens()

    def get_token_info_detailed(self) -> Dict[str, Any]:
        """## Get Detailed Token Information

        Get comprehensive information about current authentication tokens including
        expiration details and validity status.

        ### Returns

        - `Dict[str, Any]`: Detailed token information containing:
            - `has_tokens` (bool): Whether tokens are available
            - `is_authenticated` (bool): Whether tokens are valid
            - `access_token` (Optional[str]): Current access token
            - `refresh_token` (Optional[str]): Current refresh token
            - `expires_at` (Optional[float]): Expiration timestamp
            - `is_expired` (bool): Whether tokens are expired
            - `time_until_expiry` (Optional[float]): Seconds until expiration

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        info = client.get_token_info_detailed()
        print(f"Authenticated: {info['is_authenticated']}")
        print(f"Expires in: {info['time_until_expiry']} seconds")
        print(f"Is expired: {info['is_expired']}")

        # Check if token needs refresh
        if info['time_until_expiry'] and info['time_until_expiry'] < 300:
            print("Token expires soon, refreshing...")
            client.refresh_access_token()
        ```
        """
        return self.auth_manager.get_token_info()

    def get_token_info(self, token_address: str) -> Dict[str, Any]:
        """## Get Token Information

        Retrieve detailed information about a specific token by its address.

        ### Parameters

        - `token_address` (str): The blockchain address of the token

        ### Returns

        - `Dict[str, Any]`: Token information from the API

        ### Raises

        - `ValueError`: If authentication fails
        - `httpx.HTTPStatusError`: If the API request fails
        - `Exception`: For other errors during the request

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        # Get token info by address
        token_info = client.get_token_info("0x1234567890abcdef...")
        print(f"Token name: {token_info['name']}")
        print(f"Token symbol: {token_info['symbol']}")
        print(f"Market cap: {token_info['marketCap']}")
        ```
        """
        # Ensure valid authentication
        if not self.ensure_authenticated():
            self.logger.error("Authentication failed when getting token info")
            raise ValueError(
                "Authentication failed. Please login first or check your credentials."
            )

        url = f"{API_BASE_URL_V6}/token/{token_address}"
        self.logger.debug("Getting token info for %s", token_address)

        try:
            response = self.auth_manager.make_authenticated_request("GET", url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            self.logger.error(
                "HTTP error getting token info: %s - %s", e.response.status_code, e
            )
            raise Exception(
                f"Failed to get token info (HTTP {e.response.status_code}): {e}"
            ) from e
        except Exception as e:
            self.logger.error("Error getting token info: %s", e)
            raise Exception(f"Failed to get token info: {e}") from e

    def get_user_portfolio(self) -> Dict[str, Any]:
        """## Get User Portfolio

        Retrieve the authenticated user's portfolio information including holdings,
        positions, and balances.

        ### Returns

        - `Dict[str, Any]`: Portfolio information from the API

        ### Raises

        - `ValueError`: If authentication fails
        - `httpx.HTTPStatusError`: If the API request fails
        - `Exception`: For other errors during the request

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        # Get portfolio
        portfolio = client.get_user_portfolio()
        print(f"Total value: ${portfolio['totalValue']}")
        print(f"Holdings: {len(portfolio['holdings'])} tokens")

        for holding in portfolio['holdings']:
            print(f"- {holding['symbol']}: {holding['amount']} tokens")
        ```
        """
        # Ensure valid authentication
        if not self.ensure_authenticated():
            self.logger.error("Authentication failed when getting portfolio")
            raise ValueError(
                "Authentication failed. Please login first or check your credentials."
            )

        url = f"{API_BASE_URL_V6}/portfolio"
        self.logger.debug("Getting user portfolio")

        try:
            response = self.auth_manager.make_authenticated_request("GET", url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            self.logger.error(
                "HTTP error getting portfolio: %s - %s", e.response.status_code, e
            )
            raise Exception(
                f"Failed to get portfolio (HTTP {e.response.status_code}): {e}"
            ) from e
        except Exception as e:
            self.logger.error("Error getting portfolio: %s", e)
            raise Exception(f"Failed to get portfolio: {e}") from e

    def get_token_info_by_pair(self, pair_address: str) -> Dict[str, Any]:
        """## Get Token Information by Pair Address

        Retrieve token information using a trading pair address instead of token address.
        Useful when working with DEX pairs.

        ### Parameters

        - `pair_address` (str): The blockchain address of the trading pair

        ### Returns

        - `Dict[str, Any]`: Token information from the API

        ### Raises

        - `ValueError`: If authentication fails
        - `httpx.HTTPStatusError`: If the API request fails
        - `Exception`: For other errors during the request

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        # Get token info by pair address
        pair_info = client.get_token_info_by_pair("0xabcdef1234567890...")
        print(f"Token: {pair_info['token']['symbol']}")
        print(f"Pair: {pair_info['pair']['name']}")
        print(f"Liquidity: ${pair_info['liquidity']}")
        print(f"Volume 24h: ${pair_info['volume24h']}")
        ```
        """
        # Ensure valid authentication
        if not self.ensure_authenticated():
            self.logger.error("Authentication failed when getting token info by pair")
            raise ValueError(
                "Authentication failed. Please login first or check your credentials."
            )

        url = f"{API_BASE_URL_V10}/token-info?pairAddress={pair_address}"
        self.logger.debug("Getting token info for pair %s", pair_address)

        try:
            response = self.auth_manager.make_authenticated_request("GET", url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            self.logger.error(
                "HTTP error getting token info by pair: %s - %s",
                e.response.status_code,
                e,
            )
            raise Exception(
                f"Failed to get token info by pair (HTTP {e.response.status_code}): {e}"
            ) from e
        except Exception as e:
            self.logger.error("Error getting token info by pair: %s", e)
            raise Exception(f"Failed to get token info by pair: {e}") from e

    async def subscribe_new_tokens(self, callback):
        """## Subscribe to New Token Updates

        Subscribe to real-time updates for newly created tokens via WebSocket.
        This is a convenience method that delegates to the WebSocket client.

        ### Parameters

        - `callback` (Callable): Function called when new token data is received.
          Receives a dict with token information.

        ### Example

        ```python
        async def handle_new_token(data):
            print(f"New token: {data['symbol']} at {data['address']}")

        client = AxiomTradeClient(username="user@example.com", password="password")
        await client.ws.connect()
        await client.subscribe_new_tokens(callback=handle_new_token)
        ```
        """
        return await self.ws.subscribe_new_tokens(callback)

    async def subscribe_migrations(self, callback):
        """## Subscribe to Token Migrations

        Subscribe to real-time token migration events via WebSocket.
        This is a convenience method that delegates to the WebSocket client.

        ### Parameters

        - `callback` (Callable): Function called when migration data is received.
          Receives a dict with migration information.

        ### Example

        ```python
        async def handle_migration(data):
            print(f"Migration: {data['from']} -> {data['to']}")

        client = AxiomTradeClient(username="user@example.com", password="password")
        await client.ws.connect()
        await client.subscribe_migrations(callback=handle_migration)
        ```
        """
        return await self.ws.subscribe_migrations(callback)

    def get_trending_tokens(self, time_period: str = "1h") -> Dict[str, Any]:
        """## Get Trending Meme Tokens

        Retrieve the most trending meme tokens for a specified time period.
        Returns tokens sorted by popularity, volume, or other trending metrics.

        ### Parameters

        - `time_period` (str): Time period for trending calculation. Valid values:
            - `"1h"`: Last 1 hour (default)
            - `"24h"`: Last 24 hours
            - `"7d"`: Last 7 days

        ### Returns

        - `Dict[str, Any]`: Trending tokens information from the API

        ### Raises

        - `ValueError`: If authentication fails or invalid time_period provided
        - `httpx.HTTPStatusError`: If the API request fails
        - `Exception`: For other errors during the request

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        # Get trending tokens for last hour
        trending_1h = client.get_trending_tokens(time_period="1h")
        print(f"Top trending token: {trending_1h[0]['symbol']}")

        # Get trending tokens for last 24 hours
        trending_24h = client.get_trending_tokens(time_period="24h")
        for token in trending_24h[:10]:
            print(f"{token['symbol']}: Volume ${token['volume']}")

        # Get trending tokens for last 7 days
        trending_7d = client.get_trending_tokens(time_period="7d")
        ```
        """
        # Validate time period
        valid_periods = ["1h", "24h", "7d"]
        if time_period not in valid_periods:
            raise ValueError(
                f"Invalid time_period '{time_period}'. "
                f"Must be one of: {', '.join(valid_periods)}"
            )

        # Ensure valid authentication
        if not self.ensure_authenticated():
            self.logger.error("Authentication failed when getting trending tokens")
            raise ValueError(
                "Authentication failed. Please login first or check your credentials."
            )

        url = f"{API_BASE_URL_V6}/meme-trending?timePeriod={time_period}"
        self.logger.debug("Getting trending tokens for period: %s", time_period)

        try:
            response = self.auth_manager.make_authenticated_request("GET", url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            self.logger.error(
                "HTTP error getting trending tokens: %s - %s",
                e.response.status_code,
                e,
            )
            raise Exception(
                f"Failed to get trending tokens (HTTP {e.response.status_code}): {e}"
            ) from e
        except Exception as e:
            self.logger.error("Error getting trending tokens: %s", e)
            raise Exception(f"Failed to get trending tokens: {e}") from e

    def get_last_transaction(self, pair_address: str) -> Dict[str, Any]:
        """## Get Last Transaction for Pair

        Retrieve the most recent transaction for a specific trading pair.
        Useful for monitoring real-time trading activity.

        ### Parameters

        - `pair_address` (str): The blockchain address of the trading pair

        ### Returns

        - `Dict[str, Any]`: Last transaction information containing:
            - Transaction hash, timestamp, type (buy/sell)
            - Amount, price, sender/receiver addresses
            - Other transaction-specific data

        ### Raises

        - `ValueError`: If authentication fails
        - `httpx.HTTPStatusError`: If the API request fails
        - `Exception`: For other errors during the request

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        # Get last transaction for a pair
        last_tx = client.get_last_transaction("0xabcdef1234567890...")
        print(f"Type: {last_tx['type']}")
        print(f"Amount: {last_tx['amount']} tokens")
        print(f"Price: ${last_tx['price']}")
        print(f"Time: {last_tx['timestamp']}")
        print(f"Hash: {last_tx['hash']}")
        ```
        """
        # Ensure valid authentication
        if not self.ensure_authenticated():
            self.logger.error("Authentication failed when getting last transaction")
            raise ValueError(
                "Authentication failed. Please login first or check your credentials."
            )

        url = f"{API_BASE_URL_V10}/last-transaction?pairAddress={pair_address}"
        self.logger.debug("Getting last transaction for pair %s", pair_address)

        try:
            response = self.auth_manager.make_authenticated_request("GET", url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            self.logger.error(
                "HTTP error getting last transaction: %s - %s",
                e.response.status_code,
                e,
            )
            raise Exception(
                f"Failed to get last transaction (HTTP {e.response.status_code}): {e}"
            ) from e
        except Exception as e:
            self.logger.error("Error getting last transaction: %s", e)
            raise Exception(f"Failed to get last transaction: {e}") from e

    def get_pair_info(self, pair_address: str) -> Dict[str, Any]:
        """## Get Trading Pair Information

        Retrieve comprehensive information about a specific trading pair including
        token details, liquidity, reserves, and metadata.

        ### Parameters

        - `pair_address` (str): The blockchain address of the trading pair

        ### Returns

        - `Dict[str, Any]`: Pair information containing:
            - Token addresses and symbols
            - Reserve amounts for both tokens
            - Total liquidity value
            - Creation date and DEX information
            - Other pair-specific metadata

        ### Raises

        - `ValueError`: If authentication fails
        - `httpx.HTTPStatusError`: If the API request fails
        - `Exception`: For other errors during the request

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        # Get pair information
        pair_info = client.get_pair_info("0xabcdef1234567890...")
        print(f"Pair: {pair_info['token0']['symbol']}/{pair_info['token1']['symbol']}")
        print(f"Liquidity: ${pair_info['liquidityUSD']}")
        print(f"Reserve0: {pair_info['reserve0']}")
        print(f"Reserve1: {pair_info['reserve1']}")
        print(f"DEX: {pair_info['dex']}")
        ```
        """
        # Ensure valid authentication
        if not self.ensure_authenticated():
            self.logger.error("Authentication failed when getting pair info")
            raise ValueError(
                "Authentication failed. Please login first or check your credentials."
            )

        url = f"{API_BASE_URL_V10}/pair-info?pairAddress={pair_address}"
        self.logger.debug("Getting pair info for %s", pair_address)

        try:
            response = self.auth_manager.make_authenticated_request("GET", url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            self.logger.error(
                "HTTP error getting pair info: %s - %s", e.response.status_code, e
            )
            raise Exception(
                f"Failed to get pair info (HTTP {e.response.status_code}): {e}"
            ) from e
        except Exception as e:
            self.logger.error("Error getting pair info: %s", e)
            raise Exception(f"Failed to get pair info: {e}") from e

    def get_pair_stats(self, pair_address: str) -> Dict[str, Any]:
        """## Get Trading Pair Statistics

        Retrieve statistical data and trading metrics for a specific trading pair.
        Includes volume, price changes, transaction counts, and other analytics.

        ### Parameters

        - `pair_address` (str): The blockchain address of the trading pair

        ### Returns

        - `Dict[str, Any]`: Pair statistics containing:
            - Trading volume (24h, 7d, etc.)
            - Price changes and percentage moves
            - Transaction counts (buys, sells, total)
            - High/low prices for various timeframes
            - Other statistical metrics

        ### Raises

        - `ValueError`: If authentication fails
        - `httpx.HTTPStatusError`: If the API request fails
        - `Exception`: For other errors during the request

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        # Get pair statistics
        stats = client.get_pair_stats("0xabcdef1234567890...")
        print(f"24h Volume: ${stats['volume24h']}")
        print(f"Price Change 24h: {stats['priceChange24h']}%")
        print(f"Transactions 24h: {stats['txCount24h']}")
        print(f"24h High: ${stats['high24h']}")
        print(f"24h Low: ${stats['low24h']}")
        print(f"Current Price: ${stats['currentPrice']}")
        ```
        """
        # Ensure valid authentication
        if not self.ensure_authenticated():
            self.logger.error("Authentication failed when getting pair stats")
            raise ValueError(
                "Authentication failed. Please login first or check your credentials."
            )

        url = f"{API_BASE_URL_V10}/pair-stats?pairAddress={pair_address}"
        self.logger.debug("Getting pair stats for %s", pair_address)

        try:
            response = self.auth_manager.make_authenticated_request("GET", url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            self.logger.error(
                "HTTP error getting pair stats: %s - %s", e.response.status_code, e
            )
            raise Exception(
                f"Failed to get pair stats (HTTP {e.response.status_code}): {e}"
            ) from e
        except Exception as e:
            self.logger.error("Error getting pair stats: %s", e)
            raise Exception(f"Failed to get pair stats: {e}") from e

    def get_meme_open_positions(self, wallet_address: str) -> Dict[str, Any]:
        """## Get Open Meme Token Positions

        Retrieve all currently open (non-closed) meme token positions for a specific wallet.
        Useful for tracking active holdings and calculating portfolio value.

        ### Parameters

        - `wallet_address` (str): The blockchain wallet address to query

        ### Returns

        - `Dict[str, Any]`: Open positions information containing:
            - List of open positions with token details
            - Entry prices, current prices, and P&L
            - Position sizes and values
            - Token metadata for each position

        ### Raises

        - `ValueError`: If authentication fails
        - `httpx.HTTPStatusError`: If the API request fails
        - `Exception`: For other errors during the request

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        # Get open positions for a wallet
        positions = client.get_meme_open_positions("0x1234567890abcdef...")

        print(f"Total positions: {len(positions['positions'])}")
        print(f"Total value: ${positions['totalValue']}")

        for position in positions['positions']:
            print(f"\nToken: {position['symbol']}")
            print(f"Amount: {position['amount']}")
            print(f"Entry: ${position['entryPrice']}")
            print(f"Current: ${position['currentPrice']}")
            print(f"P&L: {position['profitLoss']}%")
        ```
        """
        # Ensure valid authentication
        if not self.ensure_authenticated():
            self.logger.error("Authentication failed when getting open positions")
            raise ValueError(
                "Authentication failed. Please login first or check your credentials."
            )

        url = f"{API_BASE_URL_V10}/meme-open-positions?walletAddress={wallet_address}"
        self.logger.debug("Getting open positions for wallet %s", wallet_address)

        try:
            response = self.auth_manager.make_authenticated_request("GET", url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            self.logger.error(
                "HTTP error getting open positions: %s - %s",
                e.response.status_code,
                e,
            )
            raise Exception(
                f"Failed to get open positions (HTTP {e.response.status_code}): {e}"
            ) from e
        except Exception as e:
            self.logger.error("Error getting open positions: %s", e)
            raise Exception(f"Failed to get open positions: {e}") from e

    def get_holder_data(
        self, pair_address: str, only_tracked_wallets: bool = False
    ) -> Dict[str, Any]:
        """## Get Token Holder Data

        Retrieve detailed information about token holders for a specific trading pair.
        Includes holder addresses, balances, percentages, and optional tracking data.

        ### Parameters

        - `pair_address` (str): The blockchain address of the trading pair

        - `only_tracked_wallets` (bool): If True, only return data for wallets that are
          being tracked/monitored by the platform (e.g., notable traders, whales).
          If False (default), return all holders.

        ### Returns

        - `Dict[str, Any]`: Holder data information containing:
            - List of holder addresses with balances
            - Percentage ownership for each holder
            - Total holder count
            - Distribution metrics (concentration, etc.)
            - Tracked wallet flags and metadata

        ### Raises

        - `ValueError`: If authentication fails
        - `httpx.HTTPStatusError`: If the API request fails
        - `Exception`: For other errors during the request

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        # Get all holder data
        all_holders = client.get_holder_data("0xabcdef1234567890...")
        print(f"Total holders: {all_holders['totalHolders']}")
        print(f"Top 10 hold: {all_holders['top10Percentage']}%")

        # Get only tracked wallets (whales, notable traders)
        tracked = client.get_holder_data(
            pair_address="0xabcdef1234567890...",
            only_tracked_wallets=True
        )

        for holder in tracked['holders'][:5]:
            print(f"\nWallet: {holder['address']}")
            print(f"Balance: {holder['balance']} tokens")
            print(f"Percentage: {holder['percentage']}%")
            print(f"Tracked: {holder['isTracked']}")
        ```
        """
        # Ensure valid authentication
        if not self.ensure_authenticated():
            self.logger.error("Authentication failed when getting holder data")
            raise ValueError(
                "Authentication failed. Please login first or check your credentials."
            )

        url = (
            f"{API_BASE_URL_V10}/holder-data-v3?pairAddress={pair_address}"
            f"&onlyTrackedWallets={str(only_tracked_wallets).lower()}"
        )
        self.logger.debug(
            "Getting holder data for pair %s (tracked only: %s)",
            pair_address,
            only_tracked_wallets,
        )

        try:
            response = self.auth_manager.make_authenticated_request("GET", url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            self.logger.error(
                "HTTP error getting holder data: %s - %s", e.response.status_code, e
            )
            raise Exception(
                f"Failed to get holder data (HTTP {e.response.status_code}): {e}"
            ) from e
        except Exception as e:
            self.logger.error("Error getting holder data: %s", e)
            raise Exception(f"Failed to get holder data: {e}") from e

    def get_dev_tokens(self, dev_address: str) -> Dict[str, Any]:
        """## Get Developer's Created Tokens

        Retrieve all tokens created by a specific developer/deployer address.
        Useful for analyzing developer track record and finding related tokens.

        ### Parameters

        - `dev_address` (str): The blockchain address of the token developer/deployer

        ### Returns

        - `Dict[str, Any]`: Developer tokens information containing:
            - List of all tokens created by this developer
            - Token metadata (name, symbol, address)
            - Performance metrics for each token
            - Creation dates and deployment details
            - Overall developer statistics

        ### Raises

        - `ValueError`: If authentication fails
        - `httpx.HTTPStatusError`: If the API request fails
        - `Exception`: For other errors during the request

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        # Get all tokens by a developer
        dev_tokens = client.get_dev_tokens("0x9876543210fedcba...")

        print(f"Developer: {dev_tokens['developerAddress']}")
        print(f"Total tokens created: {len(dev_tokens['tokens'])}")
        print(f"Success rate: {dev_tokens['successRate']}%")

        for token in dev_tokens['tokens']:
            print(f"\nToken: {token['symbol']} ({token['name']})")
            print(f"Address: {token['address']}")
            print(f"Created: {token['createdAt']}")
            print(f"ATH Market Cap: ${token['athMarketCap']}")
            print(f"Current Status: {token['status']}")
        ```
        """
        # Ensure valid authentication
        if not self.ensure_authenticated():
            self.logger.error("Authentication failed when getting dev tokens")
            raise ValueError(
                "Authentication failed. Please login first or check your credentials."
            )

        url = f"{API_BASE_URL_V10}/dev-tokens-v2?devAddress={dev_address}"
        self.logger.debug("Getting tokens for developer %s", dev_address)

        try:
            response = self.auth_manager.make_authenticated_request("GET", url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            self.logger.error(
                "HTTP error getting dev tokens: %s - %s", e.response.status_code, e
            )
            raise Exception(
                f"Failed to get dev tokens (HTTP {e.response.status_code}): {e}"
            ) from e
        except Exception as e:
            self.logger.error("Error getting dev tokens: %s", e)
            raise Exception(f"Failed to get dev tokens: {e}") from e

    def get_token_analysis(self, dev_address: str, token_ticker: str) -> Dict[str, Any]:
        """## Get Token Analysis

        Retrieve comprehensive analysis for a specific token created by a developer.
        Combines developer history with token-specific metrics for detailed insights.

        ### Parameters

        - `dev_address` (str): The blockchain address of the token developer/deployer

        - `token_ticker` (str): The ticker symbol of the token to analyze (e.g., "PEPE")

        ### Returns

        - `Dict[str, Any]`: Token analysis information containing:
            - Detailed token metrics and performance data
            - Developer track record and patterns
            - Risk assessment and scoring
            - Holder distribution analysis
            - Liquidity and volume metrics
            - Historical performance comparison
            - Red flags and positive indicators

        ### Raises

        - `ValueError`: If authentication fails
        - `httpx.HTTPStatusError`: If the API request fails
        - `Exception`: For other errors during the request

        ### Example

        ```python
        client = AxiomTradeClient(username="user@example.com", password="password")
        client.login()

        # Analyze a specific token by developer and ticker
        analysis = client.get_token_analysis(
            dev_address="0x9876543210fedcba...",
            token_ticker="PEPE"
        )

        print(f"Token: {analysis['token']['name']} ({analysis['token']['ticker']})")
        print(f"Risk Score: {analysis['riskScore']}/100")
        print(f"Liquidity: ${analysis['liquidity']}")
        print(f"Holder Count: {analysis['holderCount']}")
        print(f"Dev Success Rate: {analysis['developer']['successRate']}%")

        # Check for red flags
        if analysis['redFlags']:
            print("\n⚠️ Red Flags:")
            for flag in analysis['redFlags']:
                print(f"- {flag}")

        # Check for positive indicators
        if analysis['positiveIndicators']:
            print("\n✅ Positive Indicators:")
            for indicator in analysis['positiveIndicators']:
                print(f"- {indicator}")
        ```
        """
        # Ensure valid authentication
        if not self.ensure_authenticated():
            self.logger.error("Authentication failed when getting token analysis")
            raise ValueError(
                "Authentication failed. Please login first or check your credentials."
            )

        url = (
            f"{API_BASE_URL_V10}/token-analysis?devAddress={dev_address}"
            f"&tokenTicker={token_ticker}"
        )
        self.logger.debug(
            "Getting token analysis for %s by developer %s",
            token_ticker,
            dev_address,
        )

        try:
            response = self.auth_manager.make_authenticated_request("GET", url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            self.logger.error(
                "HTTP error getting token analysis: %s - %s",
                e.response.status_code,
                e,
            )
            raise Exception(
                f"Failed to get token analysis (HTTP {e.response.status_code}): {e}"
            ) from e
        except Exception as e:
            self.logger.error("Error getting token analysis: %s", e)
            raise Exception(f"Failed to get token analysis: {e}") from e
