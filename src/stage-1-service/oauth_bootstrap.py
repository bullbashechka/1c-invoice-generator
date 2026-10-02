"""One-time interactive import of the app-owner OAuth pair into a private local file."""

import argparse
from getpass import getpass
from pathlib import Path
import time

from oauth_tokens import OAuthTokenStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path', required=True, type=Path,
                        help='Path outside the source tree, protected by local OS permissions')
    args = parser.parse_args()
    client_id = input('Bitrix24 app client ID: ').strip()
    client_secret = getpass('Bitrix24 app client secret: ').strip()
    access_token = getpass('Initial OAuth access token: ').strip()
    refresh_token = getpass('Initial OAuth refresh token: ').strip()
    expires_text = input('Access token lifetime in seconds [3600]: ').strip() or '3600'
    try:
        expires_in = int(expires_text)
        if not 1 <= expires_in <= 86400:
            raise ValueError
        OAuthTokenStore.initialize(args.path, {
            'clientId':client_id, 'clientSecret':client_secret,
            'accessToken':access_token, 'refreshToken':refresh_token,
            'expiresAt':time.time() + expires_in,
        })
    except FileExistsError:
        raise SystemExit('OAuth file already exists; refusing to replace the token pair') from None
    except (OSError, ValueError):
        raise SystemExit('OAuth data is invalid or the local file could not be written') from None
    print('OAuth pair imported. The existing file will not be overwritten by this command.')


if __name__ == '__main__':
    main()
