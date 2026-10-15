import base64
import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

KEY_IDS = ["user-george", "merchant-A", "merchant-B", "merchant-C", "merchant-D", "merchant-E"]


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def private_key(key_id: str) -> Ed25519PrivateKey:
    seed = hashlib.sha256(b"proto-demo-" + key_id.encode()).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def public_key(key_id: str) -> Ed25519PublicKey:
    return private_key(key_id).public_key()


def sign(payload: dict, key_id: str) -> dict:
    sig = private_key(key_id).sign(canonical(payload))
    return {"payload": payload, "signature": base64.b64encode(sig).decode()}


def verify(signed: dict, key_id: str) -> bool:
    try:
        public_key(key_id).verify(base64.b64decode(signed["signature"]), canonical(signed["payload"]))
        return True
    except (InvalidSignature, KeyError, TypeError, ValueError):
        return False


def sha256_hex(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def encode_token(signed_intent: dict) -> str:
    return base64.urlsafe_b64encode(canonical(signed_intent)).decode()


def decode_token(token: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(token.encode()))
