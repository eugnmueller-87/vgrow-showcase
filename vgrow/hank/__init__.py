"""Hank, the read-only chat assistant of the grow-box cockpit.

He is a guest: if anything in this package fails, the cockpit, the alarms, the humidifier automation and the
port lock (ports.json always_on) keep running. Hank never switches anything himself; actions are suggestion cards that the page sends
through the normal /api/cmd with all existing guards. The spec is docs/hank-v1-spec.md plus the binding
addendum docs/hank-v1-nachtrag.md.

This file imports nothing on purpose, so importing one module of the package never pulls in the others.
"""
