from typing import Optional

import requests


class ReachClient:
    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

    def _url(self, path: str) -> str:
        return self.api_url + path

    def create_job(self, agent_id: str, command: str = "", dry_run: bool = False,
                   argv: list = None) -> dict:
        # Structured exec: pass argv (bin + args) for a no-shell run; else a command string.
        body = {"agent_id": agent_id}
        if argv is not None:
            body["argv"] = argv
        else:
            body["command"] = command
        if dry_run:
            body["dry_run"] = True
        resp = self.session.post(
            self._url("/jobs"),
            json=body,
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

    def list_jobs(self, agent_id: Optional[str] = None, limit: int = 20, cursor: Optional[str] = None,
                  fleet_id: Optional[str] = None, run_id: Optional[str] = None) -> dict:
        params: dict = {"limit": limit}
        if agent_id:
            params["agent_id"] = agent_id
        if fleet_id:
            params["fleet_id"] = fleet_id
        if run_id:
            params["run_id"] = run_id
        if cursor:
            params["cursor"] = cursor
        resp = self.session.get(self._url("/jobs"), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def list_fleets(self) -> dict:
        resp = self.session.get(self._url("/fleets"), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def list_fleet_agents(self, fleet_id: str) -> dict:
        resp = self.session.get(self._url(f"/fleets/{fleet_id}/agents"), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def list_fleet_approved(self, fleet_id: str, status: str = "approved") -> dict:
        resp = self.session.get(self._url(f"/fleets/{fleet_id}/approvals"), params={"status": status}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def fanout_by_tag(self, tag: str, command: str, agent_type: Optional[str] = None,
                      dry_run: bool = False) -> dict:
        body: dict = {"tag": tag, "command": command}
        if agent_type:
            body["type"] = agent_type
        if dry_run:
            body["dry_run"] = True
        resp = self.session.post(self._url("/jobs/fanout"), json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def list_fleet_runs(self, fleet_id: str, limit: int = 20) -> dict:
        resp = self.session.get(self._url(f"/fleets/{fleet_id}/runs"), params={"limit": limit}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def list_tag_runs(self, limit: int = 20) -> dict:
        """Fan-out runs across standalone agents (tag fan-outs), grouped by batch."""
        resp = self.session.get(self._url("/jobs/runs"), params={"limit": limit}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def fleet_fanout(self, fleet_id: str, command: str, max_targets: Optional[int] = None,
                     idempotency_key: Optional[str] = None, dry_run: bool = False) -> dict:
        body: dict = {"command": command}
        if max_targets is not None:
            body["max_targets"] = max_targets
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        if dry_run:
            body["dry_run"] = True
        resp = self.session.post(self._url(f"/fleets/{fleet_id}/jobs"), json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_run(self, run_id: str) -> dict:
        """Status of a fan-out run: state, counts, terminal, bounded failures."""
        resp = self.session.get(self._url(f"/tenant/runs/{run_id}"), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def pause_run(self, run_id: str) -> dict:
        """Pause a staged run - hold its remaining waves."""
        resp = self.session.post(self._url(f"/tenant/runs/{run_id}/pause"), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def resume_run(self, run_id: str) -> dict:
        """Resume a paused staged run - release the next wave."""
        resp = self.session.post(self._url(f"/tenant/runs/{run_id}/resume"), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def cancel_run(self, run_id: str) -> dict:
        """Cancel a staged run - drop its not-yet-released waves."""
        resp = self.session.post(self._url(f"/tenant/runs/{run_id}/cancel"), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def list_agent_approved(self, agent_id: str, status: str = "approved") -> dict:
        resp = self.session.get(self._url(f"/agents/{agent_id}/approved-commands"), params={"status": status}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def create_approval(self, command: str, agent_id: Optional[str] = None,
                        fleet_id: Optional[str] = None, duration: Optional[str] = None) -> dict:
        body: dict = {"command": command}
        if agent_id:
            body["agent_id"] = agent_id
        if fleet_id:
            body["fleet_id"] = fleet_id
        if duration:
            body["duration"] = duration
        resp = self.session.post(self._url("/tenant/approvals"), json=body, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def approve_approval(self, approval_id: str, duration: Optional[str] = None) -> dict:
        body = {"duration": duration} if duration else {}
        resp = self.session.put(self._url(f"/tenant/approvals/{approval_id}/approve"), json=body, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def deny_approval(self, approval_id: str) -> dict:
        resp = self.session.put(self._url(f"/tenant/approvals/{approval_id}/deny"), json={}, timeout=15)
        resp.raise_for_status()
        return resp.json()
