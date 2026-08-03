import base64
import glob
import os
from typing import Dict, Optional
from pywidevine.cdm import Cdm
from pywidevine.device import Device, DeviceTypes
from pywidevine.pssh import PSSH
from .http_client import CrunchyrollHttpClient


def get_widevine_device() -> Optional[Device]:
    """Finds and loads a Widevine Device (.wvd file or client_id.bin + private_key.pem)."""
    wvd_files = glob.glob("*.wvd")
    if wvd_files:
        return Device.load(wvd_files[0])

    if os.path.exists("client_id.bin") and os.path.exists("private_key.pem"):
        with open("client_id.bin", "rb") as f:
            client_id = f.read()
        with open("private_key.pem", "rb") as f:
            private_key = f.read()
        return Device(
            type_=DeviceTypes.ANDROID,
            security_level=3,
            flags=None,
            private_key=private_key,
            client_id=client_id,
        )

    return None


def send_challenge(
    client: CrunchyrollHttpClient, content_id: str, video_token: str, challenge: bytes
) -> bytes:
    """Sends Widevine license challenge to Crunchyroll and returns raw license bytes."""
    url = "https://www.crunchyroll.com/license/v1/license/widevine"
    headers = {
        "Content-Type": "application/octet-stream",
        "X-Cr-Content-Id": content_id,
        "X-Cr-Video-Token": video_token,
        "Origin": "https://static.crunchyroll.com",
        "Referer": "https://static.crunchyroll.com/",
    }

    resp = client.do_request("POST", url, headers=headers, data=challenge)
    resp.raise_for_status()

    result = resp.json()
    license_b64 = result.get("license")
    if not license_b64:
        raise RuntimeError(f"License field missing in response: {result}")

    return base64.b64decode(license_b64)


def get_license(
    client: CrunchyrollHttpClient, pssh_data: str, content_id: str, video_token: str
) -> Dict[bytes, bytes]:
    """
    Executes Widevine CDM challenge-response exchange to obtain media decryption keys.
    Returns a dictionary mapping Key ID (16 bytes) -> Key (16 bytes).
    """
    device = get_widevine_device()
    if device is None:
        raise RuntimeError(
            "no widevine device provided. You either need:\n"
            '- a ".wvd" file,\n'
            '- or "client_id.bin" and "private_key.pem" files.\n'
            "I'm not sharing links for obvious reasons, but search \"ready to use cdms\" on Google :)"
        )

    cdm = Cdm.from_device(device)
    session_id = cdm.open()

    try:
        pssh = PSSH(pssh_data)
        challenge = cdm.get_license_challenge(session_id, pssh)
        license_payload = send_challenge(client, content_id, video_token, challenge)
        cdm.parse_license(session_id, license_payload)

        keys: Dict[bytes, bytes] = {}
        for k in cdm.get_keys(session_id):
            if str(k.type).upper() in ("CONTENT", "STREAMING", "1", "KEYTYPE.CONTENT", "KEYTYPE.STREAMING"):
                kid_bytes = k.kid.bytes if hasattr(k.kid, "bytes") else (k.kid if isinstance(k.kid, bytes) else bytes.fromhex(str(k.kid).replace("-", "")))
                key_bytes = k.key if isinstance(k.key, bytes) else bytes.fromhex(str(k.key))
                keys[kid_bytes] = key_bytes

        if not keys:
            # Fallback: take all keys if type filtering was strict
            for k in cdm.get_keys(session_id):
                kid_bytes = k.kid.bytes if hasattr(k.kid, "bytes") else (k.kid if isinstance(k.kid, bytes) else bytes.fromhex(str(k.kid).replace("-", "")))
                key_bytes = k.key if isinstance(k.key, bytes) else bytes.fromhex(str(k.key))
                keys[kid_bytes] = key_bytes

        return keys
    finally:
        cdm.close(session_id)

