package main

// Kubernetes job execution: a gated, shell-free model.
//
// On a host the agent runs jobs via `/bin/bash -lc`, sandboxed with Landlock.
// In a cluster that model is wrong: the pod holds a cluster credential, so
// arbitrary shell would let a job read the ServiceAccount token, reach internal
// services (SSRF), or run any binary - none of which RBAC bounds. Landlock only
// guards the filesystem, which is irrelevant for kubectl's API calls.
//
// So in k8s mode the agent: (1) parses the command into a pipeline itself - no
// shell, so `;`, `&&`, `$(...)`, backticks, and redirects are rejected; (2)
// requires every stage's binary to be allow-listed; (3) wires the pipes in Go.
// The result is that a job can only ever be `kubectl ... | <safe filter> ...`,
// bounded by the agent's RBAC - which is exactly the model the chart advertises.

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// defaultK8sBinaries: kubectl plus read-only text filters that consume stdin.
// Deliberately excludes awk and sed (both can execute commands), and
// cat/curl/wget/nc/xargs/sh/bash (file read, egress, or arbitrary exec).
var defaultK8sBinaries = []string{
	"kubectl", "grep", "jq", "head", "tail", "wc", "sort", "uniq", "cut", "tr",
}

// k8sAllowedBinaries gates job execution in kubernetes mode; nil outside k8s.
var k8sAllowedBinaries []string

// k8sBinaryAllowlist resolves the effective allowlist:
//   - REACH_K8S_ALLOWED_BINARIES (comma-separated) REPLACES the default - the
//     explicit lock-down / full-control knob.
//   - REACH_K8S_EXTRA_BINARIES is ADDED to whatever the base is - the common
//     "I also want helm" case, so adding a tool never drops kubectl or filters.
// Both are optional; with neither set, the default applies.
func k8sBinaryAllowlist() []string {
	base := defaultK8sBinaries
	if override := parseBinaryList(os.Getenv("REACH_K8S_ALLOWED_BINARIES")); len(override) > 0 {
		base = override
	}
	base = append(append([]string{}, base...), parseBinaryList(os.Getenv("REACH_K8S_EXTRA_BINARIES"))...)
	return dedupeStrings(base)
}

func parseBinaryList(raw string) []string {
	var out []string
	for _, b := range strings.Split(raw, ",") {
		if b = strings.TrimSpace(b); b != "" {
			out = append(out, b)
		}
	}
	return out
}

func dedupeStrings(in []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, s := range in {
		if !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	return out
}

func containsStr(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

// argReadsLocalFile reports whether any argument resolves to an existing regular
// file (so the stage would read pod-local data). It checks bare positional tokens
// and `--flag=value` right-hand sides; "-" (stdin) and values that don't stat as
// a file (patterns, URLs, resource names) pass. Relative paths resolve against the
// stage's working directory ("/"), matching how the command would run.
func argReadsLocalFile(args []string) (string, bool) {
	isFile := func(c string) bool {
		if c == "" || c == "-" {
			return false
		}
		p := c
		if !strings.HasPrefix(p, "/") {
			p = "/" + p // stages run with Dir="/"
		}
		fi, err := os.Stat(p)
		return err == nil && fi.Mode().IsRegular()
	}
	for _, a := range args {
		if i := strings.IndexByte(a, '='); i >= 0 { // --flag=value / -f=value
			if v := a[i+1:]; isFile(v) {
				return v, true
			}
			continue
		}
		if strings.HasPrefix(a, "-") {
			continue // a bare flag, not a path (a separate value token is checked on its own)
		}
		if isFile(a) {
			return a, true
		}
	}
	return "", false
}

// executeK8sCommand runs a job under the kubectl-only-plus-filters model: no
// shell, every pipeline stage's binary allow-listed. This bounds the pod's blast
// radius (agent-local config, so it holds even against a compromised backend).
//
// Policy mode (readonly/approved) is NOT enforced here - the backend is the
// single source of truth for that (it gates k8s jobs at submission and never
// dispatches a blocked command), and RBAC is the unbypassable floor.
// k8sBlockReason applies the pre-execution guards (no shell operators, allow-listed
// binaries only, no local-file reads) and reports why a command would be blocked,
// if at all. Pure: it runs no process, so it's the hermetic hook tests assert on.
// execEscapeReason blocks flags/subcommands of allow-listed tools that run an ARBITRARY
// executable, which would escape the no-shell allowlist. Currently helm (the common opt-in
// tool): `--post-renderer <exe>` runs any binary, and `helm plugin …` installs/runs plugin
// code. Other allow-listed tools rely on the layered defenses (no shell, RBAC, the
// local-file-read guard, and structured approval). Returns ("", false) when clean.
func execEscapeReason(bin string, args []string) (string, bool) {
	if bin != "helm" {
		return "", false
	}
	// The first positional after helm is its subcommand; `helm plugin` runs arbitrary code.
	for _, a := range args {
		if !strings.HasPrefix(a, "-") {
			if a == "plugin" {
				return "helm plugin management is not permitted (it runs arbitrary code)", true
			}
			break
		}
	}
	for _, a := range args {
		if l := strings.ToLower(a); l == "--post-renderer" || strings.HasPrefix(l, "--post-renderer=") {
			return "helm --post-renderer runs an arbitrary executable, which is not permitted (it escapes the no-shell allowlist)", true
		}
	}
	return "", false
}


func k8sBlockReason(command string, allowed []string) (string, bool) {
	stages, err := splitPipeline(command)
	if err != nil {
		return err.Error(), true
	}
	for _, st := range stages {
		if len(st) == 0 {
			return "empty pipeline stage", true
		}
		bin := filepath.Base(st[0])
		if !containsStr(allowed, bin) {
			return fmt.Sprintf("%q is not an allowed command", bin), true
		}
		// Some allow-listed tools have flags/subcommands that run an arbitrary executable,
		// escaping the no-shell allowlist. Block the known ones.
		if reason, ok := execEscapeReason(bin, st[1:]); ok {
			return reason, true
		}
		// Reject reading existing local files. Without this, an allow-listed binary
		// (grep, jq, head, even `kubectl create --from-file`) could dump the mounted
		// ServiceAccount token or other pod files - a file read bypasses RBAC. The
		// agent has no shell or file-writing binary, so jobs never have a legitimate
		// reason to reference a pre-existing local path; data comes from the API or
		// the piped stream. Stdin ("-") and URLs are allowed (they don't stat).
		if f, ok := argReadsLocalFile(st[1:]); ok {
			return fmt.Sprintf("reading local file %q is not permitted - jobs read the API or the piped stream, not pod files", f), true
		}
	}
	return "", false
}

func executeK8sCommand(command string, allowed []string) CommandResult {
	start := time.Now()
	if reason, blocked := k8sBlockReason(command, allowed); blocked {
		return blockedK8s(reason, command, allowed, start)
	}
	stages, _ := splitPipeline(command) // already validated by k8sBlockReason above

	// Pin an explicit namespace on unqualified kubectl stages so a command runs
	// where the backend classified it (which also defaults to "default"), not the
	// agent's own pod namespace that in-cluster kubectl would otherwise target.
	applyDefaultNamespace(stages)

	ctx, cancel := context.WithTimeout(context.Background(), commandTimeout())
	defer cancel()
	stdout, stderr, exitCode, stdoutTrunc, stderrTrunc := runK8sPipeline(ctx, stages)
	// runK8sPipeline already bounded stdout/stderr to maxOutputSize() and set the flags.
	return CommandResult{
		Stdout:          stdout,
		Stderr:          stderr,
		ExitCode:        exitCode,
		DurationMS:      time.Since(start).Milliseconds(),
		StdoutTruncated: stdoutTrunc,
		StderrTruncated: stderrTrunc,
	}
}

func blockedK8s(reason, command string, allowed []string, start time.Time) CommandResult {
	msg := fmt.Sprintf(
		"Blocked: %s\n\nCommand:\n  %s\n\nThis is a Kubernetes agent: jobs run without a shell and are limited to\n[%s] connected by pipes. Use kubectl (e.g. -o json | jq) - shell operators\n(;, &&, $(), redirects) and other binaries are not permitted.\n",
		reason, strings.TrimSpace(command), strings.Join(allowed, " "))
	return CommandResult{
		Stderr:     truncate(msg, maxOutputSize()),
		ExitCode:   126,
		DurationMS: time.Since(start).Milliseconds(),
		Blocked:    true,
	}
}

// k8sDefaultNamespace is the namespace injected into kubectl stages that don't
// select one. Defaults to "default" - matching the backend's approval
// classification and the conventional kubectl default - and is overridable per
// install with REACH_K8S_DEFAULT_NAMESPACE (e.g. a namespace-scoped agent can
// point it at its own namespace).
func k8sDefaultNamespace() string {
	if v := strings.TrimSpace(os.Getenv("REACH_K8S_DEFAULT_NAMESPACE")); v != "" {
		return v
	}
	return "default"
}

// hasNamespaceFlag reports whether a kubectl arg list already selects a namespace
// or all namespaces. Case-insensitive (so -A is caught) and mirrors the backend's
// namespace detection, so injection happens exactly when the backend assumed the
// default namespace.
func hasNamespaceFlag(args []string) bool {
	for _, a := range args {
		if a == "--" {
			break // container command (exec/run) follows, not kubectl flags
		}
		low := strings.ToLower(a)
		switch {
		case low == "-n", low == "--namespace", low == "-a", low == "--all-namespaces":
			return true
		case strings.HasPrefix(low, "-n="), strings.HasPrefix(low, "--namespace="):
			return true
		}
	}
	return false
}

// applyDefaultNamespace inserts `--namespace=<default>` into any kubectl stage
// that doesn't already select a namespace, as a global flag right after the
// binary (kubectl ignores it for cluster-scoped resources). Mutates stages.
func applyDefaultNamespace(stages [][]string) {
	ns := k8sDefaultNamespace()
	for i, st := range stages {
		if len(st) == 0 || filepath.Base(st[0]) != "kubectl" {
			continue
		}
		if hasNamespaceFlag(st[1:]) {
			continue
		}
		stages[i] = append([]string{st[0], "--namespace=" + ns}, st[1:]...)
	}
}

// runK8sPipeline execs the stages directly (no shell), wiring each stage's
// stdout to the next stage's stdin. Exit code is the last stage's, matching a
// shell pipeline without pipefail.
func runK8sPipeline(ctx context.Context, stages [][]string) (stdout, stderr string, exitCode int, stdoutTrunc, stderrTrunc bool) {
	cmds := make([]*exec.Cmd, len(stages))
	limit := maxOutputSize()
	// One stderr buffer shared across concurrently-running stages, and the final stage's
	// stdout - both bounded so a chatty kubectl/jq can't balloon agent memory. cappedBuffer
	// is mutex-guarded, making the shared stderr safe for concurrent stage writes.
	errBuf := &cappedBuffer{limit: limit}
	outBuf := &cappedBuffer{limit: limit}
	for i, st := range stages {
		c := exec.CommandContext(ctx, st[0], st[1:]...)
		c.Dir = "/"
		c.Stderr = errBuf
		cmds[i] = c
	}
	for i := 0; i < len(cmds)-1; i++ {
		pipe, err := cmds[i].StdoutPipe()
		if err != nil {
			return "", err.Error(), 1, false, false
		}
		cmds[i+1].Stdin = pipe
	}
	cmds[len(cmds)-1].Stdout = outBuf

	for _, c := range cmds {
		if err := c.Start(); err != nil {
			return "", err.Error(), 1, false, false
		}
	}
	exitCode = 0
	for i, c := range cmds {
		err := c.Wait()
		if err == nil {
			continue
		}
		if ctx.Err() == context.DeadlineExceeded {
			exitCode = 124
			continue
		}
		// Only the final stage's failure determines the pipeline's exit code.
		if i == len(cmds)-1 {
			if ee, ok := err.(*exec.ExitError); ok {
				exitCode = ee.ExitCode()
			} else {
				exitCode = 1
			}
		}
	}
	return outBuf.String(), errBuf.String(), exitCode, outBuf.Truncated(), errBuf.Truncated()
}

// splitPipeline tokenizes a command into pipeline stages (split on top-level
// `|`), honoring single/double quotes but performing NO shell interpretation.
// Shell operators that imply execution or redirection are rejected outright.
func splitPipeline(s string) ([][]string, error) {
	var stages [][]string
	var cur []string
	var tok []rune
	inTok := false

	flushTok := func() {
		if inTok {
			cur = append(cur, string(tok))
			tok = nil
			inTok = false
		}
	}
	flushStage := func() error {
		flushTok()
		if len(cur) == 0 {
			return fmt.Errorf("empty command in pipeline")
		}
		stages = append(stages, cur)
		cur = nil
		return nil
	}

	rs := []rune(s)
	for i := 0; i < len(rs); i++ {
		c := rs[i]
		switch {
		case c == '\'':
			inTok = true
			i++
			for i < len(rs) && rs[i] != '\'' {
				tok = append(tok, rs[i])
				i++
			}
			if i >= len(rs) {
				return nil, fmt.Errorf("unterminated single quote")
			}
		case c == '"':
			inTok = true
			i++
			for i < len(rs) && rs[i] != '"' {
				// POSIX: inside double quotes a backslash only escapes " \ $ `.
				// Otherwise it is literal (so e.g. \n stays \n for printf/jq).
				if rs[i] == '\\' && i+1 < len(rs) {
					n := rs[i+1]
					if n == '"' || n == '\\' || n == '$' || n == '`' {
						tok = append(tok, n)
						i += 2
						continue
					}
				}
				tok = append(tok, rs[i])
				i++
			}
			if i >= len(rs) {
				return nil, fmt.Errorf("unterminated double quote")
			}
		case c == ' ' || c == '\t' || c == '\n':
			flushTok()
		case c == '|':
			if err := flushStage(); err != nil {
				return nil, err
			}
		case c == '\\':
			if i+1 < len(rs) {
				tok = append(tok, rs[i+1])
				i++
				inTok = true
			}
		case strings.ContainsRune(";&<>()$`", c):
			return nil, fmt.Errorf("shell operator %q is not allowed (no shell in kubernetes mode)", string(c))
		default:
			tok = append(tok, c)
			inTok = true
		}
	}
	if err := flushStage(); err != nil {
		return nil, err
	}
	return stages, nil
}
