package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
)

func TestLegacyCheckpointMaximumEscapedValueResumes(t *testing.T) {
	for _, char := range []string{"\x00", "<", "\\"} {
		t.Run(char, func(t *testing.T) {
			raw, err := json.Marshal(strings.Repeat(char, observedcatalog.MaxStringValueBytes))
			if err != nil {
				t.Fatal(err)
			}
			value := checkpoint{Binding: strings.Repeat("a", 64), Hour: time.Now().UTC(), Pages: 1,
				Legacy: legacyCursor{Values: true, SourceKind: "custom_attribute", AttributeType: "string",
					AttributeKey:     strings.Repeat("<", observedcatalog.MaxKeyBytes),
					ValueFingerprint: strings.Repeat("b", 64), ValueJSON: string(raw)}}
			path := filepath.Join(t.TempDir(), "progress.json")
			if err := saveCheckpoint(path, value); err != nil {
				t.Fatal(err)
			}
			resumed, err := loadCheckpoint(path, value.Binding, value.Hour)
			if err != nil || !reflect.DeepEqual(resumed, value) {
				t.Fatalf("maximum eligible value could not resume: %v", err)
			}
		})
	}
}

func TestOversizedCheckpointDoesNotReplaceProgress(t *testing.T) {
	path := filepath.Join(t.TempDir(), "progress.json")
	value := checkpoint{Binding: "scope", Hour: time.Now().UTC(), Pages: 1}
	if err := saveCheckpoint(path, value); err != nil {
		t.Fatal(err)
	}
	oversized := value
	oversized.Legacy.ValueJSON = strings.Repeat("x", maxCheckpointBytes)
	if err := saveCheckpoint(path, oversized); err == nil {
		t.Fatal("oversized checkpoint accepted")
	}
	resumed, err := loadCheckpoint(path, value.Binding, value.Hour)
	if err != nil || !reflect.DeepEqual(resumed, value) {
		t.Fatal("failed save replaced valid progress", err)
	}
	if err := os.WriteFile(path, []byte(oversized.Legacy.ValueJSON+"x"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := loadCheckpoint(path, value.Binding, value.Hour); err == nil {
		t.Fatal("oversized checkpoint loaded")
	}
}
