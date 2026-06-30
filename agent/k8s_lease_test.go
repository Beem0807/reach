package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// leaseFake is a minimal in-memory Lease endpoint for one named lease.
type leaseFake struct {
	lease *leaseObject
	rv    int
}

func (f *leaseFake) handler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			if f.lease == nil {
				w.WriteHeader(http.StatusNotFound)
				return
			}
			_ = json.NewEncoder(w).Encode(f.lease)
		case http.MethodPost:
			var l leaseObject
			_ = json.NewDecoder(r.Body).Decode(&l)
			if f.lease != nil {
				w.WriteHeader(http.StatusConflict)
				return
			}
			f.rv++
			l.Metadata.ResourceVersion = "1"
			f.lease = &l
			w.WriteHeader(http.StatusCreated)
			_ = json.NewEncoder(w).Encode(l)
		case http.MethodPut:
			var l leaseObject
			_ = json.NewDecoder(r.Body).Decode(&l)
			f.rv++
			f.lease = &l
			_ = json.NewEncoder(w).Encode(l)
		}
	}
}

func newElector(url, identity string) *leaseElector {
	return &leaseElector{
		client:    &k8sClient{host: url, token: "t", http: http.DefaultClient},
		namespace: "reach",
		name:      "reach-agent",
		identity:  identity,
	}
}

func TestLeaseAcquireWhenAbsent(t *testing.T) {
	f := &leaseFake{}
	srv := httptest.NewServer(f.handler())
	defer srv.Close()

	e := newElector(srv.URL, "pod-a")
	leading, err := e.tryAcquireOrRenew(time.Now().UTC())
	if err != nil {
		t.Fatal(err)
	}
	if !leading {
		t.Fatal("expected to acquire a non-existent lease")
	}
	if f.lease == nil || f.lease.Spec.HolderIdentity != "pod-a" {
		t.Fatalf("lease not held by pod-a: %+v", f.lease)
	}
}

func TestLeaseRenewWhenHeld(t *testing.T) {
	now := time.Now().UTC()
	f := &leaseFake{lease: &leaseObject{}}
	f.lease.Spec.HolderIdentity = "pod-a"
	f.lease.Spec.LeaseDurationSeconds = leaseDurationSeconds
	f.lease.Spec.RenewTime = now.Add(-5 * time.Second).Format(microTimeFormat)
	f.lease.Spec.LeaseTransitions = 2
	srv := httptest.NewServer(f.handler())
	defer srv.Close()

	e := newElector(srv.URL, "pod-a")
	leading, err := e.tryAcquireOrRenew(now)
	if err != nil {
		t.Fatal(err)
	}
	if !leading {
		t.Fatal("holder should renew and stay leader")
	}
	// Renewing must not count as a leadership transition.
	if f.lease.Spec.LeaseTransitions != 2 {
		t.Fatalf("renew should not bump transitions, got %d", f.lease.Spec.LeaseTransitions)
	}
}

func TestLeaseStandbyWhenHealthyHolder(t *testing.T) {
	now := time.Now().UTC()
	f := &leaseFake{lease: &leaseObject{}}
	f.lease.Spec.HolderIdentity = "pod-a"
	f.lease.Spec.LeaseDurationSeconds = leaseDurationSeconds
	f.lease.Spec.RenewTime = now.Format(microTimeFormat) // fresh
	srv := httptest.NewServer(f.handler())
	defer srv.Close()

	e := newElector(srv.URL, "pod-b")
	leading, err := e.tryAcquireOrRenew(now)
	if err != nil {
		t.Fatal(err)
	}
	if leading {
		t.Fatal("must not steal a healthy holder's lease")
	}
	if f.lease.Spec.HolderIdentity != "pod-a" {
		t.Fatal("holder should be unchanged")
	}
}

func TestLeaseTakeoverWhenExpired(t *testing.T) {
	now := time.Now().UTC()
	f := &leaseFake{lease: &leaseObject{}}
	f.lease.Spec.HolderIdentity = "pod-a"
	f.lease.Spec.LeaseDurationSeconds = leaseDurationSeconds
	f.lease.Spec.RenewTime = now.Add(-2 * leaseDurationSeconds * time.Second).Format(microTimeFormat)
	f.lease.Spec.LeaseTransitions = 1
	srv := httptest.NewServer(f.handler())
	defer srv.Close()

	e := newElector(srv.URL, "pod-b")
	leading, err := e.tryAcquireOrRenew(now)
	if err != nil {
		t.Fatal(err)
	}
	if !leading {
		t.Fatal("expired lease should be taken over")
	}
	if f.lease.Spec.HolderIdentity != "pod-b" {
		t.Fatalf("expected pod-b to take over, holder=%s", f.lease.Spec.HolderIdentity)
	}
	if f.lease.Spec.LeaseTransitions != 2 {
		t.Fatalf("takeover should bump transitions to 2, got %d", f.lease.Spec.LeaseTransitions)
	}
}

func TestSelfSubjectRules(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/apis/authorization.k8s.io/v1/selfsubjectrulesreviews" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"status":{"incomplete":false,
			"resourceRules":[
				{"verbs":["get","list","watch"],"apiGroups":[""],"resources":["pods"]},
				{"verbs":["*"],"apiGroups":["apps"],"resources":["deployments"]}],
			"nonResourceRules":[{"verbs":["get"],"nonResourceURLs":["/healthz"]}]}}`))
	}))
	defer srv.Close()

	c := &k8sClient{host: srv.URL, token: "t", http: srv.Client()}

	// Single-namespace review.
	ns, err := c.selfSubjectRules("team-a")
	if err != nil {
		t.Fatalf("selfSubjectRules: %v", err)
	}
	if ns.Namespace != "team-a" || ns.Incomplete {
		t.Fatalf("unexpected meta: %+v", ns)
	}
	if len(ns.ResourceRules) != 2 || len(ns.NonResourceRules) != 1 {
		t.Fatalf("unexpected rule counts: %+v", ns)
	}

	// Multi: this fake returns identical rules for every namespace, so they all
	// collapse into the cluster-wide baseline with no per-namespace deltas.
	perms, err := c.selfSubjectRulesMulti([]string{"team-a", "team-b", "team-a", ""})
	if err != nil {
		t.Fatalf("selfSubjectRulesMulti: %v", err)
	}
	if len(perms.ClusterWide) != 2 {
		t.Fatalf("expected 2 cluster-wide rules, got %d", len(perms.ClusterWide))
	}
	if len(perms.Namespaces) != 0 {
		t.Fatalf("identical namespaces should produce no deltas, got %d", len(perms.Namespaces))
	}
	if perms.Hash == "" || perms.Hash != perms.fingerprint() {
		t.Fatal("expected a stable content-derived hash")
	}
}

func TestAssemblePermissions(t *testing.T) {
	base := k8sResourceRule{Verbs: []string{"get", "list"}, APIGroups: []string{""}, Resources: []string{"pods"}}
	extra := k8sResourceRule{Verbs: []string{"delete"}, APIGroups: []string{"apps"}, Resources: []string{"deployments"}}

	// team-a: just the cluster-wide base. team-b: base + an extra grant.
	p := assemblePermissions([]*k8sNamespaceReview{
		{Namespace: "team-a", ResourceRules: []k8sResourceRule{base}},
		{Namespace: "team-b", ResourceRules: []k8sResourceRule{base, extra}},
	})
	if len(p.ClusterWide) != 1 {
		t.Fatalf("base should be cluster-wide once, got %d", len(p.ClusterWide))
	}
	if len(p.Namespaces) != 1 || p.Namespaces[0].Namespace != "team-b" || len(p.Namespaces[0].ResourceRules) != 1 {
		t.Fatalf("expected only team-b's extra rule as a delta: %+v", p.Namespaces)
	}

	// #1: the hash is stable across namespace order AND verb order within a rule.
	p2 := assemblePermissions([]*k8sNamespaceReview{
		{Namespace: "team-b", ResourceRules: []k8sResourceRule{
			extra,
			{Verbs: []string{"list", "get"}, APIGroups: []string{""}, Resources: []string{"pods"}}, // reordered verbs
		}},
		{Namespace: "team-a", ResourceRules: []k8sResourceRule{base}},
	})
	if p2.Hash != p.Hash {
		t.Fatalf("hash must be stable across ordering: %s != %s", p2.Hash, p.Hash)
	}
}

func TestCapPermissions(t *testing.T) {
	mkNS := func(name string) K8sNamespacePerms {
		var rules []k8sResourceRule
		for i := 0; i < 30; i++ {
			rules = append(rules, k8sResourceRule{
				Verbs:     []string{"get", "list", "watch", "create", "update", "delete"},
				APIGroups: []string{"apps"},
				Resources: []string{fmt.Sprintf("resource-%d-in-%s", i, name)},
			})
		}
		return K8sNamespacePerms{Namespace: name, ResourceRules: rules}
	}
	p := &K8sPermissions{Namespaces: []K8sNamespacePerms{mkNS("a"), mkNS("b"), mkNS("c"), mkNS("d")}}
	full := len(mustJSON(t, p))

	// Cap at half the full size -> must drop namespaces and mark truncated.
	capPermissions(p, full/2)
	if !p.Truncated {
		t.Fatal("truncation must set Truncated")
	}
	if p.Incomplete {
		t.Fatal("truncation must not set Incomplete (that's for eval failures)")
	}
	if got := len(mustJSON(t, p)); got > full/2 {
		t.Fatalf("still over cap: %d > %d", got, full/2)
	}
	if len(p.Namespaces) == 4 {
		t.Fatal("expected some namespaces to be dropped")
	}

	// A snapshot already under the cap is untouched.
	q := &K8sPermissions{Namespaces: []K8sNamespacePerms{mkNS("a")}}
	capPermissions(q, 1<<20)
	if q.Incomplete || len(q.Namespaces) != 1 {
		t.Fatal("under-cap snapshot must be left intact")
	}
}

func mustJSON(t *testing.T, v any) []byte {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestListNamespaceNames(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/namespaces" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		_, _ = w.Write([]byte(`{"items":[
			{"metadata":{"name":"default"}},
			{"metadata":{"name":"kube-system"}},
			{"metadata":{"name":"team-a"}}]}`))
	}))
	defer srv.Close()

	c := &k8sClient{host: srv.URL, token: "t", http: srv.Client()}
	names, err := c.listNamespaceNames()
	if err != nil {
		t.Fatalf("listNamespaceNames: %v", err)
	}
	if len(names) != 3 || names[0] != "default" || names[2] != "team-a" {
		t.Fatalf("unexpected namespaces: %v", names)
	}
}

func TestIsLeaderDefaultFalse(t *testing.T) {
	e := newElector("http://unused", "pod-a")
	if e.IsLeader() {
		t.Fatal("a fresh elector must not report leadership")
	}
}
