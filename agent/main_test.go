package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// isBlocked
// ---------------------------------------------------------------------------

func TestIsBlocked(t *testing.T) {
	blocked := []string{
		"rm -rf /",
		"rm -RF /etc",
		"sudo rm -rf /",
		"mkfs.ext4 /dev/sda",
		"MKFS /dev/sdb",
		"dd if=/dev/zero of=/dev/sda",
		"DD IF=/dev/zero",
		":(){ :|:& };:",
		"shutdown now",
		"SHUTDOWN -h now",
		"reboot",
		"REBOOT",
		"poweroff",
		"POWEROFF",
		"init 0",
		"init 6",
	}
	for _, cmd := range blocked {
		cmd := cmd
		t.Run(cmd, func(t *testing.T) {
			if !isBlocked(cmd) {
				t.Errorf("expected %q to be blocked", cmd)
			}
		})
	}

	allowed := []string{
		"ls -la",
		"docker ps",
		"rm -rf ./tmp/mydir",
		"rm -rf ./build",
		"echo hello",
		"cat /etc/hosts",
		"df -h",
		"uptime",
	}
	for _, cmd := range allowed {
		cmd := cmd
		t.Run(cmd, func(t *testing.T) {
			if isBlocked(cmd) {
				t.Errorf("expected %q to be allowed", cmd)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// truncate
// ---------------------------------------------------------------------------

func TestTruncate(t *testing.T) {
	t.Run("short string unchanged", func(t *testing.T) {
		s := "hello"
		if got := truncate(s, 100); got != s {
			t.Errorf("got %q, want %q", got, s)
		}
	})

	t.Run("exactly at limit unchanged", func(t *testing.T) {
		s := strings.Repeat("x", 50)
		if got := truncate(s, 50); got != s {
			t.Errorf("expected unchanged string at limit")
		}
	})

	t.Run("over limit appends TRUNCATED suffix", func(t *testing.T) {
		s := strings.Repeat("x", 100)
		got := truncate(s, 50)
		if !strings.HasSuffix(got, "[TRUNCATED]") {
			t.Errorf("expected [TRUNCATED] suffix, got %q", got)
		}
		// prefix should be exactly maxBytes characters before the \n[TRUNCATED]
		prefix := strings.TrimSuffix(got, "\n[TRUNCATED]")
		if len([]byte(prefix)) != 50 {
			t.Errorf("prefix length = %d, want 50", len([]byte(prefix)))
		}
	})

	t.Run("empty string unchanged", func(t *testing.T) {
		if got := truncate("", 50); got != "" {
			t.Errorf("expected empty, got %q", got)
		}
	})
}

// ---------------------------------------------------------------------------
// commandTimeout
// ---------------------------------------------------------------------------

func TestCommandTimeout(t *testing.T) {
	t.Run("default is 60s", func(t *testing.T) {
		os.Unsetenv("REACH_COMMAND_TIMEOUT_SECONDS")
		if d := commandTimeout(); d != 60*time.Second {
			t.Errorf("got %v, want 60s", d)
		}
	})

	t.Run("override via env var", func(t *testing.T) {
		t.Setenv("REACH_COMMAND_TIMEOUT_SECONDS", "120")
		if d := commandTimeout(); d != 120*time.Second {
			t.Errorf("got %v, want 120s", d)
		}
	})

	t.Run("invalid env var falls back to default", func(t *testing.T) {
		t.Setenv("REACH_COMMAND_TIMEOUT_SECONDS", "not-a-number")
		if d := commandTimeout(); d != 60*time.Second {
			t.Errorf("got %v, want 60s", d)
		}
	})

	t.Run("zero falls back to default", func(t *testing.T) {
		t.Setenv("REACH_COMMAND_TIMEOUT_SECONDS", "0")
		if d := commandTimeout(); d != 60*time.Second {
			t.Errorf("got %v, want 60s", d)
		}
	})
}

// ---------------------------------------------------------------------------
// maxOutputSize
// ---------------------------------------------------------------------------

func TestMaxOutputSize(t *testing.T) {
	t.Run("default is 50000", func(t *testing.T) {
		os.Unsetenv("REACH_MAX_OUTPUT_BYTES")
		if n := maxOutputSize(); n != 50_000 {
			t.Errorf("got %d, want 50000", n)
		}
	})

	t.Run("override via env var", func(t *testing.T) {
		t.Setenv("REACH_MAX_OUTPUT_BYTES", "1024")
		if n := maxOutputSize(); n != 1024 {
			t.Errorf("got %d, want 1024", n)
		}
	})

	t.Run("invalid env var falls back to default", func(t *testing.T) {
		t.Setenv("REACH_MAX_OUTPUT_BYTES", "bad")
		if n := maxOutputSize(); n != 50_000 {
			t.Errorf("got %d, want 50000", n)
		}
	})
}

// ---------------------------------------------------------------------------
// executeCommand
// ---------------------------------------------------------------------------

func TestExecuteCommand(t *testing.T) {
	t.Run("successful command", func(t *testing.T) {
		os.Unsetenv("REACH_COMMAND_TIMEOUT_SECONDS")
		os.Unsetenv("REACH_MAX_OUTPUT_BYTES")
		res := executeCommand("echo hello")
		if res.ExitCode != 0 {
			t.Errorf("exit code %d, want 0", res.ExitCode)
		}
		if !strings.Contains(res.Stdout, "hello") {
			t.Errorf("stdout %q does not contain 'hello'", res.Stdout)
		}
		if res.DurationMS < 0 {
			t.Errorf("duration should be non-negative")
		}
	})

	t.Run("failed command returns non-zero exit", func(t *testing.T) {
		res := executeCommand("exit 42")
		if res.ExitCode != 42 {
			t.Errorf("exit code %d, want 42", res.ExitCode)
		}
	})

	t.Run("stderr captured separately", func(t *testing.T) {
		res := executeCommand("echo errline >&2")
		if !strings.Contains(res.Stderr, "errline") {
			t.Errorf("stderr %q does not contain 'errline'", res.Stderr)
		}
		if strings.Contains(res.Stdout, "errline") {
			t.Errorf("stdout should not contain stderr output")
		}
	})

	t.Run("output truncated when over limit", func(t *testing.T) {
		t.Setenv("REACH_MAX_OUTPUT_BYTES", "50")
		// printf '%200s' prints 200 spaces; tr replaces with x → 200 bytes of 'x'
		res := executeCommand("printf '%200s' | tr ' ' x")
		if !strings.Contains(res.Stdout, "[TRUNCATED]") {
			t.Errorf("expected truncated output, got %q", res.Stdout)
		}
	})

	t.Run("command timeout returns non-zero exit", func(t *testing.T) {
		t.Setenv("REACH_COMMAND_TIMEOUT_SECONDS", "1")
		res := executeCommand("sleep 10")
		// 124 on Linux (killed via context deadline), -1 on macOS (SIGKILL via ExitError)
		if res.ExitCode == 0 {
			t.Error("expected non-zero exit code for timed-out command")
		}
	})
}

// ---------------------------------------------------------------------------
// loadConfig / saveConfig
// ---------------------------------------------------------------------------

// setConfigPath redirects the package-level configPath to a temp file.
func setConfigPath(t *testing.T) func() {
	t.Helper()
	old := configPath
	configPath = filepath.Join(t.TempDir(), "config.json")
	return func() { configPath = old }
}

func TestLoadConfig(t *testing.T) {
	t.Run("valid config", func(t *testing.T) {
		restore := setConfigPath(t)
		defer restore()

		os.WriteFile(configPath, []byte(`{"api_url":"https://api.example.com","agent_id":"agent_a","agent_token":"tok"}`), 0600)
		cfg, err := loadConfig()
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if cfg.APIURL != "https://api.example.com" {
			t.Errorf("api_url = %q", cfg.APIURL)
		}
		if cfg.AgentID != "agent_a" {
			t.Errorf("agent_id = %q", cfg.AgentID)
		}
		if cfg.AgentToken != "tok" {
			t.Errorf("agent_token = %q", cfg.AgentToken)
		}
	})

	t.Run("missing file returns error", func(t *testing.T) {
		restore := setConfigPath(t)
		defer restore()
		if _, err := loadConfig(); err == nil {
			t.Error("expected error for missing file")
		}
	})

	t.Run("missing api_url returns error", func(t *testing.T) {
		restore := setConfigPath(t)
		defer restore()
		os.WriteFile(configPath, []byte(`{"agent_id":"agent_a"}`), 0600)
		if _, err := loadConfig(); err == nil {
			t.Error("expected error for missing api_url")
		}
	})

	t.Run("missing agent_id returns error", func(t *testing.T) {
		restore := setConfigPath(t)
		defer restore()
		os.WriteFile(configPath, []byte(`{"api_url":"https://x"}`), 0600)
		if _, err := loadConfig(); err == nil {
			t.Error("expected error for missing agent_id")
		}
	})

	t.Run("invalid JSON returns error", func(t *testing.T) {
		restore := setConfigPath(t)
		defer restore()
		os.WriteFile(configPath, []byte(`not json`), 0600)
		if _, err := loadConfig(); err == nil {
			t.Error("expected error for invalid JSON")
		}
	})
}

func TestSaveConfig(t *testing.T) {
	t.Run("saves and reloads correctly", func(t *testing.T) {
		restore := setConfigPath(t)
		defer restore()

		cfg := &Config{APIURL: "https://api.example.com", AgentID: "agent_a", AgentToken: "tok_abc"}
		if err := saveConfig(cfg); err != nil {
			t.Fatalf("saveConfig: %v", err)
		}
		loaded, err := loadConfig()
		if err != nil {
			t.Fatalf("loadConfig after save: %v", err)
		}
		if loaded.AgentToken != "tok_abc" {
			t.Errorf("token = %q, want tok_abc", loaded.AgentToken)
		}
	})

	t.Run("file has 0600 permissions", func(t *testing.T) {
		restore := setConfigPath(t)
		defer restore()

		saveConfig(&Config{APIURL: "https://x", AgentID: "a"})
		info, _ := os.Stat(configPath)
		if info.Mode().Perm() != 0600 {
			t.Errorf("mode = %o, want 0600", info.Mode().Perm())
		}
	})
}

// ---------------------------------------------------------------------------
// ensureInstallID / machineFingerprint
// ---------------------------------------------------------------------------

// setInstallIDPath redirects the package-level installIDPath to a temp file.
func setInstallIDPath(t *testing.T) func() {
	t.Helper()
	old := installIDPath
	installIDPath = filepath.Join(t.TempDir(), "install_id")
	return func() { installIDPath = old }
}

func TestEnsureInstallID(t *testing.T) {
	t.Run("creates new ID when file missing", func(t *testing.T) {
		restore := setInstallIDPath(t)
		defer restore()

		id, err := ensureInstallID()
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(id) != 32 {
			t.Errorf("id length = %d, want 32 hex chars", len(id))
		}
	})

	t.Run("returns same ID on second call", func(t *testing.T) {
		restore := setInstallIDPath(t)
		defer restore()

		id1, _ := ensureInstallID()
		id2, _ := ensureInstallID()
		if id1 != id2 {
			t.Errorf("id changed between calls: %q vs %q", id1, id2)
		}
	})

	t.Run("persists ID to disk", func(t *testing.T) {
		restore := setInstallIDPath(t)
		defer restore()

		id, _ := ensureInstallID()
		data, _ := os.ReadFile(installIDPath)
		if strings.TrimSpace(string(data)) != id {
			t.Errorf("disk %q does not match returned id %q", string(data), id)
		}
	})
}

func TestMachineFingerprint(t *testing.T) {
	restore := setInstallIDPath(t)
	defer restore()

	t.Run("has fp_ prefix", func(t *testing.T) {
		fp, err := machineFingerprint()
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if !strings.HasPrefix(fp, "fp_") {
			t.Errorf("fingerprint %q does not start with fp_", fp)
		}
	})

	t.Run("is deterministic", func(t *testing.T) {
		fp1, _ := machineFingerprint()
		fp2, _ := machineFingerprint()
		if fp1 != fp2 {
			t.Errorf("not deterministic: %q vs %q", fp1, fp2)
		}
	})

	t.Run("is 35 chars total (fp_ + 32 hex)", func(t *testing.T) {
		fp, _ := machineFingerprint()
		if len(fp) != 35 {
			t.Errorf("fingerprint length = %d, want 35", len(fp))
		}
	})
}

// ---------------------------------------------------------------------------
// apiPost
// ---------------------------------------------------------------------------

func TestAPIPost(t *testing.T) {
	t.Run("sends JSON body", func(t *testing.T) {
		var gotBody map[string]string
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			json.NewDecoder(r.Body).Decode(&gotBody)
			w.WriteHeader(200)
			w.Write([]byte(`{}`))
		}))
		defer srv.Close()

		apiPost(srv.URL, "/test", "", map[string]string{"key": "value"}, nil)
		if gotBody["key"] != "value" {
			t.Errorf("body key = %q, want 'value'", gotBody["key"])
		}
	})

	t.Run("sends Bearer token when provided", func(t *testing.T) {
		var gotAuth string
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			gotAuth = r.Header.Get("Authorization")
			w.WriteHeader(200)
			w.Write([]byte(`{}`))
		}))
		defer srv.Close()

		apiPost(srv.URL, "/test", "my-token", nil, nil)
		if gotAuth != "Bearer my-token" {
			t.Errorf("Authorization = %q, want 'Bearer my-token'", gotAuth)
		}
	})

	t.Run("omits Authorization header when token is empty", func(t *testing.T) {
		var gotAuth string
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			gotAuth = r.Header.Get("Authorization")
			w.WriteHeader(200)
			w.Write([]byte(`{}`))
		}))
		defer srv.Close()

		apiPost(srv.URL, "/test", "", nil, nil)
		if gotAuth != "" {
			t.Errorf("Authorization should be empty, got %q", gotAuth)
		}
	})

	t.Run("returns HTTP status code", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(404)
			w.Write([]byte(`{}`))
		}))
		defer srv.Close()

		status, err := apiPost(srv.URL, "/test", "", nil, nil)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if status != 404 {
			t.Errorf("status = %d, want 404", status)
		}
	})

	t.Run("unmarshals response into result struct", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(200)
			w.Write([]byte(`{"agent_token":"tok_new"}`))
		}))
		defer srv.Close()

		var result ClaimResponse
		apiPost(srv.URL, "/test", "", nil, &result)
		if result.AgentToken != "tok_new" {
			t.Errorf("agent_token = %q, want 'tok_new'", result.AgentToken)
		}
	})

	t.Run("returns error on connection failure", func(t *testing.T) {
		_, err := apiPost("http://127.0.0.1:1", "/test", "", nil, nil)
		if err == nil {
			t.Error("expected error for unreachable server")
		}
	})
}

// ---------------------------------------------------------------------------
// claim
// ---------------------------------------------------------------------------

func claimCfg(apiURL string) *Config {
	return &Config{
		APIURL:       apiURL,
		AgentID:      "agent_a",
		InstallToken: "install_tok",
	}
}

func TestClaim(t *testing.T) {
	t.Run("200 saves token and clears install_token", func(t *testing.T) {
		restore := setConfigPath(t)
		defer restore()

		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(200)
			json.NewEncoder(w).Encode(ClaimResponse{AgentToken: "tok_new", Mode: "wild"})
		}))
		defer srv.Close()

		cfg := claimCfg(srv.URL)
		if err := claim(cfg, "fp_abc"); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if cfg.AgentToken != "tok_new" {
			t.Errorf("token = %q, want 'tok_new'", cfg.AgentToken)
		}
		if cfg.InstallToken != "" {
			t.Error("install_token should be cleared after claim")
		}
		if cfg.MachineFingerprint != "fp_abc" {
			t.Errorf("fingerprint = %q, want 'fp_abc'", cfg.MachineFingerprint)
		}
		if cfg.TokenIssuedAt == "" {
			t.Error("token_issued_at should be set after claim")
		}
	})

	t.Run("4xx returns ErrPermanent", func(t *testing.T) {
		restore := setConfigPath(t)
		defer restore()

		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(403)
			json.NewEncoder(w).Encode(ClaimResponse{Error: "invalid token"})
		}))
		defer srv.Close()

		err := claim(claimCfg(srv.URL), "fp_abc")
		if err == nil {
			t.Fatal("expected error")
		}
		if !errors.Is(err, ErrPermanent) {
			t.Errorf("expected ErrPermanent, got %v", err)
		}
	})

	t.Run("5xx is retryable (not ErrPermanent)", func(t *testing.T) {
		restore := setConfigPath(t)
		defer restore()

		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(500)
			json.NewEncoder(w).Encode(ClaimResponse{Error: "server error"})
		}))
		defer srv.Close()

		err := claim(claimCfg(srv.URL), "fp_abc")
		if err == nil {
			t.Fatal("expected error")
		}
		if errors.Is(err, ErrPermanent) {
			t.Error("5xx should NOT be ErrPermanent")
		}
	})

	t.Run("sends correct path and payload", func(t *testing.T) {
		restore := setConfigPath(t)
		defer restore()

		var gotPath string
		var gotBody ClaimRequest
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			gotPath = r.URL.Path
			json.NewDecoder(r.Body).Decode(&gotBody)
			w.WriteHeader(200)
			json.NewEncoder(w).Encode(ClaimResponse{AgentToken: "tok"})
		}))
		defer srv.Close()

		claim(claimCfg(srv.URL), "fp_test")
		if gotPath != "/agent/claim" {
			t.Errorf("path = %q, want /agent/claim", gotPath)
		}
		if gotBody.AgentID != "agent_a" {
			t.Errorf("agent_id = %q", gotBody.AgentID)
		}
		if gotBody.MachineFingerprint != "fp_test" {
			t.Errorf("machine_fingerprint = %q", gotBody.MachineFingerprint)
		}
		if gotBody.AgentVersion != agentVersion {
			t.Errorf("agent_version = %q, want %q", gotBody.AgentVersion, agentVersion)
		}
	})
}

// ---------------------------------------------------------------------------
// sync
// ---------------------------------------------------------------------------

func syncCfg(apiURL string) *Config {
	return &Config{
		APIURL:             apiURL,
		AgentID:            "agent_a",
		AgentToken:         "tok_abc",
		MachineFingerprint: "fp_abc",
	}
}

func TestSync(t *testing.T) {
	t.Run("200 returns jobs and poll interval", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(200)
			json.NewEncoder(w).Encode(SyncResponse{
				Jobs:            []Job{{JobID: "job_1", Command: "ls", Mode: "wild"}},
				NextPollSeconds: 5,
			})
		}))
		defer srv.Close()

		resp, err := sync(syncCfg(srv.URL))
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(resp.Jobs) != 1 || resp.Jobs[0].JobID != "job_1" {
			t.Errorf("unexpected jobs: %+v", resp.Jobs)
		}
		if resp.NextPollSeconds != 5 {
			t.Errorf("next_poll_seconds = %d, want 5", resp.NextPollSeconds)
		}
	})

	t.Run("401 returns ErrUnauthorized and ErrPermanent", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(401)
			json.NewEncoder(w).Encode(SyncResponse{Error: "unauthorized"})
		}))
		defer srv.Close()

		_, err := sync(syncCfg(srv.URL))
		if !errors.Is(err, ErrUnauthorized) {
			t.Errorf("expected ErrUnauthorized, got %v", err)
		}
		if !errors.Is(err, ErrPermanent) {
			t.Errorf("expected ErrPermanent, got %v", err)
		}
	})

	t.Run("403 token_expired returns ErrTokenExpired", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(403)
			json.NewEncoder(w).Encode(SyncResponse{Error: "token_expired"})
		}))
		defer srv.Close()

		_, err := sync(syncCfg(srv.URL))
		if !errors.Is(err, ErrTokenExpired) {
			t.Errorf("expected ErrTokenExpired, got %v", err)
		}
	})

	t.Run("403 other error returns ErrPermanent not ErrTokenExpired", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(403)
			json.NewEncoder(w).Encode(SyncResponse{Error: "agent_not_active"})
		}))
		defer srv.Close()

		_, err := sync(syncCfg(srv.URL))
		if !errors.Is(err, ErrPermanent) {
			t.Errorf("expected ErrPermanent, got %v", err)
		}
		if errors.Is(err, ErrTokenExpired) {
			t.Error("should NOT be ErrTokenExpired for non-expiry 403")
		}
	})

	t.Run("5xx is retryable (not ErrPermanent)", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(500)
			json.NewEncoder(w).Encode(SyncResponse{Error: "server error"})
		}))
		defer srv.Close()

		_, err := sync(syncCfg(srv.URL))
		if err == nil {
			t.Fatal("expected error")
		}
		if errors.Is(err, ErrPermanent) {
			t.Error("5xx should NOT be ErrPermanent")
		}
	})

	t.Run("sends agent token as bearer", func(t *testing.T) {
		var gotAuth string
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			gotAuth = r.Header.Get("Authorization")
			w.WriteHeader(200)
			json.NewEncoder(w).Encode(SyncResponse{})
		}))
		defer srv.Close()

		sync(syncCfg(srv.URL))
		if gotAuth != "Bearer tok_abc" {
			t.Errorf("Authorization = %q, want 'Bearer tok_abc'", gotAuth)
		}
	})
}

// ---------------------------------------------------------------------------
// rotateToken
// ---------------------------------------------------------------------------

func TestRotateToken(t *testing.T) {
	t.Run("200 updates token and token_issued_at", func(t *testing.T) {
		restore := setConfigPath(t)
		defer restore()

		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(200)
			json.NewEncoder(w).Encode(RotateTokenResponse{AgentToken: "tok_rotated"})
		}))
		defer srv.Close()

		cfg := syncCfg(srv.URL)
		if err := rotateToken(cfg); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if cfg.AgentToken != "tok_rotated" {
			t.Errorf("token = %q, want 'tok_rotated'", cfg.AgentToken)
		}
		if cfg.TokenIssuedAt == "" {
			t.Error("token_issued_at should be set after rotation")
		}
	})

	t.Run("401 returns ErrUnauthorized and ErrPermanent", func(t *testing.T) {
		restore := setConfigPath(t)
		defer restore()

		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(401)
			json.NewEncoder(w).Encode(RotateTokenResponse{Error: "unauthorized"})
		}))
		defer srv.Close()

		err := rotateToken(syncCfg(srv.URL))
		if !errors.Is(err, ErrUnauthorized) {
			t.Errorf("expected ErrUnauthorized, got %v", err)
		}
		if !errors.Is(err, ErrPermanent) {
			t.Errorf("expected ErrPermanent, got %v", err)
		}
	})

	t.Run("5xx returns error", func(t *testing.T) {
		restore := setConfigPath(t)
		defer restore()

		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(500)
			json.NewEncoder(w).Encode(RotateTokenResponse{Error: "server error"})
		}))
		defer srv.Close()

		if err := rotateToken(syncCfg(srv.URL)); err == nil {
			t.Error("expected error for 500 response")
		}
	})
}

// ---------------------------------------------------------------------------
// postResult
// ---------------------------------------------------------------------------

func TestPostResult(t *testing.T) {
	t.Run("exit 0 sends SUCCEEDED status", func(t *testing.T) {
		var gotBody ResultRequest
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			json.NewDecoder(r.Body).Decode(&gotBody)
			w.WriteHeader(200)
			w.Write([]byte(`{}`))
		}))
		defer srv.Close()

		postResult(syncCfg(srv.URL), "job_1", CommandResult{ExitCode: 0, Stdout: "ok\n"})
		if gotBody.Status != "SUCCEEDED" {
			t.Errorf("status = %q, want SUCCEEDED", gotBody.Status)
		}
	})

	t.Run("non-zero exit sends FAILED status", func(t *testing.T) {
		var gotBody ResultRequest
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			json.NewDecoder(r.Body).Decode(&gotBody)
			w.WriteHeader(200)
			w.Write([]byte(`{}`))
		}))
		defer srv.Close()

		postResult(syncCfg(srv.URL), "job_1", CommandResult{ExitCode: 1, Stderr: "err\n"})
		if gotBody.Status != "FAILED" {
			t.Errorf("status = %q, want FAILED", gotBody.Status)
		}
	})

	t.Run("sends correct job path", func(t *testing.T) {
		var gotPath string
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			gotPath = r.URL.Path
			w.WriteHeader(200)
			w.Write([]byte(`{}`))
		}))
		defer srv.Close()

		postResult(syncCfg(srv.URL), "job_42", CommandResult{})
		if gotPath != "/agent/jobs/job_42/result" {
			t.Errorf("path = %q, want /agent/jobs/job_42/result", gotPath)
		}
	})

	t.Run("200 returns nil error", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(200)
			w.Write([]byte(`{}`))
		}))
		defer srv.Close()

		if err := postResult(syncCfg(srv.URL), "job_1", CommandResult{}); err != nil {
			t.Errorf("unexpected error: %v", err)
		}
	})

	t.Run("non-200 returns error", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(409)
			w.Write([]byte(`{"error":"already done"}`))
		}))
		defer srv.Close()

		if err := postResult(syncCfg(srv.URL), "job_1", CommandResult{}); err == nil {
			t.Error("expected error for non-200 response")
		}
	})

	t.Run("sends agent_id and fingerprint from config", func(t *testing.T) {
		var gotBody ResultRequest
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			json.NewDecoder(r.Body).Decode(&gotBody)
			w.WriteHeader(200)
			w.Write([]byte(`{}`))
		}))
		defer srv.Close()

		postResult(syncCfg(srv.URL), "job_1", CommandResult{})
		if gotBody.AgentID != "agent_a" {
			t.Errorf("agent_id = %q, want 'agent_a'", gotBody.AgentID)
		}
		if gotBody.MachineFingerprint != "fp_abc" {
			t.Errorf("machine_fingerprint = %q, want 'fp_abc'", gotBody.MachineFingerprint)
		}
	})
}

// ---------------------------------------------------------------------------
// sleep
// ---------------------------------------------------------------------------

func TestSleep(t *testing.T) {
	t.Run("returns true after duration elapses", func(t *testing.T) {
		if !sleep(context.Background(), 1*time.Millisecond) {
			t.Error("expected true when sleep completes normally")
		}
	})

	t.Run("returns false when context already cancelled", func(t *testing.T) {
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		if sleep(ctx, 10*time.Second) {
			t.Error("expected false when context is already cancelled")
		}
	})

	t.Run("returns false when context cancelled during sleep", func(t *testing.T) {
		ctx, cancel := context.WithCancel(context.Background())
		go func() {
			time.Sleep(10 * time.Millisecond)
			cancel()
		}()
		if sleep(ctx, 10*time.Second) {
			t.Error("expected false when context cancelled mid-sleep")
		}
	})
}
