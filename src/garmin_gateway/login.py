"""Interactive Garmin Connect login: saves fresh tokens to GARMIN_TOKEN_STORE.

Use for a first setup or when the saved tokens no longer work:
    docker exec -it <container> python -m garmin_gateway.login
"""

import getpass
import os
from pathlib import Path

DEFAULT_TOKEN_STORE = "/data/garmin_tokens.json"


def token_store() -> str:
    # Read directly so the script works without the MCP/REST keys that config.py requires.
    return os.getenv("GARMIN_TOKEN_STORE", DEFAULT_TOKEN_STORE)


def main() -> None:
    from garminconnect import Garmin

    email = os.getenv("GARMIN_EMAIL") or input("Garmin email: ")
    password = os.getenv("GARMIN_PASSWORD") or getpass.getpass("Garmin password: ")

    client = Garmin(email, password, prompt_mfa=lambda: input("MFA code: "))
    # No tokenstore: always log in with credentials, even if stale tokens exist.
    client.login()

    path = Path(token_store()).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    client.client.dump(str(path))
    print(f"Logged in as {client.get_full_name()}, tokens saved to {path}")


if __name__ == "__main__":
    main()
