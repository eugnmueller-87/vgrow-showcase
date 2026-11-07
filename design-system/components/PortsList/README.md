# PortsList

One row per port of the controller: the device's name and limits on the left, its policy or assignment on the right.

**Markup**: `<div class="ports"><div class="port"><span class="name">Light<small>Port 1 · 0–100%</small></span><div class="seg">…</div></div>…</div>`. The right side is a SegmentedControl (policy, or what hangs on a USB port), a StatusChip when there is nothing to set, or a Toggle.

**Tokens**

- Rows divided by `line`, 10px vertical padding; name in `body-strong`, the limits line in `small` `muted`.

**Rules**

- Name by function first and port second ("Light · Port 1", "USB 2 · Humidifier"); the port number alone means nothing to the person.
- The limits line states the range and quota the autopilot respects, in the same words the Timeline uses.
- A port whose settings live elsewhere (the pump under Automatic watering) says so in the small line and shows a chip, not a control.
- The consumer provides the ports in controller order with their controls.
