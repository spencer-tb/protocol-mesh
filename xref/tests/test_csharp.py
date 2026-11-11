"""Index C# files that the tree-sitter grammar cannot read."""

import unittest

from el_xref.csharp import scan
from el_xref.index import parse_source

# `ref partial struct` and an assignment to a call taking `ref` are both
# outside the grammar, and together lose every method in the file.
STACK = """\
namespace Nethermind.Evm;

public ref partial struct EvmStack
{
    public const int WordSize = 32; // not { a brace

    public EvmStack(int head, scoped in ReadOnlySpan<byte> code)
    {
        Head = head;
    }

    /// <summary>Pops an address.</summary>
    [MethodImpl(MethodImplOptions.AggressiveInlining)]
    public Address? PopAddress()
    {
        ref ulong parts = ref Unsafe.As<EvmWord, ulong>(ref word);
        Unsafe.Add(ref parts, 1) = 0;
        string text = "} not a brace {";
        return Head == 0 ? null : new Address(parts);
    }

    public readonly bool IsEmpty => Head == 0;

    public bool PushZero<T>(T tracer) where T : ITxTracer => Push(0, tracer);

    private static readonly Limits Default = new Limits(1024) { Depth = 1 };
}
"""


class ScanTests(unittest.TestCase):
    """Read methods by their braces alone."""

    def test_finds_methods_with_their_type(self) -> None:
        """Methods and constructors are found, properties and fields not."""
        found = [(m.owners, m.name) for m in scan(STACK)]
        self.assertEqual(
            found,
            [
                (("EvmStack",), "EvmStack"),
                (("EvmStack",), "PopAddress"),
                (("EvmStack",), "PushZero"),
            ],
        )

    def test_span_runs_from_attributes_to_closing_brace(self) -> None:
        """A method's text starts at its attributes and ends at its brace."""
        pop = scan(STACK)[1]
        text = STACK[pop.start : pop.end]
        self.assertTrue(text.startswith("[MethodImpl"))
        self.assertTrue(text.endswith("new Address(parts);\n    }"))

    def test_unbalanced_braces_return_nothing(self) -> None:
        """A file that does not balance is not guessed at."""
        self.assertEqual(scan("class A { void F() { "), [])

    def test_index_falls_back_to_the_scan(self) -> None:
        """The index keeps the methods of a file tree-sitter cannot read."""
        parsed = parse_source("csharp", "nethermind", "S.cs", STACK.encode())
        symbols = {symbol.qualname: symbol for symbol, _ in parsed}
        self.assertIn("EvmStack.PopAddress", symbols)
        pop = symbols["EvmStack.PopAddress"]
        self.assertEqual((pop.line, pop.end_line), (13, 20))
        self.assertEqual(pop.doc, "/// <summary>Pops an address.</summary>")

    def test_readable_files_keep_the_tree_sitter_result(self) -> None:
        """A file the grammar reads is indexed as before."""
        source = b"class A { void F() { G(); } }"
        parsed = parse_source("csharp", "nethermind", "A.cs", source)
        self.assertEqual([s.qualname for s, _ in parsed], ["A.F"])
