import json
from pydantic import Field, BaseModel


class NewTokenModel(BaseModel):
    sol_amount: float = Field(..., alias="solAmount")
    creator: str = Field(..., alias="traderPublicKey")
    mint: str
    name: str | None = None
    symbol: str | None = None
    tx_type: str = Field(..., alias="txType")
    uri: str | None = None
    signature: str
    pool: str
    market_cap_sol: float | None = Field(0.0, alias="marketCapSol")
    is_mayhem_mode: bool = False
    model_config = {"populate_by_name": True}

    def __str__(self):
        return json.dumps(self.model_dump(), indent=2, ensure_ascii=False)
