package main

// Agent half of the backend<->agent enforcement-parity check. See its Python twin,
// backend/tests/test_enforcement_parity.py, for the full rationale. Both suites load the SAME
// golden vectors (backend/tests/policy_parity_vectors.json - the server owns the contract, since
// the agent "mirrors the server") and assert their implementation returns the encoded decision.
// If hostRuleMatches or argvReadsSensitivePath ever drifts from shared/policy.py, this test (or its
// Python twin) goes red in CI, so the "kept in sync" comments become enforced, not aspirational.

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// The vectors live under the backend tree (server-authoritative). In the monorepo checkout the
// agent package sits alongside backend/, so this relative path resolves in both local and CI runs.
const parityVectorsPath = "../backend/tests/policy_parity_vectors.json"

type hostRuleVector struct {
	Desc     string   `json:"desc"`
	Argv     []string `json:"argv"`
	Rule     HostRule `json:"rule"`
	Expected bool     `json:"expected"`
}

type sensitiveReadVector struct {
	Desc     string   `json:"desc"`
	Argv     []string `json:"argv"`
	Expected bool     `json:"expected"`
}

type parityVectors struct {
	HostRuleMatches []hostRuleVector      `json:"host_rule_matches"`
	SensitiveRead   []sensitiveReadVector `json:"sensitive_read"`
}

func loadParityVectors(t *testing.T) parityVectors {
	t.Helper()
	data, err := os.ReadFile(filepath.FromSlash(parityVectorsPath))
	if err != nil {
		t.Fatalf("read parity vectors: %v", err)
	}
	var v parityVectors
	if err := json.Unmarshal(data, &v); err != nil {
		t.Fatalf("parse parity vectors: %v", err)
	}
	if len(v.HostRuleMatches) == 0 || len(v.SensitiveRead) == 0 {
		t.Fatal("parity vectors are empty - contract file not loaded correctly")
	}
	return v
}

func TestHostRuleMatchesParity(t *testing.T) {
	for _, v := range loadParityVectors(t).HostRuleMatches {
		v := v
		t.Run(v.Desc, func(t *testing.T) {
			if got := hostRuleMatches(v.Argv, v.Rule); got != v.Expected {
				t.Errorf("hostRuleMatches drift: argv=%v rule=%+v got=%v want=%v",
					v.Argv, v.Rule, got, v.Expected)
			}
		})
	}
}

func TestSensitiveReadParity(t *testing.T) {
	for _, v := range loadParityVectors(t).SensitiveRead {
		v := v
		t.Run(v.Desc, func(t *testing.T) {
			if got := argvReadsSensitivePath(v.Argv); got != v.Expected {
				t.Errorf("argvReadsSensitivePath drift: argv=%v got=%v want=%v",
					v.Argv, got, v.Expected)
			}
		})
	}
}
