"""
Loads Reddit API credentials from the .env file.
Import `get_reddit_credentials()` from your data-pull script in Phase 1.
"""
import os
from dotenv import load_dotenv

load_dotenv()  # reads the .env file in the project root


def get_reddit_credentials() -> dict:
    """Return Reddit API credentials, or raise a clear error if missing."""
    creds = {
        "client_id": os.getenv("REDDIT_CLIENT_ID"),
        "client_secret": os.getenv("REDDIT_CLIENT_SECRET"),
        "user_agent": os.getenv("REDDIT_USER_AGENT"),
    }
    # A value is "missing" if it's empty or still contains placeholder text.
    # Use `in` (not startswith) so the username placeholder inside the
    # user-agent string (".../u/your_reddit_username") is also caught.
    missing = [
        k for k, v in creds.items()
        if not v or "your_" in v.lower() or v.endswith("_here")
    ]
    if missing:
        raise RuntimeError(
            f"Missing Reddit credentials: {missing}. "
            "Copy .env.example to .env and fill in your values "
            "(see docs/REDDIT_SETUP.md)."
        )
    return creds
