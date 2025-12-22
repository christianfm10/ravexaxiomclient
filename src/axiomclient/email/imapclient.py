from imapclient import IMAPClient

import imaplib

from imapclient import imap4
from axiomclient.email import tls


class CustomIMAPClient(IMAPClient):
    def _create_IMAP4(self):  # type: ignore
        if self.stream:
            return imaplib.IMAP4_stream(self.host)

        connect_timeout = getattr(self._timeout, "connect", None)

        if self.ssl:
            return tls.IMAP4_TLS(
                self.host,
                self.port,
                self.ssl_context,
                connect_timeout,
            )

        return imap4.IMAP4WithTimeout(self.host, self.port, connect_timeout)
