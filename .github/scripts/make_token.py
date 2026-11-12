#!/usr/bin/env python3
"""Creates the Apple Music developer token for basketweave and writes it to
a small JSON file (token.json) that the Android app downloads.

Reads three values from the environment (GitHub Actions secrets):
    APPLE_TEAM_ID      the 10-character Apple team id
    APPLE_KEY_ID       the 10-character id of the MusicKit key
    APPLE_PRIVATE_KEY  the full text of the AuthKey_XXXX.p8 file

Usage:  make_token.py OUTPUT_FILE

The private key is only used to sign; it is never printed or written.
The token itself is not a secret (it is readable in every app copy), but it
is not printed either.
"""
import base64
import json
import os
import sys
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)

# Apple's maximum is 15777000 seconds (about 182.6 days). 180 days stays
# safely below it. The app only accepts tokens that are valid for another
# 7 to 190 days.
LIFETIME_SECONDS = 180 * 24 * 60 * 60


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def load_private_key(text: str) -> ec.EllipticCurvePrivateKey:
    # A secret pasted into a web form sometimes arrives with literal "\n"
    # sequences or stray blank space; accept both.
    text = text.strip().replace("\\n", "\n")
    key = serialization.load_pem_private_key(text.encode("utf-8"), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ValueError("The key is not an ES256 (P-256) key.")
    return key


def make_token(team_id: str, key_id: str, key: ec.EllipticCurvePrivateKey,
               now: int, lifetime: int = LIFETIME_SECONDS) -> str:
    header = {"alg": "ES256", "kid": key_id}
    payload = {"iss": team_id, "iat": now, "exp": now + lifetime}

    signing_input = (
        b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + b64url(json.dumps(payload, separators=(",", ":")).encode())
    )

    # The library returns the signature in DER form; JWT wants the raw
    # 32-byte r followed by the 32-byte s.
    der = key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")

    return signing_input + "." + b64url(raw)


def verify_token(token: str, key: ec.EllipticCurvePrivateKey) -> None:
    """Checks the finished token against the key's public half — a mistake
    here must fail the job instead of publishing a token Apple would reject."""
    head, body, sig = token.split(".")
    raw = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
    if len(raw) != 64:
        raise ValueError("The signature does not have the expected length.")
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"),
                               int.from_bytes(raw[32:], "big"))
    try:
        key.public_key().verify(der, f"{head}.{body}".encode("ascii"),
                                ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise ValueError("The new token's signature does not verify.") from exc


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: make_token.py OUTPUT_FILE", file=sys.stderr)
        return 2

    values = {name: os.environ.get(name, "").strip()
              for name in ("APPLE_TEAM_ID", "APPLE_KEY_ID", "APPLE_PRIVATE_KEY")}
    missing = [name for name, value in values.items() if not value]
    if missing:
        print("Missing secret(s): " + ", ".join(missing), file=sys.stderr)
        return 1

    try:
        key = load_private_key(values["APPLE_PRIVATE_KEY"])
    except Exception as exc:  # never include the key text in the message
        print(f"Could not read APPLE_PRIVATE_KEY: {type(exc).__name__}", file=sys.stderr)
        return 1

    now = int(time.time())
    token = make_token(values["APPLE_TEAM_ID"], values["APPLE_KEY_ID"], key, now)
    verify_token(token, key)

    result = {
        "version": 1,
        "token": token,
        "generatedAt": now,
        "expiresAt": now + LIFETIME_SECONDS,
    }

    tmp = sys.argv[1] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
        f.write("\n")
    os.replace(tmp, sys.argv[1])

    print(f"token.json written; generatedAt={now}, expiresAt={now + LIFETIME_SECONDS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
