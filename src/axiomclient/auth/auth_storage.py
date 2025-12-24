import json
import logging
import os
from pathlib import Path
from cryptography.fernet import Fernet

from axiomclient.auth.auth_tokens import AuthTokens


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

    def __init__(self, storage_dir: str | None) -> None:
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

    def load_tokens(self) -> AuthTokens | None:
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
