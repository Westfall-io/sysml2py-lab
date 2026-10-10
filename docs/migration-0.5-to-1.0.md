# sysml2py 1.0.0 — migration note for 0.5.x consumers

The generated `sysml2py` library (issue #13) is released as **1.0.0** — the
next major line after the hand-maintained 0.5.x textX/astropy parser. This
note explains how a 0.5.x consumer moves to 1.0.0 and what to expect.

## What changed

- **Engine**: 0.5.x was a textX-backed parser built around
  `grammar/SysML_compiled.tx`. 1.0.0 is a **purely-generated, pure-stdlib**
  AST/builder library (no textX, no astropy, no yaml at runtime). It is
  produced deterministically by `sysml2py-lab` from the committed SysML v2
  grammar + relationship model + corpus, and carries a provenance stamp.
- **No runtime deps**: `pip install sysml2py` now pulls nothing but the
  standard library. This is the biggest behavioural change — code that
  relied on `astropy.units` being present will need that import added
  explicitly.
- **Version**: distributions are now `1.x.y`. `import sysml2py` still works;
  the package exposes `__version__` and a `provenance` submodule.
- **Provenance**: every install records the grammar/spec/corpus SHAs and the
  generating lab version — so you can always prove *which* inputs produced
  the wheel (reproducibility from `(lab commit, spec, corpus manifest)`).

## What's preserved

- The **builder API** shape is retained: `Part` / `Attribute` / `Item` /
  `Port`, `_set_name(name, short)`, `_set_child(...)`, `_get_child(path)`,
  `get_definition()`, `dump()`. The classic 0.5.x README example (a part
  with mass/thrust attributes) still constructs and prints the same shaped
  output.
- SysML v2 model *content* is unaffected: the corpus that drives generation
  is the same authoritative grammar, and generated text is validated against
  the independent windtrader oracle (the cast stays honest).

## Migration steps

1. `pip install -U sysml2py` (or pin `sysml2py>=1.0.0`).
2. If you used `astropy.units` / units-in-attributes, add `astropy` to your
   project deps and `import astropy.units as u` where needed — 1.0.0 no
   longer bundles it.
3. If you constructed models with the 0.5.x API, the generated builder API
   covers the same operations; consult the README examples.
4. To verify provenance of a given install:
   ```python
   from sysml2py import provenance
   print(provenance.PROVENANCE)   # grammar/spec/corpus SHAs + generator version
   ```

## Rollback

If a 0.5.x behaviour is missing, pin `<1.0.0` and file the gap on the
`sysml2py-lab` repo (it is generator error, not a license to hand-edit the
generated library — "do not hand-edit" headers apply).
