package mcp

import (
	"os"
	"syscall"
)

// After Cmd.Wait has collected the group leader, PID 1 also owns its
// orphaned workers. Wait only in that command's process group: a global
// SIGCHLD waiter could steal another exec.Cmd's exit status.
func reapStdioGroup(pgid int) error {
	if os.Getpid() != 1 {
		return nil
	}
	for {
		_, err := syscall.Wait4(-pgid, nil, 0, nil)
		switch err {
		case nil, syscall.EINTR:
			continue
		case syscall.ECHILD:
			return nil
		default:
			return err
		}
	}
}
