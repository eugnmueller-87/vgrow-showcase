# Upstream

Source: https://github.com/lientry/homeassistant-vivosun-growhub
License: MIT (`LICENSE` in this folder, unchanged)
Commit: `d89e24d5a0447766073b72c167b380760ee57871` (2026-10-20, release 0.6.0b1)

Files copied from `custom_components/vivosun_growhub/`, unchanged:
`api.py aws_auth.py encryption.py exceptions.py model_metadata.py models.py mqtt_client.py
ports.py redaction.py shadow.py support_capture.py vseed.py`

Changed: `const.py` (removed `from homeassistant.const import Platform` and the `PLATFORMS` list).

## When Vivosun breaks the login

Vivosun changes its app now and then (login, encryption, `APP_VERSION` header). Upstream
usually ships a fix within days. Then:

1. Check upstream releases and issues.
2. Copy the same files again from the new commit, repeat the `const.py` edit.
3. Update the commit line above.
4. `python -m pytest` and `python -m vgrow --check`.

Never patch these files by hand: the next copy would silently undo it.
