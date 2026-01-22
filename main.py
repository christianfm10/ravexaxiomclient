import logging
from axiomclient.monitor import main

if __name__ == "__main__":
    import asyncio

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("✅ Shutdown completed gracefully")
