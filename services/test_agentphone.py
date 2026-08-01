"""Signature verification is the one piece of agentphone.py that's pure and
security-load-bearing — worth pinning down with tests, same spirit as
engine/test_decision.py. Everything else in that file just makes an HTTP
call and isn't meaningfully testable without a live account.
"""

from __future__ import annotations

import hashlib
import hmac

from services.agentphone import verify_signature

SECRET = "test-secret"


def _sign(timestamp: str, raw_body: bytes, secret: str = SECRET) -> str:
    signed_string = f"{timestamp}.".encode() + raw_body
    digest = hmac.new(secret.encode(), signed_string, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_valid_signature_accepted():
    body = b'{"event":"agent.message"}'
    timestamp = "1700000000"
    sig = _sign(timestamp, body)

    assert verify_signature(
        timestamp=timestamp, raw_body=body, signature_header=sig, secret=SECRET
    )


def test_tampered_body_rejected():
    timestamp = "1700000000"
    sig = _sign(timestamp, b'{"event":"agent.message"}')

    assert not verify_signature(
        timestamp=timestamp,
        raw_body=b'{"event":"agent.message","injected":true}',
        signature_header=sig,
        secret=SECRET,
    )


def test_wrong_secret_rejected():
    body = b'{"event":"agent.message"}'
    timestamp = "1700000000"
    sig = _sign(timestamp, body, secret="not-the-real-secret")

    assert not verify_signature(
        timestamp=timestamp, raw_body=body, signature_header=sig, secret=SECRET
    )


def test_missing_prefix_rejected():
    body = b'{"event":"agent.message"}'
    assert not verify_signature(
        timestamp="1700000000",
        raw_body=body,
        signature_header="not-even-the-right-shape",
        secret=SECRET,
    )
