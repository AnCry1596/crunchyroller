import uuid
import requests

DEVICE_ID = str(uuid.uuid4())


def get_access_token(etp_rt: str) -> str:
    """Fetches an access token from Crunchyroll using etp_rt cookie."""
    url = "https://www.crunchyroll.com/auth/v1/token"
    headers = {
        "Authorization": "Basic bm9haWhkZXZtXzZpeWcwYThsMHE6",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0",
    }
    cookies = {
        "device_id": DEVICE_ID,
        "etp_rt": etp_rt,
    }
    data = {
        "device_id": DEVICE_ID,
        "device_type": "Firefox on Linux",
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
