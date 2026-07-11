"""Tests for staged-rollout control: refresh_run wave advancement + pause/resume/cancel."""
import json
from unittest.mock import patch, MagicMock

from handlers.runs import refresh_run
from handlers.run_control import handle_pause_run, handle_resume_run, handle_cancel_run

TENANT = "tenant_acme"
FLEET_ID = "fleet_1"
RUN = "run_stage1"
ADMIN = {"user_id": "u_admin", "tenant_id": TENANT, "role": "admin", "username": "admin"}
_FLEET = {"fleet_id": FLEET_ID, "tenant_id": TENANT, "name": "web-prod", "status": "ACTIVE"}


def _staged_run(state="running", current_wave=0, wave_total=2, mode="auto",
                on_failure="stop", fleet_id=FLEET_ID):
    return {"run_id": RUN, "tenant_id": TENANT, "fleet_id": fleet_id, "tag": None,
            "command": "deploy.sh", "created_by": "u_admin", "created_at": "2026-06-01T10:00:00Z",
            "dispatched": 4, "state": state, "counts": {"ok": 0, "failed": 0, "pending": 4, "running": 0},
            "rollout": {"waves": [2, 2], "mode": mode, "on_failure": on_failure},
            "current_wave": current_wave, "wave_total": wave_total}


def _job(agent, wave, status, exit_code=0):
    return {"agent_id": agent, "run_id": RUN, "wave": wave, "status": status,
            "exit_code": exit_code, "command": "deploy.sh"}


# --- refresh_run wave advancement -------------------------------------------

class TestRefreshAdvancement:
    def _refresh(self, run, jobs):
        with patch("handlers.runs.runs_repo") as rr, \
             patch("handlers.runs.jobs_repo") as jr, \
             patch("handlers.runs.agents_repo") as ar:
            rr.get.return_value = run
            jr.list_by_run.return_value = jobs
            jr.release_wave.return_value = [j for j in jobs if j["wave"] == run["current_wave"] + 1]
            agg = refresh_run(TENANT, RUN)
            return agg, rr, jr, ar

    def test_clean_wave0_releases_wave1(self):
        jobs = [_job("a1", 0, "SUCCEEDED"), _job("a2", 0, "SUCCEEDED"),
                _job("a3", 1, "HELD"), _job("a4", 1, "HELD")]
        agg, rr, jr, ar = self._refresh(_staged_run(current_wave=0), jobs)
        jr.release_wave.assert_called_once_with(RUN, 1)
        # current_wave persisted as 1, state running
        _, kwargs = rr.set_counts.call_args
        assert kwargs["current_wave"] == 1
        assert rr.set_counts.call_args[0][1] == "running"
        # released agents reactivated
        assert ar.set_active_until.call_count == 2

    def test_failing_wave0_auto_pauses(self):
        jobs = [_job("a1", 0, "FAILED", exit_code=1), _job("a2", 0, "SUCCEEDED"),
                _job("a3", 1, "HELD"), _job("a4", 1, "HELD")]
        agg, rr, jr, ar = self._refresh(_staged_run(current_wave=0, on_failure="stop"), jobs)
        jr.release_wave.assert_not_called()
        assert rr.set_counts.call_args[0][1] == "paused"
        assert agg["state"] == "paused"

    def test_wave0_in_flight_holds(self):
        jobs = [_job("a1", 0, "RUNNING"), _job("a2", 0, "PENDING"),
                _job("a3", 1, "HELD"), _job("a4", 1, "HELD")]
        _, rr, jr, _ = self._refresh(_staged_run(current_wave=0), jobs)
        jr.release_wave.assert_not_called()
        assert rr.set_counts.call_args[0][1] == "running"

    def test_last_wave_terminal(self):
        jobs = [_job("a1", 0, "SUCCEEDED"), _job("a2", 0, "SUCCEEDED"),
                _job("a3", 1, "SUCCEEDED"), _job("a4", 1, "SUCCEEDED")]
        agg, rr, jr, _ = self._refresh(_staged_run(current_wave=1), jobs)
        jr.release_wave.assert_not_called()
        assert agg["state"] == "succeeded"

    def test_paused_run_not_advanced(self):
        jobs = [_job("a1", 0, "SUCCEEDED"), _job("a2", 0, "SUCCEEDED"),
                _job("a3", 1, "HELD"), _job("a4", 1, "HELD")]
        _, rr, jr, _ = self._refresh(_staged_run(state="paused", current_wave=0), jobs)
        jr.release_wave.assert_not_called()
        assert rr.set_counts.call_args[0][1] == "paused"


# --- pause / resume / cancel ------------------------------------------------

def _ctl(handler, run, user=ADMIN, fleet=_FLEET, released=None):
    # run_control.* and runs.* import the repos separately, so patch both. Wave release
    # happens via _release_wave (in handlers.runs), hence the runs-side jobs repo (jr2).
    with patch("handlers.run_control._verify_tenant_token", return_value=user), \
         patch("handlers.run_control.runs_repo") as rr, \
         patch("handlers.run_control.jobs_repo") as jr, \
         patch("handlers.run_control.fleets_repo") as fr, \
         patch("handlers.run_control.audit"), \
         patch("handlers.runs.runs_repo") as rr2, \
         patch("handlers.runs.jobs_repo") as jr2, \
         patch("handlers.runs.agents_repo"):
        rr.get.return_value = run
        rr2.get.return_value = run
        fr.get.return_value = fleet
        jr.cancel_staged.return_value = 2
        jr2.list_by_run.return_value = []
        jr2.release_wave.return_value = released or []
        return handler(RUN, "tok"), rr, jr, jr2


class TestPause:
    def test_pauses_running_staged(self):
        r, rr, _, _ = _ctl(handle_pause_run, _staged_run(state="running"))
        assert r["statusCode"] == 200
        rr.set_state.assert_called_once_with(RUN, "paused")
        assert json.loads(r["body"])["state"] == "paused"

    def test_reject_non_staged(self):
        run = _staged_run(); run["wave_total"] = 1
        r, rr, _, _ = _ctl(handle_pause_run, run)
        assert r["statusCode"] == 409
        rr.set_state.assert_not_called()

    def test_reject_already_terminal(self):
        r, _, _, _ = _ctl(handle_pause_run, _staged_run(state="succeeded"))
        assert r["statusCode"] == 409

    def test_unknown_run_404(self):
        r, _, _, _ = _ctl(handle_pause_run, None)
        assert r["statusCode"] == 404

    def test_no_write_access_404(self):
        r, _, _, _ = _ctl(handle_pause_run, _staged_run(),
                       fleet={"fleet_id": FLEET_ID, "tenant_id": "other", "status": "ACTIVE"})
        assert r["statusCode"] == 404


class TestResume:
    def test_resume_releases_next_wave(self):
        run = _staged_run(state="paused", current_wave=0)
        released = [_job("a3", 1, "PENDING"), _job("a4", 1, "PENDING")]
        r, rr, jr, jr2 = _ctl(handle_resume_run, run, released=released)
        assert r["statusCode"] == 200
        jr2.release_wave.assert_called_once_with(RUN, 1)
        _, kwargs = rr.set_counts.call_args
        assert kwargs["current_wave"] == 1

    def test_reject_when_not_paused(self):
        r, _, _, jr2 = _ctl(handle_resume_run, _staged_run(state="running"))
        assert r["statusCode"] == 409
        jr2.release_wave.assert_not_called()


class TestCancel:
    def test_cancel_staged_jobs(self):
        r, rr, jr, _ = _ctl(handle_cancel_run, _staged_run(state="running"))
        assert r["statusCode"] == 200
        jr.cancel_staged.assert_called_once_with(RUN)
        rr.set_state.assert_called_once_with(RUN, "canceled")
        assert json.loads(r["body"])["canceled"] == 2

    def test_reject_already_done(self):
        r, _, jr, _ = _ctl(handle_cancel_run, _staged_run(state="canceled"))
        assert r["statusCode"] == 409
        jr.cancel_staged.assert_not_called()
