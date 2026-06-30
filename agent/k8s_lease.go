package main

// Leader election via a coordination.k8s.io/v1 Lease. With multiple replicas we
// want exactly one active agent: the leader claims, heartbeats, and runs jobs;
// the rest stand by and take over on failover. We renew in a background loop
// (independent of the job-poll cadence) so a large server-driven poll interval
// can never let the lease lapse while we still hold it.

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strings"
	stdsync "sync"
	"time"
)

// leaderElector, when set (k8s mode with REACH_K8S_LEASE + REACH_POD_NAME),
// gates claim/sync/execute so only the lease holder acts. nil otherwise.
var leaderElector *leaseElector

const (
	leaseDurationSeconds = 30 // a leader must renew within this window or lose it
	microTimeFormat      = "2006-01-02T15:04:05.000000Z"
)

type leaseElector struct {
	client    *k8sClient
	namespace string
	name      string
	identity  string // this pod's name

	mu      stdsync.Mutex
	leading bool
}

type leaseObject struct {
	Metadata struct {
		Name            string `json:"name"`
		ResourceVersion string `json:"resourceVersion,omitempty"`
	} `json:"metadata"`
	Spec struct {
		HolderIdentity       string `json:"holderIdentity"`
		LeaseDurationSeconds int    `json:"leaseDurationSeconds"`
		AcquireTime          string `json:"acquireTime,omitempty"`
		RenewTime            string `json:"renewTime,omitempty"`
		LeaseTransitions     int    `json:"leaseTransitions"`
	} `json:"spec"`
}

// IsLeader reports whether this instance currently holds the lease.
func (l *leaseElector) IsLeader() bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.leading
}

func (l *leaseElector) setLeading(v bool) {
	l.mu.Lock()
	was := l.leading
	l.leading = v
	l.mu.Unlock()
	if v && !was {
		log.Printf("Became leader (%s)", l.identity)
	} else if !v && was {
		log.Printf("Lost leadership (%s)", l.identity)
	}
}

// Run renews/acquires the lease until ctx is cancelled. Renews at a third of the
// lease duration so transient API hiccups don't immediately drop leadership.
func (l *leaseElector) Run(ctx context.Context) {
	interval := time.Duration(leaseDurationSeconds) * time.Second / 3
	for {
		leading, err := l.tryAcquireOrRenew(time.Now().UTC())
		if err != nil {
			log.Printf("Leader election: %v", err)
			l.setLeading(false)
		} else {
			l.setLeading(leading)
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(interval):
		}
	}
}

func (l *leaseElector) itemPath() string {
	return fmt.Sprintf("/apis/coordination.k8s.io/v1/namespaces/%s/leases/%s", l.namespace, l.name)
}

// tryAcquireOrRenew performs one election step and reports whether we now lead.
func (l *leaseElector) tryAcquireOrRenew(now time.Time) (bool, error) {
	status, body, err := l.client.request(http.MethodGet, l.itemPath(), "", nil)
	if err != nil {
		return false, err
	}
	if status == http.StatusNotFound {
		return l.create(now)
	}
	if status != http.StatusOK {
		return false, fmt.Errorf("get lease %s: status %d: %s", l.name, status, strings.TrimSpace(string(body)))
	}

	var lease leaseObject
	if err := json.Unmarshal(body, &lease); err != nil {
		return false, err
	}
	held := lease.Spec.HolderIdentity == l.identity
	if held {
		return l.update(lease, now, false)
	}
	if l.expired(lease, now) {
		return l.update(lease, now, true)
	}
	return false, nil // a healthy leader exists; stand by
}

func (l *leaseElector) expired(lease leaseObject, now time.Time) bool {
	renew, err := time.Parse(time.RFC3339, lease.Spec.RenewTime)
	if err != nil {
		return true // no/parse-less renew time -> treat as expired
	}
	dur := lease.Spec.LeaseDurationSeconds
	if dur <= 0 {
		dur = leaseDurationSeconds
	}
	return now.After(renew.Add(time.Duration(dur) * time.Second))
}

// create POSTs a new Lease held by us. A 409 means another replica created it
// first, so we are a follower this round.
func (l *leaseElector) create(now time.Time) (bool, error) {
	var lease leaseObject
	lease.Metadata.Name = l.name
	lease.Spec.HolderIdentity = l.identity
	lease.Spec.LeaseDurationSeconds = leaseDurationSeconds
	lease.Spec.AcquireTime = now.Format(microTimeFormat)
	lease.Spec.RenewTime = now.Format(microTimeFormat)
	lease.Spec.LeaseTransitions = 0
	payload, _ := json.Marshal(lease)
	collPath := fmt.Sprintf("/apis/coordination.k8s.io/v1/namespaces/%s/leases", l.namespace)
	status, body, err := l.client.request(http.MethodPost, collPath, "application/json", payload)
	if err != nil {
		return false, err
	}
	switch status {
	case http.StatusCreated, http.StatusOK:
		return true, nil
	case http.StatusConflict:
		return false, nil
	default:
		return false, fmt.Errorf("create lease %s: status %d: %s", l.name, status, strings.TrimSpace(string(body)))
	}
}

// update PUTs the lease with our renewTime (and, on takeover, our holder identity
// and an incremented transition count), using resourceVersion for optimistic
// concurrency. A 409 means we lost a race -> follower this round.
func (l *leaseElector) update(lease leaseObject, now time.Time, takeover bool) (bool, error) {
	if takeover {
		lease.Spec.HolderIdentity = l.identity
		lease.Spec.AcquireTime = now.Format(microTimeFormat)
		lease.Spec.LeaseTransitions++
	}
	lease.Spec.LeaseDurationSeconds = leaseDurationSeconds
	lease.Spec.RenewTime = now.Format(microTimeFormat)
	payload, _ := json.Marshal(lease)
	status, body, err := l.client.request(http.MethodPut, l.itemPath(), "application/json", payload)
	if err != nil {
		return false, err
	}
	switch status {
	case http.StatusOK:
		return true, nil
	case http.StatusConflict:
		return false, nil
	default:
		return false, fmt.Errorf("update lease %s: status %d: %s", l.name, status, strings.TrimSpace(string(body)))
	}
}
