//go:build linux

package main

import (
	"fmt"
	"log"
	"os"
	"syscall"

	"github.com/landlock-lsm/go-landlock/landlock"
)

// sandboxExec applies a Landlock read-only filesystem restriction to the
// current process, then replaces it with args via exec. Only /tmp remains
// writable. Called in the re-exec child - the parent agent process is
// unaffected.
func sandboxExec(args []string) {
	err := landlock.V3.BestEffort().Restrict(
		landlock.RODirs("/"),
		landlock.RWDirs("/tmp"),
	)
	if err != nil {
		log.Printf("landlock: restriction not applied (%v) - executing without kernel sandbox", err)
	}

	if err := syscall.Exec(args[0], args, os.Environ()); err != nil {
		fmt.Fprintf(os.Stderr, "sandbox exec failed: %v\n", err)
		os.Exit(1)
	}
}
