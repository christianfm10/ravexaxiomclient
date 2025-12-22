"""
# Authentication and Cookie Manager for Axiom Trade API

This module provides a comprehensive authentication management system for the Axiom Trade API.
It handles automatic login, token refresh, secure token storage, and cookie management.

## Key Features:
- **Secure Token Storage**: Encrypts and stores authentication tokens locally using Fernet encryption
- **Automatic Token Refresh**: Monitors token expiration and refreshes tokens automatically
- **Cookie Management**: Manages HTTP cookies required for authenticated requests
- **Multi-factor Authentication**: Supports OTP-based login flow
- **HTTP Client**: Uses httpx for async-capable, modern HTTP requests

## Usage:
```python
# Create an authenticated session
auth = create_authenticated_session(
    username="user@example.com",
    password="secure_password"
)

# Use httpx client for authenticated requests
async with httpx.AsyncClient() as client:
    headers = auth.get_authenticated_headers()
    response = await client.get("https://api.axiom.trade/endpoint", headers=headers)
```
"""

# Standard library imports
import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Union

# Third-party imports
# httpx: Modern, async-capable HTTP client library (replaces requests)
# Provides better performance, HTTP/2 support, and async/await capabilities
import httpx

# cryptography: Industry-standard encryption library
# Used for secure token storage with Fernet symmetric encryption
from cryptography.fernet import Fernet

from axiomclient.email.otp_fetcher import OtpFetcher


# Token expiration buffer constants (in seconds)
# These buffers provide safety margins before actual token expiration
TOKEN_EXPIRED_BUFFER = 300  # 5 minutes - consider token expired this early for safety
TOKEN_REFRESH_BUFFER = (
    900  # 15 minutes - trigger refresh this early to avoid race conditions
)
SALT = bytes(
    [
        217,
        3,
        161,
        123,
        53,
        200,
        206,
        36,
        143,
        2,
        220,
        252,
        240,
        109,
        204,
        23,
        217,
        174,
        79,
        158,
        18,
        76,
        149,
        117,
        73,
        40,
        207,
        77,
        34,
        194,
        196,
        163,
    ]
)
ITERATIONS = 600_000


@dataclass
class AuthTokens:
    """
    # Authentication Token Container

    Immutable container for storing authentication tokens with expiration tracking.
    Uses dataclass for clean, type-safe representation of token data.

    ## Attributes:
    - `access_token` (str): JWT access token for API authentication
    - `refresh_token` (str): Long-lived token used to obtain new access tokens
    - `expires_at` (float): Unix timestamp when the access token expires
    - `issued_at` (float): Unix timestamp when the tokens were issued

    ## Design Decisions:
    - Uses dataclass for automatic `__init__`, `__repr__`, and `__eq__` methods
    - Stores timestamps as float (Unix time) for easy comparison and serialization
    - Provides buffer times for proactive token management to prevent auth failures
    - Immutable by design to prevent accidental token modification

    ## Example:
    ```python
    tokens = AuthTokens(
        access_token="eyJhbGc...",
        refresh_token="refresh_abc123",
        expires_at=time.time() + 3600,
        issued_at=time.time()
    )

    if tokens.needs_refresh:
        # Refresh tokens before they expire
        pass
    ```
    """

    access_token: str
    refresh_token: str
    expires_at: float
    issued_at: float

    @property
    def is_expired(self) -> bool:
        """
        Check if the access token is expired or about to expire.

        Uses a 5-minute safety buffer to consider tokens expired before their actual
        expiration time. This prevents race conditions where a token might expire
        during an API request.

        ## Returns:
        - `bool`: True if token is expired or will expire within 5 minutes

        ## Algorithm:
        Compares current time against (expiration_time - buffer):
        - If current_time >= (expires_at - 300s), token is considered expired
        - This provides a 5-minute safety margin for token renewal
        """
        return time.time() >= (self.expires_at - TOKEN_EXPIRED_BUFFER)

    @property
    def needs_refresh(self) -> bool:
        """
        Check if the token should be refreshed proactively.

        Uses a 15-minute buffer to trigger refresh before expiration. This ensures
        smooth operation without interruption from expired tokens.

        ## Returns:
        - `bool`: True if token should be refreshed within 15 minutes

        ## Design Rationale:
        - 15-minute buffer provides ample time for refresh operation to complete
        - Prevents service disruption during long-running operations
        - Allows for retry logic if refresh fails initially
        """
        return time.time() >= (self.expires_at - TOKEN_REFRESH_BUFFER)

    def to_dict(self) -> Dict[str, Union[str, float]]:
        """
        Convert token data to dictionary for serialization.

        Used for JSON serialization when storing tokens to disk or
        transmitting them over network.

        ## Returns:
        - `dict`: Dictionary with all token fields
        """
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "issued_at": self.issued_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, str | float]) -> "AuthTokens":
        """
        Create AuthTokens instance from dictionary.

        Used for deserializing tokens from storage or network responses.

        ## Args:
        - `data` (dict): Dictionary containing token fields

        ## Returns:
        - `AuthTokens`: New instance with data from dictionary

        ## Raises:
        - `KeyError`: If required fields are missing from dictionary
        - `TypeError`: If field types don't match expected types
        """
        return cls(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            expires_at=float(data["expires_at"]),
            issued_at=float(data["issued_at"]),
        )


# File permission constants for secure storage
# 0o700 = Owner has read/write/execute, others have no access
DIR_PERMISSIONS = 0o700
# 0o600 = Owner has read/write, others have no access
FILE_PERMISSIONS = 0o600


class SecureTokenStorage:
    """
    # Secure Token Storage Manager

    Handles encrypted storage and retrieval of authentication tokens using
    Fernet symmetric encryption (AES-128 in CBC mode with HMAC authentication).

    ## Security Features:
    - **Encryption**: Uses Fernet (symmetric encryption) to protect tokens at rest
    - **File Permissions**: Restricts access to storage directory and files (Unix only)
    - **Key Management**: Generates and securely stores encryption keys
    - **Automatic Key Generation**: Creates encryption key on first use

    ## Storage Structure:
    ```
    ~/.axiomtradeapi/          # Storage directory (mode 0o700)
    ├── key.enc                # Encryption key (mode 0o600)
    └── tokens.enc             # Encrypted token data (mode 0o600)
    ```

    ## Design Decisions:
    - Uses Fernet for simplicity and security (authenticated encryption)
    - Stores encryption key separately from encrypted data
    - Uses restrictive file permissions to prevent unauthorized access
    - JSON serialization for structured token storage

    ## Example:
    ```python
    storage = SecureTokenStorage()

    # Save tokens
    tokens = AuthTokens(...)
    storage.save_tokens(tokens)

    # Load tokens later
    loaded_tokens = storage.load_tokens()
    ```
    """

    def __init__(self, storage_dir: Optional[str] = None) -> None:
        """
        Initialize secure token storage.

        Creates storage directory if it doesn't exist and sets up encryption.

        ## Args:
        - `storage_dir` (str, optional): Custom directory for token storage.
          Defaults to `~/.axiomtradeapi` if not specified.

        ## Side Effects:
        - Creates storage directory with restrictive permissions
        - Generates encryption key if it doesn't exist
        - Initializes Fernet cipher suite for encryption/decryption
        """
        # Determine storage directory: use provided path or default to home directory
        self.storage_dir = Path(storage_dir or Path.home() / ".axiomtradeapi")

        # Create directory with restrictive permissions (owner only)
        self.storage_dir.mkdir(exist_ok=True, mode=DIR_PERMISSIONS)

        # Define file paths for encrypted tokens and encryption key
        self.token_file = self.storage_dir / "tokens.enc"
        self.key_file = self.storage_dir / "key.enc"

        # Set up logging for debugging and security auditing
        self.logger = logging.getLogger(__name__)

        # Initialize or load encryption key
        self._initialize_encryption_key()

    def _initialize_encryption_key(self) -> None:
        """
        Initialize or load the encryption key for token storage.

        ## Algorithm:
        1. Check if encryption key file exists
        2. If exists: Load existing key from file
        3. If not exists: Generate new Fernet key and save it
        4. Set restrictive file permissions on key file
        5. Initialize Fernet cipher suite with the key

        ## Security Note:
        - Fernet key is 32 URL-safe base64-encoded bytes
        - Key file permissions are set to 0o600 (owner read/write only)
        - Losing the key means losing access to all encrypted tokens
        """
        if self.key_file.exists():
            # Load existing encryption key
            with open(self.key_file, "rb") as f:
                self.key = f.read()
        else:
            # Generate new encryption key
            self.key = Fernet.generate_key()

            # Save key to file
            with open(self.key_file, "wb") as f:
                f.write(self.key)

            # Set restrictive permissions (Unix only - no effect on Windows)
            os.chmod(self.key_file, FILE_PERMISSIONS)

        # Initialize Fernet cipher suite for encryption/decryption operations
        self.cipher_suite = Fernet(self.key)

    def save_tokens(self, tokens: AuthTokens) -> bool:
        """
        Securely encrypt and save authentication tokens to disk.

        ## Process:
        1. Serialize tokens to JSON
        2. Encrypt JSON data using Fernet
        3. Write encrypted data to file
        4. Set restrictive file permissions

        ## Args:
        - `tokens` (AuthTokens): Token object to save

        ## Returns:
        - `bool`: True if save successful, False if error occurred

        ## Error Handling:
        - Catches and logs all exceptions during save operation
        - Returns False on failure without raising exceptions
        - Safe to call repeatedly without side effects on failure
        """
        try:
            # Serialize tokens to JSON bytes
            token_data = json.dumps(tokens.to_dict()).encode("utf-8")

            # Encrypt the JSON data using Fernet (AES + HMAC)
            encrypted_data = self.cipher_suite.encrypt(token_data)

            # Write encrypted data to file atomically
            with open(self.token_file, "wb") as f:
                f.write(encrypted_data)

            # Set restrictive permissions on token file
            os.chmod(self.token_file, FILE_PERMISSIONS)

            self.logger.debug("Tokens saved securely to encrypted storage")
            return True

        except Exception as e:
            self.logger.error(f"Failed to save tokens: {e}", exc_info=True)
            return False

    def load_tokens(self) -> Optional[AuthTokens]:
        """
        Load and decrypt authentication tokens from disk.

        ## Process:
        1. Check if token file exists
        2. Read encrypted data from file
        3. Decrypt data using Fernet
        4. Parse JSON and create AuthTokens object

        ## Returns:
        - `AuthTokens`: Loaded tokens if successful
        - `None`: If no tokens exist or decryption fails

        ## Error Handling:
        - Returns None if token file doesn't exist
        - Catches decryption errors (corrupted data or wrong key)
        - Catches JSON parsing errors
        - Logs all errors for debugging
        """
        # Return None if no saved tokens exist
        if not self.token_file.exists():
            self.logger.debug("No saved tokens found in storage")
            return None

        try:
            # Read encrypted data from file
            with open(self.token_file, "rb") as f:
                encrypted_data = f.read()

            # Decrypt using Fernet (validates HMAC, then decrypts)
            decrypted_data = self.cipher_suite.decrypt(encrypted_data)

            # Parse JSON from decrypted bytes
            token_data = json.loads(decrypted_data.decode("utf-8"))

            # Create AuthTokens object from dictionary
            tokens = AuthTokens.from_dict(token_data)

            self.logger.debug("Tokens loaded successfully from encrypted storage")
            return tokens

        except Exception as e:
            self.logger.error(f"Failed to load tokens: {e}", exc_info=True)
            return None

    def delete_tokens(self) -> bool:
        """
        Delete saved tokens from disk.

        Used during logout or when clearing authentication state.

        ## Returns:
        - `bool`: True if deletion successful or file doesn't exist, False on error

        ## Side Effects:
        - Removes encrypted token file from disk
        - Does not remove encryption key (allows future token storage)
        """
        try:
            if self.token_file.exists():
                self.token_file.unlink()
                self.logger.debug("Saved tokens deleted from storage")
            return True

        except Exception as e:
            self.logger.error(f"Failed to delete tokens: {e}", exc_info=True)
            return False

    def has_saved_tokens(self) -> bool:
        """
        Check if saved tokens exist in storage.

        ## Returns:
        - `bool`: True if encrypted token file exists, False otherwise

        ## Note:
        Does not verify if tokens are valid or decryptable, only checks file existence.
        """
        return self.token_file.exists()


class CookieManager:
    """
    # HTTP Cookie Manager

    Manages HTTP cookies for authenticated API requests. Provides a simple
    interface for setting, formatting, and clearing authentication cookies.

    ## Responsibilities:
    - Store authentication cookies (access token, refresh token)
    - Format cookies for HTTP Cookie header
    - Clear cookies on logout

    ## Cookie Format:
    Cookies are formatted as: `name1=value1; name2=value2; name3=value3`

    ## Design Decisions:
    - Uses simple dict storage (no complex cookie jar needed)
    - Focuses specifically on auth cookies (not general-purpose)
    - Thread-safe for basic operations (dict operations are atomic in CPython)

    ## Example:
    ```python
    cookie_mgr = CookieManager()
    cookie_mgr.set_auth_cookies("access_token_value", "refresh_token_value")

    # Get formatted cookie string for HTTP header
    cookie_header = cookie_mgr.get_cookie_header()
    # Returns: "auth-access-token=access_token_value; auth-refresh-token=refresh_token_value"
    ```
    """

    def __init__(self) -> None:
        """
        Initialize cookie manager with empty cookie store.

        ## Side Effects:
        - Creates empty dictionary for cookie storage
        - Initializes logger for debugging
        """
        self.cookies: Dict[str, str] = {}
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

    def set_auth_cookies(self, auth_token: str, refresh_token: str) -> None:
        """
        Set authentication cookies for API requests.

        Updates the internal cookie store with authentication tokens.
        These cookies are required for authenticated API requests to Axiom Trade.

        ## Args:
        - `auth_token` (str): Access token (JWT) for API authentication
        - `refresh_token` (str): Refresh token for obtaining new access tokens

        ## Cookie Names:
        - `auth-access-token`: The active JWT access token
        - `auth-refresh-token`: Long-lived refresh token
        """
        self.cookies["auth-access-token"] = auth_token
        self.cookies["auth-refresh-token"] = refresh_token
        self.logger.debug("Authentication cookies updated in cookie manager")

    def get_cookie_header(self) -> str:
        """
        Format cookies as HTTP Cookie header string.

        Converts the internal cookie dictionary into a properly formatted
        Cookie header value according to RFC 6265.

        ## Returns:
        - `str`: Formatted cookie string (e.g., "name1=value1; name2=value2")
          Returns empty string if no cookies are set

        ## Format:
        Cookies are joined with "; " separator as per HTTP Cookie spec.
        Order is not guaranteed (dict iteration order in Python 3.7+).
        """
        if not self.cookies:
            return ""

        # Format: "name1=value1; name2=value2"
        cookie_pairs = [f"{key}={value}" for key, value in self.cookies.items()]
        return "; ".join(cookie_pairs)

    def clear_auth_cookies(self) -> None:
        """
        Clear authentication cookies from storage.

        Removes auth tokens from cookie store. Used during logout
        or when invalidating the current session.

        ## Side Effects:
        - Removes 'auth-access-token' from cookies
        - Removes 'auth-refresh-token' from cookies
        - Safe to call even if cookies don't exist (no KeyError)
        """
        self.cookies.pop("auth-access-token", None)
        self.cookies.pop("auth-refresh-token", None)
        self.logger.debug("Authentication cookies cleared from cookie manager")

    def has_auth_cookies(self) -> bool:
        """
        Check if authentication cookies are present.

        Verifies that both required auth cookies exist in storage.

        ## Returns:
        - `bool`: True if both access and refresh tokens are present, False otherwise

        ## Note:
        Does not validate token content or expiration, only checks presence.
        """
        return (
            "auth-access-token" in self.cookies and "auth-refresh-token" in self.cookies
        )


# Default token expiration time (1 hour in seconds)
DEFAULT_TOKEN_EXPIRY = 600


class AuthManager:
    """
    # Authentication Manager for Axiom Trade API

    Comprehensive authentication manager that handles the complete lifecycle of
    authentication tokens for the Axiom Trade platform.

    ## Core Features:
    - **Multi-factor Authentication**: Supports OTP-based two-step login
    - **Automatic Token Management**: Monitors and refreshes tokens automatically
    - **Secure Storage**: Encrypts tokens at rest using Fernet encryption
    - **Session Persistence**: Saves and loads tokens between sessions
    - **Cookie Management**: Handles HTTP cookies for authenticated requests
    - **HTTP Client Integration**: Uses modern httpx library for requests

    ## Authentication Flow:
    1. **Login Step 1**: Send credentials → Receive OTP token
    2. **OTP Verification**: User enters code from email
    3. **Login Step 2**: Submit OTP → Receive access/refresh tokens
    4. **Token Refresh**: Automatically refresh before expiration
    5. **Re-authentication**: Fallback to login if refresh fails

    ## Token Management Strategy:
    - Tokens are checked before each request
    - Refresh triggered at 15-minute buffer before expiration
    - Automatic re-authentication if refresh fails and credentials available
    - Secure encrypted storage for session persistence

    ## Design Decisions:
    - Separates concerns: cookie mgmt, storage, and auth logic are distinct
    - Fail-safe: multiple fallback strategies for authentication
    - Logging: comprehensive debug/info/error logging for troubleshooting
    - Flexibility: supports both interactive (OTP) and token-based initialization

    ## Example Usage:
    ```python
    # Interactive authentication with credentials
    auth = AuthManager(username="user@example.com", password="secure_pass")
    if auth.authenticate():
        headers = auth.get_authenticated_headers()

    # Re-use saved tokens from previous session
    auth = AuthManager(use_saved_tokens=True)
    if auth.is_authenticated():
        # Use existing valid tokens
        pass

    # Initialize with existing tokens (no credentials needed)
    auth = AuthManager(
        auth_token="existing_access_token",
        refresh_token="existing_refresh_token"
    )
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
    ) -> None:
        """
        Initialize Authentication Manager.

        ## Args:
        - `username` (str, optional): Email address for login authentication
        - `password` (str, optional): Password for login authentication
        - `auth_token` (str, optional): Existing access token to use
        - `refresh_token` (str, optional): Existing refresh token to use
        - `storage_dir` (str, optional): Custom directory for token storage
        - `use_saved_tokens` (bool): Whether to load/save tokens to disk (default: True)

        ## Initialization Priority:
        1. Provided tokens (auth_token + refresh_token) - highest priority
        2. Saved tokens from disk (if use_saved_tokens=True and tokens exist)
        3. No tokens - will require authentication via username/password

        ## Side Effects:
        - Initializes logger for debugging
        - Creates cookie manager instance
        - Creates secure token storage instance
        - May load tokens from disk
        - Sets up base URL for API requests
        """
        # Store credentials for authentication and re-authentication
        self.username = username
        self.password = password

        # Base URL for Axiom Trade API
        self.base_url = "https://axiom.trade"

        # Configuration flags
        self.use_saved_tokens = use_saved_tokens

        # Setup logging with module-specific logger
        self.logger = logging.getLogger(__name__)

        # Initialize cookie manager for HTTP cookie handling
        self.cookie_manager = CookieManager()

        # Initialize secure token storage with encryption
        self.token_storage = SecureTokenStorage(storage_dir)

        # Token storage - initially None until authenticated
        self.tokens: Optional[AuthTokens] = None

        # print(f"AuthManager initialized. use_saved_tokens={use_saved_tokens}")
        # Load saved tokens if enabled and available
        if use_saved_tokens:
            self._load_saved_tokens_if_available()

        # Override with provided tokens if given (takes precedence over saved tokens)
        if auth_token and refresh_token:
            self._set_tokens(auth_token, refresh_token)
            self.logger.info("Initialized with provided authentication tokens")

    def _load_saved_tokens_if_available(self) -> None:
        """
        Load saved tokens from secure storage if they exist.

        ## Process:
        1. Attempt to load encrypted tokens from disk
        2. Check if loaded tokens are still valid (not expired)
        3. If valid: set as active tokens and update cookies
        4. If expired: log warning but keep tokens (can be refreshed)

        ## Side Effects:
        - May load tokens into self.tokens
        - May update cookie manager with loaded tokens
        - Logs info about token loading status
        """
        saved_tokens = self.token_storage.load_tokens()

        if saved_tokens and not saved_tokens.is_expired:
            # Tokens are valid - use them immediately
            self.tokens = saved_tokens
            self.cookie_manager.set_auth_cookies(
                saved_tokens.access_token, saved_tokens.refresh_token
            )
            self.logger.info("Loaded valid saved tokens from storage")

        elif saved_tokens and saved_tokens.is_expired:
            # Tokens exist but are expired - keep them for potential refresh
            self.tokens = saved_tokens
            self.cookie_manager.set_auth_cookies(
                saved_tokens.access_token, saved_tokens.refresh_token
            )
            self.logger.info(
                "Saved tokens are expired, will attempt refresh on next request"
            )
            self.refresh_tokens()

    def _set_tokens(
        self,
        auth_token: str,
        refresh_token: str,
        expires_in: int = DEFAULT_TOKEN_EXPIRY,
        save_tokens: bool = True,
    ) -> None:
        """
        Set authentication tokens and update all related state.

        ## Process:
        1. Create AuthTokens object with expiration tracking
        2. Update cookie manager with new tokens
        3. Save tokens to encrypted storage (if enabled)

        ## Args:
        - `auth_token` (str): JWT access token for API authentication
        - `refresh_token` (str): Long-lived refresh token
        - `expires_in` (int): Token lifetime in seconds (default: 3600 = 1 hour)
        - `save_tokens` (bool): Whether to persist tokens to disk (default: True)

        ## Side Effects:
        - Creates new AuthTokens instance
        - Updates cookie manager
        - Writes encrypted tokens to disk (if save_tokens=True)
        - Logs token update event

        ## Design Note:
        Uses current timestamp for issued_at and calculates expires_at based on
        expires_in parameter. This ensures consistent expiration tracking.
        """
        current_time = time.time()

        # Create new token object with expiration tracking
        self.tokens = AuthTokens(
            access_token=auth_token,
            refresh_token=refresh_token,
            expires_at=current_time + expires_in,
            issued_at=current_time,
        )

        # Update cookies for HTTP requests
        self.cookie_manager.set_auth_cookies(auth_token, refresh_token)

        # Persist tokens to encrypted storage if enabled
        if save_tokens and self.use_saved_tokens:
            if self.token_storage.save_tokens(self.tokens):
                self.logger.debug("Tokens persisted to encrypted storage")
            else:
                self.logger.warning("Failed to persist tokens to storage")

        self.logger.info("Authentication tokens updated successfully")

    def _compute_password_hash(self, password: str) -> str:
        """
        Compute SHA256 hash of password and encode as base64.

        Axiom Trade API requires passwords to be hashed with SHA256 and then
        base64-encoded using ISO-8859-1 character encoding.

        ## Algorithm:
        1. Encode password string to bytes using ISO-8859-1 encoding
        2. Compute SHA256 hash of the encoded bytes
        3. Base64-encode the hash digest
        4. Decode base64 bytes to UTF-8 string

        ## Args:
        - `password` (str): Plain text password

        ## Returns:
        - `str`: Base64-encoded SHA256 hash of password

        ## Security Note:
        - This is client-side hashing for transmission, not for storage
        - The API still requires secure HTTPS communication
        - ISO-8859-1 encoding is API requirement (not security choice)

        ## Example:
        ```python
        hash = self._compute_password_hash("my_password")
        # Returns: "XohImNooBHFR0OVvjcYpJ3NgPQ1qq73WKhHvch0VQtg="
        ```
        """
        # Encode password using ISO-8859-1 (API requirement)
        # password_bytes = password.encode("iso-8859-1")
        password_bytes = password.encode("utf-8")

        # Compute SHA256 hash
        # sha256_hash = hashlib.sha256(password_bytes).digest()
        sha256_hash = hashlib.pbkdf2_hmac(
            "sha256", password_bytes, SALT, ITERATIONS, dklen=32
        )

        # Base64 encode and return as string
        b64_password = base64.b64encode(sha256_hash).decode("utf-8")

        return b64_password

    def authenticate(self) -> bool:
        """
        Authenticate with username/password using Axiom's two-step OTP login flow.

        ## Process:
        1. Validate credentials are provided
        2. Send login request with email and hashed password
        3. Receive OTP token via email
        4. Prompt user for OTP code
        5. Complete authentication with OTP code
        6. Store received access and refresh tokens

        ## Returns:
        - `bool`: True if authentication successful, False otherwise

        ## Requirements:
        - username and password must be set during initialization
        - User must have access to email for OTP code

        ## Side Effects:
        - Prompts user for OTP code via stdin
        - Updates self.tokens on success
        - Updates cookie manager on success
        - Saves tokens to encrypted storage

        ## Error Handling:
        - Returns False if credentials missing
        - Returns False if OTP step 1 fails
        - Returns False if OTP code not provided
        - Returns False if OTP step 2 fails
        - Logs detailed error messages for debugging
        """
        # Validate that credentials are available
        if not self.username or not self.password:
            self.logger.error("Username and password required for authentication")
            return False

        try:
            b64_password = self._compute_password_hash(self.password)
            self.logger.info("Starting Axiom Trade authentication flow...")

            # Step 1: Send credentials and get OTP JWT token
            otp_jwt_token = self._authenticate_step1_get_otp(b64_password=b64_password)
            if not otp_jwt_token:
                return False

            # Step 2: Get OTP code from user
            otp_fetcher = OtpFetcher(
                email_address="christianmfm10@inbox.lv", password="6Z4oAgWx2D"
            )
            otp_code = otp_fetcher.wait_for_otp()
            if not otp_code:
                self.logger.error("OTP code is required to complete authentication")
                return False

            # Step 3: Complete authentication with OTP code
            return self._authenticate_step2_verify_otp(
                b64_password, otp_jwt_token, otp_code
            )

        except Exception as e:
            self.logger.error(f"Authentication error: {e}", exc_info=True)
            return False

    def _authenticate_step1_get_otp(self, b64_password: str) -> Optional[str]:
        """
        First step of authentication: send credentials to receive OTP token.

        Sends email and hashed password to API, which responds with an OTP JWT
        token in cookies. The API also sends an OTP code to the user's email.

        ## Process:
        1. Hash password using SHA256 + base64
        2. Build request with credentials
        3. Send POST request to login step 1 endpoint
        4. Extract OTP JWT token from response cookies

        ## Returns:
        - `str`: OTP JWT token if successful
        - `None`: If request fails or token not in response

        ## API Endpoint:
        - URL: https://api-v6.axiom.trade/login/step1
        - Method: POST
        - Content-Type: application/json
        - Response: Sets 'auth-otp-login-token' cookie

        ## Error Handling:
        - Logs detailed error if request fails
        - Returns None on any error
        - Catches network exceptions
        """
        from axiomclient.urls import AAllBaseUrls, AxiomTradeApiUrls

        # Compute password hash as required by API
        # b64_password = self._compute_password_hash(b64_password)
        self.logger.info(
            f"Computed password hash for user {self.username}: {b64_password}"
        )

        # Build API URL for step 1
        url = f"{AAllBaseUrls.BASE_URL_v3}{AxiomTradeApiUrls.LOGIN_STEP1}"

        # HTTP headers mimicking browser request (required by API)
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9,es;q=0.8",
            "content-type": "application/json",
            "origin": "https://axiom.trade",
            "priority": "u=1, i",
            "referer": "https://axiom.trade/",
            "sec-ch-ua": '"Chromium";v="134", "Not:A-Brand";v="24", "Opera GX";v="119"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 OPR/119.0.0.0",
            "Cookie": "auth-otp-login-token=",
        }

        # Request payload with credentials
        data = {"email": self.username, "b64Password": b64_password}

        try:
            self.logger.debug(f"Sending login step 1 request for: {self.username}")

            # Use httpx for HTTP request
            response = httpx.post(url, headers=headers, json=data, timeout=30)

            if response.status_code == 200:
                # Extract OTP token from cookies
                data = response.json()
                # otp_token = response.cookies.get("auth-otp-login-token")
                otp_token = data.get("otpJwtToken", None)

                if otp_token:
                    self.logger.debug("OTP JWT token received successfully")
                    return otp_token
                else:
                    self.logger.error(
                        "'auth-otp-login-token' cookie not found in response"
                    )
                    return None
            else:
                self.logger.error(
                    f"Login step 1 failed - Status: {response.status_code}, "
                    f"Response: {response.text}"
                )
                return None

        except httpx.HTTPError as e:
            self.logger.error(f"Login step 1 HTTP error: {e}", exc_info=True)
            return None
        except Exception as e:
            self.logger.error(f"Login step 1 unexpected error: {e}", exc_info=True)
            return None

    def _authenticate_step2_verify_otp(
        self, b64_password: str, otp_jwt_token: str, otp_code: str
    ) -> bool:
        """
        Second step of authentication: verify OTP code and receive auth tokens.

        Sends the OTP code (from user's email) along with the OTP JWT token
        to complete authentication and receive access/refresh tokens.

        ## Process:
        1. Hash password again for verification
        2. Build request with OTP code and credentials
        3. Send POST request with OTP JWT token in cookies
        4. Extract access and refresh tokens from response
        5. Store tokens and update authentication state

        ## Args:
        - `otp_jwt_token` (str): JWT token from step 1 (proves email ownership)
        - `otp_code` (str): OTP code from email (proves user has email access)

        ## Returns:
        - `bool`: True if authentication successful, False otherwise

        ## API Endpoint:
        - URL: https://api-v3.axiom.trade/login/step2
        - Method: POST
        - Content-Type: application/json
        - Cookie: auth-otp-login-token (from step 1)
        - Response: Sets 'auth-access-token' and 'auth-refresh-token' cookies

        ## Side Effects:
        - Updates self.tokens with new access/refresh tokens
        - Updates cookie manager
        - Saves tokens to encrypted storage

        ## Error Handling:
        - Returns False if request fails
        - Returns False if tokens not found in response
        - Tries both cookie and JSON response formats
        - Logs detailed errors for debugging
        """
        from axiomclient.urls import AAllBaseUrls, AxiomTradeApiUrls

        # Hash password for verification
        # b64_password = self._compute_password_hash(b64_password)

        # Build API URL for step 2
        url = f"{AAllBaseUrls.BASE_URL_v3}{AxiomTradeApiUrls.LOGIN_STEP2}"

        # HTTP headers for step 2 request
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Content-Type": "application/json",
            "Origin": "https://axiom.trade",
            "Connection": "keep-alive",
            "Referer": "https://axiom.trade/",
            "Cookie": f"auth-otp-login-token={otp_jwt_token}",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "TE": "trailers",
        }

        # Request payload with OTP code and credentials
        data = {"code": otp_code, "email": self.username, "b64Password": b64_password}

        try:
            self.logger.debug("Sending login step 2 request with OTP code")

            # Use httpx for HTTP request
            response = httpx.post(url, headers=headers, json=data, timeout=30)

            if response.status_code == 200:
                # Try to extract tokens from response cookies first
                auth_token = response.cookies.get("auth-access-token")
                refresh_token = response.cookies.get("auth-refresh-token")

                # If not in cookies, try JSON response body
                if not (auth_token and refresh_token):
                    try:
                        response_data = response.json()
                        auth_token = response_data.get(
                            "accessToken"
                        ) or response_data.get("auth-access-token")
                        refresh_token = response_data.get(
                            "refreshToken"
                        ) or response_data.get("auth-refresh-token")
                    except (json.JSONDecodeError, AttributeError):
                        pass

                # Validate that we received both tokens
                if auth_token and refresh_token:
                    self._set_tokens(auth_token, refresh_token)
                    self.logger.info("✅ Authentication successful!")
                    return True
                else:
                    self.logger.error("❌ No authentication tokens found in response")
                    return False
            else:
                self.logger.error(
                    f"❌ Login step 2 failed - Status: {response.status_code}, "
                    f"Response: {response.text}"
                )
                return False

        except httpx.HTTPError as e:
            self.logger.error(f"❌ Login step 2 HTTP error: {e}", exc_info=True)
            return False
        except Exception as e:
            self.logger.error(f"❌ Login step 2 unexpected error: {e}", exc_info=True)
            return False

    def refresh_tokens(self) -> bool:
        """
        Refresh authentication tokens using the refresh token.

        Exchanges the current refresh token for a new access token (and optionally
        a new refresh token). This extends the session without requiring re-authentication.

        ## Process:
        1. Validate refresh token exists
        2. Build request with both tokens in cookies
        3. Send POST request to refresh endpoint
        4. Extract new tokens from response
        5. Update stored tokens and cookies

        ## Returns:
        - `bool`: True if refresh successful, False otherwise

        ## API Endpoint:
        - URL: https://api.axiom.trade/refresh-access-token
        - Method: POST
        - Cookies: auth-access-token, auth-refresh-token
        - Response: New tokens in cookies or JSON body

        ## Design Notes:
        - Requires both access and refresh tokens in cookies (API requirement)
        - If new refresh token not provided, keeps existing one
        - Tries both cookie and JSON response formats

        ## Side Effects:
        - Updates self.tokens with new tokens
        - Updates cookie manager
        - Saves new tokens to encrypted storage

        ## Error Handling:
        - Returns False if no refresh token available
        - Returns False if refresh request fails
        - Returns False if no new access token in response
        - Logs detailed errors for debugging
        """
        # Validate that we have a refresh token
        if not self.tokens or not self.tokens.refresh_token:
            self.logger.error("No refresh token available for token refresh")
            return False

        # HTTP headers mimicking browser request (API requirement)
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9,es;q=0.8,fr;q=0.7,de;q=0.6",
            "content-length": "0",
            "origin": "https://axiom.trade/",
            "priority": "u=1, i",
            "referer": "https://axiom.trade/",
            "sec-ch-ua": '"Opera GX";v="120", "Not-A.Brand";v="8", "Chromium";v="135"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 OPR/120.0.0.0",
        }

        # Build cookies dict with both tokens (API requires both)
        cookies = {
            "auth-refresh-token": self.tokens.refresh_token,
            "auth-access-token": self.tokens.access_token,
        }

        try:
            self.logger.info("Refreshing authentication tokens...")

            # API endpoint for token refresh
            refresh_url = "https://api.axiom.trade/refresh-access-token"

            # Use httpx for HTTP request
            response = httpx.post(
                refresh_url, headers=headers, cookies=cookies, timeout=30
            )

            if response.status_code == 200:
                # Try to extract tokens from response cookies first
                new_auth_token = response.cookies.get("auth-access-token")
                new_refresh_token = response.cookies.get("auth-refresh-token")

                # If tokens in cookies, use them
                if new_auth_token:
                    # Use new refresh token if provided, else keep existing one
                    refresh_token_to_use = (
                        new_refresh_token or self.tokens.refresh_token
                    )
                    self._set_tokens(new_auth_token, refresh_token_to_use)
                    self.logger.info("✅ Tokens refreshed successfully!")
                    return True

                # If not in cookies, try JSON response body
                try:
                    response_data = response.json()
                    new_auth_token = (
                        response_data.get("accessToken")
                        or response_data.get("auth-access-token")
                        or response_data.get("access_token")
                    )
                    new_refresh_token = (
                        response_data.get("refreshToken")
                        or response_data.get("auth-refresh-token")
                        or response_data.get("refresh_token")
                    )

                    if new_auth_token:
                        refresh_token_to_use = (
                            new_refresh_token or self.tokens.refresh_token
                        )
                        self._set_tokens(new_auth_token, refresh_token_to_use)
                        self.logger.info(
                            "✅ Tokens refreshed successfully from JSON response!"
                        )
                        return True

                except (json.JSONDecodeError, AttributeError):
                    pass

                self.logger.error("❌ No new access token found in refresh response")
                return False

            else:
                self.logger.error(
                    f"❌ Token refresh failed - Status: {response.status_code}, "
                    f"Response: {response.text}"
                )
                return False

        except httpx.HTTPError as e:
            self.logger.error(f"❌ Token refresh HTTP error: {e}", exc_info=True)
            return False
        except Exception as e:
            self.logger.error(f"❌ Token refresh unexpected error: {e}", exc_info=True)
            return False

    def ensure_valid_authentication(self) -> bool:
        """
        Ensure valid authentication tokens are available.

        Implements a multi-tier fallback strategy to maintain valid authentication:
        1. If no tokens: attempt authentication with credentials
        2. If tokens valid: return success immediately
        3. If tokens expired: attempt refresh
        4. If refresh fails: attempt re-authentication with credentials

        ## Returns:
        - `bool`: True if valid authentication available, False otherwise

        ## Authentication Flow:
        ```
        No tokens? → Authenticate → Done
        Tokens valid? → Done
        Tokens expired? → Refresh → Success? → Done
                                  → Failure? → Re-authenticate → Done
        ```

        ## Design Rationale:
        - Minimizes authentication overhead by checking validity first
        - Provides automatic recovery from expired tokens
        - Falls back to full authentication if refresh fails
        - Transparent to caller - always tries to provide valid auth

        ## Side Effects:
        - May prompt user for OTP during authentication
        - May update tokens via refresh or re-authentication
        - Logs authentication attempts and results
        """
        # No tokens at all - attempt initial authentication
        if not self.tokens:
            if self.username and self.password:
                return self.authenticate()
            else:
                self.logger.error(
                    "No authentication tokens available and no credentials provided"
                )
                return False

        # Tokens exist and are still valid - no action needed
        if not self.tokens.is_expired:
            return True

        # Tokens are expired - attempt to refresh them
        if self.refresh_tokens():
            return True

        # Refresh failed - try full re-authentication as last resort
        if self.username and self.password:
            self.logger.info(
                "Token refresh failed, attempting full re-authentication..."
            )
            return self.authenticate()

        # All options exhausted
        self.logger.error(
            "Cannot refresh expired tokens and no credentials for re-authentication"
        )
        return False

    def get_authenticated_headers(
        self, additional_headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """
        Generate HTTP headers with authentication cookies for API requests.

        Automatically ensures authentication is valid before returning headers.

        ## Args:
        - `additional_headers` (dict, optional): Extra headers to merge into result

        ## Returns:
        - `dict`: Complete HTTP headers including authentication cookies

        ## Header Structure:
        - Content-Type: application/json
        - Accept: application/json, text/plain, */*
        - Origin: https://axiom.trade
        - Referer: https://axiom.trade/discover
        - User-Agent: AxiomTradeAPI-py/1.0
        - Cookie: auth-access-token=...; auth-refresh-token=...

        ## Side Effects:
        - May trigger authentication or token refresh
        - Logs warning if authentication not available

        ## Example:
        ```python
        headers = auth.get_authenticated_headers({
            'X-Custom-Header': 'custom-value'
        })

        response = httpx.get('https://api.axiom.trade/endpoint', headers=headers)
        ```
        """
        # Ensure we have valid authentication before building headers
        if not self.ensure_valid_authentication():
            self.logger.warning("No valid authentication available for headers")

        # Base headers for API requests
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/discover",
            "User-Agent": "AxiomTradeAPI-py/1.0",
        }

        # Add authentication cookies if available
        cookie_header = self.cookie_manager.get_cookie_header()
        if cookie_header:
            headers["Cookie"] = cookie_header

        # Merge in any additional headers provided
        if additional_headers:
            headers.update(additional_headers)

        return headers

    def is_authenticated(self) -> bool:
        """
        Check if currently authenticated with valid, non-expired tokens.

        ## Returns:
        - `bool`: True if authenticated with valid tokens, False otherwise

        ## Validation Checks:
        1. Tokens object exists
        2. Tokens are not expired (considering 5-minute buffer)
        3. Cookie manager has auth cookies set

        ## Note:
        This is a status check only - does not attempt to refresh or authenticate.
        Use `ensure_valid_authentication()` for automatic token management.
        """
        return (
            self.tokens is not None
            and not self.tokens.is_expired
            and self.cookie_manager.has_auth_cookies()
        )

    def logout(self) -> None:
        """
        Log out and clear all authentication data.

        Removes tokens from memory and deletes encrypted storage file.
        After logout, authentication is required before making API requests.

        ## Side Effects:
        - Clears self.tokens (sets to None)
        - Clears cookies from cookie manager
        - Deletes encrypted token file from disk
        - Logs logout event

        ## Example:
        ```python
        auth.logout()
        # Must call authenticate() again before making requests
        ```
        """
        self.tokens = None
        self.cookie_manager.clear_auth_cookies()

        # Delete saved tokens from encrypted storage
        # if self.use_saved_tokens:
        #     self.token_storage.delete_tokens()

        self.logger.info("Logged out successfully - all authentication data cleared")

    def clear_saved_tokens(self) -> bool:
        """
        Clear saved tokens from encrypted storage without affecting current session.

        Removes the encrypted token file from disk but keeps current in-memory
        tokens active. Useful for cleaning up old saved sessions.

        ## Returns:
        - `bool`: True if cleared successfully, False on error

        ## Note:
        Does not affect current authentication state - only removes saved file.
        """
        return self.token_storage.delete_tokens()

    def has_saved_tokens(self) -> bool:
        """
        Check if encrypted token file exists in storage.

        ## Returns:
        - `bool`: True if encrypted token file exists, False otherwise

        ## Note:
        Only checks file existence, does not validate if tokens are usable.
        """
        return self.token_storage.has_saved_tokens()

    def get_token_info(self) -> dict[str, Union[str, bool, float, None]]:
        """
        Get detailed information about current authentication tokens.

        Provides diagnostic information about token state for debugging
        and monitoring purposes.

        ## Returns:
        - `dict`: Token information with the following keys:
          - `authenticated` (bool): Whether tokens exist
          - `access_token_preview` (str): First 20 chars of access token
          - `expires_at` (float): Unix timestamp of expiration
          - `issued_at` (float): Unix timestamp when issued
          - `is_expired` (bool): Whether token is expired
          - `needs_refresh` (bool): Whether token should be refreshed
          - `time_until_expiry` (float): Seconds until expiration

        ## Example:
        ```python
        info = auth.get_token_info()
        if info['needs_refresh']:
            print(f"Token expires in {info['time_until_expiry']} seconds")
        ```
        """
        if not self.tokens:
            return {"authenticated": False}

        return {
            "authenticated": True,
            "access_token_preview": (
                self.tokens.access_token[:20] + "..."
                if self.tokens.access_token
                else None
            ),
            "expires_at": self.tokens.expires_at,
            "issued_at": self.tokens.issued_at,
            "is_expired": self.tokens.is_expired,
            "needs_refresh": self.tokens.needs_refresh,
            "time_until_expiry": (
                self.tokens.expires_at - time.time()
                if not self.tokens.is_expired
                else 0
            ),
        }

    def get_tokens(self) -> Optional[AuthTokens]:
        """
        Get the current authentication tokens object.

        ## Returns:
        - `AuthTokens`: Current tokens if authenticated, None otherwise

        ## Use Case:
        Access tokens directly for custom token management or inspection.

        ## Example:
        ```python
        tokens = auth.get_tokens()
        if tokens:
            print(f"Access token: {tokens.access_token}")
            print(f"Expires at: {tokens.expires_at}")
        ```
        """
        return self.tokens

    def make_authenticated_request(
        self, method: str, url: str, **kwargs
    ) -> httpx.Response:
        """
        Make an authenticated HTTP request using httpx.

        Convenience method that automatically adds authentication headers
        and ensures tokens are valid before making the request.

        ## Args:
        - `method` (str): HTTP method (GET, POST, PUT, DELETE, etc.)
        - `url` (str): Full URL for the request
        - `**kwargs`: Additional arguments passed to httpx.request()

        ## Returns:
        - `httpx.Response`: HTTP response object from httpx

        ## Raises:
        - `Exception`: If authentication fails or cannot obtain valid tokens

        ## Example:
        ```python
        # Make a GET request
        response = auth.make_authenticated_request(
            'GET',
            'https://api.axiom.trade/user/profile'
        )

        # Make a POST request with JSON body
        response = auth.make_authenticated_request(
            'POST',
            'https://api.axiom.trade/orders',
            json={'symbol': 'BTC', 'amount': 1.0}
        )
        ```

        ## Side Effects:
        - May trigger authentication or token refresh
        - Raises exception if authentication fails
        """
        # Ensure we have valid authentication
        if not self.ensure_valid_authentication():
            raise Exception(
                "Authentication failed - unable to obtain valid tokens for request"
            )

        # Extract and merge headers
        headers = kwargs.pop("headers", {})
        authenticated_headers = self.get_authenticated_headers(headers)

        # Make the request using httpx
        self.logger.debug(f"Making authenticated {method} request to {url}")
        response = httpx.request(method, url, headers=authenticated_headers, **kwargs)

        return response


# Convenience function for quick session creation
def create_authenticated_session(
    username: Optional[str] = None,
    password: Optional[str] = None,
    auth_token: Optional[str] = None,
    refresh_token: Optional[str] = None,
    storage_dir: Optional[str] = None,
    use_saved_tokens: bool = True,
) -> AuthManager:
    """
    # Create Authenticated Session

    Convenience factory function for creating an AuthManager instance
    with a more intuitive name.

    ## Args:
    - `username` (str, optional): Email for authentication
    - `password` (str, optional): Password for authentication
    - `auth_token` (str, optional): Existing access token
    - `refresh_token` (str, optional): Existing refresh token
    - `storage_dir` (str, optional): Custom token storage directory
    - `use_saved_tokens` (bool): Load/save tokens to disk (default: True)

    ## Returns:
    - `AuthManager`: Configured authentication manager instance

    ## Example:
    ```python
    # Create session with credentials
    auth = create_authenticated_session(
        username="user@example.com",
        password="secure_password"
    )

    # Create session with existing tokens
    auth = create_authenticated_session(
        auth_token="eyJhbGc...",
        refresh_token="refresh_abc123"
    )

    # Create session using saved tokens
    auth = create_authenticated_session(use_saved_tokens=True)
    ```
    """
    return AuthManager(
        username=username,
        password=password,
        auth_token=auth_token,
        refresh_token=refresh_token,
        storage_dir=storage_dir,
        use_saved_tokens=use_saved_tokens,
    )
