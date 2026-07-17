package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	stdsync "sync"
	"syscall"
	"time"
)

// ErrUnauthorized is returned when the server rejects the agent's token -
// retrying won't fix this, the agent needs a new token.
var ErrUnauthorized = errors.New("unauthorized")

// ErrTokenExpired is returned when the server rejects the token due to age.
// The agent should rotate and retry rather than going dormant.
var ErrTokenExpired = errors.New("token expired")

// ErrPermanent marks failures where retrying - even via a process
// restart - will never succeed without the user fixing config on disk.
var ErrPermanent = errors.New("permanent failure")

const (
	machineIDPath     = "/etc/machine-id"
	agentVersion      = "0.1.0"
	idlePollSeconds   = 15
	maxOutputBytes    = 50_000
	tokenRotationDays = 30
	// Default cadence for re-reviewing cluster-wide RBAC. RBAC changes rarely, so
	// this is infrequent - it bounds the API cost of reviewing every namespace
	// while still surfacing a sneaked-in grant within minutes (as drift).
	// Override with REACH_K8S_REVIEW_INTERVAL_SECONDS.
	defaultPermReviewInterval = 5 * time.Minute
)

// minPermReviewInterval floors the configured cadence: the review is cluster-wide
// (cost scales with namespace count), so we never re-review more often than this
// even if misconfigured. RBAC is config drift, so 30s detection latency is ample.
const minPermReviewInterval = 30 * time.Second

// permReviewInterval is how often the agent re-reviews its cluster-wide RBAC,
// from REACH_K8S_REVIEW_INTERVAL_SECONDS (floored at minPermReviewInterval),
// defaulting to defaultPermReviewInterval.
func permReviewInterval() time.Duration {
	if v := os.Getenv("REACH_K8S_REVIEW_INTERVAL_SECONDS"); v != "" {
		var secs int
		if _, err := fmt.Sscanf(v, "%d", &secs); err == nil && secs > 0 {
			if d := time.Duration(secs) * time.Second; d >= minPermReviewInterval {
				return d
			}
			return minPermReviewInterval
		}
	}
	return defaultPermReviewInterval
}

// configPath and installIDPath are overridable via REACH_CONFIG_PATH for local dev.
var (
	configPath    = "/etc/reach-agent/config.json"
	installIDPath = "/etc/reach-agent/install_id"
)

func init() {
	if v := os.Getenv("REACH_CONFIG_PATH"); v != "" {
		configPath = v
		installIDPath = filepath.Join(filepath.Dir(v), "install_id")
	}
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

type Config struct {
	// Credential-only: the agent has no agent_id. The install token identifies it
	// at claim, the agent token on every call after; the backend keys on those.
	APIURL             string `json:"api_url"`
	AgentToken         string `json:"agent_token,omitempty"`
	InstallToken       string `json:"install_token,omitempty"`
	MachineFingerprint string `json:"machine_fingerprint,omitempty"`
	TokenIssuedAt      string `json:"token_issued_at,omitempty"`
	// Type is "k8s" in a Kubernetes cluster, otherwise "host". The cluster id is
	// used only to derive the fingerprint; it is not sent or stored separately.
	Type string `json:"type,omitempty"`
}

func loadConfig() (*Config, error) {
	data, err := os.ReadFile(configPath)
	if err != nil {
		return nil, fmt.Errorf("read config: %w", err)
	}
	var cfg Config
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parse config: %w", err)
	}
	if cfg.APIURL == "" {
		return nil, fmt.Errorf("config missing api_url")
	}
	return &cfg, nil
}

func saveConfig(cfg *Config) error {
	// In Kubernetes the managed Secret is the sole store: the token (the only
	// stateful field) lives there, shared across replicas and restarts, and
	// everything else is re-derived from env/cluster each start. We deliberately
	// do not write to local disk - the rootfs is read-only and the token must
	// not be cached on the node.
	if k8sTokenStore != nil {
		if cfg.AgentToken == "" {
			return nil
		}
		if err := k8sTokenStore.save(cfg.AgentToken, cfg.TokenIssuedAt); err != nil {
			return fmt.Errorf("write token secret %s: %w", k8sTokenStore.name, err)
		}
		return nil
	}
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(configPath, data, 0600); err != nil {
		return fmt.Errorf("write config: %w", err)
	}
	return nil
}

// touchHealthFile updates the liveness freshness file's mtime. A no-op when path
// is empty (host installs) or on write error (best-effort; the probe will catch a
// genuinely wedged loop regardless).
func touchHealthFile(path string) {
	if path == "" {
		return
	}
	_ = os.WriteFile(path, []byte(time.Now().UTC().Format(time.RFC3339)), 0600)
}

// healthFilePath returns the liveness health-file path, but only in Kubernetes
// mode. The file backs the pod liveness probe (the chart sets REACH_HEALTH_FILE);
// it has no meaning on host installs, so it is ignored there even if the env var
// is somehow set. An empty result makes touchHealthFile a no-op.
func healthFilePath() string {
	if !inKubernetes() {
		return ""
	}
	return os.Getenv("REACH_HEALTH_FILE")
}

// ---------------------------------------------------------------------------
// Machine fingerprint
// ---------------------------------------------------------------------------

func ensureInstallID() (string, error) {
	data, err := os.ReadFile(installIDPath)
	if err == nil {
		return strings.TrimSpace(string(data)), nil
	}
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	id := hex.EncodeToString(b)
	if err := os.WriteFile(installIDPath, []byte(id), 0600); err != nil {
		return "", fmt.Errorf("write install_id: %w", err)
	}
	return id, nil
}

func machineFingerprint() (string, error) {
	machineID := ""
	if data, err := os.ReadFile(machineIDPath); err == nil {
		machineID = strings.TrimSpace(string(data))
	}
	installID, err := ensureInstallID()
	if err != nil {
		return "", err
	}
	h := sha256.Sum256([]byte(machineID + ":" + installID))
	return "fp_" + hex.EncodeToString(h[:])[:32], nil
}

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------

var httpClient = &http.Client{Timeout: 30 * time.Second}

func apiPost(apiURL, path, token string, payload, result any) (int, error) {
	body, _ := json.Marshal(payload)
	req, err := http.NewRequest("POST", apiURL+path, bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	if result != nil {
		_ = json.Unmarshal(respBody, result)
	}
	return resp.StatusCode, nil
}

// ---------------------------------------------------------------------------
// Claim
// ---------------------------------------------------------------------------

type ClaimRequest struct {
	InstallToken       string `json:"install_token"`
	MachineFingerprint string `json:"machine_fingerprint"`
	Hostname           string `json:"hostname"`
	AgentVersion       string `json:"agent_version"`
	Type               string `json:"type,omitempty"`
}

type ClaimResponse struct {
	AgentToken string `json:"agent_token"`
	Mode       string `json:"mode"`
	Error      string `json:"error"`
}

func claim(cfg *Config, fp string) error {
	hostname, _ := os.Hostname()
	payload := ClaimRequest{
		InstallToken:       cfg.InstallToken,
		MachineFingerprint: fp,
		Hostname:           hostname,
		AgentVersion:       agentVersion,
		Type:               cfg.Type,
	}
	var result ClaimResponse
	status, err := apiPost(cfg.APIURL, "/agent/claim", "", payload, &result)
	if err != nil {
		return fmt.Errorf("claim request: %w", err)
	}
	if status >= 400 && status < 500 {
		return fmt.Errorf("claim failed (%d): %s: %w", status, result.Error, ErrPermanent)
	}
	if status != 200 {
		return fmt.Errorf("claim failed (%d): %s", status, result.Error)
	}
	cfg.AgentToken = result.AgentToken
	cfg.MachineFingerprint = fp
	cfg.TokenIssuedAt = time.Now().UTC().Format(time.RFC3339)
	cfg.InstallToken = ""
	if err := saveConfig(cfg); err != nil {
		return fmt.Errorf("save config after claim: %w", err)
	}
	log.Printf("Agent claimed successfully, mode=%s", result.Mode)
	return nil
}

// ---------------------------------------------------------------------------
// Capability probes
// ---------------------------------------------------------------------------

// probeDocker reports whether a Docker daemon socket is accessible.
// Checks /var/run/docker.sock first (Linux + older Docker Desktop for Mac),
// then $HOME/.docker/run/docker.sock (Docker Desktop for Mac 4.13+).
func probeDocker() bool {
	paths := []string{"/var/run/docker.sock"}
	if home, err := os.UserHomeDir(); err == nil {
		paths = append(paths, filepath.Join(home, ".docker", "run", "docker.sock"))
	}
	for _, p := range paths {
		if _, err := os.Stat(p); err == nil {
			return true
		}
	}
	return false
}

// probeServiceMgmt reports whether a service manager is available on this host.
// Detects systemctl (Linux/systemd) or launchctl (macOS/launchd).
func probeServiceMgmt() bool {
	for _, cmd := range []string{"systemctl", "launchctl"} {
		if _, err := exec.LookPath(cmd); err == nil {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------------------
// Sync
// ---------------------------------------------------------------------------

type SyncRequest struct {
	MachineFingerprint  string `json:"machine_fingerprint"`
	AgentVersion        string `json:"agent_version"`
	RunningAsRoot       bool   `json:"running_as_root"`
	DockerDetected      bool   `json:"docker_detected"`
	ServiceMgmtDetected bool   `json:"service_mgmt_detected"`
	Type                string `json:"type,omitempty"`
	// In k8s mode the agent reports its effective RBAC, but only on the first
	// sync and whenever it changes - omitted otherwise to keep the heartbeat
	// small. See k8sPermsProvider / lastSentPermHash.
	K8sPermissions *K8sPermissions `json:"k8s_permissions,omitempty"`
	// In k8s mode the agent reports its effective execution allowlist (kubectl +
	// filters + any extras) so the console can warn/block approving a command whose
	// binary the agent won't run. Omitted for host agents.
	K8sAllowedBinaries []string `json:"k8s_allowed_binaries,omitempty"`
}

// k8sPermsProvider returns the agent's current effective permissions (set in
// k8s mode); nil otherwise. lastSentPermHash is the hash last accepted by the
// backend, so the full rule set is sent only when it actually changes.
var (
	k8sPermsProvider func() (*K8sPermissions, error)
	lastSentPermHash string
)

// HostRule is a structured host approval: a bin plus positional args where an arg may be
// "*" (wildcard). Mirrors the server's host_rule. Arity is fixed.
type HostRule struct {
	Bin  string   `json:"bin"`
	Args []string `json:"args"`
}

type Job struct {
	JobID   string `json:"job_id"`
	Command string `json:"command"`
	Mode    string `json:"mode"`
	IsWrite bool   `json:"is_write"`
	// Structured exec: when set, run this argv with execve (no shell). ApprovedHostRules
	// are the {bin,args} rules matched for the approved-mode bypass.
	Argv              []string   `json:"argv"`
	ApprovedHostRules []HostRule `json:"approved_host_rules"`
}

type SyncResponse struct {
	Jobs            []Job  `json:"jobs"`
	NextPollSeconds int    `json:"next_poll_seconds"`
	RotateToken     bool   `json:"rotate_token"`
	Error           string `json:"error"`
}

func sync(cfg *Config) (*SyncResponse, error) {
	payload := SyncRequest{
		MachineFingerprint:  cfg.MachineFingerprint,
		AgentVersion:        agentVersion,
		RunningAsRoot:       os.Getuid() == 0,
		DockerDetected:      probeDocker(),
		ServiceMgmtDetected: probeServiceMgmt(),
		Type:                cfg.Type,
	}
	// In k8s mode, attach the effective RBAC only when it changed since the last
	// accepted sync (first send, or after an RBAC change). Self-review failures
	// are non-fatal - we just skip reporting this round.
	if k8sPermsProvider != nil {
		if perms, perr := k8sPermsProvider(); perr != nil {
			log.Printf("WARNING: effective-permissions review failed: %v", perr)
		} else if perms != nil && perms.Hash != lastSentPermHash {
			payload.K8sPermissions = perms
		}
	}
	// In k8s mode, report the effective execution allowlist (kubectl + filters + any
	// REACH_K8S_EXTRA_BINARIES / REACH_K8S_ALLOWED_BINARIES) so the console can warn
	// against approving a command whose binary the agent won't run.
	if k8sAllowedBinaries != nil {
		payload.K8sAllowedBinaries = k8sAllowedBinaries
	}
	var result SyncResponse
	status, err := apiPost(cfg.APIURL, "/agent/sync", cfg.AgentToken, payload, &result)
	if err != nil {
		return nil, fmt.Errorf("sync request: %w", err)
	}
	if status == 401 {
		return nil, fmt.Errorf("sync failed (401): %s: %w: %w", result.Error, ErrUnauthorized, ErrPermanent)
	}
	if status == 403 {
		if result.Error == "token_expired" {
			return nil, fmt.Errorf("sync failed: %w", ErrTokenExpired)
		}
		// "agent not active" or "fingerprint mismatch" - both require
		// re-claiming with a fresh install token, retrying won't help.
		return nil, fmt.Errorf("sync failed (403): %s: %w", result.Error, ErrPermanent)
	}
	if status != 200 {
		return nil, fmt.Errorf("sync failed (%d): %s", status, result.Error)
	}
	// The backend accepted this sync; remember the permissions hash we sent so we
	// don't resend the full rule set until it changes again.
	if payload.K8sPermissions != nil {
		lastSentPermHash = payload.K8sPermissions.Hash
	}
	return &result, nil
}

// ---------------------------------------------------------------------------
// Rotate token
// ---------------------------------------------------------------------------

type RotateTokenRequest struct {
	MachineFingerprint string `json:"machine_fingerprint"`
}

type RotateTokenResponse struct {
	AgentToken string `json:"agent_token"`
	Error      string `json:"error"`
}

func rotateToken(cfg *Config) error {
	payload := RotateTokenRequest{
		MachineFingerprint: cfg.MachineFingerprint,
	}
	var result RotateTokenResponse
	status, err := apiPost(cfg.APIURL, "/agent/rotate-token", cfg.AgentToken, payload, &result)
	if err != nil {
		return fmt.Errorf("rotate token request: %w", err)
	}
	if status == 401 {
		return fmt.Errorf("rotate token failed (401): %s: %w: %w", result.Error, ErrUnauthorized, ErrPermanent)
	}
	if status != 200 {
		return fmt.Errorf("rotate token failed (%d): %s", status, result.Error)
	}
	cfg.AgentToken = result.AgentToken
	cfg.TokenIssuedAt = time.Now().UTC().Format(time.RFC3339)
	recordTokenRotation()
	if err := saveConfig(cfg); err != nil {
		// Token rotated on server but config write failed. New token is in memory
		// for this session but will be lost on restart - agent will need manual reclaim.
		log.Printf("WARNING: token rotated but config save failed: %v - manual reclaim required on restart", err)
		return nil
	}
	log.Printf("Agent token rotated successfully")
	return nil
}

// ---------------------------------------------------------------------------
// Deregister (fleet scale-in)
// ---------------------------------------------------------------------------

type DeregisterRequest struct {
	MachineFingerprint string `json:"machine_fingerprint"`
}

type DeregisterResponse struct {
	Deregistered bool   `json:"deregistered"`
	Error        string `json:"error"`
}

// systemIsShuttingDown reports whether the whole OS is going down (reboot,
// poweroff, or an ASG instance terminating) as opposed to just this service being
// restarted. Both deliver SIGTERM, but systemd's manager state is "stopping" only
// during a real shutdown - a plain `systemctl restart reach-agent` leaves it
// "running". Anything we can't positively confirm as "stopping" is treated as a
// restart, so a normal restart never deregisters the fleet member. The reaper is
// the backstop if a genuine shutdown is somehow misread here.
func systemIsShuttingDown() bool {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	// `systemctl is-system-running` prints the state to stdout and exits non-zero
	// for any state other than "running" (including "stopping"), so read stdout
	// regardless of the exit status; only give up if we got nothing back.
	out, err := exec.CommandContext(ctx, "systemctl", "is-system-running").Output()
	if err != nil && len(out) == 0 {
		return false
	}
	return strings.TrimSpace(string(out)) == "stopping"
}

// deregister removes this host from its fleet immediately (best-effort), instead
// of waiting for the server-side reaper. The backend ignores anything that isn't a
// host fleet member, so a non-fleet host calling this is a harmless no-op (409).
func deregister(cfg *Config) {
	payload := DeregisterRequest{MachineFingerprint: cfg.MachineFingerprint}
	var result DeregisterResponse
	status, err := apiPost(cfg.APIURL, "/agent/deregister", cfg.AgentToken, payload, &result)
	if err != nil {
		log.Printf("Deregister request failed: %v", err)
		return
	}
	switch {
	case status == 200 && result.Deregistered:
		log.Printf("Deregistered from fleet on shutdown")
	case status == 409:
		log.Printf("Deregister skipped (not a fleet member): %s", result.Error)
	default:
		log.Printf("Deregister returned %d: %s", status, result.Error)
	}
}

// ---------------------------------------------------------------------------
// Execute command
// ---------------------------------------------------------------------------

type CommandResult struct {
	Stdout          string
	Stderr          string
	ExitCode        int
	DurationMS      int64
	Blocked         bool
	StdoutTruncated bool
	StderrTruncated bool
}

func commandTimeout() time.Duration {
	if v := os.Getenv("REACH_COMMAND_TIMEOUT_SECONDS"); v != "" {
		var secs int
		if _, err := fmt.Sscanf(v, "%d", &secs); err == nil && secs > 0 {
			return time.Duration(secs) * time.Second
		}
	}
	return 60 * time.Second
}

func maxOutputSize() int {
	if v := os.Getenv("REACH_MAX_OUTPUT_BYTES"); v != "" {
		var n int
		if _, err := fmt.Sscanf(v, "%d", &n); err == nil && n > 0 {
			return n
		}
	}
	return maxOutputBytes
}

func truncate(s string, maxBytes int) string {
	b := []byte(s)
	if len(b) <= maxBytes {
		return s
	}
	return string(b[:maxBytes]) + "\n[TRUNCATED]"
}


// cappedBuffer is an io.Writer that keeps at most `limit` bytes and records whether any
// beyond that were dropped. It bounds agent memory even when a command produces unbounded
// output (`yes`, `find /`, an unfiltered `journalctl`/`docker logs`): excess bytes are
// counted-as-truncated and discarded instead of buffered. Write never errors or
// short-writes, so the child never blocks on a full pipe - it runs to completion (or the
// command timeout) while we retain only a bounded slice. It is mutex-guarded so a single
// buffer can safely take concurrent writers (the k8s pipeline shares one stderr buffer
// across stages).
type cappedBuffer struct {
	limit     int
	mu        stdsync.Mutex
	buf       bytes.Buffer
	truncated bool
}

func (c *cappedBuffer) Write(p []byte) (int, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if remaining := c.limit - c.buf.Len(); remaining > 0 {
		if len(p) <= remaining {
			c.buf.Write(p)
		} else {
			c.buf.Write(p[:remaining])
			c.truncated = true
		}
	} else if len(p) > 0 {
		c.truncated = true
	}
	return len(p), nil
}

// String returns the captured output, appending the [TRUNCATED] marker when bytes were
// dropped (mirrors truncate() so the inline signal is identical either way).
func (c *cappedBuffer) String() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.truncated {
		return c.buf.String() + "\n[TRUNCATED]"
	}
	return c.buf.String()
}

// Truncated reports whether any bytes were dropped (mutex-guarded for concurrent writers).
func (c *cappedBuffer) Truncated() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.truncated
}

func runCmd(ctx context.Context, args []string) (stdout, stderr string, exitCode int, stdoutTrunc, stderrTrunc bool) {
	cmd := exec.CommandContext(ctx, args[0], args[1:]...)
	cmd.Dir = "/"
	limit := maxOutputSize()
	outBuf := &cappedBuffer{limit: limit}
	errBuf := &cappedBuffer{limit: limit}
	cmd.Stdout = outBuf
	cmd.Stderr = errBuf
	exitCode = 0
	if err := cmd.Run(); err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
		} else if ctx.Err() == context.DeadlineExceeded {
			exitCode = 124
		} else {
			exitCode = 1
		}
	}
	return outBuf.String(), errBuf.String(), exitCode, outBuf.Truncated(), errBuf.Truncated()
}

// executeCommand runs a command freeform under the shell. It is used for reads (any mode)
// and wild-mode commands; approved-mode *writes* are structured and run via executeStructured
// (matched against host rules), or rejected at submission if they need a shell - so a write
// never rides an approval past the sandbox here. Reads/unapproved writes run under Landlock on
// Linux (reads pass, writes are kernel-blocked); on macOS an approved-mode write is refused.
func executeCommand(command, mode string, isWrite bool) CommandResult {
	start := time.Now()
	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout())
	defer cancel()

	useSandbox := (mode == "readonly" || mode == "approved") && runtime.GOOS == "linux"

	// On non-Linux (macOS): no Landlock available. Block writes in approved mode using the
	// is_write flag annotated by the server (approved-mode writes run structured, not here).
	if mode == "approved" && runtime.GOOS != "linux" && isWrite {
		richErr := fmt.Sprintf(
			"Blocked: approval required\n\nCommand:\n  %s\n\nContact your admin to approve this command for this agent.\n",
			strings.TrimSpace(command),
		)
		return CommandResult{
			Stdout:     "",
			Stderr:     truncate(richErr, maxOutputSize()),
			ExitCode:   126,
			DurationMS: time.Since(start).Milliseconds(),
			Blocked:    true,
		}
	}

	var args []string
	if useSandbox {
		args = []string{"/proc/self/exe", "--sandbox", "/bin/bash", "-lc", command}
	} else {
		args = []string{"/bin/bash", "-lc", command}
	}

	stdout, stderr, exitCode, stdoutTrunc, stderrTrunc := runCmd(ctx, args)

	// If Landlock blocked the command (permission denied), the command tried to
	// write but was not in the approved list. Return a structured error.
	// Guard on isWrite to avoid false positives: read commands that fail due to
	// OS file permissions (e.g. cat /etc/shadow) also produce "permission denied"
	// but should surface as a normal failure, not a write-approval block.
	if mode == "approved" && useSandbox && isWrite && exitCode != 0 {
		stderrLower := strings.ToLower(stderr)
		if strings.Contains(stderrLower, "permission denied") || strings.Contains(stderrLower, "operation not permitted") {
			richErr := fmt.Sprintf(
				"Blocked: approval required\n\nCommand:\n  %s\n\nContact your admin to approve this command for this agent.\n",
				strings.TrimSpace(command),
			)
			limit := maxOutputSize()
			return CommandResult{
				Stdout:     "",
				Stderr:     truncate(richErr, limit),
				ExitCode:   126,
				DurationMS: time.Since(start).Milliseconds(),
				Blocked:    true,
			}
		}
	}

	// runCmd already bounded stdout/stderr to maxOutputSize() and set the truncation
	// flags, so there's no second truncate() here (that would double-cut the marker).
	return CommandResult{
		Stdout:          stdout,
		Stderr:          stderr,
		ExitCode:        exitCode,
		DurationMS:      time.Since(start).Milliseconds(),
		StdoutTruncated: stdoutTrunc,
		StderrTruncated: stderrTrunc,
	}
}

// hostRuleMatches mirrors the server's host_rule_matches: bin equal, each positional arg
// equal or wildcarded with "*". Arity is fixed unless the rule's last arg is "..." (the
// trailing variadic wildcard), in which case the leading args must match and any number
// of remaining call args (including none) are permitted.
func hostRuleMatches(argv []string, rule HostRule) bool {
	if len(argv) == 0 || argv[0] != rule.Bin {
		return false
	}
	callArgs := argv[1:]
	if n := len(rule.Args); n > 0 && rule.Args[n-1] == "..." {
		prefix := rule.Args[:n-1]
		if len(callArgs) < len(prefix) {
			return false
		}
		for i, ra := range prefix {
			if ra != "*" && ra != callArgs[i] {
				return false
			}
		}
		return true
	}
	if len(callArgs) != len(rule.Args) {
		return false
	}
	for i, ra := range rule.Args {
		if ra != "*" && ra != callArgs[i] {
			return false
		}
	}
	return true
}

func isHostArgvApproved(argv []string, rules []HostRule) bool {
	for _, r := range rules {
		if hostRuleMatches(argv, r) {
			return true
		}
	}
	return false
}

func blockedResult(display string, start time.Time) CommandResult {
	richErr := fmt.Sprintf(
		"Blocked: approval required\n\nCommand:\n  %s\n\nContact your admin to approve this command for this agent.\n",
		strings.TrimSpace(display),
	)
	return CommandResult{
		Stdout:     "",
		Stderr:     truncate(richErr, maxOutputSize()),
		ExitCode:   126,
		DurationMS: time.Since(start).Milliseconds(),
		Blocked:    true,
	}
}

// executeStructured runs a structured argv with execve (no shell). It mirrors
// executeCommand's mode logic but matches approved *rules* instead of command strings:
//   - approved write matched by a host rule -> run directly (bypass), writes permitted.
//   - otherwise readonly/approved on Linux -> run under Landlock (reads pass, writes blocked).
//   - approved-mode unapproved write on non-Linux (no Landlock) -> blocked up front.
func executeStructured(argv []string, mode string, isWrite bool, rules []HostRule) CommandResult {
	start := time.Now()
	display := strings.Join(argv, " ")
	if len(argv) == 0 {
		return CommandResult{Stderr: "empty argv", ExitCode: 2, DurationMS: 0}
	}
	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout())
	defer cancel()

	// Approved only by a structured host rule (JSON) - no command-string matching.
	approvedWrite := mode == "approved" && isHostArgvApproved(argv, rules)
	useSandbox := (mode == "readonly" || mode == "approved") && runtime.GOOS == "linux" && !approvedWrite

	// No Landlock (macOS): block an unapproved write in approved mode up front.
	if mode == "approved" && runtime.GOOS != "linux" && isWrite && !approvedWrite {
		return blockedResult(display, start)
	}

	// Resolve the bin to an absolute path: the sandbox re-exec (syscall.Exec) needs one,
	// and it makes the bypass explicit about which binary runs.
	binPath := argv[0]
	if p, err := exec.LookPath(argv[0]); err == nil {
		binPath = p
	}
	resolved := append([]string{binPath}, argv[1:]...)

	var args []string
	if useSandbox {
		args = append([]string{"/proc/self/exe", "--sandbox"}, resolved...)
	} else {
		args = resolved
	}
	stdout, stderr, exitCode, stdoutTrunc, stderrTrunc := runCmd(ctx, args)

	// Landlock kernel-blocked an unapproved structured write -> structured approval error.
	if mode == "approved" && useSandbox && isWrite && exitCode != 0 {
		low := strings.ToLower(stderr)
		if strings.Contains(low, "permission denied") || strings.Contains(low, "operation not permitted") {
			return blockedResult(display, start)
		}
	}

	// stdout/stderr are already bounded by runCmd - see executeCommand.
	return CommandResult{
		Stdout:          stdout,
		Stderr:          stderr,
		ExitCode:        exitCode,
		DurationMS:      time.Since(start).Milliseconds(),
		StdoutTruncated: stdoutTrunc,
		StderrTruncated: stderrTrunc,
	}
}

// ---------------------------------------------------------------------------
// Post result
// ---------------------------------------------------------------------------

type ResultRequest struct {
	MachineFingerprint string `json:"machine_fingerprint"`
	Status             string `json:"status"`
	ExitCode           int    `json:"exit_code"`
	Stdout             string `json:"stdout"`
	Stderr             string `json:"stderr"`
	DurationMS         int64  `json:"duration_ms"`
	Blocked            bool   `json:"blocked,omitempty"`
	IsWrite            bool   `json:"is_write,omitempty"`
	StdoutTruncated    bool   `json:"stdout_truncated,omitempty"`
	StderrTruncated    bool   `json:"stderr_truncated,omitempty"`
}

func postResult(cfg *Config, jobID string, res CommandResult) error {
	status := "SUCCEEDED"
	if res.ExitCode != 0 {
		status = "FAILED"
	}
	payload := ResultRequest{
		MachineFingerprint: cfg.MachineFingerprint,
		Status:             status,
		ExitCode:           res.ExitCode,
		Stdout:             res.Stdout,
		Stderr:             res.Stderr,
		DurationMS:         res.DurationMS,
		Blocked:            res.Blocked,
		IsWrite:            res.Blocked, // if blocked, the command was a write by definition
		StdoutTruncated:    res.StdoutTruncated,
		StderrTruncated:    res.StderrTruncated,
	}
	var result map[string]any
	statusCode, err := apiPost(cfg.APIURL, "/agent/jobs/"+jobID+"/result", cfg.AgentToken, payload, &result)
	if err != nil {
		return fmt.Errorf("post result: %w", err)
	}
	if statusCode != 200 {
		return fmt.Errorf("post result failed (%d): %v", statusCode, result["error"])
	}
	return nil
}

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------

func sleep(ctx context.Context, d time.Duration) bool {
	select {
	case <-ctx.Done():
		return false
	case <-time.After(d):
		return true
	}
}

func run(ctx context.Context) error {
	var cfg *Config
	var fp string

	if inKubernetes() {
		// Kubernetes mode: identity is derived from the cluster (kube-system UID)
		// so every replica computes the same fingerprint; bootstrap comes from env.
		kc, err := newK8sClient()
		if err != nil {
			return fmt.Errorf("kubernetes client: %w", err)
		}
		clusterID, err := kc.clusterID()
		if err != nil {
			return err
		}
		cfg, err = k8sConfig()
		if err != nil {
			return err
		}
		cfg.Type = "k8s"
		fp = clusterFingerprint(clusterID)
		log.Printf("Kubernetes mode: cluster %s", clusterID)

		// Report effective RBAC via self-review (no extra grant needed for the
		// review itself). The agent discovers every namespace itself and reviews
		// each, so it reports its full cluster-wide access automatically - including
		// grants bound in namespaces nobody told it about. If listing namespaces is
		// not permitted, it falls back to its own namespace (which still captures
		// cluster-wide bindings). Throttled so a busy poll loop doesn't re-review
		// constantly; the backend only stores/flags drift when the rule set changes.
		ownNS := podNamespace()
		reviewEvery := permReviewInterval()
		log.Printf("Kubernetes mode: reviewing cluster-wide RBAC every %s", reviewEvery)
		var permCache *K8sPermissions
		var permCacheAt time.Time
		k8sPermsProvider = func() (*K8sPermissions, error) {
			if permCache != nil && time.Since(permCacheAt) < reviewEvery {
				return permCache, nil
			}
			namespaces, err := kc.listNamespaceNames()
			if err != nil || len(namespaces) == 0 {
				log.Printf("WARNING: listing namespaces failed (%v); reviewing own namespace only", err)
				namespaces = []string{ownNS}
			}
			perms, perr := kc.selfSubjectRulesMulti(namespaces)
			if perr != nil {
				return nil, perr
			}
			permCache, permCacheAt = perms, time.Now()
			return perms, nil
		}

		// Gate job execution: kubectl + safe filters, no shell (see k8s_exec.go).
		k8sAllowedBinaries = k8sBinaryAllowlist()
		log.Printf("Kubernetes mode: jobs limited to [%s], no shell", strings.Join(k8sAllowedBinaries, " "))

		// The shared Secret is the authoritative token store across replicas and
		// restarts. Read it first so a fresh replica reuses the claimed token
		// instead of re-claiming (which the backend rejects once ACTIVE).
		if name := strings.TrimSpace(os.Getenv("REACH_K8S_TOKEN_SECRET")); name != "" {
			k8sTokenStore = &secretTokenStore{client: kc, namespace: podNamespace(), name: name}
			if tok, issued, lerr := k8sTokenStore.load(); lerr != nil {
				log.Printf("WARNING: reading token secret %s: %v", name, lerr)
			} else if tok != "" {
				cfg.AgentToken = tok
				cfg.TokenIssuedAt = issued
				log.Printf("Reusing shared agent token from secret %s", name)
			}
		}

		// Leader election: with multiple replicas only the lease holder claims,
		// heartbeats, and runs jobs; the rest stand by and fail over.
		if lease := strings.TrimSpace(os.Getenv("REACH_K8S_LEASE")); lease != "" {
			pod := strings.TrimSpace(os.Getenv("REACH_POD_NAME"))
			if pod == "" {
				pod = fp // fall back to the cluster fingerprint if POD_NAME is unset
			}
			leaderElector = &leaseElector{client: kc, namespace: podNamespace(), name: lease, identity: pod}
			go leaderElector.Run(ctx)
			log.Printf("Leader election enabled (lease %s, identity %s)", lease, pod)
		}
	} else {
		var err error
		cfg, err = loadConfig()
		if err != nil {
			return err
		}
		fp, err = machineFingerprint()
		if err != nil {
			return fmt.Errorf("fingerprint: %w", err)
		}
		cfg.Type = "host"
	}
	cfg.MachineFingerprint = fp

	// Non-leader-elected agents (every non-k8s agent) claim up front. Under
	// leader election the claim is deferred into the loop and gated on the lease,
	// so only the leader claims and followers reuse the shared token.
	if cfg.AgentToken == "" && leaderElector == nil {
		if cfg.InstallToken == "" {
			return fmt.Errorf("no agent_token and no install_token in config")
		}
		log.Println("No agent_token found, claiming...")
		if err := claim(cfg, fp); err != nil {
			return err
		}
	}

	log.Printf("Agent starting poll loop (type=%s)", cfg.Type)
	pollSeconds := idlePollSeconds
	syncFailed := false
	// Liveness: touch a freshness file each loop iteration (leader and follower
	// alike). A liveness probe restarts the pod if the loop wedges. Kubernetes
	// only - a no-op on host installs (see healthFilePath).
	healthFile := healthFilePath()

	// Optional Prometheus /metrics endpoint (k8s only, opt-in via the chart which
	// sets REACH_METRICS_ADDR). No-op when unset - host installs stay port-free.
	startMetricsServer(ctx, os.Getenv("REACH_METRICS_ADDR"), cfg.Type)

	// Fleet scale-in: a host deregisters from its fleet when the machine is going
	// down (ASG instance terminating), so the member is removed at once rather than
	// waiting for the reaper. Fires only on signal-driven shutdown (ctx cancelled)
	// AND only when the OS itself is stopping - never on a plain service restart,
	// and never on a permanent-error idle. The backend no-ops (409) for non-fleet
	// hosts, so it's always safe to attempt.
	if cfg.Type == "host" {
		defer func() {
			if ctx.Err() != nil && cfg.AgentToken != "" && systemIsShuttingDown() {
				deregister(cfg)
			}
		}()
	}

	for {
		touchHealthFile(healthFile)
		// Leadership gate: a non-leader stands by, only picking up the shared
		// token the leader claimed so it can take over instantly on failover.
		if leaderElector != nil && !leaderElector.IsLeader() {
			if cfg.AgentToken == "" && k8sTokenStore != nil {
				if tok, issued, lerr := k8sTokenStore.load(); lerr == nil && tok != "" {
					cfg.AgentToken = tok
					cfg.TokenIssuedAt = issued
				}
			}
			if !sleep(ctx, time.Duration(idlePollSeconds)*time.Second) {
				return nil
			}
			continue
		}

		// We are the leader (or not elected at all). Ensure we hold a token.
		if cfg.AgentToken == "" {
			if cfg.InstallToken == "" {
				return fmt.Errorf("no agent_token and no install_token in config")
			}
			log.Println("Leader has no agent_token, claiming...")
			if err := claim(cfg, fp); err != nil {
				return err
			}
		}

		if cfg.TokenIssuedAt != "" {
			if issuedAt, err := time.Parse(time.RFC3339, cfg.TokenIssuedAt); err == nil {
				if time.Since(issuedAt) >= time.Duration(tokenRotationDays)*24*time.Hour {
					log.Printf("Agent token is %d+ days old, rotating...", tokenRotationDays)
					if err := rotateToken(cfg); err != nil {
						if errors.Is(err, ErrPermanent) {
							return fmt.Errorf("token rotation failed permanently: %w", err)
						}
						log.Printf("Token rotation failed (will retry next poll): %v", err)
					}
				}
			}
		}

		syncResp, err := sync(cfg)
		if err != nil {
			if errors.Is(err, ErrTokenExpired) {
				log.Printf("Agent token expired server-side, rotating...")
				if rotErr := rotateToken(cfg); rotErr != nil {
					if errors.Is(rotErr, ErrPermanent) {
						return fmt.Errorf("token rotation after server expiry failed permanently: %w", rotErr)
					}
					log.Printf("Token rotation failed (will retry next poll): %v", rotErr)
				}
				if !sleep(ctx, time.Duration(pollSeconds)*time.Second) {
					return nil
				}
				continue
			}
			if errors.Is(err, ErrPermanent) {
				return fmt.Errorf("re-claim required: %w", err)
			}
			syncFailed = true
			recordSyncError()
			log.Printf("Sync error: %v - retrying in %ds", err, pollSeconds)
			if !sleep(ctx, time.Duration(pollSeconds)*time.Second) {
				return nil
			}
			continue
		}
		recordSyncOK()

		if syncFailed {
			log.Printf("Sync recovered - heartbeat sent successfully")
			syncFailed = false
		}

		pollSeconds = syncResp.NextPollSeconds
		if pollSeconds <= 0 {
			pollSeconds = idlePollSeconds
		}

		if syncResp.RotateToken {
			log.Printf("Admin-requested token rotation received, rotating...")
			if err := rotateToken(cfg); err != nil {
				if errors.Is(err, ErrPermanent) {
					return fmt.Errorf("admin-requested token rotation failed permanently: %w", err)
				}
				log.Printf("Admin-requested token rotation failed (will retry next poll): %v", err)
			}
		}

		for _, job := range syncResp.Jobs {
			if ctx.Err() != nil {
				log.Printf("Shutdown requested, skipping job %s", job.JobID)
				return nil
			}

			log.Printf("Received job %s: %q", job.JobID, job.Command)

			var res CommandResult
			if k8sAllowedBinaries != nil {
				// Kubernetes: shell-free execution (kubectl + allow-listed filters).
				// Policy mode is enforced by the backend at submission, not here.
				res = executeK8sCommand(job.Command, k8sAllowedBinaries)
			} else if len(job.Argv) > 0 {
				// Structured exec: run the argv with execve (no shell); approved-mode
				// writes are gated by host-rule match, not command-string prefix.
				res = executeStructured(job.Argv, job.Mode, job.IsWrite, job.ApprovedHostRules)
			} else {
				res = executeCommand(job.Command, job.Mode, job.IsWrite)
			}
			log.Printf("Job %s done: exit=%d duration=%dms", job.JobID, res.ExitCode, res.DurationMS)
			recordJob(res)

			if err := postResult(cfg, job.JobID, res); err != nil {
				log.Printf("Error posting result for %s: %v", job.JobID, err)
			}
		}

		if !sleep(ctx, time.Duration(pollSeconds)*time.Second) {
			return nil
		}
	}
}

func main() {
	log.SetFlags(log.LstdFlags | log.Lshortfile)

	// Internal re-exec path: apply Landlock sandbox then exec the command.
	// Invoked as: reach-agent --sandbox /bin/bash -lc <command>
	if len(os.Args) > 1 && os.Args[1] == "--sandbox" {
		sandboxExec(os.Args[2:])
		return
	}

	ctx, cancel := context.WithCancel(context.Background())
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGTERM, syscall.SIGINT)
	go func() {
		sig := <-quit
		log.Printf("Signal %v received, shutting down gracefully...", sig)
		cancel()
	}()

	err := run(ctx)
	if err == nil {
		return
	}
	if errors.Is(err, ErrPermanent) {
		// Don't exit - under Restart=always, any exit code respawns us and
		// we'd hammer the API with the same doomed request forever. Report
		// the error and idle instead; fix the config and restart manually
		// (e.g. systemctl restart reach-agent) once it's corrected.
		log.Printf("Permanent failure, not retrying: %v", err)
		log.Printf("Fix the agent config and restart the service manually")
		// On Kubernetes, keep the liveness health file fresh while idle so the
		// kubelet leaves the pod Running instead of restarting it into an
		// identical doomed claim - a CrashLoop would bury the reason logged
		// above. staleSeconds defaults to 120, so a 30s touch has ample margin.
		// healthFilePath is empty off-cluster, so on host this just idles.
		healthFile := healthFilePath()
		ticker := time.NewTicker(30 * time.Second)
		defer ticker.Stop()
		for {
			touchHealthFile(healthFile)
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
			}
		}
	}
	log.Fatalf("Fatal: %v", err)
}
