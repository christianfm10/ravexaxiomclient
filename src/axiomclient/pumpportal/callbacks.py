from types import CoroutineType
from typing import Any
from axiomclient.pumpportal.models import NewTokenModel


async def should_skip_token(data: dict) -> bool:
    # Example logic: skip tokens with solAmount less than 1.0
    pool = data.get("pool", "")
    if pool != "pump":
        return True
    sol_amount = data.get("solAmount", 0)
    if not ((0.99 < sol_amount < 0.991) or (2.97 < sol_amount < 2.98)):
        return True
    uri = data.get("uri", "")
    if "ipfs" not in uri:
        return True
    is_mayhem_mode = data.get("is_mayhem_mode", False)
    if is_mayhem_mode:
        return True

    return False


async def new_token_callback(data: dict[str, Any]):
    if await should_skip_token(data):
        return

    new_token = NewTokenModel(**data)  # Validate data structure

    print("🔔 Token Monitor Event:", new_token)
