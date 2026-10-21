package ingestbench

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"testing"
)

type failingWriter struct{}

func (failingWriter) Write([]byte) (int, error) { return 0, errors.New("fixture disk full") }

func TestTelemetryIsVersionedAndCarriesFinalCounts(t *testing.T) {
	var out bytes.Buffer
	ctx, cancel := context.WithCancel(context.Background())
	s := &runState{counters: Counters{Generated: 12, Attempted: 12, Acknowledged: 10, PartialRejected: 1}}
	stop, wait := startTelemetry(ctx, testConfig(), clickHouse{}, s, &out)
	stop()
	cancel()
	if err := wait(); err != nil {
		t.Fatal(err)
	}
	d := json.NewDecoder(&out)
	foundFinal := false
	for {
		var event telemetryEvent
		err := d.Decode(&event)
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		if event.SchemaVersion != SchemaVersion || event.RunID != testConfig().RunID || event.ElapsedSeconds < 0 {
			t.Fatal("unversioned/invalid telemetry")
		}
		if event.Kind == "progress-final" {
			foundFinal = true
			if event.Counters.Attempted != 12 || event.Counters.Unacknowledged != 1 {
				t.Fatal("incorrect final counter snapshot")
			}
		}
	}
	if !foundFinal {
		t.Fatal("missing final interval")
	}
}

func TestTelemetryReportsArtifactWriteFailure(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	stop, wait := startTelemetry(ctx, testConfig(), clickHouse{}, &runState{}, failingWriter{})
	stop()
	cancel()
	if err := wait(); err == nil {
		t.Fatal("telemetry write failure hidden")
	}
}
