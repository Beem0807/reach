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
)

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
	APIURL             string `json:"api_url"`
	AgentID            string `json:"agent_id"`
	AgentToken         string `json:"agent_token,omitempty"`
	InstallToken       string `json:"install_token,omitempty"`
	MachineFingerprint string `json:"machine_fingerprint,omitempty"`
	TokenIssuedAt      string `json:"token_issued_at,omitempty"`
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
	if cfg.APIURL == "" || cfg.AgentID == "" {
		return nil, fmt.Errorf("config missing api_url or agent_id")
	}
	return &cfg, nil
}

func saveConfig(cfg *Config) error {
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(configPath, data, 0600); err != nil {
		return fmt.Errorf("write config: %w", err)
	}
	return nil
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
	AgentID            string `json:"agent_id"`
	InstallToken       string `json:"install_token"`
	MachineFingerprint string `json:"machine_fingerprint"`
	Hostname           string `json:"hostname"`
	AgentVersion       string `json:"agent_version"`
}

type ClaimResponse struct {
	AgentToken string `json:"agent_token"`
	Mode       string `json:"mode"`
	Error      string `json:"error"`
}

func claim(cfg *Config, fp string) error {
	hostname, _ := os.Hostname()
	payload := ClaimRequest{
		AgentID:            cfg.AgentID,
		InstallToken:       cfg.InstallToken,
		MachineFingerprint: fp,
		Hostname:           hostname,
		AgentVersion:       agentVersion,
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
// Sync
// ---------------------------------------------------------------------------

type SyncRequest struct {
	AgentID            string `json:"agent_id"`
	MachineFingerprint string `json:"machine_fingerprint"`
	AgentVersion       string `json:"agent_version"`
	RunningAsRoot      bool   `json:"running_as_root"`
}

type Job struct {
	JobID            string   `json:"job_id"`
	Command          string   `json:"command"`
	Mode             string   `json:"mode"`
	IsWrite          bool     `json:"is_write"`
	ApprovedCommands []string `json:"approved_commands"`
}

type SyncResponse struct {
	Jobs            []Job  `json:"jobs"`
	NextPollSeconds int    `json:"next_poll_seconds"`
	Error           string `json:"error"`
}

func sync(cfg *Config) (*SyncResponse, error) {
	payload := SyncRequest{
		AgentID:            cfg.AgentID,
		MachineFingerprint: cfg.MachineFingerprint,
		AgentVersion:       agentVersion,
		RunningAsRoot:      os.Getuid() == 0,
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
	return &result, nil
}

// ---------------------------------------------------------------------------
// Rotate token
// ---------------------------------------------------------------------------

type RotateTokenRequest struct {
	AgentID            string `json:"agent_id"`
	MachineFingerprint string `json:"machine_fingerprint"`
}

type RotateTokenResponse struct {
	AgentToken string `json:"agent_token"`
	Error      string `json:"error"`
}

func rotateToken(cfg *Config) error {
	payload := RotateTokenRequest{
		AgentID:            cfg.AgentID,
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
// Execute command
// ---------------------------------------------------------------------------

type CommandResult struct {
	Stdout     string
	Stderr     string
	ExitCode   int
	DurationMS int64
	Blocked    bool
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

// isApprovedLocally mirrors the server-side _is_approved: exact match or prefix + space boundary.
func isApprovedLocally(command string, approved []string) bool {
	cmd := strings.TrimSpace(command)
	for _, allowed := range approved {
		allowed = strings.TrimSpace(allowed)
		if cmd == allowed || strings.HasPrefix(cmd, allowed+" ") {
			return true
		}
	}
	return false
}

func runCmd(ctx context.Context, args []string) (string, string, int) {
	cmd := exec.CommandContext(ctx, args[0], args[1:]...)
	cmd.Dir = "/"
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	exitCode := 0
	if err := cmd.Run(); err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
		} else if ctx.Err() == context.DeadlineExceeded {
			exitCode = 124
		} else {
			exitCode = 1
		}
	}
	return stdout.String(), stderr.String(), exitCode
}

func executeCommand(command, mode string, isWrite bool, approvedCommands []string) CommandResult {
	start := time.Now()
	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout())
	defer cancel()

	// Approved commands bypass the sandbox - writes are explicitly permitted.
	// Everything else runs under Landlock on Linux: reads pass, unapproved writes
	// are kernel-blocked and surface as a structured approval error.
	approvedWrite := mode == "approved" && isApprovedLocally(command, approvedCommands)
	useSandbox := (mode == "readonly" || mode == "approved") && runtime.GOOS == "linux" && !approvedWrite

	// On non-Linux (macOS): no Landlock available. Block unapproved writes in
	// approved mode using the is_write flag annotated by the server.
	if mode == "approved" && runtime.GOOS != "linux" && isWrite && !approvedWrite {
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

	stdout, stderr, exitCode := runCmd(ctx, args)

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

	limit := maxOutputSize()
	return CommandResult{
		Stdout:     truncate(stdout, limit),
		Stderr:     truncate(stderr, limit),
		ExitCode:   exitCode,
		DurationMS: time.Since(start).Milliseconds(),
	}
}

// ---------------------------------------------------------------------------
// Post result
// ---------------------------------------------------------------------------

type ResultRequest struct {
	AgentID            string `json:"agent_id"`
	MachineFingerprint string `json:"machine_fingerprint"`
	Status             string `json:"status"`
	ExitCode           int    `json:"exit_code"`
	Stdout             string `json:"stdout"`
	Stderr             string `json:"stderr"`
	DurationMS         int64  `json:"duration_ms"`
	Blocked            bool   `json:"blocked,omitempty"`
	IsWrite            bool   `json:"is_write,omitempty"`
}

func postResult(cfg *Config, jobID string, res CommandResult) error {
	status := "SUCCEEDED"
	if res.ExitCode != 0 {
		status = "FAILED"
	}
	payload := ResultRequest{
		AgentID:            cfg.AgentID,
		MachineFingerprint: cfg.MachineFingerprint,
		Status:             status,
		ExitCode:           res.ExitCode,
		Stdout:             res.Stdout,
		Stderr:             res.Stderr,
		DurationMS:         res.DurationMS,
		Blocked:            res.Blocked,
		IsWrite:            res.Blocked, // if blocked, the command was a write by definition
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
	cfg, err := loadConfig()
	if err != nil {
		return err
	}

	fp, err := machineFingerprint()
	if err != nil {
		return fmt.Errorf("fingerprint: %w", err)
	}
	cfg.MachineFingerprint = fp

	if cfg.AgentToken == "" {
		if cfg.InstallToken == "" {
			return fmt.Errorf("no agent_token and no install_token in config")
		}
		log.Println("No agent_token found, claiming...")
		if err := claim(cfg, fp); err != nil {
			return err
		}
	}

	log.Printf("Agent %s starting poll loop", cfg.AgentID)
	pollSeconds := idlePollSeconds

	for {
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
			log.Printf("Sync error: %v - retrying in %ds", err, pollSeconds)
			if !sleep(ctx, time.Duration(pollSeconds)*time.Second) {
				return nil
			}
			continue
		}

		pollSeconds = syncResp.NextPollSeconds
		if pollSeconds <= 0 {
			pollSeconds = idlePollSeconds
		}

		for _, job := range syncResp.Jobs {
			if ctx.Err() != nil {
				log.Printf("Shutdown requested, skipping job %s", job.JobID)
				return nil
			}

			log.Printf("Received job %s: %q", job.JobID, job.Command)

			res := executeCommand(job.Command, job.Mode, job.IsWrite, job.ApprovedCommands)
			log.Printf("Job %s done: exit=%d duration=%dms", job.JobID, res.ExitCode, res.DurationMS)

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
		for {
			time.Sleep(1 * time.Hour)
		}
	}
	log.Fatalf("Fatal: %v", err)
}
