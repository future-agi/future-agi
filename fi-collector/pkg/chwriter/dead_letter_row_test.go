package chwriter

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// deadLetterIDs decodes the dead-letter file into id -> row.
func deadLetterIDs(t *testing.T, path string) map[int]map[string]any {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	out := map[int]map[string]any{}
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		var row map[string]any
		if err := json.Unmarshal([]byte(line), &row); err != nil {
			t.Fatalf("dead-letter line %q: %v", line, err)
		}
		id, _ := row["id"].(float64)
		out[int(id)] = row
	}
	return out
}

// TestInsertConfirmedWithDeadLetterRow: with InsertOptions.DeadLetterRow the
// dead-letter file holds the rewritten form of every dead-lettered row, on
// each path that dead-letters (a refused insert, and the unsent rest of the
// batch once ClickHouse is unavailable, sequential and concurrent). Stored
// rows are sent unchanged, the caller's rows are never modified, and the
// zero option keeps dev's dead-letter lines byte for byte.
func TestInsertConfirmedWithDeadLetterRow(t *testing.T) {
	rewrite := func(calls *atomic.Int64) func(map[string]any) map[string]any {
		return func(row map[string]any) map[string]any {
			calls.Add(1)
			out := map[string]any{"rewritten": true}
			for k, v := range row {
				out[k] = v
			}
			return out
		}
	}
	t.Run("refused insert", func(t *testing.T) {
		w, ch, dl := newChunkWriter(t, `"id":42,`)
		w.SetMaxInsertBytes(10 << 10)
		rows := chunkRows(100, 1000)
		var calls atomic.Int64
		confirmed, _, err := w.InsertConfirmedWith(context.Background(), rows, InsertOptions{DeadLetterRow: rewrite(&calls)})
		if err == nil {
			t.Fatal("the refused insert must be reported")
		}
		dead := deadLetterIDs(t, dl)
		if _, ok := dead[42]; !ok || int(calls.Load()) != len(dead) {
			t.Fatalf("dead-lettered %d rows with %d rewrites", len(dead), calls.Load())
		}
		for id, row := range dead {
			if row["rewritten"] != true || confirmed[id] {
				t.Fatalf("row %d dead-lettered without the rewrite (or also confirmed)", id)
			}
		}
		for _, b := range ch.bodies {
			if strings.Contains(string(b), "rewritten") {
				t.Fatal("a stored row was sent rewritten")
			}
		}
		for _, row := range rows {
			if _, ok := row["rewritten"]; ok {
				t.Fatal("the caller's row was modified")
			}
		}
	})
	for _, concurrent := range []bool{false, true} {
		name := "unsent rest, sequential"
		if concurrent {
			name = "unsent rest, concurrent"
		}
		t.Run(name, func(t *testing.T) {
			w, ch, dl := newSlowCH(t, 10*time.Millisecond, map[int]bool{0: true})
			w.SetMaxInsertBytes(10 << 10)
			if concurrent {
				w.SetMaxConcurrentInserts(2)
			}
			rows := chunkRows(100, 1000)
			var calls atomic.Int64
			if _, _, err := w.InsertConfirmedWith(context.Background(), rows, InsertOptions{DeadLetterRow: rewrite(&calls)}); err == nil {
				t.Fatal("an unavailable ClickHouse must be reported")
			}
			dead := deadLetterIDs(t, dl)
			if len(dead) == 0 || int(calls.Load()) != len(dead) {
				t.Fatalf("dead-lettered %d rows with %d rewrites", len(dead), calls.Load())
			}
			for id, row := range dead {
				if row["rewritten"] != true {
					t.Fatalf("row %d dead-lettered without the rewrite", id)
				}
			}
			for i := range rows {
				if _, isDead := dead[i]; ch.stored[i]+map[bool]int{true: 1}[isDead] != 1 {
					t.Fatalf("row %d stored %d times, dead-lettered %v", i, ch.stored[i], isDead)
				}
			}
		})
	}
	t.Run("zero option", func(t *testing.T) {
		w, _, dl := newChunkWriter(t, `"id":7,`)
		rows := chunkRows(10, 10)
		if _, _, err := w.InsertConfirmedWith(context.Background(), rows, InsertOptions{}); err == nil {
			t.Fatal("refused")
		}
		for _, row := range deadLetterIDs(t, dl) {
			if _, ok := row["rewritten"]; ok {
				t.Fatal("no rewrite without the option")
			}
		}
	})
}
