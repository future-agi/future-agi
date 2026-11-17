package routebinding

import (
	"errors"
	"testing"
)

func TestReservationRejectsInvalidOwnerAndEndedWrite(t *testing.T) {
	g := testGate(t)
	o := testOwner(t, g)
	var w *Write
	var reservation *Reservation
	if err := g.Publish(func(current *Write) error {
		w = current
		copyOwner := *o
		for _, owner := range []*Owner{nil, {}, &copyOwner} {
			if _, err := owner.Reserve(w); !errors.Is(err, ErrOwner) {
				t.Fatal("invalid owner reserved")
			}
			if err := owner.Commit(w, nil, func(Stamp) { t.Fatal("invalid owner ran") }); !errors.Is(err, ErrOwner) {
				t.Fatal("invalid owner committed")
			}
		}
		var err error
		reservation, err = o.Reserve(w)
		return err
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := o.Reserve(w); !errors.Is(err, ErrScope) {
		t.Fatal("ended write reserved")
	}
	if err := o.Commit(w, reservation, func(Stamp) { t.Fatal("ended write ran") }); !errors.Is(err, ErrScope) {
		t.Fatal("ended write committed")
	}
	if err := g.Publish(func(fresh *Write) error {
		if err := o.Commit(fresh, reservation, nil); !errors.Is(err, ErrCallback) {
			return errors.New("nil install accepted")
		}
		return o.Commit(fresh, reservation, func(Stamp) {})
	}); err != nil {
		t.Fatal("invalid attempts consumed a valid reservation")
	}
	if err := g.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := o.Reserve(w); !errors.Is(err, ErrScope) {
		t.Fatal("shutdown revived a retained token")
	}
}

func TestReservationPanicIsConsumedNotRollback(t *testing.T) {
	g := testGate(t)
	o := testOwner(t, g)
	var reservation *Reservation
	func() {
		defer func() {
			if recover() == nil {
				t.Error("install did not panic")
			}
		}()
		_ = g.Publish(func(w *Write) error {
			var err error
			reservation, err = o.Reserve(w)
			if err != nil {
				return err
			}
			return o.Commit(w, reservation, func(Stamp) { panic("synthetic") })
		})
	}()
	if err := g.Publish(func(w *Write) error {
		return expectError(o.Commit(w, reservation, func(Stamp) { t.Fatal("replayed failed install") }), ErrReservation)
	}); err != nil {
		t.Fatal(err)
	}
}

func TestConcurrentNextIsSerializedAndReadCallbackErrorPropagates(t *testing.T) {
	g := testGate(t)
	if err := g.Publish(func(w *Write) error {
		stamps := make(chan Stamp, 8)
		errorsSeen := make(chan error, 8)
		for i := 0; i < cap(stamps); i++ {
			go func() {
				stamp, err := w.Next()
				stamps <- stamp
				errorsSeen <- err
			}()
		}
		seen := make(map[Stamp]bool)
		for i := 0; i < cap(stamps); i++ {
			result(t, errorsSeen)
			stamp := <-stamps
			if !stamp.Valid() || seen[stamp] {
				return errors.New("concurrent allocator repeated an ordinal")
			}
			seen[stamp] = true
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	sentinel := errors.New("synthetic read failure")
	if err := g.Read(func(r *Read) error {
		return r.Use(g, func() error { return sentinel })
	}); !errors.Is(err, sentinel) {
		t.Fatal("read callback error was lost")
	}
	if err := g.Read(nil); !errors.Is(err, ErrCallback) {
		t.Fatal("nil read callback accepted")
	}
	if err := g.Publish(nil); !errors.Is(err, ErrCallback) {
		t.Fatal("nil publication callback accepted")
	}
}
