package routebinding

import (
	"errors"
	"math"
	"testing"
)

func testGate(t *testing.T) *Gate {
	t.Helper()
	g, err := NewGate()
	if err != nil {
		t.Fatal("gate initialization failed")
	}
	return g
}

func testOwner(t *testing.T, g *Gate) *Owner {
	t.Helper()
	o, err := g.NewOwner()
	if err != nil {
		t.Fatal("owner initialization failed")
	}
	return o
}

func TestGateRejectsZeroForeignCopiedAndRetainedTokens(t *testing.T) {
	g, other := testGate(t), testGate(t)
	called := false
	fn := func() error { called = true; return nil }
	if err := new(Gate).Read(func(*Read) error { return fn() }); !errors.Is(err, ErrGate) {
		t.Fatal("zero gate accepted")
	}
	if err := new(Read).Use(g, fn); !errors.Is(err, ErrToken) {
		t.Fatal("zero read accepted")
	}
	if err := new(Write).Use(g, fn); !errors.Is(err, ErrToken) {
		t.Fatal("zero write accepted")
	}
	if _, err := new(Write).Next(); !errors.Is(err, ErrToken) {
		t.Fatal("zero write issued a stamp")
	}
	var retainedRead *Read
	if err := g.Read(func(r *Read) error {
		retainedRead = r
		copyRead := *r
		if err := copyRead.Use(g, fn); !errors.Is(err, ErrToken) {
			t.Fatal("copied read accepted")
		}
		return expectError(r.Use(other, fn), ErrToken)
	}); err != nil {
		t.Fatal(err)
	}
	if err := retainedRead.Use(g, fn); !errors.Is(err, ErrScope) {
		t.Fatal("retained read accepted")
	}
	if err := g.Read(func(*Read) error {
		return expectError(retainedRead.Use(g, fn), ErrScope)
	}); err != nil {
		t.Fatal("old scope accepted during a new scope")
	}
	var retainedWrite *Write
	if err := g.Publish(func(w *Write) error {
		retainedWrite = w
		copyWrite := *w
		if _, err := copyWrite.Next(); !errors.Is(err, ErrToken) {
			t.Fatal("copied write issued a stamp")
		}
		return expectError(w.Use(other, fn), ErrToken)
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := retainedWrite.Next(); !errors.Is(err, ErrScope) {
		t.Fatal("retained write issued a stamp")
	}
	if called {
		t.Fatal("rejected token invoked owner code")
	}
}

func expectError(got, want error) error {
	if !errors.Is(got, want) {
		return errors.New("unexpected publication error class")
	}
	return nil
}

func TestGateOrdinalsEpochBurnAndExhaustion(t *testing.T) {
	g, other := testGate(t), testGate(t)
	var a, burned, b, foreign Stamp
	if err := g.Publish(func(w *Write) error {
		var err error
		a, err = w.Next()
		return err
	}); err != nil {
		t.Fatal(err)
	}
	sentinel := errors.New("synthetic callback failure")
	if err := g.Publish(func(w *Write) error {
		var err error
		burned, err = w.Next()
		if err != nil {
			return err
		}
		return sentinel
	}); !errors.Is(err, sentinel) {
		t.Fatal("callback error was lost")
	}
	if err := g.Publish(func(w *Write) error { var err error; b, err = w.Next(); return err }); err != nil {
		t.Fatal(err)
	}
	if a.ordinal != 1 || burned.ordinal != 2 || b.ordinal != 3 || a.Equal(b) || (Stamp{}).Equal(Stamp{}) {
		t.Fatal("ordinals were reused or zero stamp compared as valid")
	}
	if err := other.Publish(func(w *Write) error { var err error; foreign, err = w.Next(); return err }); err != nil {
		t.Fatal(err)
	}
	if foreign.epoch == a.epoch || foreign.Equal(a) {
		t.Fatal("new gate reused an epoch")
	}
	// Private counter setup is a boundary fixture, not a production setter.
	g.ordinal = math.MaxUint64 - 1
	if err := g.Publish(func(w *Write) error {
		last, err := w.Next()
		if err != nil || last.ordinal != math.MaxUint64 {
			t.Fatal("last ordinal unavailable")
		}
		_, err = w.Next()
		return expectError(err, ErrExhausted)
	}); err != nil {
		t.Fatal(err)
	}
	revoked := false
	if err := g.Publish(func(w *Write) error {
		return w.Use(g, func() error { revoked = true; return nil })
	}); err != nil || !revoked || g.ordinal != math.MaxUint64 {
		t.Fatal("exhaustion blocked revocation or wrapped the ordinal")
	}
}

func TestOwnerReservationsRemainCommittableAfterExhaustion(t *testing.T) {
	g := testGate(t)
	o, other := testOwner(t, g), testOwner(t, g)
	var reservation *Reservation
	if err := g.Publish(func(w *Write) error {
		var err error
		reservation, err = o.Reserve(w)
		return err
	}); err != nil {
		t.Fatal(err)
	}
	// Represents unrelated writers consuming the remaining ordinals while
	// this owner's sequencer performs mandatory disk work outside the gate.
	g.ordinal = math.MaxUint64
	var committed Stamp
	if err := g.Publish(func(w *Write) error {
		copyReservation := *reservation
		for _, r := range []*Reservation{nil, {}, &copyReservation} {
			if err := o.Commit(w, r, func(Stamp) { t.Fatal("invalid reservation ran") }); !errors.Is(err, ErrReservation) {
				t.Fatal("invalid reservation accepted")
			}
		}
		if err := other.Commit(w, reservation, func(Stamp) { t.Fatal("foreign owner ran") }); !errors.Is(err, ErrReservation) {
			t.Fatal("reservation crossed owner boundary")
		}
		if err := o.Commit(w, reservation, func(s Stamp) { committed = s }); err != nil {
			return err
		}
		return expectError(o.Commit(w, reservation, func(Stamp) { t.Fatal("replay ran") }), ErrReservation)
	}); err != nil || !committed.Valid() || committed.ordinal != 1 {
		t.Fatal("reserved commit failed after unrelated exhaustion")
	}
}

func TestOwnerReservationBurnStalenessAndForeignGate(t *testing.T) {
	g, foreign := testGate(t), testGate(t)
	o := testOwner(t, g)
	var old, newer *Reservation
	if err := g.Publish(func(w *Write) error {
		var err error
		old, err = o.Reserve(w)
		if err != nil {
			return err
		}
		_, err = o.Reserve(w) // Failed disk operation: unused ordinal is burned.
		if err != nil {
			return err
		}
		newer, err = o.Reserve(w)
		return err
	}); err != nil {
		t.Fatal(err)
	}
	if err := foreign.Publish(func(w *Write) error {
		return expectError(o.Commit(w, newer, func(Stamp) { t.Fatal("foreign gate ran") }), ErrToken)
	}); err != nil {
		t.Fatal(err)
	}
	if err := g.Publish(func(w *Write) error {
		if err := o.Commit(w, newer, func(s Stamp) {
			if s.ordinal != 3 {
				t.Fatal("burned reservation reused")
			}
		}); err != nil {
			return err
		}
		return expectError(o.Commit(w, old, func(Stamp) { t.Fatal("stale commit ran") }), ErrReservation)
	}); err != nil {
		t.Fatal(err)
	}
}

func TestGatePanicClosesScopeAndQuiescedCloseIsFinal(t *testing.T) {
	g := testGate(t)
	var retained *Read
	func() {
		defer func() {
			if recover() == nil {
				t.Error("callback did not panic")
			}
		}()
		_ = g.Read(func(r *Read) error { retained = r; panic("synthetic") })
	}()
	if err := retained.Use(g, func() error { t.Fatal("panic retained token ran"); return nil }); !errors.Is(err, ErrScope) {
		t.Fatal("panic left scope active")
	}
	if err := g.Publish(func(*Write) error { return nil }); err != nil {
		t.Fatal("panic left fence locked")
	}
	if err := g.Close(); err != nil {
		t.Fatal(err)
	}
	if err := g.Close(); err != nil {
		t.Fatal("close not idempotent")
	}
	if err := g.Read(func(*Read) error { t.Fatal("closed read ran"); return nil }); !errors.Is(err, ErrClosed) {
		t.Fatal("read after close accepted")
	}
	if err := g.Publish(func(*Write) error { t.Fatal("closed write ran"); return nil }); !errors.Is(err, ErrClosed) {
		t.Fatal("write after close accepted")
	}
	if _, err := g.NewOwner(); !errors.Is(err, ErrClosed) {
		t.Fatal("owner created after shutdown")
	}
}
