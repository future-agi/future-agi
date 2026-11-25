package main

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"syscall"
	"testing"
	"time"
)

// Drive the real main during a blocked initial handshake, before Start. The
// runner is not an init process here: abrupt exit cannot hide a surviving child.
func TestSIGTERMDuringStdioStartup(t *testing.T) {
	dir := t.TempDir()
	pidFile := filepath.Join(dir, "pid")
	config := filepath.Join(dir, "config.yaml")
	body := fmt.Sprintf("server:\n  host: 127.0.0.1\n  port: 18973\nmcp:\n  enabled: true\n  servers:\n    local:\n      transport: stdio\n      command: %q\n      args: ['-test.run=^TestBlockedStdioStartupFixture$', '--', %q]\n", os.Args[0], pidFile)
	if err := os.WriteFile(config, []byte(body), 0600); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestGatewayMainFixture$", "--", config)
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() {
		if cmd.ProcessState == nil {
			_ = cmd.Process.Kill()
			_ = cmd.Wait()
		}
	}()
	var pid int
	for pid == 0 && ctx.Err() == nil {
		data, _ := os.ReadFile(pidFile)
		pid, _ = strconv.Atoi(string(data))
		if pid == 0 {
			time.Sleep(10 * time.Millisecond)
		}
	}
	if pid <= 0 {
		t.Fatal("startup never launched fixture")
	}
	if err := cmd.Process.Signal(syscall.SIGTERM); err != nil {
		t.Fatal(err)
	}
	exit := cmd.Wait()
	if _, err := os.Stat(fmt.Sprintf("/proc/%d", pid)); !os.IsNotExist(err) {
		_ = syscall.Kill(pid, syscall.SIGKILL) // clean up a failing control
		t.Fatalf("startup signal left the MCP child behind: gateway exit %v", exit)
	}
	if exit != nil {
		t.Fatal(exit)
	}
}

func TestGatewayMainFixture(t *testing.T) {
	if len(os.Args) == 4 && os.Args[2] == "--" {
		os.Args = []string{os.Args[0], "-config", os.Args[3]}
		main()
	}
}

func TestBlockedStdioStartupFixture(t *testing.T) {
	if len(os.Args) == 4 && os.Args[2] == "--" {
		if err := os.WriteFile(os.Args[3], []byte(strconv.Itoa(os.Getpid())), 0600); err != nil {
			os.Exit(2)
		}
		time.Sleep(15 * time.Second)
		os.Exit(0)
	}
}
