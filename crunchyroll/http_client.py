import time
import requests
from typing import Optional
from .auth import get_access_token, login_with_credentials, load_config, save_config
from .session_pool import SessionPool, ConcurrencyConfig


class CrunchyrollHttpClient:
    def __init__(
        self,
        etp_rt: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        session_pool: Optional[SessionPool] = None,
    ):
        self.etp_rt = etp_rt or ""
        self.username = username
        self.password = password
        self.token = ""

        self.session_pool = session_pool or SessionPool(
            config=ConcurrencyConfig(
                pool_size=32,
                max_retries=5,
                backoff_factor=1.5,
                timeout=20,
            )
        )
        self.session = self.session_pool.get_session()

        # try to load etp_rt from config
        if not self.etp_rt:
            cfg = load_config()
            if "etp_rt" in cfg and cfg["etp_rt"]:
                self.etp_rt = cfg["etp_rt"]

        # still no etp_rt? try grabbing it with creds (good luck)
        if not self.etp_rt and self.username and self.password:
            acc_tok, ref_tok = login_with_credentials(self.username, self.password)
            self.etp_rt = ref_tok
            self.token = acc_tok
            save_config({"etp_rt": ref_tok, "username": self.username})

        if not self.token and self.etp_rt:
            self.refresh_token()

    def refresh_token(self) -> None:
        self.token = get_access_token(self.etp_rt)

    def do_request(self, method: str, url: str, **kwargs) -> requests.Response:
        headers = kwargs.pop("headers", {})
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if "User-Agent" not in headers:
            headers["User-Agent"] = "Mozilla/5.0 (X11; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0"

        response = self.session.request(method, url, headers=headers, **kwargs)
        if response.status_code == 401:
            print("Access token expired. Refetching one...")
            self.refresh_token()
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            response = self.session.request(method, url, headers=headers, **kwargs)

        retries = 0
        while response.status_code == 420 and retries < 10:
            retries += 1
            print(f"Rate limited by Crunchyroll (420). Waiting 30 seconds for session cooldown ({retries}/10)...")
            time.sleep(30)
            response = self.session.request(method, url, headers=headers, **kwargs)

        return response

    def close(self):
        if self.session_pool:
            self.session_pool.close()
