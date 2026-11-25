//go:build !unix

package mcp

import "os/exec"

// On systems without Unix process groups, retain direct command ownership.
func stdioCommand(name string, args ...string) *exec.Cmd { return exec.Command(name, args...) }

func stopStdioCommand(cmd *exec.Cmd) error { return cmd.Process.Kill() }
