package main

import (
	"os"
	"regexp"
	"testing"
)

// TestChartAppVersionMatchesAgent enforces the invariant that always holds: the
// chart's `appVersion` equals the agent's compiled-in agentVersion.
//
// The k8s image is built from this same agent code and tagged with appVersion,
// and the backend installs the chart by version alone (`--version`, no image.tag
// override) - so the image resolves from the chart's appVersion. appVersion IS
// the agent/image version and must move with the binary.
//
// The chart `version` is intentionally not checked here (a unit test has no
// memory of the previous release). It bumps on every release - equal to
// appVersion on an agent release, ahead of it for chart-only changes - and
// scripts/release_chart.sh refuses to publish a version already in the repo.
//
// Skipped only when the chart isn't present (e.g. the agent module built outside
// the monorepo).
func TestChartAppVersionMatchesAgent(t *testing.T) {
	const chartPath = "../deploy/helm/reach-agent/Chart.yaml"
	data, err := os.ReadFile(chartPath)
	if err != nil {
		t.Skipf("chart not found (%s): %v", chartPath, err)
	}
	m := regexp.MustCompile(`(?m)^appVersion:\s*"?([^"\s]+)"?\s*$`).FindSubmatch(data)
	if m == nil {
		t.Fatalf("could not find appVersion in %s", chartPath)
	}
	appVersion := string(m[1])
	if !regexp.MustCompile(`^\d+\.\d+\.\d+$`).MatchString(appVersion) {
		t.Fatalf("chart appVersion %q is not a bare semver image tag (e.g. 0.1.0)", appVersion)
	}
	if appVersion != agentVersion {
		t.Fatalf("chart appVersion %q != agentVersion %q - the image is built from this code, so they must be released together", appVersion, agentVersion)
	}
}
