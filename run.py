#!/usr/bin/env python3
"""Entry point for the Knock Not server."""

import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))

    uvicorn.run(
        "server.main:app",
        host=host,
        port=port,
        reload=True,
        log_level="info",
    )
