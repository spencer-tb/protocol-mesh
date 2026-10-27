// Shared helpers for the xfuzz tests. fuzz/run.py copies this file into
// each geth package it tests and sets the package name, so the tests can
// reach unexported functions.
package PKG

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"testing"
)

type xfuzzCase struct {
	ID   int             `json:"id"`
	Fn   string          `json:"fn"`
	Args json.RawMessage `json:"args"`
}

// xfuzzRun reads the cases, hands each one to `eval` and writes one result
// line per case it handles. `eval` returns false for cases of other
// packages.
func xfuzzRun(t *testing.T, name string, eval func(c xfuzzCase) (any, error, bool)) {
	source := os.Getenv("XFUZZ_IN")
	if source == "" {
		t.Skip("XFUZZ_IN is not set")
	}
	in, err := os.Open(source)
	if err != nil {
		t.Fatal(err)
	}
	defer in.Close()
	out, err := os.Create(filepath.Join(os.Getenv("XFUZZ_OUT"), "geth-"+name+".jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer out.Close()
	scanner := bufio.NewScanner(in)
	scanner.Buffer(make([]byte, 1<<20), 1<<26)
	encoder := json.NewEncoder(out)
	for scanner.Scan() {
		var c xfuzzCase
		if err := json.Unmarshal(scanner.Bytes(), &c); err != nil {
			t.Fatal(err)
		}
		result, err, handled := eval(c)
		if !handled {
			continue
		}
		line := map[string]any{"id": c.ID}
		if err != nil {
			line["error"] = err.Error()
		} else {
			line["ok"] = result
		}
		if err := encoder.Encode(line); err != nil {
			t.Fatal(err)
		}
	}
	if err := scanner.Err(); err != nil {
		t.Fatal(err)
	}
}

// xfuzzU64 parses a decimal string argument.
func xfuzzU64(args map[string]any, key string) uint64 {
	value, err := strconv.ParseUint(fmt.Sprint(args[key]), 10, 64)
	if err != nil {
		panic(fmt.Sprintf("%s: %v", key, err))
	}
	return value
}

// xfuzzArgs decodes a case's arguments.
func xfuzzArgs(c xfuzzCase) map[string]any {
	var args map[string]any
	if err := json.Unmarshal(c.Args, &args); err != nil {
		panic(err)
	}
	return args
}
