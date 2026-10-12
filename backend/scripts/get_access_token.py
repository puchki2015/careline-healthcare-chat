"""Interactively sign in a test patient and display a Supabase access token.

Run this helper only during development with:

    python -m scripts.get_access_token

The project URL and anon key come from ``backend/.env``. The password is read
with ``getpass`` so it is not displayed on screen or stored in shell history.
This script never uses the service-role key.
"""

from getpass import getpass

from supabase import create_client
from supabase_auth.errors import AuthApiError

from app.core.config import get_settings


def main() -> int:
    """Sign in one existing test user and print the short-lived access token."""

    settings = get_settings()
    email = input("Test patient email: ").strip()
    password = getpass("Test patient password: ")

    if not email or not password:
        print("Email and password are required.")
        return 1

    # The anon/publishable key is correct for an ordinary patient sign-in. A
    # service-role key must never be used to simulate a normal browser session.
    client = create_client(
        str(settings.supabase_url).rstrip("/"),
        settings.supabase_anon_key.get_secret_value(),
    )

    try:
        response = client.auth.sign_in_with_password(
            {
                "email": email,
                "password": password,
            }
        )
    except AuthApiError as exc:
        # Supabase deliberately uses generic credential errors. Do not print
        # the password or add it to logging context when reporting a failure.
        print(f"Sign-in failed: {exc}")
        return 1

    if response.session is None or not response.session.access_token:
        print("Sign-in completed without a session. Confirm the user's email and try again.")
        return 1

    print("\nAccess token (treat it like a temporary password):\n")
    print(response.session.access_token)
    print("\nDo not commit, save, screenshot, or share this token.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
