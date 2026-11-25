//go:build unix

package mcp

import (
	"errors"
	"os/exec"
	"syscall"
)

// A package launcher and the server it starts share a group. Allocate a
// fresh one; never send a group signal to the gateway's own process group.
func stdioCommand(name string, args ...string) *exec.Cmd {
	cmd := exec.Command(name, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	return cmd
}

func stopStdioCommand(cmd *exec.Cmd) error {
	// This is the original unreaped command, so its PID cannot have been
	// reused as another group's ID. Descendants that intentionally start a
	// separate session are not members of this group.
	err := syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	if errors.Is(err, syscall.ESRCH) {
		err = nil
	}
	return errors.Join(err, cmd.Process.Kill())
}
