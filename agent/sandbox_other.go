//go:build !linux

package main

import (
	"fmt"
	"os"
	"syscall"
)

// sandboxExec on non-Linux: no Landlock available, exec without sandbox.
// On macOS readonly mode falls back to server-side pattern enforcement.
func sandboxExec(args []string) {
	if err := syscall.Exec(args[0], args, os.Environ()); err != nil {
		fmt.Fprintf(os.Stderr, "sandbox exec failed: %v\n", err)
		os.Exit(1)
	}
}
