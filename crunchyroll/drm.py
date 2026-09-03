import base64
import glob
import os
from typing import Dict, Optional
from pywidevine.cdm import Cdm
from pywidevine.device import Device, DeviceTypes
from pywidevine.pssh import PSSH
from .http_client import CrunchyrollHttpClient


def _key_bytes(value) -> bytes:
    """Convert pywidevine key/KID values to raw bytes consistently."""
    if isinstance(value, bytes):
        return value
    if hasattr(value, "bytes"):
        return value.bytes
    return bytes.fromhex(str(value).replace("-", "").replace(" ", ""))


def get_widevine_device() -> Optional[Device]:
    """hunt for a widevine device (.wvd or bin+pem)"""
    wvd_files = glob.glob("*.wvd")
    if wvd_files:
        return Device.load(wvd_files[0])

    if os.path.exists("client_id.bin") and os.path.exists("private_key.pem"):
        with open("client_id.bin", "rb") as f:
            client_id = f.read()
        with open("private_key.pem", "rb") as f:
            private_key = f.read()
        return Device(
            type_=DeviceTypes.CHROME,
            security_level=3,
            flags=None,
            private_key=private_key,
            client_id=client_id,
        )


    return None


def send_challenge(
    client: CrunchyrollHttpClient, content_id: str, video_token: str, challenge: bytes
) -> bytes:
    """send the cdm challenge and get back the license"""
    import requests as _requests

    url = "https://www.crunchyroll.com/license/v1/license/widevine"
    headers = {
        "Content-Type": "application/octet-stream",
        "X-Cr-Content-Id": content_id,
        "X-Cr-Video-Token": video_token,
        "Authorization": f"Bearer {client.token}",
        "Origin": "https://static.crunchyroll.com",
        "Referer": "https://static.crunchyroll.com/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0",
    }

    resp = _requests.post(url, headers=headers, data=challenge)
    resp.raise_for_status()

    result = resp.json()
    license_b64 = result.get("license")
    if not license_b64:
        raise RuntimeError(f"License field missing in response: {result}")

    return base64.b64decode(license_b64)




def get_license(
    client: CrunchyrollHttpClient, pssh_data: str, content_id: str, video_token: str
) -> Dict[bytes, bytes]:
    """do the widevine handshake and extract the decryption keys"""
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
                kid_bytes = _key_bytes(k.kid)
                key_bytes = _key_bytes(k.key)
                keys[kid_bytes] = key_bytes

        if not keys:
            # whatever, just grab all the keys
            for k in cdm.get_keys(session_id):
                kid_bytes = _key_bytes(k.kid)
                key_bytes = _key_bytes(k.key)
                keys[kid_bytes] = key_bytes

        return keys
    finally:
        cdm.close(session_id)

