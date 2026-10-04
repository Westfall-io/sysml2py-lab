# language_spec.json — schema

**Schema version:** `sysml2py.grammar.language_spec/1`

Produced by `sysml2py-lab spec` from the vendored `.xtext` grammar inputs
(`grammar_inputs/*.xtext`).  Every rule carries file + line provenance; the
document is deterministic (same inputs → byte-identical JSON, sorted keys).

## Top level

```jsonc
{
  "schema": "sysml2py.grammar.language_spec/1",
  "files": [
    {
      "file": "SysML.xtext",
      "line_count": 2380,
      "grammar": { "name": "org.omg.sysml.xtext.SysML",
                   "with": ["org.omg.kerml.expressions.xtext.KerMLExpressions"] },
      "rule_names": ["RootNamespace", "..."]
    }
  ],
  "rules": [ { "rule": ... }, ... ],
  "counts": {
    "rules": 544, "fragments": 147, "terminals": 9, "enums": 13, "total": 713
  }
}
```

- `files` — one entry per input file, in sorted-path order, with the `grammar`
  declaration (`name`, super-grammar `with` chain), `line_count`, and the rule
  names defined in that file (source order).
- `rules` — **every** parsed rule definition across all files, in stable
  order.  Rules are NOT deduplicated by name: KerML and SysML are distinct
  grammars, so a name defined in both is retained twice with its own
  `source.file` provenance.  `sum(files[].rule_names) == counts.total` always.
- `counts` — kind counts over the full (non-deduped) rule set.

## Rule object

```jsonc
{
  "kind": "rule",               // "rule"
  "name": "RootNamespace",
  "rule_kind": "rule",          // "rule" | "fragment" | "terminal" | "enum"
  "returns": "SysML::Namespace",// nullable
  "override": false,            // true when preceded by @Override
  "source": { "file": "SysML.xtext", "line": 38 },
  "body": { ... },              // element tree, see below
  "index": 0
}
```

`source.file` is the path of the file relative to the grammar-inputs
directory (e.g. `"SysML.xtext"`).  For `terminal` / `enum` rules
the `body` is a raw token stream:

```jsonc
{ "kind": "terminal_body", "tokens": [ { "kind": "literal", "value": "0", "line": 533 }, ... ] }
{ "kind": "enum_body",     "tokens": [ ... ] }
```

## Body element tree

Every element is `{ "kind": <kind>, "line": <int>, ...fields }`, optionally
with `"card": "?" | "*" | "+"`.

| kind      | fields                                        | meaning |
| ---       | ---                                           | ---     |
| `seq`     | `items: [element]`                            | ordered sequence |
| `alt`     | `choices: [element]`                          | alternation (`\|`) |
| `group`   | `body: element`                               | parenthesized group; `body` is the single element, or an `alt` when the group is `( A \| B )` |
| `action`  | `type: str`                                   | `{TypeName}` / `{Type.feature = current}` |
| `assign`  | `name`, `op` (`=` `+=` `?=` `:=`), `value`    | assignment |
| `call`    | `name`                                        | reference to another rule |
| `lit`     | `value`                                       | keyword literal (unquoted) |
| `xref`    | `type`, `ref` (`{"name": ...}` or null)       | `[Type\|Name]` |
| `pred`    | `arrow` (`=>` `->`), `body`                   | syntactic predicate; `body` is exactly the one immediately-following element |
| `tok`     | `value`                                       | unclassified passthrough (kept for losslessness) |

- A `group`'s `body` is the *single* wrapped element.  For a parenthesized
  alternation `( A | B )` the `body` is an `alt` node; for a sequence group
  `( A B )` it is a `seq` node.
- A `pred`'s `body` is exactly the one element the `=>`/`->` scopes over
  (never a run of following siblings).
- Structural tokens are matched kind-aware: a quoted `';'` keyword stays a
  `lit ";"` while a bare `;` is the rule terminator (not in the body); a
  quoted `'|'` stays a `lit "|"` while a bare `|` is an alternation.  So a
  keyword literal is never lost.
- `lit.value` is the unquoted literal text.

## Grammar capture

Each file's `grammar` declaration records the fully-qualified grammar name
and its `with` super-grammar chain (used by consumers to resolve overrides
and cross-file rule references).  `grammar` is `null` when a file has no
declaration; `with` is absent when the grammar has no super-grammar and
`hidden` (the `hidden(WS, ...)` terminal list) is present only when the
source declares it.  `import "..." as Alias` URI maps precede the rule
stream.

## Determinism

`json.dumps(..., indent=2, sort_keys=True)` — stable key order, stable rule
order (source order per file, files sorted by path), stable grammatical
output.  Two runs over identical vendored inputs are byte-identical; commit
the output and diff-review it on grammar changes.  A `test_committed_spec_is_current`
guard asserts a fresh build equals the committed spec.
