package main

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestInKubernetes(t *testing.T) {
	dir := t.TempDir()
	tok := filepath.Join(dir, "token")
	old := saTokenPath
	saTokenPath = tok
	defer func() { saTokenPath = old }()

	// No KUBERNETES_SERVICE_HOST -> not in k8s.
	t.Setenv("KUBERNETES_SERVICE_HOST", "")
	if inKubernetes() {
		t.Fatal("expected false without KUBERNETES_SERVICE_HOST")
	}

	// Host set but no token file -> not in k8s.
	t.Setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
	if inKubernetes() {
		t.Fatal("expected false without token file")
	}

	// Host set and token file present -> in k8s.
	if err := os.WriteFile(tok, []byte("t"), 0600); err != nil {
		t.Fatal(err)
	}
	if !inKubernetes() {
		t.Fatal("expected true with host + token file")
	}
}

func TestClusterID(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/namespaces/kube-system" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		if r.Header.Get("Authorization") != "Bearer test-token" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"metadata": map[string]any{"name": "kube-system", "uid": "11111111-2222-3333-4444-555555555555"},
		})
	}))
	defer srv.Close()

	c := &k8sClient{host: srv.URL, token: "test-token", http: srv.Client()}
	id, err := c.clusterID()
	if err != nil {
		t.Fatalf("clusterID: %v", err)
	}
	if id != "11111111-2222-3333-4444-555555555555" {
		t.Fatalf("unexpected cluster id: %q", id)
	}
}

func TestClusterIDBadAuth(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"message":"forbidden"}`))
	}))
	defer srv.Close()

	c := &k8sClient{host: srv.URL, token: "wrong", http: srv.Client()}
	if _, err := c.clusterID(); err == nil {
		t.Fatal("expected error on 403")
	}
}

func TestClusterIDEmptyUID(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"metadata": map[string]any{"uid": ""}})
	}))
	defer srv.Close()

	c := &k8sClient{host: srv.URL, token: "t", http: srv.Client()}
	if _, err := c.clusterID(); err == nil {
		t.Fatal("expected error on empty uid")
	}
}

func TestNewK8sClientNotInCluster(t *testing.T) {
	t.Setenv("KUBERNETES_SERVICE_HOST", "")
	if _, err := newK8sClient(); err == nil {
		t.Fatal("expected error when not in cluster")
	}
}

func TestClusterFingerprint(t *testing.T) {
	a := clusterFingerprint("uid-1")
	b := clusterFingerprint("uid-1")
	c := clusterFingerprint("uid-2")
	if a != b {
		t.Fatal("same cluster id must yield the same fingerprint (replicas must agree)")
	}
	if a == c {
		t.Fatal("different cluster ids must yield different fingerprints")
	}
	if len(a) != len("fp_")+32 || a[:3] != "fp_" {
		t.Fatalf("unexpected fingerprint format: %q", a)
	}
}

func TestK8sConfigFromEnv(t *testing.T) {
	dir := t.TempDir()
	oldPath := configPath
	configPath = filepath.Join(dir, "config.json")
	defer func() { configPath = oldPath }()

	// Missing required env -> error. Credential-only: no agent_id is needed.
	t.Setenv("REACH_API_URL", "")
	if _, err := k8sConfig(); err == nil {
		t.Fatal("expected error without REACH_API_URL")
	}

	// With env set -> populated, no token yet.
	t.Setenv("REACH_API_URL", "https://reach.example.com")
	t.Setenv("REACH_INSTALL_TOKEN", "install_xyz")
	cfg, err := k8sConfig()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.APIURL != "https://reach.example.com" || cfg.InstallToken != "install_xyz" {
		t.Fatalf("config not populated from env: %+v", cfg)
	}
	if cfg.AgentToken != "" {
		t.Fatal("expected no agent token before claim")
	}

	// The token is never read from local disk in k8s mode (the Secret is the
	// sole store): a stray config file at configPath must be ignored.
	persisted := `{"api_url":"x","agent_id":"y","agent_token":"agent_persisted","token_issued_at":"2026-01-01T00:00:00Z"}`
	if err := os.WriteFile(configPath, []byte(persisted), 0600); err != nil {
		t.Fatal(err)
	}
	cfg, err = k8sConfig()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.AgentToken != "" {
		t.Fatalf("k8sConfig must not read a token from disk, got %q", cfg.AgentToken)
	}
}

func TestSecretTokenStoreCreateThenLoad(t *testing.T) {
	// A tiny in-memory secrets fake: GET 404 until created, then returns base64 data.
	var stored map[string]string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			if stored == nil {
				w.WriteHeader(http.StatusNotFound)
				return
			}
			data := map[string]string{}
			for k, v := range stored {
				data[k] = base64.StdEncoding.EncodeToString([]byte(v))
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"data": data})
		case http.MethodPost:
			var sec struct {
				StringData map[string]string `json:"stringData"`
			}
			_ = json.NewDecoder(r.Body).Decode(&sec)
			stored = sec.StringData
			w.WriteHeader(http.StatusCreated)
			_, _ = w.Write([]byte("{}"))
		case http.MethodPatch:
			if stored == nil {
				w.WriteHeader(http.StatusNotFound)
				return
			}
			var p struct {
				StringData map[string]string `json:"stringData"`
			}
			_ = json.NewDecoder(r.Body).Decode(&p)
			for k, v := range p.StringData {
				stored[k] = v
			}
			_, _ = w.Write([]byte("{}"))
		}
	}))
	defer srv.Close()

	s := &secretTokenStore{
		client:    &k8sClient{host: srv.URL, token: "t", http: srv.Client()},
		namespace: "reach",
		name:      "reach-agent-token",
	}

	// Empty before anything is stored.
	tok, _, err := s.load()
	if err != nil {
		t.Fatalf("load (empty): %v", err)
	}
	if tok != "" {
		t.Fatalf("expected empty token before save, got %q", tok)
	}

	// First save creates the Secret.
	if err := s.save("agent_first", "2026-06-24T00:00:00Z"); err != nil {
		t.Fatalf("save (create): %v", err)
	}
	tok, issued, err := s.load()
	if err != nil {
		t.Fatalf("load after create: %v", err)
	}
	if tok != "agent_first" || issued != "2026-06-24T00:00:00Z" {
		t.Fatalf("unexpected loaded token/issued: %q / %q", tok, issued)
	}

	// Second save (rotation) patches the existing Secret.
	if err := s.save("agent_rotated", "2026-07-24T00:00:00Z"); err != nil {
		t.Fatalf("save (patch): %v", err)
	}
	tok, _, _ = s.load()
	if tok != "agent_rotated" {
		t.Fatalf("expected rotated token, got %q", tok)
	}
}

func TestPodNamespace(t *testing.T) {
	dir := t.TempDir()
	nsFile := filepath.Join(dir, "namespace")
	old := saNamespacePath
	saNamespacePath = nsFile
	defer func() { saNamespacePath = old }()

	// From the projected file.
	if err := os.WriteFile(nsFile, []byte("reach\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if ns := podNamespace(); ns != "reach" {
		t.Fatalf("expected namespace from file, got %q", ns)
	}

	// Fallback to env when the file is absent.
	saNamespacePath = filepath.Join(dir, "missing")
	t.Setenv("REACH_NAMESPACE", "fallback-ns")
	if ns := podNamespace(); ns != "fallback-ns" {
		t.Fatalf("expected env fallback, got %q", ns)
	}
}
