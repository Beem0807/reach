package main

// Kubernetes mode: when the agent runs inside a pod it derives its identity from
// the cluster itself rather than from /etc/machine-id. The cluster id is the
// kube-system namespace UID - created once at cluster bootstrap and stable for
// the cluster's lifetime - so every replica computes the same agent identity.
//
// Implemented with the standard library only (no client-go) to keep the agent
// binary small: in-cluster config is just a token file, a CA file, and the
// KUBERNETES_SERVICE_HOST/PORT env vars the kubelet injects.

import (
	"bytes"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"sort"
	"strings"
	"time"
)

// Overridable in tests.
var (
	saTokenPath     = "/var/run/secrets/kubernetes.io/serviceaccount/token"
	saCACertPath    = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
	saNamespacePath = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
)

// inKubernetes reports whether the agent is running inside a Kubernetes pod.
func inKubernetes() bool {
	if os.Getenv("KUBERNETES_SERVICE_HOST") == "" {
		return false
	}
	if _, err := os.Stat(saTokenPath); err != nil {
		return false
	}
	return true
}

// k8sClient is a minimal in-cluster Kubernetes API client.
type k8sClient struct {
	host  string // https://host:port
	token string
	http  *http.Client
}

// newK8sClient builds a client from the in-cluster ServiceAccount credentials.
func newK8sClient() (*k8sClient, error) {
	host := os.Getenv("KUBERNETES_SERVICE_HOST")
	port := os.Getenv("KUBERNETES_SERVICE_PORT")
	if host == "" || port == "" {
		return nil, fmt.Errorf("not running in kubernetes (KUBERNETES_SERVICE_HOST/PORT unset)")
	}
	token, err := os.ReadFile(saTokenPath)
	if err != nil {
		return nil, fmt.Errorf("read serviceaccount token: %w", err)
	}
	caPEM, err := os.ReadFile(saCACertPath)
	if err != nil {
		return nil, fmt.Errorf("read serviceaccount ca: %w", err)
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(caPEM) {
		return nil, fmt.Errorf("parse serviceaccount ca: no certificates found")
	}
	return &k8sClient{
		host:  fmt.Sprintf("https://%s:%s", host, port),
		token: strings.TrimSpace(string(token)),
		http: &http.Client{
			Timeout:   10 * time.Second,
			Transport: &http.Transport{TLSClientConfig: &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12}},
		},
	}, nil
}

// namespace returns the pod's own namespace (from the projected SA volume).
func podNamespace() string {
	if data, err := os.ReadFile(saNamespacePath); err == nil {
		return strings.TrimSpace(string(data))
	}
	return os.Getenv("REACH_NAMESPACE")
}

// listNamespaceNames enumerates every namespace in the cluster, so the agent can
// review its effective RBAC everywhere - catching grants bound in namespaces no
// one configured. Requires the read-only `list namespaces` grant (chart RBAC).
func (c *k8sClient) listNamespaceNames() ([]string, error) {
	var resp struct {
		Items []struct {
			Metadata struct {
				Name string `json:"name"`
			} `json:"metadata"`
		} `json:"items"`
	}
	if err := c.getJSON("/api/v1/namespaces", &resp); err != nil {
		return nil, err
	}
	names := make([]string, 0, len(resp.Items))
	for _, it := range resp.Items {
		if it.Metadata.Name != "" {
			names = append(names, it.Metadata.Name)
		}
	}
	return names, nil
}

// clusterID returns the kube-system namespace UID - the stable cluster identity.
func (c *k8sClient) clusterID() (string, error) {
	var ns struct {
		Metadata struct {
			UID string `json:"uid"`
		} `json:"metadata"`
	}
	if err := c.getJSON("/api/v1/namespaces/kube-system", &ns); err != nil {
		return "", fmt.Errorf("fetch cluster id: %w", err)
	}
	if ns.Metadata.UID == "" {
		return "", fmt.Errorf("fetch cluster id: kube-system uid is empty")
	}
	return ns.Metadata.UID, nil
}

// ---------------------------------------------------------------------------
// Effective permissions (self-review)
//
// The agent introspects its OWN RBAC via SelfSubjectRulesReview - the same API
// `kubectl auth can-i --list` uses. Self-review is always allowed for any
// authenticated identity, so this needs no extra grant. We enumerate the full
// rule set rather than probing fixed verbs, so anything granted is visible (even
// permissions we'd never have thought to ask about). The backend stores the raw
// rules and the UI turns them into a readable capability view.
// ---------------------------------------------------------------------------

type k8sResourceRule struct {
	Verbs         []string `json:"verbs"`
	APIGroups     []string `json:"api_groups,omitempty"`
	Resources     []string `json:"resources,omitempty"`
	ResourceNames []string `json:"resource_names,omitempty"`
}

type k8sNonResourceRule struct {
	Verbs           []string `json:"verbs"`
	NonResourceURLs []string `json:"non_resource_urls,omitempty"`
}

// k8sNamespaceReview is the raw SelfSubjectRulesReview result for one namespace.
type k8sNamespaceReview struct {
	Namespace        string
	ResourceRules    []k8sResourceRule
	NonResourceRules []k8sNonResourceRule
	Incomplete       bool
}

// K8sNamespacePerms reports the rules effective ONLY in a namespace - the extra
// grants beyond the cluster-wide baseline.
type K8sNamespacePerms struct {
	Namespace     string            `json:"namespace"`
	ResourceRules []k8sResourceRule `json:"resource_rules"`
}

// K8sPermissions is the agent's effective RBAC across the cluster, deduped:
// cluster-wide rules (effective in every namespace) are reported once in
// ClusterWide; each namespace lists only the rules bound there beyond that
// baseline. Everything is canonicalized and sorted so the hash is stable (the
// same RBAC always yields the same hash, regardless of API ordering).
type K8sPermissions struct {
	ClusterWide      []k8sResourceRule    `json:"cluster_wide"`
	NonResourceRules []k8sNonResourceRule `json:"non_resource_rules,omitempty"`
	Namespaces       []K8sNamespacePerms  `json:"namespaces,omitempty"`
	// Incomplete: a namespace review could not be fully evaluated by the API.
	// Truncated: the snapshot exceeded the size cap and some entries were dropped.
	// They are distinct so the UI can explain which happened.
	Incomplete bool `json:"incomplete"`
	Truncated  bool `json:"truncated,omitempty"`
	// Hash lets the backend dedupe/detect drift without re-deriving it.
	Hash string `json:"hash"`
}

// selfSubjectRules enumerates the agent's effective permissions in one namespace.
func (c *k8sClient) selfSubjectRules(namespace string) (*k8sNamespaceReview, error) {
	reqBody, _ := json.Marshal(map[string]any{
		"apiVersion": "authorization.k8s.io/v1",
		"kind":       "SelfSubjectRulesReview",
		"spec":       map[string]string{"namespace": namespace},
	})
	status, body, err := c.request(
		http.MethodPost,
		"/apis/authorization.k8s.io/v1/selfsubjectrulesreviews",
		"application/json", reqBody)
	if err != nil {
		return nil, err
	}
	if status != http.StatusCreated && status != http.StatusOK {
		return nil, fmt.Errorf("self subject rules review: status %d: %s", status, strings.TrimSpace(string(body)))
	}
	var resp struct {
		Status struct {
			ResourceRules []struct {
				Verbs         []string `json:"verbs"`
				APIGroups     []string `json:"apiGroups"`
				Resources     []string `json:"resources"`
				ResourceNames []string `json:"resourceNames"`
			} `json:"resourceRules"`
			NonResourceRules []struct {
				Verbs           []string `json:"verbs"`
				NonResourceURLs []string `json:"nonResourceURLs"`
			} `json:"nonResourceRules"`
			Incomplete bool `json:"incomplete"`
		} `json:"status"`
	}
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, err
	}
	rv := &k8sNamespaceReview{Namespace: namespace, Incomplete: resp.Status.Incomplete}
	for _, r := range resp.Status.ResourceRules {
		rv.ResourceRules = append(rv.ResourceRules, k8sResourceRule{
			Verbs: r.Verbs, APIGroups: r.APIGroups, Resources: r.Resources, ResourceNames: r.ResourceNames,
		})
	}
	for _, r := range resp.Status.NonResourceRules {
		rv.NonResourceRules = append(rv.NonResourceRules, k8sNonResourceRule{
			Verbs: r.Verbs, NonResourceURLs: r.NonResourceURLs,
		})
	}
	return rv, nil
}

// selfSubjectRulesMulti reviews each namespace (deduped) and assembles a deduped,
// sorted snapshot. A failure to review one namespace is non-fatal - it is marked
// incomplete rather than dropping the whole snapshot.
func (c *k8sClient) selfSubjectRulesMulti(namespaces []string) (*K8sPermissions, error) {
	seen := map[string]bool{}
	var reviews []*k8sNamespaceReview
	for _, ns := range namespaces {
		ns = strings.TrimSpace(ns)
		if ns == "" || seen[ns] {
			continue
		}
		seen[ns] = true
		rv, err := c.selfSubjectRules(ns)
		if err != nil {
			rv = &k8sNamespaceReview{Namespace: ns, Incomplete: true}
		}
		reviews = append(reviews, rv)
	}
	if len(reviews) == 0 {
		return nil, fmt.Errorf("no namespaces to review")
	}
	return assemblePermissions(reviews), nil
}

// assemblePermissions dedupes the cluster-wide rules (present in every reviewed
// namespace) out of the per-namespace lists, and canonicalizes + sorts everything
// so the same effective RBAC always produces the same bytes (and hash).
func assemblePermissions(reviews []*k8sNamespaceReview) *K8sPermissions {
	resKey := func(r k8sResourceRule) string {
		return strings.Join(sortedStrings(r.APIGroups), ",") + "\x1f" +
			strings.Join(sortedStrings(r.Resources), ",") + "\x1f" +
			strings.Join(sortedStrings(r.Verbs), ",") + "\x1f" +
			strings.Join(sortedStrings(r.ResourceNames), ",")
	}
	canonRes := func(r k8sResourceRule) k8sResourceRule {
		return k8sResourceRule{
			Verbs: sortedStrings(r.Verbs), APIGroups: sortedStrings(r.APIGroups),
			Resources: sortedStrings(r.Resources), ResourceNames: sortedStrings(r.ResourceNames),
		}
	}

	n := len(reviews)
	counts := map[string]int{}
	repr := map[string]k8sResourceRule{}
	incomplete := false
	for _, rv := range reviews {
		if rv.Incomplete {
			incomplete = true
		}
		nsSeen := map[string]bool{}
		for _, r := range rv.ResourceRules {
			k := resKey(r)
			if !nsSeen[k] {
				nsSeen[k] = true
				counts[k]++
				repr[k] = canonRes(r)
			}
		}
	}

	// Rules present in every namespace are cluster-wide; the rest are deltas.
	baseline := map[string]bool{}
	var clusterWide []k8sResourceRule
	for k, c := range counts {
		if c == n {
			baseline[k] = true
			clusterWide = append(clusterWide, repr[k])
		}
	}
	sortRules(clusterWide, resKey)

	var nsPerms []K8sNamespacePerms
	for _, rv := range reviews {
		var delta []k8sResourceRule
		added := map[string]bool{}
		for _, r := range rv.ResourceRules {
			k := resKey(r)
			if baseline[k] || added[k] {
				continue
			}
			added[k] = true
			delta = append(delta, canonRes(r))
		}
		if len(delta) > 0 {
			sortRules(delta, resKey)
			nsPerms = append(nsPerms, K8sNamespacePerms{Namespace: rv.Namespace, ResourceRules: delta})
		}
	}
	sort.Slice(nsPerms, func(i, j int) bool { return nsPerms[i].Namespace < nsPerms[j].Namespace })

	// Non-resource rules are cluster-scoped (same in every review): keep the
	// intersection as the baseline.
	nrKey := func(r k8sNonResourceRule) string {
		return strings.Join(sortedStrings(r.NonResourceURLs), ",") + "\x1f" + strings.Join(sortedStrings(r.Verbs), ",")
	}
	nrCounts := map[string]int{}
	nrRepr := map[string]k8sNonResourceRule{}
	for _, rv := range reviews {
		nsSeen := map[string]bool{}
		for _, r := range rv.NonResourceRules {
			k := nrKey(r)
			if !nsSeen[k] {
				nsSeen[k] = true
				nrCounts[k]++
				nrRepr[k] = k8sNonResourceRule{Verbs: sortedStrings(r.Verbs), NonResourceURLs: sortedStrings(r.NonResourceURLs)}
			}
		}
	}
	var nonRes []k8sNonResourceRule
	for k, c := range nrCounts {
		if c == n {
			nonRes = append(nonRes, nrRepr[k])
		}
	}
	sort.Slice(nonRes, func(i, j int) bool { return nrKey(nonRes[i]) < nrKey(nonRes[j]) })

	p := &K8sPermissions{ClusterWide: clusterWide, NonResourceRules: nonRes, Namespaces: nsPerms, Incomplete: incomplete}
	// Hash the FULL effective RBAC (before any capping) so drift reflects the real
	// permission set on every backend - even a change in a rule the payload cap
	// would later drop still flips the hash. Storage-side truncation (DynamoDB's
	// 400KB item limit) is the backend's job and never affects the hash.
	p.Hash = p.fingerprint()
	capPermissions(p, maxPermBytes)
	return p
}

// maxPermBytes is a payload sanity cap on the serialized snapshot the agent sends
// (realistic RBAC is a few KB - cluster-wide rules dedupe away, so size is driven
// by *distinct* per-namespace grants). It bounds the sync request body; it is not
// the storage limit. Postgres stores the snapshot in full; the DynamoDB backend
// truncates on write to fit its 400KB item limit. The hash is computed over the
// full snapshot above, so truncation only affects display/diff fidelity.
const maxPermBytes = 1024 * 1024

// capPermissions ensures the snapshot serializes under maxBytes. If it would
// exceed, it drops per-namespace deltas (then, as a last resort, cluster-wide
// rules) and marks the snapshot truncated - so the operator sees a "partial"
// signal rather than a silently failed report. Deterministic: same input, same
// truncation.
func capPermissions(p *K8sPermissions, maxBytes int) {
	jsonLen := func() int {
		b, _ := json.Marshal(p)
		return len(b)
	}
	if jsonLen() <= maxBytes {
		return
	}
	p.Truncated = true
	for len(p.Namespaces) > 0 && jsonLen() > maxBytes {
		p.Namespaces = p.Namespaces[:len(p.Namespaces)-1]
	}
	for len(p.ClusterWide) > 0 && jsonLen() > maxBytes {
		p.ClusterWide = p.ClusterWide[:len(p.ClusterWide)-1]
	}
}

func sortedStrings(s []string) []string {
	if len(s) <= 1 {
		return s
	}
	out := append([]string(nil), s...)
	sort.Strings(out)
	return out
}

func sortRules(rules []k8sResourceRule, key func(k8sResourceRule) string) {
	sort.Slice(rules, func(i, j int) bool { return key(rules[i]) < key(rules[j]) })
}

// fingerprint is a stable hash of the rule set, used by the backend to dedupe
// snapshots and detect drift against the operator-acknowledged version.
func (p *K8sPermissions) fingerprint() string {
	clone := *p
	clone.Hash = ""
	data, _ := json.Marshal(clone)
	h := sha256.Sum256(data)
	return hex.EncodeToString(h[:])
}

// clusterFingerprint derives the agent's machine fingerprint from the cluster id
// alone - no install_id or /etc/machine-id - so every replica in the cluster
// computes the same value and presents one identity to the backend.
func clusterFingerprint(clusterID string) string {
	h := sha256.Sum256([]byte("k8s:" + clusterID))
	return "fp_" + hex.EncodeToString(h[:])[:32]
}

// k8sConfig builds the agent Config from the environment (the chart injects the
// bootstrap from a Secret). The agent token is not read from local disk - in
// Kubernetes the managed Secret is the sole store and run() loads the token from
// it. Every other field is re-derived from env/cluster on each start.
func k8sConfig() (*Config, error) {
	cfg := &Config{
		APIURL:       strings.TrimSpace(os.Getenv("REACH_API_URL")),
		InstallToken: strings.TrimSpace(os.Getenv("REACH_INSTALL_TOKEN")),
	}
	if cfg.APIURL == "" {
		return nil, fmt.Errorf("REACH_API_URL is required in kubernetes mode")
	}
	return cfg, nil
}

// request performs an authenticated call to the API server and returns the
// status code and (size-limited) response body. contentType may be empty.
func (c *k8sClient) request(method, path, contentType string, body []byte) (int, []byte, error) {
	var rdr io.Reader
	if body != nil {
		rdr = bytes.NewReader(body)
	}
	req, err := http.NewRequest(method, c.host+path, rdr)
	if err != nil {
		return 0, nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	req.Header.Set("Accept", "application/json")
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	return resp.StatusCode, data, nil
}

// getJSON performs an authenticated GET and decodes the JSON response.
func (c *k8sClient) getJSON(path string, out interface{}) error {
	status, body, err := c.request(http.MethodGet, path, "", nil)
	if err != nil {
		return err
	}
	if status != http.StatusOK {
		return fmt.Errorf("GET %s: status %d: %s", path, status, strings.TrimSpace(string(body)))
	}
	return json.Unmarshal(body, out)
}

// ---------------------------------------------------------------------------
// Shared agent-token Secret
//
// The agent claims once and stores its agent_token in a Kubernetes Secret it
// manages via the API (mounted Secrets are read-only, so it cannot just write a
// file). The token is therefore shared across all replicas and survives pod
// restarts and the 30-day rotation - independent of replica count.
// ---------------------------------------------------------------------------

const (
	secretTokenKey  = "agent-token"
	secretIssuedKey = "token-issued-at"
)

// k8sTokenStore, when set (k8s mode with REACH_K8S_TOKEN_SECRET), mirrors the
// agent token into a Secret on every saveConfig. nil outside the cluster.
var k8sTokenStore *secretTokenStore

type secretTokenStore struct {
	client    *k8sClient
	namespace string
	name      string
}

func (s *secretTokenStore) path() string {
	return fmt.Sprintf("/api/v1/namespaces/%s/secrets/%s", s.namespace, s.name)
}

// load returns the shared token (and its issued-at) from the Secret, or empty
// strings if the Secret does not exist yet.
func (s *secretTokenStore) load() (token, issuedAt string, err error) {
	status, body, err := s.client.request(http.MethodGet, s.path(), "", nil)
	if err != nil {
		return "", "", err
	}
	if status == http.StatusNotFound {
		return "", "", nil
	}
	if status != http.StatusOK {
		return "", "", fmt.Errorf("get secret %s: status %d: %s", s.name, status, strings.TrimSpace(string(body)))
	}
	var sec struct {
		Data map[string]string `json:"data"`
	}
	if err := json.Unmarshal(body, &sec); err != nil {
		return "", "", err
	}
	return decodeSecretValue(sec.Data[secretTokenKey]), decodeSecretValue(sec.Data[secretIssuedKey]), nil
}

// save writes the token into the Secret, creating it if it does not yet exist.
func (s *secretTokenStore) save(token, issuedAt string) error {
	merge, _ := json.Marshal(map[string]any{
		"stringData": map[string]string{secretTokenKey: token, secretIssuedKey: issuedAt},
	})
	status, body, err := s.client.request(
		http.MethodPatch, s.path(), "application/merge-patch+json", merge)
	if err != nil {
		return err
	}
	if status == http.StatusOK {
		return nil
	}
	if status != http.StatusNotFound {
		return fmt.Errorf("patch secret %s: status %d: %s", s.name, status, strings.TrimSpace(string(body)))
	}
	// Secret does not exist yet - create it.
	create, _ := json.Marshal(map[string]any{
		"apiVersion": "v1",
		"kind":       "Secret",
		"metadata":   map[string]string{"name": s.name},
		"type":       "Opaque",
		"stringData": map[string]string{secretTokenKey: token, secretIssuedKey: issuedAt},
	})
	cPath := fmt.Sprintf("/api/v1/namespaces/%s/secrets", s.namespace)
	status, body, err = s.client.request(http.MethodPost, cPath, "application/json", create)
	if err != nil {
		return err
	}
	if status == http.StatusConflict {
		// Lost a create race with another replica; the Secret now exists - patch it.
		status, body, err = s.client.request(
			http.MethodPatch, s.path(), "application/merge-patch+json", merge)
		if err != nil {
			return err
		}
	}
	if status != http.StatusOK && status != http.StatusCreated {
		return fmt.Errorf("create secret %s: status %d: %s", s.name, status, strings.TrimSpace(string(body)))
	}
	return nil
}

// decodeSecretValue base64-decodes a Secret .data value, tolerating values that
// are already plain text (e.g. from .stringData round-trips in fakes).
func decodeSecretValue(v string) string {
	if v == "" {
		return ""
	}
	if dec, err := base64.StdEncoding.DecodeString(v); err == nil {
		return string(dec)
	}
	return v
}
