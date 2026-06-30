package main

import (
	"strings"
	"testing"
)

func resetMetrics() {
	metricJobsTotal.Store(0)
	metricJobsBlocked.Store(0)
	metricJobFailures.Store(0)
	metricJobDurationMs.Store(0)
	metricSyncs.Store(0)
	metricSyncErrors.Store(0)
	metricTokenRotations.Store(0)
	metricLastSyncUnix.Store(0)
}

func TestRecordJobCounters(t *testing.T) {
	resetMetrics()
	recordJob(CommandResult{ExitCode: 0, DurationMS: 100})                // success
	recordJob(CommandResult{ExitCode: 1, DurationMS: 50})                 // failure
	recordJob(CommandResult{Blocked: true, ExitCode: 126, DurationMS: 5}) // blocked

	if got := metricJobsTotal.Load(); got != 3 {
		t.Errorf("jobs_total = %d, want 3", got)
	}
	if got := metricJobFailures.Load(); got != 1 {
		t.Errorf("job_failures = %d, want 1", got)
	}
	if got := metricJobsBlocked.Load(); got != 1 {
		t.Errorf("jobs_blocked = %d, want 1", got)
	}
	if got := metricJobDurationMs.Load(); got != 155 {
		t.Errorf("duration_ms_sum = %d, want 155", got)
	}
}

func TestBlockedJobNotCountedAsFailure(t *testing.T) {
	resetMetrics()
	recordJob(CommandResult{Blocked: true, ExitCode: 126})
	if metricJobFailures.Load() != 0 {
		t.Errorf("a blocked job must not also count as a failure")
	}
}

func TestRenderMetricsExpositionFormat(t *testing.T) {
	resetMetrics()
	recordSyncOK()
	out := renderMetrics("k8s")
	for _, want := range []string{
		"# TYPE reach_agent_jobs_total counter",
		"reach_agent_jobs_total 0",
		"# TYPE reach_agent_up gauge",
		"reach_agent_up 1",
		`reach_agent_info{version="0.1.0",type="k8s"} 1`,
		"reach_agent_syncs_total 1",
		"# TYPE reach_agent_is_leader gauge",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("metrics output missing %q\n---\n%s", want, out)
		}
	}
}

func TestLeaderGaugeWhenNoElection(t *testing.T) {
	orig := leaderElector
	leaderElector = nil // no election -> this is the sole agent -> leader
	defer func() { leaderElector = orig }()
	if isLeaderGauge() != 1 {
		t.Errorf("is_leader should be 1 when leader election is not in use")
	}
}

func TestStartMetricsServerNoopWhenAddrBlank(t *testing.T) {
	// A blank addr (host installs, metrics disabled) must not start a listener.
	startMetricsServer(t.Context(), "", "host") // must simply return
}
