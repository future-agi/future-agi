package main

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"testing"
)

func TestOutputCommitRefusesLateCollisionAndRollsBack(t *testing.T) {
	d := t.TempDir()
	a, b := filepath.Join(d, "a.txt"), filepath.Join(d, "b.json")
	s, err := stageOutputs(a, b)
	if err != nil {
		t.Fatal(err)
	}
	for _, f := range s.files {
		if _, err := f.WriteString("staged"); err != nil {
			t.Fatal(err)
		}
	}
	// A writer arrives after advisory checks but before the exclusive commit.
	if err := os.WriteFile(b, []byte("other writer"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := s.commit(); err == nil {
		t.Fatal("late collision was overwritten")
	}
	s.cleanup()
	if _, err := os.Lstat(a); !os.IsNotExist(err) {
		t.Fatal("first link survived failed pair", err)
	}
	if body, _ := os.ReadFile(b); string(body) != "other writer" {
		t.Fatal("other writer changed")
	}
	files, _ := os.ReadDir(d)
	if len(files) != 1 {
		t.Fatal("staging files leaked")
	}
}

func TestOutputCleanupDoesNotRemoveReplacement(t *testing.T) {
	for _, reserved := range []bool{false, true} {
		d := t.TempDir()
		p := filepath.Join(d, "output.json")
		var s *outputSet
		var err error
		if reserved {
			s, err = reserveOutputs(p)
		} else {
			s, err = stageOutputs(p)
		}
		if err != nil {
			t.Fatal(err)
		}
		if !reserved {
			if err := os.Link(s.files[0].Name(), p); err != nil {
				t.Fatal(err)
			}
			s.linked[0] = true
		}
		if err := os.Remove(p); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte("replacement"), 0600); err != nil {
			t.Fatal(err)
		}
		s.cleanup()
		if body, _ := os.ReadFile(p); string(body) != "replacement" {
			t.Fatal("cleanup deleted a replacement")
		}
	}
}

func TestLoadReservesTelemetryBeforeAnyExecution(t *testing.T) {
	d := t.TempDir()
	p := filepath.Join(d, "result.json")
	if err := os.WriteFile(p+".telemetry.jsonl", []byte("keep"), 0600); err != nil {
		t.Fatal(err)
	}
	args := append(append([]string{}, dryArgs...), "--execute", "--allow-unauthenticated-fixture", "--out", p)
	if err := command(context.Background(), args, io.Discard); err == nil {
		t.Fatal("existing telemetry accepted")
	}
	if _, err := os.Lstat(p); !os.IsNotExist(err) {
		t.Fatal("failed reservation left result", err)
	}
	if body, _ := os.ReadFile(p + ".telemetry.jsonl"); string(body) != "keep" {
		t.Fatal("telemetry changed")
	}
}
