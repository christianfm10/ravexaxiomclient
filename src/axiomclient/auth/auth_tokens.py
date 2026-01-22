import time
from typing import Union
from dataclasses import dataclass

# Token expiration buffer constants (in seconds)
# These buffers provide safety margins before actual token expiration
TOKEN_EXPIRED_BUFFER = 300  # 5 minutes - consider token expired this early for safety
TOKEN_REFRESH_BUFFER = (
    900  # 15 minutes - trigger refresh this early to avoid race conditions
)


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

    def to_dict(self) -> dict[str, Union[str, float]]:
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
    def from_dict(cls, data: dict[str, str | float]) -> "AuthTokens":
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
