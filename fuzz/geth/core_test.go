// Intrinsic gas and the calldata floor under Amsterdam rules, for
// fuzz/run.py. The sender and recipient match fuzz/eels_side.py.
package core

import (
	"encoding/hex"
	"fmt"
	"strconv"
	"testing"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/params"
	"github.com/holiman/uint256"
)

func TestXFuzz(t *testing.T) {
	rules := params.Rules{
		IsHomestead: true, IsEIP150: true, IsEIP155: true, IsEIP158: true,
		IsEIP2929: true, IsByzantium: true, IsConstantinople: true,
		IsPetersburg: true, IsIstanbul: true, IsBerlin: true, IsLondon: true,
		IsMerge: true, IsShanghai: true, IsCancun: true, IsPrague: true,
		IsOsaka: true, IsAmsterdam: true,
	}
	sender := common.BytesToAddress(common.FromHex("0x" + repeat("11", 20)))
	other := common.BytesToAddress(common.FromHex("0x" + repeat("22", 20)))
	xfuzzRun(t, "core", func(c xfuzzCase) (any, error, bool) {
		if c.Fn != "intrinsic" {
			return nil, nil, false
		}
		args := xfuzzArgs(c)
		data, err := hex.DecodeString(fmt.Sprint(args["data"]))
		if err != nil {
			panic(err)
		}
		var to *common.Address
		switch args["to"] {
		case "self":
			to = &sender
		case "other":
			to = &other
		}
		value, err := uint256.FromDecimal(fmt.Sprint(args["value"]))
		if err != nil {
			panic(err)
		}
		var accessList types.AccessList
		for i, keys := range args["access"].([]any) {
			entry := types.AccessTuple{Address: common.BytesToAddress(bytesOf(byte(i+1), 20))}
			for k := 0; k < int(keys.(float64)); k++ {
				entry.StorageKeys = append(entry.StorageKeys, common.BytesToHash(bytesOf(byte(k+1), 32)))
			}
			accessList = append(accessList, entry)
		}
		var auths []types.SetCodeAuthorization
		if n, ok := args["auths"].(float64); ok {
			auths = make([]types.SetCodeAuthorization, int(n))
		}
		gas, err := IntrinsicGas(data, accessList, auths, sender, to, value, rules)
		if err != nil {
			return nil, err, true
		}
		floor, err := FloorDataGas(rules, sender, to, value, data, accessList)
		if err != nil {
			return nil, err, true
		}
		return []string{strconv.FormatUint(gas, 10), strconv.FormatUint(floor, 10)}, nil, true
	})
}

func repeat(s string, n int) string {
	out := ""
	for i := 0; i < n; i++ {
		out += s
	}
	return out
}

func bytesOf(b byte, n int) []byte {
	out := make([]byte, n)
	for i := range out {
		out[i] = b
	}
	return out
}
