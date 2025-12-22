from pydantic import Field, BaseModel


class DevWalletFunding(BaseModel):
    wallet_address: str = Field(..., alias="walletAddress")
    funding_wallet_address: str = Field(..., alias="fundingWalletAddress")
    signature: str = Field(..., alias="signature")
    amount_sol: float = Field(..., alias="amountSol")
    funded_at: str = Field(..., alias="fundedAt")
    model_config = {"populate_by_name": True}


# class PulseItem(BaseModel):
#     pair_address: str
#     token_address: str | None = None
#     dev_address: str | None = None
#     token_name: str | None = None
#     token_ticker: str | None = None
#     token_decimals: int | None = None
#     protocol: str | None = None
#     market_cap_sol: float | None = None
#     liquidity_sol: float | None = None
#     liquidity_token: float | None = None
#     bonding_curve_percent: float | None = None
#     migrated_tokens: int | None = None
#     first_mint_date: str | None = None
#     dev_wallet_funding: DevWalletFunding | None = None
#     dev_tokens: int | None = None


class PairItem(BaseModel):
    pair_address: str
    signature: str | None = None
    token_address: str | None = None
    deployer_address: str | None = None
    token_name: str | None = None
    token_ticker: str | None = None
    token_uri: str | None = None
    token_decimals: int | None = None
    protocol: str | None = None
    first_market_cap_sol: float | None = None
    high_market_cap_sol: float | None = None
    market_cap_sol: float | None = None
    initial_liquidity_sol: float | None = None
    initial_liquidity_token: float | None = None
    supply: float | None = None
    bonding_curve_percent: float | None = None
    migrated_tokens: int | None = None
    created_at: str | None = None
    dev_wallet_funding: DevWalletFunding | None = None
    dev_tokens: int | None = None
    num_txn: int | None = None
    num_buys: int | None = None
    num_sells: int | None = None
