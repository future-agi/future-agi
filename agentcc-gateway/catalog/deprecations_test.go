package catalog

import (
	"os"
	"testing"
	"time"
)

// The embedded calendar is the file in this directory, and it parses.
func TestEmbeddedDeprecationsParse(t *testing.T) {
	cal, err := Deprecations()
	if err != nil {
		t.Fatal(err)
	}
	disk, err := os.ReadFile("deprecations.json")
	if err != nil {
		t.Fatal(err)
	}
	if string(disk) != string(deprecationsJSON) {
		t.Fatal("embedded calendar differs from deprecations.json")
	}
	if cal.Len() == 0 {
		t.Fatal("the shipped calendar is empty")
	}
	if _, gone := cal.ShutDown("openai", "dall-e-3", time.Date(2026, 10, 16, 0, 0, 0, 0, time.UTC)); !gone {
		t.Error("dall-e-3 (shut down 2026-05-12) not refused")
	}
}
