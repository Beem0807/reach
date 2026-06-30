package main

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestSplitPipeline(t *testing.T) {
	cases := []struct {
		in   string
		want [][]string
	}{
		{"kubectl get pods", [][]string{{"kubectl", "get", "pods"}}},
		{"kubectl get pods | grep foo", [][]string{{"kubectl", "get", "pods"}, {"grep", "foo"}}},
		{`kubectl get pods -o jsonpath='{.items[*].metadata.name}'`,
			[][]string{{"kubectl", "get", "pods", "-o", "jsonpath={.items[*].metadata.name}"}}},
		{`kubectl get po | grep "my pod" | wc -l`,
			[][]string{{"kubectl", "get", "po"}, {"grep", "my pod"}, {"wc", "-l"}}},
	}
	for _, c := range cases {
		got, err := splitPipeline(c.in)
		if err != nil {
			t.Fatalf("%q: unexpected error %v", c.in, err)
		}
		if !reflect.DeepEqual(got, c.want) {
			t.Fatalf("%q: got %v want %v", c.in, got, c.want)
		}
	}
}

func TestSplitPipelineRejectsShellOperators(t *testing.T) {
	for _, in := range []string{
		"kubectl get pods; rm -rf /",
		"kubectl get pods && curl evil",
		"echo $(cat /var/run/secrets/token)",
		"kubectl get pods > /tmp/x",
		"kubectl get pods `whoami`",
	} {
		if _, err := splitPipeline(in); err == nil {
			t.Fatalf("expected rejection for %q", in)
		}
	}
}

func TestExecuteK8sCommandAllowlist(t *testing.T) {
	allowed := []string{"kubectl", "grep"}

	// Disallowed binary is blocked before anything runs.
	r := executeK8sCommand("curl http://evil", allowed)
	if !r.Blocked || r.ExitCode != 126 {
		t.Fatalf("expected blocked curl, got %+v", r)
	}

	// A disallowed stage in an otherwise-fine pipeline is blocked.
	r = executeK8sCommand("kubectl get pods | awk '{print $1}'", allowed)
	if !r.Blocked {
		t.Fatal("expected awk stage to be blocked")
	}

	// Shell operators are blocked.
	r = executeK8sCommand("kubectl get pods; rm -rf /", allowed)
	if !r.Blocked {
		t.Fatal("expected shell operator to be blocked")
	}
}

func TestExecuteK8sBlocksLocalFileReads(t *testing.T) {
	// A real existing file stands in for the mounted SA token.
	dir := t.TempDir()
	secret := filepath.Join(dir, "token")
	if err := os.WriteFile(secret, []byte("s3cret"), 0600); err != nil {
		t.Fatal(err)
	}
	allowed := []string{"kubectl", "grep", "jq", "head"}

	// Direct file read via a filter -> blocked. Assert on the pure guard so the
	// test never execs a real binary (kubectl against a live cluster can hang).
	for _, c := range []string{
		"grep . " + secret,
		"head " + secret,
		"grep --file=" + secret + " x",
		"kubectl create cm x --from-file=" + secret,
		"kubectl get pods | grep . " + secret,
	} {
		if _, blocked := k8sBlockReason(c, allowed); !blocked {
			t.Fatalf("expected local-file read to be blocked: %q", c)
		}
	}

	// Patterns / stdin / resource names that are not files -> not blocked by this
	// check (the real exec would need a cluster, so we only assert the guard).
	for _, c := range []string{
		"kubectl get pods | grep my-pod",
		"kubectl get pods | grep app/web",
		"kubectl apply -f -",
	} {
		if reason, blocked := k8sBlockReason(c, allowed); blocked {
			t.Fatalf("non-file arg should not be blocked: %q -> %s", c, reason)
		}
	}
}

func TestArgReadsLocalFile(t *testing.T) {
	dir := t.TempDir()
	f := filepath.Join(dir, "f")
	_ = os.WriteFile(f, []byte("x"), 0600)

	if _, ok := argReadsLocalFile([]string{f}); !ok {
		t.Fatal("absolute existing file should be detected")
	}
	if _, ok := argReadsLocalFile([]string{"--file=" + f}); !ok {
		t.Fatal("--flag=file should be detected")
	}
	if _, ok := argReadsLocalFile([]string{"ERROR", "-i", "-"}); ok {
		t.Fatal("patterns/flags/stdin should not be flagged")
	}
}

func TestExecuteK8sCommandRunsPipeline(t *testing.T) {
	// Real execution with allow-listed coreutils (no kubectl needed).
	allowed := []string{"printf", "grep", "tr"}

	r := executeK8sCommand(`printf "alpha\nbeta\n" | grep beta`, allowed)
	if r.Blocked {
		t.Fatalf("unexpected block: %s", r.Stderr)
	}
	if r.ExitCode != 0 {
		t.Fatalf("exit=%d stderr=%s", r.ExitCode, r.Stderr)
	}
	if got := r.Stdout; got != "beta\n" {
		t.Fatalf("pipeline output = %q, want %q", got, "beta\n")
	}
}

func TestK8sBinaryAllowlist(t *testing.T) {
	// Default: kubectl + filters.
	t.Setenv("REACH_K8S_ALLOWED_BINARIES", "")
	t.Setenv("REACH_K8S_EXTRA_BINARIES", "")
	if got := k8sBinaryAllowlist(); !containsStr(got, "kubectl") || !containsStr(got, "jq") {
		t.Fatalf("default allowlist missing kubectl/jq: %v", got)
	}

	// Extra is ADDITIVE - kubectl and the filters survive.
	t.Setenv("REACH_K8S_EXTRA_BINARIES", "helm, kustomize ,")
	got := k8sBinaryAllowlist()
	if !containsStr(got, "kubectl") || !containsStr(got, "jq") {
		t.Fatalf("extra must not drop the default: %v", got)
	}
	if !containsStr(got, "helm") || !containsStr(got, "kustomize") {
		t.Fatalf("extra binaries not added: %v", got)
	}

	// Override REPLACES the default (lock-down).
	t.Setenv("REACH_K8S_EXTRA_BINARIES", "")
	t.Setenv("REACH_K8S_ALLOWED_BINARIES", "kubectl")
	if got := k8sBinaryAllowlist(); !reflect.DeepEqual(got, []string{"kubectl"}) {
		t.Fatalf("override allowlist = %v, want [kubectl]", got)
	}

	// Override + extra: replace, then add (deduped).
	t.Setenv("REACH_K8S_ALLOWED_BINARIES", "kubectl")
	t.Setenv("REACH_K8S_EXTRA_BINARIES", "helm,kubectl")
	if got := k8sBinaryAllowlist(); !reflect.DeepEqual(got, []string{"kubectl", "helm"}) {
		t.Fatalf("override+extra = %v, want [kubectl helm]", got)
	}
}

func TestApplyDefaultNamespace(t *testing.T) {
	cases := []struct {
		name string
		in   [][]string
		want [][]string
	}{
		{"unqualified gets default",
			[][]string{{"kubectl", "get", "pods"}},
			[][]string{{"kubectl", "--namespace=default", "get", "pods"}}},
		{"explicit -n preserved",
			[][]string{{"kubectl", "get", "pods", "-n", "team-a"}},
			[][]string{{"kubectl", "get", "pods", "-n", "team-a"}}},
		{"--namespace= preserved",
			[][]string{{"kubectl", "get", "pods", "--namespace=team-a"}},
			[][]string{{"kubectl", "get", "pods", "--namespace=team-a"}}},
		{"all-namespaces -A preserved",
			[][]string{{"kubectl", "get", "pods", "-A"}},
			[][]string{{"kubectl", "get", "pods", "-A"}}},
		{"exec injects before verb; -n after -- is the container command, not a ns flag",
			[][]string{{"kubectl", "exec", "mypod", "--", "ls", "-n", "/tmp"}},
			[][]string{{"kubectl", "--namespace=default", "exec", "mypod", "--", "ls", "-n", "/tmp"}}},
		{"non-kubectl pipe stage untouched",
			[][]string{{"kubectl", "get", "pods"}, {"grep", "foo"}},
			[][]string{{"kubectl", "--namespace=default", "get", "pods"}, {"grep", "foo"}}},
	}
	for _, c := range cases {
		applyDefaultNamespace(c.in)
		if !reflect.DeepEqual(c.in, c.want) {
			t.Fatalf("%s: got %v want %v", c.name, c.in, c.want)
		}
	}
}

func TestApplyDefaultNamespaceEnvOverride(t *testing.T) {
	t.Setenv("REACH_K8S_DEFAULT_NAMESPACE", "reach")
	stages := [][]string{{"kubectl", "get", "pods"}}
	applyDefaultNamespace(stages)
	want := [][]string{{"kubectl", "--namespace=reach", "get", "pods"}}
	if !reflect.DeepEqual(stages, want) {
		t.Fatalf("got %v want %v", stages, want)
	}
}
