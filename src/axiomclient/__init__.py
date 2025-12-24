from axiomclient.client import AxiomClient
from axiomclient.auth.auth_manager import AuthManager as AxiomAuth
from axiomclient.websocket._client import AxiomWebSocketClient

__version__ = "1.2.0"
__all__ = ["AxiomClient", "AxiomAuth", "AxiomWebSocketClient", "__version__"]

# def main() -> None:
#     print("Hello from axiomclient!")
