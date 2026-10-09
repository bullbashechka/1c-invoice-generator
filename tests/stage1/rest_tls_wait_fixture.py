"""Temporary loopback TLS wait fixture for BSL diagnostics, not an integration service."""
import socket
import time


if __name__ == "__main__":
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 443))
        listener.listen(1)
        listener.settimeout(45)
        print("TLS_FIXTURE_READY", flush=True)
        try:
            connection, _ = listener.accept()
        except TimeoutError:
            print("TLS_FIXTURE_NO_CLIENT", flush=True)
        else:
            with connection:
                print("TLS_FIXTURE_CONNECTED", flush=True)
                # Do not finish TLS, read bytes, supply certificates or log credentials.
                time.sleep(16)
        print("TLS_FIXTURE_CLOSED", flush=True)
