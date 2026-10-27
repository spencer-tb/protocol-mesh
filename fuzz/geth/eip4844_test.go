// Blob base fee and excess blob gas, for fuzz/run.py. The blob schedule
// matches the EELS Amsterdam constants: target 14, max 21.
package eip4844

import (
	"fmt"
	"math/big"
	"strconv"
	"testing"

	"github.com/ethereum/go-ethereum/core/types"
)

func TestXFuzz(t *testing.T) {
	schedule := BlobConfig{Target: 14, Max: 21, UpdateFraction: 11684671}
	xfuzzRun(t, "eip4844", func(c xfuzzCase) (any, error, bool) {
		args := xfuzzArgs(c)
		switch c.Fn {
		case "blobfee":
			return schedule.blobBaseFee(xfuzzU64(args, "excess")).String(), nil, true
		case "excess":
			excess, used := xfuzzU64(args, "excess"), xfuzzU64(args, "used")
			baseFee, ok := new(big.Int).SetString(fmt.Sprint(args["base_fee"]), 10)
			if !ok {
				panic("base_fee")
			}
			parent := &types.Header{ExcessBlobGas: &excess, BlobGasUsed: &used, BaseFee: baseFee}
			return strconv.FormatUint(calcExcessBlobGas(true, schedule, parent), 10), nil, true
		}
		return nil, nil, false
	})
}
