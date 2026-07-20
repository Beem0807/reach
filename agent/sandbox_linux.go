//go:build linux

package main

import (
	"fmt"
	"os"
	"syscall"

	"github.com/landlock-lsm/go-landlock/landlock"
	lls "github.com/landlock-lsm/go-landlock/landlock/syscall"
)

// detectLandlock probes whether this kernel enforces Landlock (the filesystem sandbox behind
// readonly/approved mode's write protection). "active" = the kernel supports it and it will be
// enforced; "unavailable" = a Linux kernel without Landlock (< 5.13 or compiled out), where we
// must NOT run unsandboxed in readonly/approved mode.
func detectLandlock() string {
	if v, err := lls.LandlockGetABIVersion(); err == nil && v >= 1 {
		return "active"
	}
	return "unavailable"
}

// sandboxExec applies a Landlock read-only filesystem restriction to the
// current process, then replaces it with args via exec. Only /tmp remains
// writable. Called in the re-exec child - the parent agent process is
// unaffected. FAILS CLOSED: if the restriction can't be applied, it refuses
// to exec rather than run the command without the kernel sandbox.
func sandboxExec(args []string) {
	err := landlock.V3.BestEffort().Restrict(
		landlock.RODirs("/"),
		landlock.RWDirs("/tmp"),
	)
	if err != nil {
		fmt.Fprintf(os.Stderr, "landlock: sandbox could not be applied (%v); refusing to run unsandboxed\n", err)
		os.Exit(126)
	}

	if err := syscall.Exec(args[0], args, os.Environ()); err != nil {
		fmt.Fprintf(os.Stderr, "sandbox exec failed: %v\n", err)
		os.Exit(1)
	}
}
