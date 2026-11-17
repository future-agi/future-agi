package tenant

import "reflect"

// cloneConfigData never calls user methods. Inputs must remain stable for the
// entire synchronous copy. Shared subgraph identity is not part of the contract;
// all mutable descendants, including slice backing arrays, are detached.
func cloneConfigData(src *OrgConfig) (*OrgConfig, error) {
	if src == nil {
		return nil, nil
	}
	v, err := cloneValue(reflect.ValueOf(src), make(map[cloneVisit]bool))
	if err != nil {
		return nil, err
	}
	return v.Interface().(*OrgConfig), nil
}

type cloneVisit struct {
	typ     reflect.Type
	pointer uintptr
	length  int
}

var orgConfigType = reflect.TypeOf(OrgConfig{})

func cloneValue(v reflect.Value, active map[cloneVisit]bool) (reflect.Value, error) {
	if !v.IsValid() {
		return v, nil
	}
	typ := v.Type()
	// Pointer-key maps are outside the domain even when currently nil/empty.
	if v.Kind() == reflect.Map && typ.Key().Kind() != reflect.Interface && !scalarGraphType(typ.Key()) {
		return reflect.Value{}, ErrConfigGraph
	}
	out := reflect.New(typ).Elem()
	switch v.Kind() {
	case reflect.Pointer, reflect.Map, reflect.Slice:
		if v.IsNil() {
			return out, nil
		}
		id := cloneVisit{typ: typ, pointer: uintptr(v.UnsafePointer())}
		if v.Kind() == reflect.Slice {
			id.length = v.Len()
		}
		if active[id] {
			return reflect.Value{}, ErrConfigGraph
		}
		active[id] = true
		defer delete(active, id)
	}
	switch v.Kind() {
	case reflect.Pointer:
		item, err := cloneValue(v.Elem(), active)
		if err != nil {
			return reflect.Value{}, err
		}
		out.Set(reflect.New(typ.Elem()))
		out.Elem().Set(item)
	case reflect.Interface:
		if !v.IsNil() {
			item, err := cloneValue(v.Elem(), active)
			if err != nil {
				return reflect.Value{}, err
			}
			out.Set(item)
		}
	case reflect.Map:
		out.Set(reflect.MakeMapWithSize(typ, v.Len()))
		it := v.MapRange()
		for it.Next() {
			if !scalarKey(it.Key()) {
				return reflect.Value{}, ErrConfigGraph
			}
			key, err := cloneValue(it.Key(), active)
			if err != nil {
				return reflect.Value{}, err
			}
			value, err := cloneValue(it.Value(), active)
			if err != nil {
				return reflect.Value{}, err
			}
			out.SetMapIndex(key, value)
		}
	case reflect.Slice:
		out.Set(reflect.MakeSlice(typ, v.Len(), v.Len()))
		for i := 0; i < v.Len(); i++ {
			item, err := cloneValue(v.Index(i), active)
			if err != nil {
				return reflect.Value{}, err
			}
			out.Index(i).Set(item)
		}
	case reflect.Array:
		for i := 0; i < v.Len(); i++ {
			item, err := cloneValue(v.Index(i), active)
			if err != nil {
				return reflect.Value{}, err
			}
			out.Index(i).Set(item)
		}
	case reflect.Struct:
		// Copy scalar private fields (notably fallbackOff) without unsafe access.
		// OrgConfig provenance is stripped even inside dynamic/nested OrgConfigs.
		if typ == orgConfigType {
			plain := v.Interface().(OrgConfig)
			plain.legacy = nil
			v = reflect.ValueOf(plain)
		}
		out.Set(v)
		for i := 0; i < v.NumField(); i++ {
			field := typ.Field(i)
			if typ == orgConfigType && field.Name == "legacy" {
				continue
			}
			if field.PkgPath != "" {
				if !scalarGraphType(field.Type) {
					return reflect.Value{}, ErrConfigGraph
				}
				continue
			}
			item, err := cloneValue(v.Field(i), active)
			if err != nil {
				return reflect.Value{}, err
			}
			out.Field(i).Set(item)
		}
	case reflect.Bool, reflect.String, reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64, reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Uintptr, reflect.Float32, reflect.Float64, reflect.Complex64, reflect.Complex128:
		out.Set(v)
	default:
		return reflect.Value{}, ErrConfigGraph
	}
	return out, nil
}

func scalarKey(v reflect.Value) bool {
	if v.Kind() == reflect.Interface {
		return v.IsNil() || scalarKey(v.Elem())
	}
	return scalarGraphType(v.Type())
}

// scalarGraphType forbids pointer-key maps and private mutable references,
// including references hidden in arrays/structs. It invokes no methods.
func scalarGraphType(t reflect.Type) bool {
	switch t.Kind() {
	case reflect.Struct:
		for i := 0; i < t.NumField(); i++ {
			if !scalarGraphType(t.Field(i).Type) {
				return false
			}
		}
		return true
	case reflect.Array:
		return scalarGraphType(t.Elem())
	case reflect.Bool, reflect.String, reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64, reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Uintptr, reflect.Float32, reflect.Float64, reflect.Complex64, reflect.Complex128:
		return true
	default:
		return false
	}
}

// Used only on checked, provenance-free graphs. DeepEqual is structural and
// invokes neither serializer nor user equality methods. NaNs conservatively
// classify as changed rather than allowing data to survive under an old image.
func equalConfigData(a, b *OrgConfig) bool { return reflect.DeepEqual(a, b) }
