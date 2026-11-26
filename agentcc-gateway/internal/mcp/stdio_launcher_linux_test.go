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

// A helper starts a brief worker in the command's group and exits, so the
// worker is adopted by PID 1. When the worker finishes, it must be collected
// while the original session keeps serving, not only when the session closes.
func TestLiveSessionCollectsAdoptedWorker(t *testing.T) {
	if os.Getpid() != 1 {
		t.Skip("run the compiled test binary as container PID 1 to exercise adoption")
	}
	path := filepath.Join(t.TempDir(), "worker")
	tr := NewStdioTransport(os.Args[0], []string{"-test.run=^TestStdioAdoptedWorkerFixture$", "--", "serve", path})
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := tr.Start(ctx); err != nil {
		t.Fatal(err)
	}
	defer tr.Close()
	ping := func(id int) {
		t.Helper()
		resp, err := tr.Send(ctx, &Message{JSONRPC: "2.0", ID: json.RawMessage(strconv.Itoa(id)), Method: MethodPing})
		if err != nil || resp == nil || resp.Error != nil {
			t.Fatalf("session stopped answering: %v", err)
		}
	}
	ping(1) // the fixture starts the helper before answering
	var pid int
	for deadline := time.Now().Add(3 * time.Second); pid == 0; time.Sleep(10 * time.Millisecond) {
		if data, err := os.ReadFile(path + ".pid"); err == nil {
			pid, _ = strconv.Atoi(string(data))
		}
		if time.Now().After(deadline) {
			t.Fatal("worker never started")
		}
	}
	stat := func() string {
		data, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "stat"))
		if err != nil {
			return ""
		}
		return string(data)
	}
	var last string
	for deadline := time.Now().Add(3 * time.Second); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
		if last = stat(); last == "" {
			break
		}
	}
	if last != "" {
		t.Fatalf("expected the already-exited adopted worker to be collected while serving; /proc stat: %s", last)
	}
	if !tr.Healthy() {
		t.Fatal("session closed while collecting the worker")
	}
	ping(2)
	// The launcher itself stays with Cmd.Wait.
	if _, err := os.Stat(filepath.Join("/proc", strconv.Itoa(tr.cmd.Process.Pid))); err != nil {
		t.Fatalf("launcher was collected outside Cmd.Wait: %v", err)
	}
	if err := tr.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestStdioAdoptedWorkerFixture(t *testing.T) {
	if len(os.Args) < 3 || os.Args[len(os.Args)-3] != "--" {
		return
	}
	mode, path := os.Args[len(os.Args)-2], os.Args[len(os.Args)-1]
	self := func(mode string) *exec.Cmd {
		return exec.Command(os.Args[0], "-test.run=^TestStdioAdoptedWorkerFixture$", "--", mode, path)
	}
	switch mode {
	case "serve":
		dec, enc := json.NewDecoder(os.Stdin), json.NewEncoder(os.Stdout)
		for started := false; ; started = true {
			var request Message
			if dec.Decode(&request) != nil {
				os.Exit(0)
			}
			if !started && self("helper").Run() != nil {
				os.Exit(2)
			}
			resp, _ := NewResponse(request.ID, struct{}{})
			if enc.Encode(resp) != nil {
				os.Exit(2)
			}
		}
	case "helper":
		// Leave the worker behind without waiting for it.
		if self("work").Start() != nil {
			os.Exit(2)
		}
		os.Exit(0)
	case "work":
		if os.WriteFile(path+".pid", []byte(strconv.Itoa(os.Getpid())), 0600) != nil {
			os.Exit(2)
		}
		time.Sleep(200 * time.Millisecond)
		os.Exit(0)
	}
}

// Live collection must not take the exit status of a PID 1 child that no
// stdio session owns, even while a session is being collected.
func TestLiveCollectionLeavesUnrelatedChildren(t *testing.T) {
	if os.Getpid() != 1 {
		t.Skip("run the compiled test binary as container PID 1 to exercise adoption")
	}
	tr := NewStdioTransport(os.Args[0], []string{"-test.run=^TestStdioAdoptedWorkerFixture$", "--", "serve", filepath.Join(t.TempDir(), "worker")})
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := tr.Start(ctx); err != nil {
		t.Fatal(err)
	}
	defer tr.Close()
	other := exec.Command("/bin/sh", "-c", "exit 3")
	other.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := other.Start(); err != nil {
		t.Fatal(err)
	}
	if _, err := tr.Send(ctx, &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPing}); err != nil {
		t.Fatal(err)
	}
	time.Sleep(500 * time.Millisecond) // let SIGCHLD scans run while it is a zombie
	var exit *exec.ExitError
	if err := other.Wait(); !errors.As(err, &exit) || exit.ExitCode() != 3 {
		t.Fatalf("unrelated child lost its exit status: %v", err)
	}
}
