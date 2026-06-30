package main

import (
	"context"
	"fmt"
	"io"
	"log"
	"net/http"
	"strconv"
	"strings"
	"sync/atomic"
	"time"
)

// Prometheus metrics, exposed at /metrics only when REACH_METRICS_ADDR is set
// (the Helm chart sets it solely when metrics.enabled=true). Rendered by hand in
// the Prometheus text exposition format so the agent stays stdlib-only - no
// client library.
//
// NOTE: the agent is otherwise strictly outbound-only. This is the one opt-in
// inbound port; it serves read-only counters (no secrets) and must be locked
// down with a NetworkPolicy - see deploy/helm/reach-agent and SECURITY.md.
var (
	metricJobsTotal      atomic.Int64 // reach_agent_jobs_total
	metricJobsBlocked    atomic.Int64 // reach_agent_jobs_blocked_total
	metricJobFailures    atomic.Int64 // reach_agent_job_failures_total (exit != 0, not blocked)
	metricJobDurationMs  atomic.Int64 // reach_agent_job_duration_ms_sum
	metricSyncs          atomic.Int64 // reach_agent_syncs_total
	metricSyncErrors     atomic.Int64 // reach_agent_sync_errors_total
	metricTokenRotations atomic.Int64 // reach_agent_token_rotations_total
	metricLastSyncUnix   atomic.Int64 // reach_agent_last_successful_sync_timestamp_seconds
	metricStartUnix      atomic.Int64 // reach_agent_start_timestamp_seconds
)

func recordJob(res CommandResult) {
	metricJobsTotal.Add(1)
	metricJobDurationMs.Add(res.DurationMS)
	if res.Blocked {
		metricJobsBlocked.Add(1)
	} else if res.ExitCode != 0 {
		metricJobFailures.Add(1)
	}
}

func recordSyncOK() {
	metricSyncs.Add(1)
	metricLastSyncUnix.Store(time.Now().Unix())
}

func recordSyncError()     { metricSyncErrors.Add(1) }
func recordTokenRotation() { metricTokenRotations.Add(1) }

// isLeaderGauge is 1 when this instance is the active leader, or when leader
// election is not in use (it is then the sole agent).
func isLeaderGauge() int64 {
	if leaderElector == nil || leaderElector.IsLeader() {
		return 1
	}
	return 0
}

// renderMetrics returns the full /metrics body in Prometheus text format.
func renderMetrics(agentType string) string {
	var b strings.Builder
	emit := func(name, help, typ, labels, value string) {
		fmt.Fprintf(&b, "# HELP %s %s\n# TYPE %s %s\n%s%s %s\n", name, help, name, typ, name, labels, value)
	}
	gauge := func(name, help string, v int64) { emit(name, help, "gauge", "", strconv.FormatInt(v, 10)) }
	counter := func(name, help string, v int64) { emit(name, help, "counter", "", strconv.FormatInt(v, 10)) }

	emit("reach_agent_info", "Agent build and mode info (value is always 1).", "gauge",
		fmt.Sprintf("{version=%q,type=%q}", agentVersion, agentType), "1")
	gauge("reach_agent_up", "1 if the agent process is up.", 1)
	gauge("reach_agent_is_leader", "1 if this instance is the active leader (or the sole agent).", isLeaderGauge())
	gauge("reach_agent_start_timestamp_seconds", "Unix time the agent process started.", metricStartUnix.Load())
	gauge("reach_agent_last_successful_sync_timestamp_seconds", "Unix time of the last successful sync.", metricLastSyncUnix.Load())

	counter("reach_agent_jobs_total", "Jobs executed by this agent.", metricJobsTotal.Load())
	counter("reach_agent_jobs_blocked_total", "Jobs blocked by policy or the k8s allowlist.", metricJobsBlocked.Load())
	counter("reach_agent_job_failures_total", "Jobs that exited non-zero (excludes blocked jobs).", metricJobFailures.Load())
	counter("reach_agent_job_duration_ms_sum", "Cumulative job execution time in milliseconds.", metricJobDurationMs.Load())
	counter("reach_agent_syncs_total", "Successful syncs (heartbeats) with the backend.", metricSyncs.Load())
	counter("reach_agent_sync_errors_total", "Failed sync attempts.", metricSyncErrors.Load())
	counter("reach_agent_token_rotations_total", "Agent token rotations performed.", metricTokenRotations.Load())

	return b.String()
}

// startMetricsServer starts the /metrics HTTP server when addr is non-empty and
// returns immediately; the server is shut down when ctx is cancelled. A blank
// addr (the default, and always the case for host installs) starts nothing.
func startMetricsServer(ctx context.Context, addr, agentType string) {
	addr = strings.TrimSpace(addr)
	if addr == "" {
		return
	}
	metricStartUnix.Store(time.Now().Unix())

	mux := http.NewServeMux()
	mux.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
		io.WriteString(w, renderMetrics(agentType))
	})
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, "reach-agent: metrics at /metrics\n")
	})

	srv := &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		<-ctx.Done()
		shctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = srv.Shutdown(shctx)
	}()
	go func() {
		log.Printf("Metrics server listening on %s (/metrics)", addr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Printf("Metrics server error: %v", err)
		}
	}()
}
