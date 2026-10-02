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
      "line_count": 2379,
      "rule_names": ["RootNamespace", "..."]
    }
  ],
  "rules": [ { "rule": ... }, ... ],
  "counts": {
    "rules": 494, "fragments": 126, "terminals": 9, "enums": 10, "total": 639
  }
}
```

- `files` — one entry per input file, with the rule names defined in that
  file (in source order).  A rule defined in an overridden/`with` grammar
  appears in the file that defines it; the `rules` array is deduplicated by
  rule name (later files win for overrides).
- `rules` — all grammar rules, deduplicated by name, in stable order
  (alphabetical by name), each with an `index`.
- `counts` — kind counts over the deduplicated rule set.

## Rule object

```jsonc
{
  "kind": "rule",               // "rule"
  "name": "RootNamespace",
  "rule_kind": "rule",          // "rule" | "fragment" | "terminal" | "enum"
  "returns": "SysML::Namespace",// nullable
  "source": { "file": "SysML.xtext", "line": 38 },
  "body": { ... },              // element tree, see below
  "index": 0
}
```

For `terminal` / `enum` rules the `body` is a token stream:

```jsonc
{ "kind": "terminal_body", "tokens": [ { "kind": "symbol", "value": "'0'..'9'", "line": 533 }, ... ] }
{ "kind": "enum_body",     "tokens": [ ... ] }
```

## Body element tree

Every element is `{ "kind": <kind>, "line": <int>, ...fields }`, optionally
with `"card": "?" | "*" | "+"`.

| kind      | fields                                        | meaning |
| ---       | ---                                           | ---     |
| `seq`     | `items: [element]`                            | ordered sequence |
| `alt`     | `choices: [element]`                          | alternation (`\|`) |
| `group`   | `items: [element]`                            | parenthesized group |
| `action`  | `type: str`                                   | `{TypeName}` / `{Type.feature = current}` |
| `assign`  | `name`, `op` (`=` `+=` `?=` `:=` `-=`), `value` | assignment |
| `call`    | `name`                                        | reference to another rule |
| `lit`     | `value`                                       | keyword literal (unquoted) |
| `xref`    | `type`, `ref` (`{"name": ...}` or null)       | `[Type\|Name]` |
| `pred`    | `arrow` (`=>` `->`), `body`                   | syntactic predicate |
| `tok`     | `value`                                       | unclassified passthrough (kept for losslessness) |

Assignments, calls, groups, xrefs may carry `card`.  `lit.value` is the
unquoted literal text; a quoted `';'` stays a literal `;` while a bare `;`
is the rule terminator and is not part of the body.

## Determinism

`json.dumps(..., indent=2, sort_keys=True)` — stable key order, stable rule
order (alphabetical by name), stable file order (sorted by glob).  Two runs
over identical vendored inputs are byte-identical; commit the output and
diff-review it on grammar changes.
