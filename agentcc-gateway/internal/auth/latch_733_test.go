package auth

import (
	"bytes"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"testing"
)

// seqLogBuffer is a slog destination the sync's goroutines may share.
type seqLogBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *seqLogBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *seqLogBuffer) take() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	s := b.buf.String()
	b.buf.Reset()
	return s
}

// 733 add.11 (a): ops see a pre-733 control plane as a log line (and as a
// /readyz field: internal/server's
// Test733ReadyzReportsAPre733ControlPlaneUntilThe733Latch) until the first
// 733 answer latches 733 semantics. The line is logged once, when the first
// pre-733 answer is applied before any 733 answer, which is normally the
// startup sync's, and never once the latch holds. A standalone gateway has
// no control plane to report.
func Test733Pre733ControlPlaneIsLoggedUntilThe733Latch(t *testing.T) {
	const line = "control plane pre-733"
	logs := &seqLogBuffer{}
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(logs, &slog.HandlerOptions{Level: slog.LevelDebug})))
	t.Cleanup(func() { slog.SetDefault(prev) })

	lists := []string{seqPre733, seq733, seq733FreshEmpty, seq733Incomplete}
	for _, controlPlane := range []bool{true, false} {
		for _, steps := range seqSequences(lists, 3) {
			mode := "standalone"
			if controlPlane {
				mode = "control-plane"
			}
			t.Run(fmt.Sprintf("%s,%s", mode, strings.Join(steps, ">")), func(t *testing.T) {
				w := newSeqWorld(controlPlane, true, true)
				ks := deployOrderStore(w.config(), controlPlane)
				cp := newSeqControlPlane(t)
				logs.take()
				announced, latched := false, false
				for i, step := range steps {
					cp.pull(t, ks, w.answer(step))
					w.expect(step)
					want := 0
					if controlPlane && step == seqPre733 && !latched && !announced {
						want = 1
					}
					if step == seqPre733 && !latched {
						announced = true
					}
					if step != seqPre733 {
						latched = true
					}
					if got := strings.Count(logs.take(), line); got != want {
						t.Errorf("event %d (%s): %q logged %d times, want %d (latched=%v)", i+1, step, line, got, want, latched)
					}
				}
			})
		}
	}
}
