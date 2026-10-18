package video_test

import (
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
	"testing"
)

func TestRegistry(t *testing.T) {
	r := video.NewRegistry()
	f := videotest.NewFakeProvider(videotest.Script{})
	t.Cleanup(f.Close)
	if err := r.Register(f.Adapter()); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name string
		ok   bool
	}{{"fake", true}, {"unknown", false}} {
		t.Run(tc.name, func(t *testing.T) {
			_, ok := r.Get(tc.name)
			if ok != tc.ok {
				t.Fatal(ok)
			}
		})
	}
	if err := r.Register(f.Adapter()); err == nil {
		t.Fatal("duplicate accepted")
	}
	if err := r.Register(nil); err == nil {
		t.Fatal("nil accepted")
	}
	if len(r.Services()) != 1 {
		t.Fatal(r.Services())
	}
}
