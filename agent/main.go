package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

const (
	machineIDPath   = "/etc/machine-id"
	agentVersion    = "0.1.0"
	idlePollSeconds = 30
	maxOutputBytes  = 50_000
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
// Blocked command patterns
// ---------------------------------------------------------------------------

var blockedPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)rm\s+-rf\s+/`),
	regexp.MustCompile(`(?i)mkfs`),
	regexp.MustCompile(`(?i)dd\s+if=`),
	regexp.MustCompile(`:(\(\)){.*:.*\|.*:.*&.*}`),
	regexp.MustCompile(`(?i)shutdown`),
	regexp.MustCompile(`(?i)reboot`),
	regexp.MustCompile(`(?i)poweroff`),
	regexp.MustCompile(`(?i)init\s+[06]`),
}

func isBlocked(command string) bool {
	for _, re := range blockedPatterns {
		if re.MatchString(command) {
			return true
		}
	}
	return false
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
	if status != 200 {
		return fmt.Errorf("claim failed (%d): %s", status, result.Error)
	}
	cfg.AgentToken = result.AgentToken
	cfg.MachineFingerprint = fp
	// Clear install token from disk after successful claim
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
}

type Job struct {
	JobID   string `json:"job_id"`
	Command string `json:"command"`
	Mode    string `json:"mode"`
}

type SyncResponse struct {
	Jobs           []Job  `json:"jobs"`
	NextPollSeconds int   `json:"next_poll_seconds"`
	Error          string `json:"error"`
}

func sync(cfg *Config) (*SyncResponse, error) {
	payload := SyncRequest{
		AgentID:            cfg.AgentID,
		MachineFingerprint: cfg.MachineFingerprint,
	}
	var result SyncResponse
	status, err := apiPost(cfg.APIURL, "/agent/sync", cfg.AgentToken, payload, &result)
	if err != nil {
		return nil, fmt.Errorf("sync request: %w", err)
	}
	if status != 200 {
		return nil, fmt.Errorf("sync failed (%d): %s", status, result.Error)
	}
	return &result, nil
}

// ---------------------------------------------------------------------------
// Execute command
// ---------------------------------------------------------------------------

type CommandResult struct {
	Stdout     string
	Stderr     string
	ExitCode   int
	DurationMS int64
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

func executeCommand(command string) CommandResult {
	start := time.Now()
	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout())
	defer cancel()

	cmd := exec.CommandContext(ctx, "/bin/bash", "-lc", command)
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

	limit := maxOutputSize()
	return CommandResult{
		Stdout:     truncate(stdout.String(), limit),
		Stderr:     truncate(stderr.String(), limit),
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

func run() error {
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
		syncResp, err := sync(cfg)
		if err != nil {
			log.Printf("Sync error: %v - retrying in %ds", err, pollSeconds)
			time.Sleep(time.Duration(pollSeconds) * time.Second)
			continue
		}

		pollSeconds = syncResp.NextPollSeconds
		if pollSeconds <= 0 {
			pollSeconds = idlePollSeconds
		}

		for _, job := range syncResp.Jobs {
			log.Printf("Received job %s: %q", job.JobID, job.Command)

			if isBlocked(job.Command) {
				log.Printf("Job %s blocked by safety policy", job.JobID)
				_ = postResult(cfg, job.JobID, CommandResult{
					Stdout:   "",
					Stderr:   "Command blocked by safety policy",
					ExitCode: 1,
				})
				continue
			}

			res := executeCommand(job.Command)
			log.Printf("Job %s done: exit=%d duration=%dms", job.JobID, res.ExitCode, res.DurationMS)

			if err := postResult(cfg, job.JobID, res); err != nil {
				log.Printf("Error posting result for %s: %v", job.JobID, err)
			}
		}

		time.Sleep(time.Duration(pollSeconds) * time.Second)
	}
}

func main() {
	log.SetFlags(log.LstdFlags | log.Lshortfile)
	if err := run(); err != nil {
		log.Fatalf("Fatal: %v", err)
	}
}
