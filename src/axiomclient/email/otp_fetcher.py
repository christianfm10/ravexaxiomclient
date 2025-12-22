"""
# OTP Fetcher for Axiom Trade Authentication

This module provides functionality to automatically fetch OTP (One-Time Password) codes
from email for Axiom Trade authentication. It connects to an IMAP email server and
searches for OTP codes in unread Axiom security emails.

## Key Features:
- **IMAP Email Access**: Connects to inbox.lv or other IMAP servers
- **Automatic OTP Extraction**: Parses email subject and body for OTP codes
- **Time-based Filtering**: Fetch only recent OTP emails within a time window
- **Wait for OTP**: Poll for new OTP emails with configurable timeout
- **Mark as Read**: Automatically marks processed emails as read

## Dependencies:
- `imapclient`: Modern, Pythonic IMAP client library
- `email`: Standard library for email parsing
- `re`: Regular expressions for OTP extraction

## Usage:
```python
from axiomclient.email.otp_fetcher import OtpFetcher, from_env

# Create from environment variables
fetcher = from_env()

# Or create with explicit credentials
fetcher = OtpFetcher(
    email="user@inbox.lv",
    password="email_password"
)

# Fetch latest OTP from unread emails
otp = fetcher.fetch_otp()

# Wait for new OTP with timeout
otp = fetcher.wait_for_otp(timeout_seconds=120, check_interval_seconds=5)
```
"""

# Standard library imports
import email
import os
import re
import ssl
import time
from datetime import datetime, timedelta, timezone
from email.message import Message
from typing import Optional, List

# Third-party imports
# imapclient: Modern, easy-to-use IMAP client library
# Provides a more Pythonic interface compared to the standard imaplib

from axiomclient.email.imapclient import CustomIMAPClient


# IMAP server configuration for inbox.lv
IMAP_DOMAIN = "mail.inbox.lv"
# IMAP_DOMAIN = "imap.gmail.com"
IMAP_PORT = 993


class OtpFetcher:
    """
    # OTP Fetcher for Email-Based Authentication

    Connects to an IMAP email server to automatically retrieve OTP codes
    from Axiom Trade security emails. Supports filtering by read status
    and time window.

    ## Features:
    - Connects to IMAP server with SSL/TLS
    - Searches for unread Axiom security emails
    - Extracts OTP codes from subject line and email body
    - Marks processed emails as read
    - Filters emails by date/time
    - Polls for new emails with configurable timeout

    ## Design Decisions:
    - Uses IMAPClient for cleaner API than standard imaplib
    - Searches UNSEEN flag to avoid reprocessing emails
    - Extracts OTP from subject first (faster) before parsing body
    - Multiple regex patterns for robustness across email formats
    - Automatically marks emails as read after extraction

    ## Security Notes:
    - Credentials should be stored in environment variables
    - Uses SSL/TLS for secure IMAP connection
    - Does not store credentials in memory longer than necessary

    ## Example:
    ```python
    fetcher = OtpFetcher("user@inbox.lv", "password")

    # Get latest OTP
    otp = fetcher.fetch_otp()
    if otp:
        print(f"OTP code: {otp}")

    # Wait for new OTP (useful after triggering auth)
    otp = fetcher.wait_for_otp(timeout_seconds=120)
    ```
    """

    def __init__(self, email_address: str, password: str) -> None:
        """
        Initialize OTP fetcher with email credentials.

        ## Args:
        - `email_address` (str): Full email address (e.g., "user@inbox.lv")
        - `password` (str): Email account password or app-specific password

        ## Side Effects:
        - Stores credentials in instance (consider security implications)
        - Does not establish connection (lazy connection on first use)
        """
        self.email = email_address
        self.password = password

    def _connect(self) -> CustomIMAPClient:
        """
        Establish secure IMAP connection to email server.

        Creates a new SSL/TLS connection to the IMAP server and
        authenticates with stored credentials.

        ## Process:
        1. Create IMAPClient with SSL enabled
        2. Connect to IMAP server on port 993
        3. Authenticate with email and password

        ## Returns:
        - `IMAPClient`: Connected and authenticated IMAP client

        ## Raises:
        - `IMAPClient.Error`: If connection fails
        - `IMAPClient.Error`: If authentication fails

        ## Design Note:
        Creates a new connection for each operation to avoid stale connections.
        For high-frequency operations, consider connection pooling.
        """
        # Create IMAP client with SSL/TLS
        ctx = ssl.create_default_context()
        client = CustomIMAPClient(
            IMAP_DOMAIN, port=IMAP_PORT, ssl=True, ssl_context=ctx, timeout=30
        )
        # with CustomIMAPClient(
        #     IMAP_DOMAIN,
        #     port=IMAP_PORT,
        #     ssl=True,
        #     ssl_context=ctx,
        #     timeout=30,
        # ) as client:
        #     print("Connected to:", IMAP_DOMAIN, IMAP_PORT)
        #     print("welcome:", client.welcome)
        # Authenticate with credentials
        client.login(self.email, self.password)

        return client

    def fetch_otp(self) -> Optional[str]:
        """
        Fetch OTP from the latest unread Axiom security email.

        Searches for unread emails with "Your Axiom security code" in the subject,
        extracts the OTP code, and marks the email as read.

        ## Process:
        1. Connect to IMAP server
        2. Select INBOX folder
        3. Search for UNSEEN emails with Axiom subject
        4. Get the most recent matching email
        5. Try to extract OTP from subject line (fast path)
        6. If not found, extract from email body (slower)
        7. Mark email as read
        8. Logout and close connection

        ## Returns:
        - `str`: 6-digit OTP code if found
        - `None`: If no unread OTP email exists

        ## Side Effects:
        - Marks the processed email as SEEN (read)
        - Closes IMAP connection after operation

        ## Error Handling:
        - Returns None if no matching emails found
        - Raises exception if connection or parsing fails

        ## Example:
        ```python
        otp = fetcher.fetch_otp()
        if otp:
            print(f"Found OTP: {otp}")
        else:
            print("No unread OTP emails")
        ```
        """
        client = self._connect()

        try:
            # Select INBOX folder
            client.select_folder("INBOX")

            # Search for unread Axiom security emails
            message_ids = client.search(
                ["UNSEEN", "SUBJECT", "Your Axiom security code"]
            )

            if not message_ids:
                return None

            # Get the most recent message ID
            latest_id = max(message_ids)

            # Try to extract OTP from subject first (faster)
            # Fetch only the subject header
            messages = client.fetch([latest_id], ["BODY[HEADER.FIELDS (SUBJECT)]"])

            if latest_id in messages:
                subject_data = messages[latest_id][b"BODY[HEADER.FIELDS (SUBJECT)]"]

                subject_str = subject_data.decode("utf-8", errors="ignore")
                otp = self._extract_otp_from_subject(subject_str)

                if otp:
                    # Mark as read and return
                    client.add_flags([latest_id], ["\\Seen"])
                    return otp

            # # If not in subject, fetch full email body
            messages = client.fetch([latest_id], ["RFC822"])

            if latest_id in messages:
                email_data = messages[latest_id][b"RFC822"]

                email_message = email.message_from_bytes(email_data)

                # Extract OTP from email body
                otp = self._extract_otp_from_email_body(email_message)

                if otp:
                    # Mark as read
                    client.add_flags([latest_id], ["\\Seen"])
                    return otp

            return None

        finally:
            # Always logout and close connection
            client.logout()

    def fetch_otp_recent(self, minutes_ago: int = 3) -> Optional[str]:
        """
        Fetch OTP from unread emails received within a time window.

        Similar to `fetch_otp()` but filters emails by date to only
        consider recent messages. Useful for avoiding old OTP codes.

        ## Args:
        - `minutes_ago` (int): Number of minutes to look back (default: 3)

        ## Returns:
        - `str`: 6-digit OTP code if found within time window
        - `None`: If no recent unread OTP email exists

        ## Process:
        1. Calculate cutoff date (now - minutes_ago)
        2. Search for UNSEEN emails with Axiom subject since cutoff date
        3. Extract OTP from most recent matching email
        4. Mark email as read

        ## Design Note:
        IMAP SINCE search uses date (not datetime), so it searches for emails
        since the beginning of that day. Additional filtering might be needed
        for precise time windows.

        ## Example:
        ```python
        # Get OTP from emails received in last 5 minutes
        otp = fetcher.fetch_otp_recent(minutes_ago=5)
        ```
        """
        client = self._connect()

        try:
            # Select INBOX folder
            client.select_folder("INBOX")

            # Calculate cutoff date
            since_date = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)

            # IMAP SINCE uses date format (not datetime)
            # Format: DD-Mon-YYYY (e.g., "17-Dec-2025")
            date_str = since_date.strftime("%d-%b-%Y")

            # Search for unread Axiom emails since date
            message_ids = client.search(
                ["UNSEEN", "SUBJECT", "Your Axiom security code", "SINCE", date_str]
            )

            if not message_ids:
                return None

            # Get the most recent message ID
            latest_id = max(message_ids)

            # Try subject first (fast path)
            messages = client.fetch([latest_id], ["BODY[HEADER.FIELDS (SUBJECT)]"])

            if latest_id in messages:
                subject_data = messages[latest_id][b"BODY[HEADER.FIELDS (SUBJECT)]"]
                subject_str = subject_data.decode("utf-8", errors="ignore")
                otp = self._extract_otp_from_subject(subject_str)

                if otp:
                    client.add_flags([latest_id], ["\\Seen"])
                    return otp

            # Fetch full email if not in subject
            messages = client.fetch([latest_id], ["RFC822"])

            if latest_id in messages:
                email_data = messages[latest_id][b"RFC822"]
                email_message = email.message_from_bytes(email_data)
                otp = self._extract_otp_from_email_body(email_message)

                if otp:
                    client.add_flags([latest_id], ["\\Seen"])
                    return otp

            return None

        finally:
            client.logout()

    def _extract_otp_from_subject(self, subject: str) -> Optional[str]:
        """
        Extract OTP code from email subject line.

        Searches for pattern "Your Axiom security code is XXXXXX" where
        XXXXXX is a 6-digit number.

        ## Algorithm:
        Uses regex to match: "Your Axiom security code is (\\d{6})"

        ## Args:
        - `subject` (str): Email subject header text

        ## Returns:
        - `str`: 6-digit OTP code if found
        - `None`: If pattern doesn't match

        ## Example Subject:
        "Your Axiom security code is 280296"
        """
        # Regex pattern for subject line OTP
        pattern = r"Your Axiom security code is (\d{6})"
        match = re.search(pattern, subject)

        if match:
            return match.group(1)

        return None

    def _extract_otp_from_email_body(self, email_message: Message) -> Optional[str]:
        """
        Extract OTP code from email body content.

        Handles both plain text and HTML email formats. Tries multiple
        regex patterns to maximize compatibility with different email templates.

        ## Algorithm:
        1. Extract all text parts from email (plain text and HTML)
        2. Try specific patterns (e.g., "Your Axiom security code is: 123456")
        3. Try generic patterns (e.g., "<span>123456</span>")
        4. Fallback: search for any 6-digit number near "security code"

        ## Args:
        - `email_message` (Message): Parsed email message object

        ## Returns:
        - `str`: 6-digit OTP code if found
        - `None`: If no OTP pattern matches

        ## Regex Patterns (in order of priority):
        1. "Your Axiom security code is[:\\s]+(\\d{6})"
        2. "Your security code is[:\\s]+(\\d{6})"
        3. "security code[:\\s]+(\\d{6})"
        4. "<span[^>]*>(\\d{6})</span>"
        5. "<b>(\\d{6})</b>"
        6. "<strong>(\\d{6})</strong>"
        7. Any 6-digit number near "security code" text

        ## Design Rationale:
        - Multiple patterns handle different email templates
        - HTML patterns catch formatted emails
        - Fallback ensures detection even in unusual formats
        - Requires "security code" context to avoid false positives
        """
        # Extract body text from email
        body_text = self._get_email_body(email_message)

        # List of regex patterns to try (ordered by specificity)
        patterns: List[str] = [
            r"Your Axiom security code is[:\s]+(\d{6})",
            r"Your security code is[:\s]+(\d{6})",
            r"security code[:\s]+(\d{6})",
            r"<span[^>]*>(\d{6})</span>",
            r"<b>(\d{6})</b>",
            r"<strong>(\d{6})</strong>",
        ]

        # Try each pattern
        for pattern in patterns:
            match = re.search(pattern, body_text, re.IGNORECASE)
            if match:
                return match.group(1)

        # Fallback: find any 6-digit number near security code mention
        if "security code" in body_text.lower() or "Your Axiom" in body_text:
            # Look for any 6-digit number
            digit_pattern = r"\b(\d{6})\b"
            match = re.search(digit_pattern, body_text)
            if match:
                return match.group(1)

        return None

    def _get_email_body(self, email_message: Message) -> str:
        """
        Extract text content from email message.

        Handles multipart emails by extracting all text/plain and text/html parts.
        Decodes content transfer encoding (base64, quoted-printable, etc.).

        ## Args:
        - `email_message` (Message): Parsed email message object

        ## Returns:
        - `str`: Concatenated text content from all text parts

        ## Algorithm:
        1. Check if email is multipart (has multiple content sections)
        2. If multipart: iterate through parts and extract text content
        3. If simple: extract payload directly
        4. Decode payload based on Content-Transfer-Encoding
        5. Return concatenated text

        ## Supported Content Types:
        - text/plain: Plain text content
        - text/html: HTML content (tags preserved for pattern matching)
        """
        body_text = ""

        if email_message.is_multipart():
            # Email has multiple parts (text, HTML, attachments, etc.)
            for part in email_message.walk():
                content_type = part.get_content_type()

                # Only process text content
                if content_type in ["text/plain", "text/html"]:
                    try:
                        # Get payload and decode
                        payload = part.get_payload(decode=True)
                        if payload and isinstance(payload, bytes):
                            # Decode bytes to string
                            charset = part.get_content_charset() or "utf-8"
                            body_text += payload.decode(charset, errors="ignore")
                    except Exception:
                        # Skip parts that fail to decode
                        continue
        else:
            # Simple email with single content part
            try:
                payload = email_message.get_payload(decode=True)
                if payload and isinstance(payload, bytes):
                    charset = email_message.get_content_charset() or "utf-8"
                    body_text = payload.decode(charset, errors="ignore")
            except Exception:
                pass

        return body_text

    def wait_for_otp(
        self, timeout_seconds: int = 120, check_interval_seconds: int = 5
    ) -> Optional[str]:
        """
        Wait for a new OTP email to arrive with polling.

        Continuously checks for new unread OTP emails until one is found
        or the timeout is reached. Useful for automated authentication flows.

        ## Process:
        1. Record start time
        2. Loop until timeout:
           a. Check for recent OTP emails (last 3 minutes)
           b. If found: return OTP immediately
           c. If not found: wait for check_interval seconds
           d. Print progress message
        3. Return None if timeout reached

        ## Args:
        - `timeout_seconds` (int): Maximum time to wait in seconds (default: 120)
        - `check_interval_seconds` (int): Delay between checks in seconds (default: 5)

        ## Returns:
        - `str`: 6-digit OTP code when found
        - `None`: If timeout reached without finding OTP

        ## Side Effects:
        - Prints progress messages to stdout
        - Makes repeated IMAP connections (one per check)
        - Marks the found email as read

        ## Design Decisions:
        - Uses 3-minute lookback window to focus on recent emails
        - Polls instead of IMAP IDLE for simplicity and compatibility
        - Prints progress for user feedback during wait

        ## Example:
        ```python
        # Trigger authentication, then wait for OTP
        auth_manager.authenticate_step1()

        # Wait up to 2 minutes for OTP email
        otp = fetcher.wait_for_otp(timeout_seconds=120, check_interval_seconds=5)

        if otp:
            auth_manager.authenticate_step2(otp)
        else:
            print("No OTP received in time")
        ```
        """
        start_time = time.time()
        timeout_duration = timeout_seconds
        check_count = 0

        print("Checking for new OTP emails (will only check UNREAD messages)...")

        while (time.time() - start_time) < timeout_duration:
            check_count += 1

            # Check for recent OTP (last 3 minutes)
            otp = self.fetch_otp_recent(minutes_ago=3)

            if otp:
                print("✓ Found new OTP!")
                return otp

            # Calculate remaining time
            elapsed = time.time() - start_time
            remaining = int(timeout_duration - elapsed)

            print(
                f"  Check #{check_count}: No new OTP yet, {remaining} seconds remaining..."
            )

            # Wait before next check
            time.sleep(check_interval_seconds)

        print(f"✗ Timeout: No new OTP received within {timeout_seconds} seconds")
        return None


def from_env() -> Optional[OtpFetcher]:
    """
    # Create OTP Fetcher from Environment Variables

    Convenience factory function that reads email credentials from
    environment variables and creates an OtpFetcher instance.

    ## Environment Variables:
    - `INBOX_LV_EMAIL`: Email address (e.g., "user@inbox.lv")
    - `INBOX_LV_PASSWORD`: Email password or app-specific password

    ## Returns:
    - `OtpFetcher`: Configured fetcher instance if env vars are set
    - `None`: If either environment variable is missing

    ## Security Best Practice:
    Storing credentials in environment variables is more secure than
    hardcoding them in source code. Consider using:
    - `.env` files with python-dotenv for local development
    - Secret management services for production (e.g., AWS Secrets Manager)
    - Environment variables in CI/CD pipelines

    ## Example:
    ```python
    # Set environment variables
    import os
    os.environ['INBOX_LV_EMAIL'] = 'user@inbox.lv'
    os.environ['INBOX_LV_PASSWORD'] = 'secure_password'

    # Create fetcher from environment
    fetcher = from_env()

    if fetcher:
        otp = fetcher.fetch_otp()
    else:
        print("Email credentials not configured in environment")
    ```

    ## Usage with .env file:
    ```bash
    # .env file
    INBOX_LV_EMAIL=user@inbox.lv
    INBOX_LV_PASSWORD=secure_password
    ```

    ```python
    # Python code
    from dotenv import load_dotenv
    from axiomclient.email.otp_fetcher import from_env

    load_dotenv()  # Load .env file
    fetcher = from_env()  # Create fetcher from loaded env vars
    ```
    """
    email_address = os.environ.get("INBOX_LV_EMAIL")
    password = os.environ.get("INBOX_LV_PASSWORD")

    # Only create fetcher if both credentials are present
    if email_address and password:
        return OtpFetcher(email_address, password)

    return None
