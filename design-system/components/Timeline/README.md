# Timeline

The autopilot's decisions in order, each with its time and the reason in one sentence; the dot's colour says who acted.

**Markup**: `<p class="eyebrow">Steps and reasons</p><ol class="timeline"><li class="step|suggest|manual|hold|failed"><time>8:37 AM</time><span>sentence</span></li>…</ol>`. A grouped repeat: `<span class="grp">sentence <span class="status">4×</span></span>`.

**Dots and tokens**

- `step`: filled `accent`: the autopilot switched something.
- `suggest`: `info` outline: a suggestion waiting for the person.
- `manual`: `warn` outline: the person switched something; the autopilot pauses.
- `hold`: filled `warn`: the autopilot is holding back (stage not confirmed, app changed the light).
- `failed`: filled `bad`: a command failed or a fail-safe fired.
- no class: `line-strong` outline: an observation.
- Rail is a 2px `line`; text `body` within `measure`; time in `caption` `muted`, tabular.

**Rules**

- Newest first, six entries, then "All entries" in a Disclosure.
- Each entry is one sentence that holds the reason ("Exhaust fan 1 → 2: humidity 68% above the 62% band for 10 minutes").
- Group repeats of the same event within ten minutes into one entry with a time span and a count chip; the group opens to its members.
- Times today as "8:37 AM"; older with the date ("Oct 27, 2:41 PM").
- The consumer provides the entries in order with their class; the list does not sort.
