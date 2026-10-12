# sysml2py-lab

Corpus-driven **generator** for the `sysml2py` SysML v2 / KerML textual
notation library (MVP: brace parsing + normalized round-trip + codegen).

`sysml2py-lab` reads the committed SysML v2 grammar, relationship model and
example corpus and **deterministically emits** a pure-stdlib Python package,
`sysml2py`.  That package is the thing you install and build models with; this
repo is the machine that writes it.

```
python model  --(sysml2py-lab codegen)-->  sysml2py Python API  -->  .sysml text
```

## What the generated library gives you

A builder API that turns a clear intent into faithful SysML v2 textual
notation:

```python
import sysml2py

pkg = sysml2py.PackageBuilder("Satellite")
part = sysml2py.Part("Stage_1")
part.add(sysml2py.Attribute("mass", value=1000, unit="kg"))
part.add(sysml2py.Attribute("thrust", value=1200, unit="N"))
pkg.add(part)

print(pkg.dump())
```

produces

```
package Satellite
{
   public import SI::kg;
   public import SI::N;
   part Stage_1
   {
      attribute mass = 1000[kg];
      attribute thrust = 1200[N];
   }
}
```

### Value-bearing attributes (issue #26)

Pass `value=` (and optionally `unit=`) to a value-bearing builder.  The unit's
import (`public import SI::kg;`) is added **automatically** as a structural
member — only the units you actually use, nothing broader.

```python
sysml2py.Attribute("mass", value=1000, unit="kg")
# -> attribute mass = 1000[kg];   +   public import SI::kg;

sysml2py.Attribute("accel", value=9.81, unit="m/s**2")
# -> attribute accel = 9.81[m/s**2];   +   public import SI::m; public import SI::s;
```

You can also pass an **astropy `Quantity`** and the unit is derived from it
(astropy is not bundled — add it to your own deps):

```python
import astropy.units as u
sysml2py.Attribute("mass", value=1000 * u.kg)
# same result; unit  [kg]  derived from the Quantity
```

**Faithfulness contract.** The generator refuses to emit a unit it cannot
render faithfully — it *fails loud* (`ValueError`) rather than write a
silently-wrong model.  Unmapped units, scaled/prefixed magnitudes
(`"100 m"`, `u.Unit(100*u.m)`), fractional powers (`m/s**0.5`), a `unit=`
that conflicts with a Quantity's own unit, and `unit=` without a `value=`
all raise at construction.

**The Casting.** Before any output ships, `sysml2py-lab` proves the round trip:
Python model → emit `.sysml` → parse that text back → the re-parsed structure
must equal the original, element-for-element.  Text that cannot cast back to
the model it claims to represent is scrap, not a release.

## Layout

- `grammar/` — the SysML v2 / KerML grammar inputs.
- `corpus/` — the example model corpus that both drives and validates
  generation.
- `src/sysml2py_lab/` — the generator: brace parser, AST, builders, codegen
  templates, and the emitted-notation test suite.
- `docs/migration-0.5-to-1.0.md` — moving from the hand-maintained 0.5.x
  parser to the 1.0.0 generated library.

## Reproducibility

Every generated install records the grammar/spec/corpus SHAs and the
generating lab version in a `provenance` submodule, so you can always prove
*which* inputs produced the wheel:

```python
from sysml2py import provenance
print(provenance.PROVENANCE)
```

## License

MIT.  Generated `sysml2py` output carries "do not hand-edit" headers — treat
hand-edits as generator error, not a license to fork the generated code.
