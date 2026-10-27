// Memory expansion and call gas, for fuzz/run.py.
package vm

import (
	"strconv"
	"testing"

	"github.com/holiman/uint256"
)

func TestXFuzz(t *testing.T) {
	xfuzzRun(t, "vm", func(c xfuzzCase) (any, error, bool) {
		args := xfuzzArgs(c)
		switch c.Fn {
		case "memory":
			fee, err := memoryGasCost(NewMemory(), xfuzzU64(args, "size"))
			return strconv.FormatUint(fee, 10), err, true
		case "memory_extend":
			mem := NewMemory()
			current := xfuzzU64(args, "current")
			if current > 0 {
				if _, err := memoryGasCost(mem, current); err != nil {
					return nil, err, true
				}
				mem.Resize(current)
			}
			fee, err := memoryGasCost(mem, xfuzzU64(args, "size"))
			return strconv.FormatUint(fee, 10), err, true
		case "callgas":
			all := new(uint256.Int).SetAllOne()
			gas, err := callGas(true, xfuzzU64(args, "gas"), 0, all)
			return strconv.FormatUint(gas, 10), err, true
		}
		return nil, nil, false
	})
}
