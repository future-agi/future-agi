package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/ingestbench"
)

func repairPlan(t *testing.T) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "plan.json")
	if err := command(context.Background(), append(append([]string{}, dryArgs...), "--out", p), io.Discard); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestRepairBudgetBounds(t *testing.T) {
	for _, tc := range []struct{ flag, max string }{{"rate", "1000000"}, {"batch-size", "10000"}, {"concurrency", "128"}} {
		t.Run(tc.flag, func(t *testing.T) {
			for _, v := range []string{"0", "-1", "9223372036854775807"} {
				var out bytes.Buffer
				err := command(context.Background(), []string{"collector-budget", "--" + tc.flag, v}, &out)
				if err == nil || !strings.Contains(err.Error(), tc.flag) || !strings.Contains(err.Error(), "1.."+tc.max) || out.Len() != 0 {
					t.Errorf("%s=%s: %v, output %s", tc.flag, v, err, out.String())
				}
			}
		})
	}
}

func TestRepairOutputOwnership(t *testing.T) {
	t.Run("corrupt-checkpoint", func(t *testing.T) {
		d := t.TempDir()
		in, out := filepath.Join(d, "bad.json"), filepath.Join(d, "result.json")
		if err := os.WriteFile(in, []byte("{"), 0600); err != nil {
			t.Fatal(err)
		}
		if err := command(context.Background(), []string{"verify", "--checkpoint", in, "--out", out}, io.Discard); err == nil {
			t.Fatal("accepted corrupt checkpoint")
		}
		if _, err := os.Lstat(out); !os.IsNotExist(err) {
			t.Fatal("failed verify left a final artifact", err)
		}
		files, _ := os.ReadDir(d)
		if len(files) != 1 {
			t.Fatal("failed verify leaked staging files")
		}
	})
	t.Run("publication-pair", func(t *testing.T) {
		p := repairPlan(t)
		d := t.TempDir()
		a, b := filepath.Join(d, "publication.txt"), filepath.Join(d, "publication.json")
		if err := os.WriteFile(b, []byte("keep"), 0600); err != nil {
			t.Fatal(err)
		}
		args := []string{"publish", "--markdown", a, "--json", b, p}
		if err := command(context.Background(), args, io.Discard); err == nil {
			t.Fatal("overwrote second output")
		}
		if _, err := os.Lstat(a); !os.IsNotExist(err) {
			t.Error("failed pair left first output")
		}
		if got, _ := os.ReadFile(b); string(got) != "keep" {
			t.Fatal("existing output changed")
		}
		if err := os.Remove(b); err != nil {
			t.Fatal(err)
		}
		if err := command(context.Background(), args, io.Discard); err != nil {
			t.Fatal("retry failed", err)
		}
	})
	t.Run("invalid-evidence", func(t *testing.T) {
		d := t.TempDir()
		in := filepath.Join(d, "schema.json")
		os.WriteFile(in, []byte(`{"schema_version":"fi.otlpbench.v4"}`), 0600)
		err := command(context.Background(), []string{"publish", "--markdown", filepath.Join(d, "a.txt"), "--json", filepath.Join(d, "b.json"), in}, io.Discard)
		if err == nil {
			t.Error("accepted schema-only input")
		}
		files, _ := os.ReadDir(d)
		if len(files) != 1 {
			t.Error("invalid input created outputs")
		}
	})
	t.Run("concurrent-pair", func(t *testing.T) {
		p := repairPlan(t)
		d := t.TempDir()
		a, b := filepath.Join(d, "a.txt"), filepath.Join(d, "b.json")
		var wg sync.WaitGroup
		errs := make(chan error, 2)
		start := make(chan struct{})
		for i := 0; i < 2; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				<-start
				errs <- command(context.Background(), []string{"publish", "--markdown", a, "--json", b, p}, io.Discard)
			}()
		}
		close(start)
		wg.Wait()
		close(errs)
		ok := 0
		for e := range errs {
			if e == nil {
				ok++
			}
		}
		if ok != 1 {
			t.Fatalf("%d successful writers, want one", ok)
		}
		var pub ingestbench.Publication
		body, err := os.ReadFile(b)
		if err != nil || json.Unmarshal(body, &pub) != nil {
			t.Fatal("invalid publication", err)
		}
		files, _ := os.ReadDir(d)
		if len(files) != 2 {
			t.Fatal("staging files leaked")
		}
		for _, p := range []string{a, b} {
			s, _ := os.Stat(p)
			if s.Mode().Perm() != 0600 {
				t.Fatal("output permissions", s.Mode())
			}
		}
	})
}

// Exercise the actual main signal/exit policy without a nested go build.
func TestRepairCLIProcess(t *testing.T) {
	if os.Getenv("OTLPBENCH_REPAIR_CHILD") != "1" {
		return
	}
	for i, a := range os.Args {
		if a == "--" {
			os.Args = append([]string{"otlpbench"}, os.Args[i+1:]...)
			main()
			os.Exit(0)
		}
	}
	os.Exit(99)
}
func repairProcess(args ...string) *exec.Cmd {
	c := exec.Command(os.Args[0], append([]string{"-test.run=^TestRepairCLIProcess$", "--"}, args...)...)
	c.Env = append(os.Environ(), "OTLPBENCH_REPAIR_CHILD=1")
	return c
}
func TestRepairHelpExitAndDiscovery(t *testing.T) {
	for _, sub := range []string{"", "report", "size", "verify", "publish", "collector-budget"} {
		for _, flag := range []string{"-h", "--help"} {
			t.Run(sub+flag, func(t *testing.T) {
				args := []string{flag}
				if sub != "" {
					args = append([]string{sub}, args...)
				}
				out, err := repairProcess(args...).CombinedOutput()
				if err != nil {
					t.Fatalf("help must exit 0: %v: %s", err, out)
				}
				if sub == "" {
					for _, s := range []string{"report", "size", "verify", "publish", "collector-budget"} {
						if !bytes.Contains(out, []byte(s)) {
							t.Errorf("root help omits %s", s)
						}
					}
				}
				if sub == "publish" && !bytes.Contains(out, []byte("result.json")) {
					t.Error("publish help omits required result file")
				}
			})
		}
	}
}
func TestRepairBlockedReportSIGINT(t *testing.T) {
	p := repairPlan(t)
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	var r ingestbench.Result
	if err := json.Unmarshal(b, &r); err != nil {
		t.Fatal(err)
	}
	r.Warnings = make([]string, 6000)
	for i := range r.Warnings {
		r.Warnings[i] = strings.Repeat("x", 1024)
	}
	b, err = json.Marshal(r)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, b, 0600); err != nil {
		t.Fatal(err)
	}
	assertBlockedInterrupt(t, repairProcess("report", p), false)
}

func TestRepairSecondInterruptTerminatesGracefulCommand(t *testing.T) {
	args := append(append([]string{}, dryArgs...), "--project-name", strings.Repeat("x", 100000))
	assertBlockedInterrupt(t, repairProcess(args...), true)
}

func assertBlockedInterrupt(t *testing.T, c *exec.Cmd, twice bool) {
	t.Helper()
	pipe, err := c.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	var stderr bytes.Buffer
	c.Stderr = &stderr
	if err := c.Start(); err != nil {
		t.Fatal(err)
	}
	defer pipe.Close()
	done := make(chan error, 1)
	go func() { done <- c.Wait() }()
	prefix := make(chan error, 1)
	go func() { _, err := io.ReadFull(pipe, make([]byte, 4096)); prefix <- err }()
	select {
	case err := <-prefix:
		if err != nil {
			c.Process.Kill()
			<-done
			t.Fatal(err)
		}
	// Parsing the intentionally 6 MiB receipt under -race is startup, not
	// the interrupt assertion. Keep the strict post-signal 2 s bound below.
	case <-time.After(30 * time.Second):
		c.Process.Kill()
		<-done
		t.Fatal("no output")
	}
	if err := c.Process.Signal(os.Interrupt); err != nil {
		c.Process.Kill()
		<-done
		t.Fatal(err)
	}
	if twice {
		// Allow the cancellation observer to restore signal defaults. The first
		// signal may also terminate if the command has not installed a handler.
		select {
		case <-done:
			return
		case <-time.After(150 * time.Millisecond):
		}
		if err := c.Process.Signal(os.Interrupt); err != nil {
			c.Process.Kill()
			<-done
			t.Fatal(err)
		}
	}
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("unexpected normal completion of blocked report")
		}
	case <-time.After(2 * time.Second):
		c.Process.Kill()
		<-done
		t.Fatal(fmt.Sprint("SIGINT ignored while stdout blocked: ", stderr.String()))
	}
}
