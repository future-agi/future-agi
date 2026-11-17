package redisstate

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestR2StrictRunProcessCrashAndLateActual(t *testing.T) {
	if prefix := os.Getenv("SOL_R2_CHILD_PREFIX"); prefix != "" {
		db, err := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
		if err != nil {
			t.Fatal(err)
		}
		client, err := NewClient(Config{Address: os.Getenv("TEST_REDIS_ADDR"), DB: db, Timeout: time.Second})
		if err != nil {
			t.Fatal(err)
		}
		store := NewAgentBudgetStore(client, prefix)
		result, err := store.ReserveStrict(context.Background(), "g8-org", "crashed-run", "child", 4000, 5000, time.Millisecond, time.Second)
		if err != nil || result.Status != AgentBudgetOK {
			t.Fatalf("child reserve=%+v error=%v", result, err)
		}
		fmt.Println("SYNTHETIC-HOLD-DURABLE")
		for {
			time.Sleep(time.Hour)
		}
	}
	for _, actual := range []int64{0, 1000, 4000} {
		t.Run(fmt.Sprint(actual), func(t *testing.T) {
			s := capTestStore(t).store
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			child := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestR2StrictRunProcessCrashAndLateActual$", "-test.v")
			child.Env = append(os.Environ(), "SOL_R2_CHILD_PREFIX="+s.prefix)
			pipe, err := child.StdoutPipe()
			if err != nil {
				t.Fatal(err)
			}
			child.Stderr = os.Stderr
			if err := child.Start(); err != nil {
				t.Fatal(err)
			}
			defer func() {
				if child.ProcessState == nil {
					_ = child.Process.Kill()
					_ = child.Wait()
				}
			}()
			reader := bufio.NewScanner(pipe)
			ready := false
			for reader.Scan() {
				if strings.Contains(reader.Text(), "SYNTHETIC-HOLD-DURABLE") {
					ready = true
					break
				}
			}
			if !ready {
				t.Fatal("child never reserved")
			}
			if err := child.Process.Kill(); err != nil {
				t.Fatal(err)
			}
			if err := child.Wait(); err == nil {
				t.Fatal("child was not killed")
			}
			replica := NewAgentBudgetStore(s.client, s.prefix)
			replica.now = func() time.Time { return time.Now().Add(10 * 24 * time.Hour) }
			result, err := replica.ReserveStrict(bg, "g8-org", "crashed-run", "next", 4000, 5000, time.Minute, time.Second)
			if err != nil || result.Status != "uncertain" || result.HeldMicros != 4000 {
				t.Fatalf("post-crash reserve=%+v err=%v", result, err)
			}
			if _, err := replica.Void(bg, "g8-org", "crashed-run", "child", time.Second); err == nil {
				t.Fatal("legacy void entered strict scope")
			}
			for i := 0; i < 2; i++ {
				result, err = replica.Complete(bg, "g8-org", "crashed-run", "child", actual, time.Second)
				if err != nil || result.SpentMicros != actual || result.HeldMicros != 0 {
					t.Fatalf("late complete=%+v err=%v", result, err)
				}
			}
			result, err = replica.ReserveStrict(bg, "g8-org", "crashed-run", "next", 4000, 5000, time.Minute, time.Second)
			want := AgentBudgetOK
			if actual > 1000 {
				want = AgentBudgetExhausted
			}
			if err != nil || result.Status != want {
				t.Fatalf("post-actual reserve=%+v err=%v want=%s", result, err, want)
			}
		})
	}
}

// Durable counters must be valid amounts, not just present hash members.
func TestR2MalformedDailyCounterFailsClosed(t *testing.T) {
	for _, field := range []string{"spent:agent:a", "held:agent:a"} {
		t.Run(field, func(t *testing.T) {
			s := capTestStore(t)
			buckets := activatedBuckets(t, "agent:a", 5000, 4000)
			result, err := s.Reserve(bg, "g8-org", "2026-10-27", "unknown", 1, buckets, time.Now().Add(time.Minute))
			if err != nil || result.Status != "ok" {
				t.Fatalf("initial=%+v err=%v", result, err)
			}
			keys, err := s.keys("g8-org", "2026-10-27")
			if err != nil {
				t.Fatal(err)
			}
			if err := s.store.client.Redis().HSet(bg, keys[0], field, -4000).Err(); err != nil {
				t.Fatal(err)
			}
			result, err = s.Reserve(bg, "g8-org", "2026-10-27", "next", 1, buckets, time.Now().Add(time.Minute))
			if err == nil && result.Status == "ok" {
				t.Errorf("invalid durable counter admitted new work: field=%s result=%+v counters=%v", field, result, s.store.client.Redis().HGetAll(bg, keys[0]).Val())
			}
		})
	}
}

func TestR2StrictRunAmbiguousRepliesRemainFailClosed(t *testing.T) {
	s := capTestStore(t).store
	hook := &capLostReply{}
	s.client.Redis().AddHook(hook)
	hook.armed.Store(true)
	if _, err := s.ReserveStrict(bg, "g8-org", "ambiguous", "first", 4000, 5000, time.Minute, time.Second); err == nil {
		t.Fatal("lost reserve reply not injected")
	}
	result, err := s.ReserveStrict(bg, "g8-org", "ambiguous", "second", 4000, 5000, time.Minute, time.Second)
	if err != nil || result.Status != AgentBudgetExhausted || result.HeldMicros != 4000 {
		t.Fatalf("lost reply reopened hold: result=%+v error=%v", result, err)
	}
	hook.armed.Store(true)
	if _, err := s.Complete(bg, "g8-org", "ambiguous", "first", 3000, time.Second); err == nil {
		t.Fatal("lost settlement reply not injected")
	}
	result, err = s.Complete(bg, "g8-org", "ambiguous", "first", 3000, time.Second)
	if err != nil || result.SpentMicros != 3000 || result.HeldMicros != 0 {
		t.Fatalf("late replay double-settled: result=%+v error=%v", result, err)
	}
	if _, err := s.Complete(bg, "g8-org", "ambiguous", "first", 0, time.Second); err == nil {
		t.Fatal("uncertain reply allowed a conflicting zero")
	}
	result, err = s.ReserveStrict(bg, "g8-org", "ambiguous", "second", 4000, 5000, time.Minute, time.Second)
	if err != nil || result.Status != AgentBudgetExhausted || result.SpentMicros != 3000 {
		t.Fatalf("ambiguous settlement erased actuals: result=%+v error=%v", result, err)
	}
}
