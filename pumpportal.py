from dotenv import load_dotenv
from axiomclient.pumpportal import PumpPortalMonitor
from axiomclient.pumpportal.callbacks import new_token_callback

load_dotenv()


async def migration_callback(data: dict):
    print("🔔 Migration Event:", data)


async def account_trade_callback(data: dict):
    print("🔔 Account Trade Event:", data)


async def token_trade_callback(data: dict):
    print("🔔 Token Trade Event:", data)


monitor = PumpPortalMonitor()


async def main():
    try:
        await monitor.subscribe_new_token(new_token_callback)
        await monitor.start()
    finally:
        await monitor.unsubscribe_new_token()
        await monitor.close()


if __name__ == "__main__":
    import asyncio

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        monitor.logger.info("✅ Shutdown completed gracefully")
