# Input

A field is a label, an input and an optional hint, stacked with `space-2`; the input is always `card` on `line-strong`, whatever ground it sits on.

**Markup**: `<label class="field [invalid]"><span class="label">…</span><input class="input" …><span class="hint">…</span></label>`. Several side by side: wrap in `.field-grid` (auto-fit, 200px minimum). A checkbox row: `<label class="check"><input type="checkbox"> …</label>`.

**Rules**

- Label in `small-strong` (13/600, `ink`), hint in `small` (`muted`). The unit lives in the label ("Soil in the pot in litres"), the placeholder shows an example ("e.g. 20", "HH:MM, HH:MM"), never the unit alone.
- Height `control-h-touch` (44px), `radius-sm`, 14px text. Focus is a 3px `accent-strong` ring with no offset.
- Invalid: `aria-invalid="true"` turns the border `bad`, and the hint (linked by `aria-describedby`) turns `bad` and says what to fix.
- Numeric fields set `inputmode="numeric"` and `font-variant-numeric: tabular-nums`.
- Native date and time inputs follow the document `lang`; set `lang="en"` so the placeholder matches the rest of the page.
- The consumer provides the label text, the input attributes and validation; the field does not validate on its own.
- Do not put a field inside a banner, and do not use `bg` as a field fill to show it is read-only: use `disabled` or `readonly` with the lock pattern from Button.
