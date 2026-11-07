# Page patterns

How the components combine on a cockpit page, written from the Automation page.

## One primary action per view

- A page has at most one `btn primary` visible at a time. On a configured box, "Start here" belongs to the empty state, not the top bar. The top bar keeps icon buttons and secondary buttons (`btn`).
- A confirmation the autopilot is waiting for ("Distance measured: at least 35 cm") is a `btn primary` inside the device's own card, not inside a collapsed section further down.
- Destructive or overriding actions (`btn arm`) never sit next to the primary; put them at the end of their card.

## Device cards

- The header row is name (`body-strong`), StatusChip, reading (`reading`), in that order. The chip is the status; the body never repeats it.
- Sort by state: needs-you, stopped, paused, monitoring, off. The card that needs the person is first and carries `device attention` (a `warn` border).
- A device with nothing to say (monitoring, on target) is a `device compact` row: one line, chip and reading on the right. Do not stretch an empty card to match a full one.
- The body is at most two `body` lines and one button. More goes into a Disclosure under the cards.
- The health line above the cards ("Hank needs your help with these devices") is `body-strong` with the counts as StatusChips: `1 monitoring · 1 needs you · 2 paused`.

## Disclosures

- Use the Disclosure component for everything that was a bare `details`: setup and confirmations, device settings and limits, all entries, how the thirst index computes, recent refills. The summary names what is inside in one `small` line.
- A Disclosure never hides a blocking action. If the autopilot cannot run until the person confirms something, the confirmation is in the card; the Disclosure holds the explanation.
- Collapsed by default; open the one that holds a `warn` or `bad` item.

## Banners

- One Banner per card, at the top of the body, with an icon and a bold lead that names the situation ("Setup needed:", "Check the humidifier:"). A second condition goes into the same banner as a second sentence, not a second banner.
- A Banner is for a condition that blocks or needs attention. A fact the person should know but need not act on is `small muted` text under the control.
- The watering card's disabled buttons stay out of the banner; they sit under it in their row.

## Steps and reasons

- The Timeline lists one entry per decision, newest first, each with its time in `caption` and one `body` sentence that holds the reason.
- Group repeats: four "Light changed in the VIVOSUN app" entries within ten minutes become one entry with a count ("Light changed 4× between 8:30 and 8:36, latest 87% to 89%"), expandable.
- The daily report line sits under the timeline as `small muted`, numbers tabular.

## Top bar and counters

- Title in `display`, box name in `small muted` under it. The health block on the right: an ok/bad StatusChip ("Online"), and the freshness line ("Readings 51 s ago · Device status 8 min ago") in `caption`, never smaller.
- One counter: unread items on the bell. Hank's floating button shows no badge of its own when the bell already counts them; the box selector pill shows no number unless it is explained in a tooltip.

## Forms

- A field is label (`small-strong`), input (`card` fill, `line-strong` border, `radius-sm`), hint (`small muted`), stacked with `space-2`. Four fields side by side from 960px, two below, one on phones.
- Placeholders are examples ("HH:MM, HH:MM", "e.g. 10"), never the unit alone; the unit is in the label ("Soil in the pot in litres").
- Native date inputs take the page's locale (`lang="en"`), so the placeholder reads "mm/dd/yyyy", not "tt.mm.jjjj".
- Choice chips show a chosen state with `sel-bg`; a chip row is preceded by a `small-strong` label that says what the choice means.
