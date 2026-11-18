package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"syscall"
	"testing"
	"time"
)

func TestCloseStopsOwnedLauncherWorker(t *testing.T) {
	path := filepath.Join(t.TempDir(), "work.lock")
	lock, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	tr := NewStdioTransport(os.Args[0], []string{"-test.run=^TestStdioLauncherFixture$", "--", "launch", path})
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := tr.Start(ctx); err != nil {
		t.Fatal(err)
	}
	defer tr.Close()
	pid := tr.cmd.Process.Pid
	if group, err := syscall.Getpgid(pid); err != nil || group != pid {
		t.Fatal("launcher has no dedicated process group for its worker")
	}
	if _, err := tr.Send(ctx, &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPing}); err != nil {
		t.Fatal(err)
	}
	tryLock := func() error { return syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) }
	if err := tryLock(); !errors.Is(err, syscall.EWOULDBLOCK) {
		t.Fatalf("fixture worker did not hold the file lock: %v", err)
	}
	if err := tr.Close(); err != nil {
		t.Fatal(err)
	}
	// Run the compiled test binary as container PID 1 to exercise adoption.
	// A zombie releases file locks too; require the /proc entry to be gone.
	if os.Getpid() == 1 {
		data, err := os.ReadFile(path + ".pid")
		if err != nil {
			t.Fatal(err)
		}
		pid, err := strconv.Atoi(string(data))
		if err != nil || pid <= 1 {
			t.Fatalf("invalid worker PID %q", data)
		}
		if _, err := os.Stat(filepath.Join("/proc", strconv.Itoa(pid))); !os.IsNotExist(err) {
			t.Fatal("worker still has a process entry after PID 1 closed the transport")
		}
	}
	for deadline := time.Now().Add(time.Second); time.Now().Before(deadline); {
		if err := tryLock(); err == nil {
			return
		} else if !errors.Is(err, syscall.EWOULDBLOCK) {
			t.Fatal(err)
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatal("worker still holds its file after the launcher was closed")
}

func TestStdioLauncherFixture(t *testing.T) {
	if len(os.Args) < 3 || os.Args[len(os.Args)-3] != "--" {
		return
	}
	mode, path := os.Args[len(os.Args)-2], os.Args[len(os.Args)-1]
	if mode == "launch" {
		var request Message
		if json.NewDecoder(os.Stdin).Decode(&request) != nil {
			os.Exit(0)
		}
		worker := exec.Command(os.Args[0], "-test.run=^TestStdioLauncherFixture$", "--", "work", path)
		worker.Stdout = os.Stdout
		if worker.Run() != nil {
			os.Exit(2)
		}
		os.Exit(0)
	}
	file, err := os.Open(path)
	if err != nil || syscall.Flock(int(file.Fd()), syscall.LOCK_EX) != nil {
		os.Exit(2)
	}
	defer file.Close()
	if err := os.WriteFile(path+".pid", []byte(strconv.Itoa(os.Getpid())), 0600); err != nil {
		os.Exit(2)
	}
	resp, _ := NewResponse(json.RawMessage(`1`), struct{}{})
	if json.NewEncoder(os.Stdout).Encode(resp) != nil {
		os.Exit(2)
	}
	time.Sleep(6 * time.Second) // bounded work, longer than the Close assertion
	os.Exit(0)
}
