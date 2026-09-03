import uuid
import requests

DEVICE_ID = str(uuid.uuid4())


def get_access_token(etp_rt: str) -> str:
    """Fetches an access token from Crunchyroll using etp_rt cookie."""
    url = "https://www.crunchyroll.com/auth/v1/token"
    headers = {
        "Authorization": "Basic bm9haWhkZXZtXzZpeWcwYThsMHE6",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    }
    cookies = {
        "device_id": DEVICE_ID,
        "etp_rt": etp_rt,
    }
    data = {
        "device_id": DEVICE_ID,
        "device_type": "Chrome on Windows",
        "grant_type": "etp_rt_cookie",
    }

    response = requests.post(url, headers=headers, cookies=cookies, data=data)
    if response.status_code != 200:
        raise RuntimeError(f"Failed to get access token (status {response.status_code}): {response.text}")

    result = response.json()
    token = result.get("access_token")
    if not token:
        raise RuntimeError(f"Access token not found in response: {result}")
    return token
