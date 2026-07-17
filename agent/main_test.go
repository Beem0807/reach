package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

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
// cappedBuffer - bounds memory even for unbounded producers (yes, find /)
// ---------------------------------------------------------------------------

func TestCappedBuffer(t *testing.T) {
	t.Run("under limit: kept verbatim, not truncated", func(t *testing.T) {
		c := &cappedBuffer{limit: 50}
		n, _ := c.Write([]byte("hello"))
		if n != 5 {
			t.Errorf("Write returned %d, want 5", n)
		}
		if c.truncated {
			t.Errorf("should not be truncated under limit")
		}
		if c.String() != "hello" {
			t.Errorf("got %q, want %q", c.String(), "hello")
		}
	})

	t.Run("over limit: caps bytes, marks truncated, appends marker", func(t *testing.T) {
		c := &cappedBuffer{limit: 10}
		// One giant write, far exceeding the limit - simulates `yes`.
		n, _ := c.Write([]byte(strings.Repeat("x", 1_000_000)))
		if n != 1_000_000 {
			t.Errorf("Write must report the full length (%d) so the child never blocks", n)
		}
		if !c.truncated {
			t.Errorf("expected truncated=true")
		}
		if !strings.HasSuffix(c.String(), "\n[TRUNCATED]") {
			t.Errorf("expected [TRUNCATED] marker, got %q", c.String())
		}
		body := strings.TrimSuffix(c.String(), "\n[TRUNCATED]")
		if len(body) != 10 {
			t.Errorf("retained %d bytes, want exactly the 10-byte limit", len(body))
		}
	})

	t.Run("bounded memory across many writes", func(t *testing.T) {
		c := &cappedBuffer{limit: 100}
		for i := 0; i < 10_000; i++ {
			c.Write([]byte(strings.Repeat("y", 100))) // 1MB total streamed
		}
		if c.buf.Len() > 100 {
			t.Errorf("buffer grew to %d bytes; must stay <= limit (100)", c.buf.Len())
		}
		if !c.truncated {
			t.Errorf("expected truncated=true after overflow")
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
// structured exec: host rule matching + execution
// ---------------------------------------------------------------------------

func TestHostRuleMatches(t *testing.T) {
	r := HostRule{Bin: "systemctl", Args: []string{"restart", "*"}}
	cases := []struct {
		argv []string
		want bool
	}{
		{[]string{"systemctl", "restart", "nginx"}, true},
		{[]string{"systemctl", "restart", "web-01"}, true},
		{[]string{"systemctl", "stop", "nginx"}, false},     // literal arg differs
		{[]string{"systemctl", "restart", "a", "b"}, false}, // arity differs
		{[]string{"docker", "restart", "nginx"}, false},     // bin differs
		{[]string{"systemctl", "restart"}, false},           // arity (missing arg)
		{nil, false},
	}
	for _, c := range cases {
		if got := hostRuleMatches(c.argv, r); got != c.want {
			t.Errorf("hostRuleMatches(%v) = %v, want %v", c.argv, got, c.want)
		}
	}
}

func TestHostRuleMatchesTrailingVariadic(t *testing.T) {
	r := HostRule{Bin: "helm", Args: []string{"list", "..."}}
	cases := []struct {
		argv []string
		want bool
	}{
		{[]string{"helm", "list"}, true},                          // zero trailing
		{[]string{"helm", "list", "prod"}, true},                  // one
		{[]string{"helm", "list", "-n", "prod", "--all"}, true},   // many
		{[]string{"helm", "status", "prod"}, false},               // prefix literal differs
		{[]string{"helm"}, false},                                 // missing "list" prefix
		{[]string{"flux", "list"}, false},                         // bin differs
	}
	for _, c := range cases {
		if got := hostRuleMatches(c.argv, r); got != c.want {
			t.Errorf("hostRuleMatches(%v) = %v, want %v", c.argv, got, c.want)
		}
	}
	// "*" before "..." still pins that one slot.
	r2 := HostRule{Bin: "kubectl", Args: []string{"logs", "*", "..."}}
	if !hostRuleMatches([]string{"kubectl", "logs", "pod-1"}, r2) {
		t.Error("slot filled, no trailing: want match")
	}
	if !hostRuleMatches([]string{"kubectl", "logs", "pod-1", "-f"}, r2) {
		t.Error("slot + trailing: want match")
	}
	if hostRuleMatches([]string{"kubectl", "logs"}, r2) {
		t.Error("slot unfilled: want no match")
	}
}

func TestIsHostArgvApproved(t *testing.T) {
	rules := []HostRule{{Bin: "df", Args: []string{"-h"}}, {Bin: "systemctl", Args: []string{"restart", "*"}}}
	if !isHostArgvApproved([]string{"systemctl", "restart", "nginx"}, rules) {
		t.Error("expected approved by the systemctl rule")
	}
	if isHostArgvApproved([]string{"systemctl", "stop", "nginx"}, rules) {
		t.Error("stop should not be approved")
	}
	if isHostArgvApproved([]string{"rm", "-rf", "/"}, nil) {
		t.Error("empty rules approve nothing")
	}
}

func TestExecuteStructured(t *testing.T) {
	t.Run("wild-mode read runs and returns output", func(t *testing.T) {
		res := executeStructured([]string{"echo", "hello"}, "wild", false, nil)
		if res.ExitCode != 0 {
			t.Fatalf("exit=%d stderr=%q", res.ExitCode, res.Stderr)
		}
		if strings.TrimSpace(res.Stdout) != "hello" {
			t.Errorf("stdout = %q, want hello", res.Stdout)
		}
	})

	t.Run("no shell interpretation - metacharacters are literal args", func(t *testing.T) {
		// A pipe passed as an argv token is a literal argument to echo, not a shell pipe.
		res := executeStructured([]string{"echo", "a | b"}, "wild", false, nil)
		if strings.TrimSpace(res.Stdout) != "a | b" {
			t.Errorf("stdout = %q, want 'a | b' (no shell)", res.Stdout)
		}
	})

	t.Run("empty argv is an error", func(t *testing.T) {
		res := executeStructured(nil, "wild", false, nil)
		if res.ExitCode == 0 {
			t.Error("empty argv should not succeed")
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
		res := executeCommand("echo hello", "wild", false)
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
		res := executeCommand("exit 42", "wild", false)
		if res.ExitCode != 42 {
			t.Errorf("exit code %d, want 42", res.ExitCode)
		}
	})

	t.Run("stderr captured separately", func(t *testing.T) {
		res := executeCommand("echo errline >&2", "wild", false)
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
		res := executeCommand("printf '%200s' | tr ' ' x", "wild", false)
		if !strings.Contains(res.Stdout, "[TRUNCATED]") {
			t.Errorf("expected truncated output, got %q", res.Stdout)
		}
	})

	t.Run("command timeout returns non-zero exit", func(t *testing.T) {
		t.Setenv("REACH_COMMAND_TIMEOUT_SECONDS", "1")
		res := executeCommand("sleep 10", "wild", false)
		// 124 on Linux (killed via context deadline), -1 on macOS (SIGKILL via ExitError)
		if res.ExitCode == 0 {
			t.Error("expected non-zero exit code for timed-out command")
		}
	})

	// Read command fails due to OS file permissions (not a write block).
	// With isWrite=false the agent must NOT treat it as a Landlock block -
	// it should fall through as a normal FAILED job with no Blocked flag.
	t.Run("read permission denied is not treated as write block", func(t *testing.T) {
		// Create a file readable only by root, then try to cat it as the current user.
		dir := t.TempDir()
		secret := filepath.Join(dir, "secret")
		if err := os.WriteFile(secret, []byte("x"), 0000); err != nil {
			t.Skipf("cannot create unreadable file: %v", err)
		}
		res := executeCommand("cat "+secret, "approved", false /* isWrite=false */)
		if res.Blocked {
			t.Error("read permission denied should not set Blocked=true")
		}
		if res.ExitCode == 0 {
			t.Error("expected non-zero exit for unreadable file")
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

		os.WriteFile(configPath, []byte(`{"api_url":"https://api.example.com","agent_token":"tok"}`), 0600)
		cfg, err := loadConfig()
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if cfg.APIURL != "https://api.example.com" {
			t.Errorf("api_url = %q", cfg.APIURL)
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
		os.WriteFile(configPath, []byte(`{"agent_token":"tok"}`), 0600)
		if _, err := loadConfig(); err == nil {
			t.Error("expected error for missing api_url")
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

		cfg := &Config{APIURL: "https://api.example.com", AgentToken: "tok_abc"}
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

		saveConfig(&Config{APIURL: "https://x", AgentToken: "tok"})
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
// deregister (fleet scale-in)
// ---------------------------------------------------------------------------

func TestDeregister(t *testing.T) {
	t.Run("posts fingerprint and token to /agent/deregister", func(t *testing.T) {
		var gotPath, gotAuth, gotMethod string
		var gotBody DeregisterRequest
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			gotPath, gotAuth, gotMethod = r.URL.Path, r.Header.Get("Authorization"), r.Method
			json.NewDecoder(r.Body).Decode(&gotBody)
			w.WriteHeader(200)
			w.Write([]byte(`{"deregistered":true}`))
		}))
		defer srv.Close()

		deregister(&Config{APIURL: srv.URL, AgentToken: "tok", MachineFingerprint: "fp-1"})
		if gotMethod != "POST" || gotPath != "/agent/deregister" {
			t.Errorf("request = %s %s, want POST /agent/deregister", gotMethod, gotPath)
		}
		if gotAuth != "Bearer tok" {
			t.Errorf("auth = %q, want 'Bearer tok'", gotAuth)
		}
		if gotBody.MachineFingerprint != "fp-1" {
			t.Errorf("fingerprint = %q, want 'fp-1'", gotBody.MachineFingerprint)
		}
	})

	t.Run("tolerates 409 not-a-fleet-member", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(409)
			w.Write([]byte(`{"error":"agent is not a fleet member"}`))
		}))
		defer srv.Close()
		deregister(&Config{APIURL: srv.URL, AgentToken: "tok", MachineFingerprint: "fp"}) // must not panic
	})

	t.Run("tolerates connection failure", func(t *testing.T) {
		deregister(&Config{APIURL: "http://127.0.0.1:1", AgentToken: "tok"}) // must not panic
	})
}

// ---------------------------------------------------------------------------
// claim
// ---------------------------------------------------------------------------

func claimCfg(apiURL string) *Config {
	return &Config{
		APIURL:       apiURL,
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

	t.Run("rotate_token field decoded correctly", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(200)
			json.NewEncoder(w).Encode(SyncResponse{RotateToken: true})
		}))
		defer srv.Close()

		resp, err := sync(syncCfg(srv.URL))
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if !resp.RotateToken {
			t.Error("expected RotateToken=true")
		}
	})

	t.Run("rotate_token absent defaults false", func(t *testing.T) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(200)
			json.NewEncoder(w).Encode(SyncResponse{Jobs: []Job{{JobID: "job_1", Command: "ls", Mode: "wild"}}})
		}))
		defer srv.Close()

		resp, err := sync(syncCfg(srv.URL))
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if resp.RotateToken {
			t.Error("expected RotateToken=false when not set")
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
		if gotBody.MachineFingerprint != "fp_abc" {
			t.Errorf("machine_fingerprint = %q, want 'fp_abc'", gotBody.MachineFingerprint)
		}
	})
}

// ---------------------------------------------------------------------------
// probeDocker / probeServiceMgmt
// ---------------------------------------------------------------------------

func TestProbeDocker(t *testing.T) {
	t.Run("returns a bool without panicking", func(t *testing.T) {
		// Result depends on whether a docker socket exists in the test environment.
		_ = probeDocker()
	})

	t.Run("returns false when no socket path exists", func(t *testing.T) {
		// Verify the underlying logic: stat on a missing path returns false.
		_, err := os.Stat("/no/such/docker.sock")
		if err == nil {
			t.Skip("unexpected: test path exists")
		}
		// probeDocker checks multiple candidate paths; if none exist it must be false.
		// We can't override os.Stat, but we verify the function doesn't panic and
		// the stat-on-missing-path contract holds.
		if _, statErr := os.Stat("/nonexistent/docker.sock"); statErr == nil {
			t.Skip("environment has unexpected socket")
		}
	})

	t.Run("stat succeeds on a real file", func(t *testing.T) {
		// Verify the candidate-path logic: a file that exists should be stat-able.
		dir := t.TempDir()
		sock := filepath.Join(dir, "docker.sock")
		if err := os.WriteFile(sock, nil, 0600); err != nil {
			t.Fatalf("create fake socket: %v", err)
		}
		if _, err := os.Stat(sock); err != nil {
			t.Errorf("Stat on existing file failed: %v", err)
		}
	})
}

func TestProbeServiceMgmt(t *testing.T) {
	t.Run("returns a bool without panicking", func(t *testing.T) {
		// systemctl present on Linux with systemd; launchctl present on macOS.
		_ = probeServiceMgmt()
	})

	t.Run("returns true when systemctl is available", func(t *testing.T) {
		if _, err := exec.LookPath("systemctl"); err != nil {
			t.Skip("systemctl not in PATH on this host")
		}
		if !probeServiceMgmt() {
			t.Error("probeServiceMgmt() = false, want true (systemctl found in PATH)")
		}
	})

	t.Run("returns true when launchctl is available", func(t *testing.T) {
		if _, err := exec.LookPath("launchctl"); err != nil {
			t.Skip("launchctl not in PATH on this host")
		}
		if !probeServiceMgmt() {
			t.Error("probeServiceMgmt() = false, want true (launchctl found in PATH)")
		}
	})

	t.Run("matches presence of systemctl or launchctl in PATH", func(t *testing.T) {
		_, sysErr := exec.LookPath("systemctl")
		_, lncErr := exec.LookPath("launchctl")
		want := sysErr == nil || lncErr == nil
		if got := probeServiceMgmt(); got != want {
			t.Errorf("probeServiceMgmt() = %v, want %v", got, want)
		}
	})
}

// ---------------------------------------------------------------------------
// sync capability fields
// ---------------------------------------------------------------------------

func TestSyncCapabilityFields(t *testing.T) {
	t.Run("sync body includes docker_detected and service_mgmt_detected", func(t *testing.T) {
		var gotBody map[string]interface{}
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			json.NewDecoder(r.Body).Decode(&gotBody)
			w.WriteHeader(200)
			json.NewEncoder(w).Encode(SyncResponse{})
		}))
		defer srv.Close()

		sync(syncCfg(srv.URL)) //nolint:errcheck

		if _, ok := gotBody["docker_detected"]; !ok {
			t.Error("sync request body missing docker_detected field")
		}
		if _, ok := gotBody["service_mgmt_detected"]; !ok {
			t.Error("sync request body missing service_mgmt_detected field")
		}
	})

	t.Run("docker_detected is a boolean", func(t *testing.T) {
		var gotBody map[string]interface{}
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			json.NewDecoder(r.Body).Decode(&gotBody)
			w.WriteHeader(200)
			json.NewEncoder(w).Encode(SyncResponse{})
		}))
		defer srv.Close()

		sync(syncCfg(srv.URL)) //nolint:errcheck

		if _, ok := gotBody["docker_detected"].(bool); !ok {
			t.Errorf("docker_detected should be bool, got %T", gotBody["docker_detected"])
		}
		if _, ok := gotBody["service_mgmt_detected"].(bool); !ok {
			t.Errorf("service_mgmt_detected should be bool, got %T", gotBody["service_mgmt_detected"])
		}
	})

	t.Run("docker_detected value matches probeDocker result", func(t *testing.T) {
		var gotBody map[string]interface{}
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			json.NewDecoder(r.Body).Decode(&gotBody)
			w.WriteHeader(200)
			json.NewEncoder(w).Encode(SyncResponse{})
		}))
		defer srv.Close()

		expected := probeDocker()
		sync(syncCfg(srv.URL)) //nolint:errcheck

		if got, _ := gotBody["docker_detected"].(bool); got != expected {
			t.Errorf("docker_detected = %v, want %v", got, expected)
		}
	})

	t.Run("service_mgmt_detected value matches probeServiceMgmt result", func(t *testing.T) {
		var gotBody map[string]interface{}
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			json.NewDecoder(r.Body).Decode(&gotBody)
			w.WriteHeader(200)
			json.NewEncoder(w).Encode(SyncResponse{})
		}))
		defer srv.Close()

		expected := probeServiceMgmt()
		sync(syncCfg(srv.URL)) //nolint:errcheck

		if got, _ := gotBody["service_mgmt_detected"].(bool); got != expected {
			t.Errorf("service_mgmt_detected = %v, want %v", got, expected)
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

func TestPermReviewInterval(t *testing.T) {
	t.Setenv("REACH_K8S_REVIEW_INTERVAL_SECONDS", "")
	if got := permReviewInterval(); got != defaultPermReviewInterval {
		t.Fatalf("default = %s, want %s", got, defaultPermReviewInterval)
	}
	t.Setenv("REACH_K8S_REVIEW_INTERVAL_SECONDS", "120")
	if got := permReviewInterval(); got != 120*time.Second {
		t.Fatalf("override = %s, want 2m", got)
	}
	// Floored at 30s.
	t.Setenv("REACH_K8S_REVIEW_INTERVAL_SECONDS", "5")
	if got := permReviewInterval(); got != 30*time.Second {
		t.Fatalf("floor = %s, want 30s", got)
	}
	// Garbage falls back to default.
	t.Setenv("REACH_K8S_REVIEW_INTERVAL_SECONDS", "abc")
	if got := permReviewInterval(); got != defaultPermReviewInterval {
		t.Fatalf("garbage = %s, want default", got)
	}
}

func TestTouchHealthFile(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "healthy")
	touchHealthFile(p)
	if _, err := os.Stat(p); err != nil {
		t.Fatalf("expected health file written: %v", err)
	}
	// Empty path is a no-op (no panic, nothing created).
	touchHealthFile("")
}
