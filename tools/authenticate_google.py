"""
authenticate_google.py — Re-authenticate Google OAuth and refresh token.json

Run this when token.json is expired or revoked:
    python tools/authenticate_google.py

Opens a browser window for Google login, then saves a fresh token.json.
"""

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/spreadsheets",
]

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
TOKEN_PATH = os.path.join(PROJECT_ROOT, "token.json")
CREDS_PATH = os.path.join(PROJECT_ROOT, "credentials.json")


def main():
    if not os.path.exists(CREDS_PATH):
        print("ERROR: credentials.json not found. Download it from Google Cloud Console.", file=sys.stderr)
        sys.exit(1)

    print("Opening browser for Google authentication...")
    flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())

    print(f"token.json saved to {TOKEN_PATH}")
    print("Authentication successful.")


if __name__ == "__main__":
    main()
