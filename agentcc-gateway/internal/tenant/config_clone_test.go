package tenant

import (
	"errors"
	"reflect"
	"testing"
	"unsafe"
)

type cloneNamed []map[string]*int
type privateReference struct{ hidden []int }
type cycleNode struct{ Next *cycleNode }

func TestConfigCloneWholeGraph(t *testing.T) {
	c := &OrgConfig{}
	populateCloneFixture(reflect.ValueOf(c).Elem(), 0)
	c.Routing.fallbackOff = true
	n := 3
	c.Routing.ConditionalRoutes = []ConditionalRoute{{Value: cloneNamed{{"x": &n}}}}
	clone, err := cloneConfigData(c)
	if err != nil || clone == nil {
		t.Fatal("supported graph rejected")
	}
	if !reflect.DeepEqual(c, clone) {
		t.Fatal("whole-field or concrete type lost")
	}
	if !clone.Routing.fallbackOff {
		t.Fatal("private routing state lost")
	}
	assertDisjoint(t, reflect.ValueOf(c), reflect.ValueOf(clone))
	(*c.Routing.ConditionalRoutes[0].Value.(cloneNamed)[0]["x"]) = 8
	if *clone.Routing.ConditionalRoutes[0].Value.(cloneNamed)[0]["x"] != 3 {
		t.Fatal("dynamic descendant borrowed")
	}
}

// Populate every exported field, not an allowlisted subset of OrgConfig.
func populateCloneFixture(v reflect.Value, depth int) {
	if depth > 18 || !v.CanSet() {
		return
	}
	switch v.Kind() {
	case reflect.Pointer:
		v.Set(reflect.New(v.Type().Elem()))
		populateCloneFixture(v.Elem(), depth+1)
	case reflect.Struct:
		for i := 0; i < v.NumField(); i++ {
			populateCloneFixture(v.Field(i), depth+1)
		}
	case reflect.Map:
		v.Set(reflect.MakeMap(v.Type()))
		k := reflect.New(v.Type().Key()).Elem()
		populateCloneFixture(k, depth+1)
		x := reflect.New(v.Type().Elem()).Elem()
		populateCloneFixture(x, depth+1)
		v.SetMapIndex(k, x)
	case reflect.Slice:
		v.Set(reflect.MakeSlice(v.Type(), 1, 3))
		populateCloneFixture(v.Index(0), depth+1)
	case reflect.Array:
		for i := 0; i < v.Len(); i++ {
			populateCloneFixture(v.Index(i), depth+1)
		}
	case reflect.Interface:
		v.Set(reflect.ValueOf(map[string]any{"x": []int{1}}))
	case reflect.Bool:
		v.SetBool(true)
	case reflect.String:
		v.SetString("synthetic")
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		v.SetInt(1)
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		v.SetUint(1)
	case reflect.Float32, reflect.Float64:
		v.SetFloat(1)
	}
}
func assertDisjoint(t *testing.T, a, b reflect.Value) {
	t.Helper()
	if !a.IsValid() {
		return
	}
	switch a.Kind() {
	case reflect.Pointer, reflect.Map, reflect.Slice:
		if a.IsNil() {
			return
		}
		if a.UnsafePointer() == b.UnsafePointer() && (a.Kind() != reflect.Slice || a.Len() != 0) {
			t.Fatal("mutable descendant borrowed")
		}
	}
	switch a.Kind() {
	case reflect.Interface, reflect.Pointer:
		if !a.IsNil() {
			assertDisjoint(t, a.Elem(), b.Elem())
		}
	case reflect.Struct:
		for i := 0; i < a.NumField(); i++ {
			assertDisjoint(t, a.Field(i), b.Field(i))
		}
	case reflect.Map:
		it := a.MapRange()
		for it.Next() {
			assertDisjoint(t, it.Value(), b.MapIndex(it.Key()))
		}
	case reflect.Array, reflect.Slice:
		for i := 0; i < a.Len(); i++ {
			assertDisjoint(t, a.Index(i), b.Index(i))
		}
	}
}
func TestConfigCloneNilEmptyAndBackingArrays(t *testing.T) {
	for _, values := range [][]string{nil, {}, make([]string, 1, 5)} {
		c := &OrgConfig{Providers: map[string]*ProviderConfig{"x": {Models: values}}}
		d, err := cloneConfigData(c)
		if err != nil || !reflect.DeepEqual(c, d) {
			t.Fatal("nil or empty changed")
		}
		if len(values) > 0 {
			values[0] = "changed"
			if d.Providers["x"].Models[0] != "" {
				t.Fatal("backing array borrowed")
			}
		}
	}
	var ptr *int
	c := &OrgConfig{Routing: &RoutingConfig{ConditionalRoutes: []ConditionalRoute{{Value: ptr}}}}
	d, err := cloneConfigData(c)
	if err != nil || reflect.TypeOf(d.Routing.ConditionalRoutes[0].Value) != reflect.TypeOf(ptr) {
		t.Fatal("typed nil lost")
	}
}
func TestConfigCloneRejectsNilPointerKeyMap(t *testing.T) {
	for _, empty := range []bool{false, true} {
		t.Run(map[bool]string{false: "nil", true: "empty"}[empty], func(t *testing.T) {
			var value map[*int]int
			if empty {
				value = make(map[*int]int)
			}
			c := &OrgConfig{Routing: &RoutingConfig{ConditionalRoutes: []ConditionalRoute{{Value: value}}}}
			copied, err := cloneConfigData(c)
			if copied != nil || !errors.Is(err, ErrConfigGraph) {
				t.Fatal("pointer-key map domain depends on current contents")
			}
		})
	}
}

func TestConfigCloneUnsupportedDomain(t *testing.T) {
	node := &cycleNode{}
	node.Next = node
	m := map[string]any{}
	m["self"] = m
	x := 1
	for _, value := range []any{func() {}, make(chan int), unsafe.Pointer(&x), map[*int]int{&x: 1}, privateReference{hidden: []int{1}}, node, m} {
		c := &OrgConfig{Routing: &RoutingConfig{ConditionalRoutes: []ConditionalRoute{{Value: value}}}}
		d, err := cloneConfigData(c)
		if d != nil || !errors.Is(err, ErrConfigGraph) {
			t.Fatal("unsupported graph borrowed")
		}
	}
}
