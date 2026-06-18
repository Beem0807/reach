from typing import Optional

import requests


class ReachClient:
    def __init__(self, api_url: str, tenant_token: str):
        self.api_url = api_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {tenant_token}",
            "Content-Type": "application/json",
        })

    def _url(self, path: str) -> str:
        return self.api_url + path

    def create_job(self, agent_id: str, command: str) -> dict:
        resp = self.session.post(
            self._url("/jobs"),
            json={"agent_id": agent_id, "command": command},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def get_job(self, job_id: str) -> dict:
        resp = self.session.get(self._url(f"/jobs/{job_id}"), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_agent(self, agent_id: str) -> dict:
        resp = self.session.get(self._url(f"/agents/{agent_id}"), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def list_agents(self, tag: Optional[str] = None) -> dict:
        params = {"tag": tag} if tag else {}
        resp = self.session.get(self._url("/agents"), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_me(self) -> dict:
        resp = self.session.get(self._url("/me"), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def list_jobs(self, agent_id: Optional[str] = None, limit: int = 20, cursor: Optional[str] = None) -> dict:
        params: dict = {"limit": limit}
        if agent_id:
            params["agent_id"] = agent_id
        if cursor:
            params["cursor"] = cursor
        resp = self.session.get(self._url("/jobs"), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
