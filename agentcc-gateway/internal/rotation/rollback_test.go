package rotation

import (
	"context"
	"strings"
	"testing"
	"time"
)

// TestRotationGoingBackAfterAPromote: a draining rotation
// is not rolled back, and its refusal tells the operator what works and when:
// a new rotation with the previous key, which starts once drain_until has
// passed. A rotation asked for while draining is refused with that time too,
// and the advice, followed after drain_until, makes the previous key primary.
func TestRotationGoingBackAfterAPromote(t *testing.T) {
	m := NewManager(30*time.Second, nil)
	m.RegisterProviderWithDrain("openai", "sk-previous", 150*time.Millisecond)
	if err := m.StartRotation("openai", "sk-promoted"); err != nil {
		t.Fatal(err)
	}
	if _, err := m.Promote(context.Background(), "openai"); err != nil {
		t.Fatal(err)
	}
	st, err := m.GetStatus("openai")
	if err != nil || st.Status != StatusDraining || st.DrainUntil == nil {
		t.Fatalf("after promote: %+v, %v; want draining with drain_until", st, err)
	}
	until := st.DrainUntil.UTC().Format(time.RFC3339Nano)

	err = m.Rollback("openai")
	if err == nil {
		t.Fatal("rollback of a draining rotation succeeded")
	}
	if msg := err.Error(); !strings.Contains(msg, "after drain_until "+until) || !strings.Contains(msg, "start a new rotation with the previous key") {
		t.Errorf("rollback while draining: %q; want it to say: after drain_until %s, start a new rotation with the previous key", msg, until)
	}
	err = m.StartRotation("openai", "sk-previous")
	if err == nil {
		t.Fatal("a rotation started while the previous one drains")
	}
	if msg := err.Error(); !strings.Contains(msg, "draining until "+until) {
		t.Errorf("rotate while draining: %q; want it to name drain_until %s", msg, until)
	}

	time.Sleep(time.Until(*st.DrainUntil) + 100*time.Millisecond)
	if err := m.StartRotation("openai", "sk-previous"); err != nil {
		t.Fatalf("rotate with the previous key after drain_until: %v", err)
	}
	if _, err := m.Promote(context.Background(), "openai"); err != nil {
		t.Fatal(err)
	}
	if st, _ := m.GetStatus("openai"); st.Primary != "sk-previous" {
		t.Errorf("after going back, primary = %q, want the previous key", st.Primary)
	}
}
