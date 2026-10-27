# Product roadmap

[Product overview](../README.md) · [Workflows](workflows.md) · [AI models](ai-models.md) · [Costs](costs.md)

Checkpoint: **27 October 2026**, application revision **`4e49083`**. This roadmap separates implemented software, enabled features in the current installation, and work still to validate. Dates below describe evidence, not delivery promises.

## The direction

A beginner should be able to understand the setup, follow a plant from its starting point to a completed record, and let configured routines handle recurring care. Refills, inspections and physical maintenance remain visible tasks. The aim is progressively more reliable automation, supported by evidence and clear explanations.

The current proof is a personally operated VGrow Y18 installation with two cameras and local AI. It is not yet a validated, unattended commercial product for arbitrary hardware.

## Delivered and operating

| Area | Implemented capability | Evidence / current boundary |
|---|---|---|
| Foundation and remote workspace | Python server, responsive DE/EN interface, persistent readings and authenticated access | Running on the owner's Mac; one connected controller |
| Climate and humidifier control | Inspectable decisions, configured bounds, manual takeover, command confirmation and refill records | Climate automation active; physical mist restart and tank level are not directly measured |
| Cameras | GrowCam C4 and C920 together, live/still views, recording and digital viewer zoom | Both feeds verified; source-labelled observations, no validated automatic ruler measurement |
| Hank and retrieval | Local Gemma language/vision, Granite search, factual reports and fallbacks | Verified model identities and own-host runtime; latency depends on the Intel host |
| Plant lifecycle | Multiple box/plant records, dated observations, finish/archive and export | Shared sensor coverage is documented; record count is not controller count |
| Knowledge and discovery | Indexed wiki, 100 editorial strain references, expanded health topics, fertilizer library, news and public community metadata | Source attribution retained; no unified social login or private-feed access |
| Alerts | Configurable rules, activity and notifications | Enabled rules depend on owner configuration and available channels |

## Implemented, with setup or validation still open

| Area | Software status | What remains before claiming the complete outcome |
|---|---|---|
| Daily light timer | Built and deployed | Save and activate a daily plan in the live installation; validate transitions over time. Current captured plan is empty and disabled |
| Watering timer and stop watchdog | Built and deployed | Confirm real pump switching and flow, save and enable a schedule. Current captured schedule is disabled |
| Water-demand check and forecast | Integrated in the current revision | Enter the actual pot setup, calibrate flow and supply recent pot observations; validate predictions against real measurements |
| Windows installer | Built on a separate development branch | Merge, complete clean-machine installation checks and distribution/signing decisions; not part of the current main installation |
| More boxes and plants | Record workspace delivered | Connect and isolate multiple independent hardware controllers; validate shared versus individual evidence |

The demand check can skip or shorten saved watering slots. It cannot start extra cycles or enlarge a dose. Neither the timer UI nor the forecast proves that measured, autonomous watering is active today.

## Next product milestones

| Priority | Outcome | Completion evidence |
|---|---|---|
| 1 | A verified daily care baseline | Configured light periods, measured pump behavior, pot setup and a documented observation period with confirmed outcomes |
| 2 | Stage-aware recommendations | Sourced, reviewed presets that distinguish plant type, stage and actual hardware; proposed changes are understandable before activation |
| 3 | Better water planning | Repeated pot measurements improve estimates; forecasts, uncertainty and real delivery are compared before adding direct dose control |
| 4 | Reliable visual tracking | Ruler calibration with known endpoints, camera-specific observations and dated stage evidence checked against human references |
| 5 | More equipment and installations | Explicit Wi-Fi/Bluetooth/USB adapters, capability discovery and ownership tested with more than one real controller |
| 6 | Complete per-plant evidence | Photos, recordings and AI observations reliably attached to the correct record, including shared-box and archived cases |
| 7 | Product distribution and onboarding | Repeatable Mac/Windows setup, permissions, updates, recovery and accessibility tested with first-time users |
| 8 | A collaborative pilot | Agreed scope, appropriate source access, measured reliability, support effort and operating costs with a hardware partner |

Longer-term candidates include a macOS application package, richer consumption reports, supported provider integrations, mobile packaging and more plant/equipment profiles. They are ideas to prioritize from pilot evidence, not promised releases.

## Validation checkpoint

The application checkpoint on 27 October recorded **2,605 passing tests and one existing XPASS**. Its offline Hank evaluation recorded **140 passed cases, nine waiting for reference cards and zero failures** out of 149. Demo checks covered both languages, themes and screen sizes; the live review submitted no actuator commands.

These are dated software checks, not field evidence of crop performance or a guarantee of future behavior. The documentation refresh separately captured 12 live screens with browser writes blocked and no JavaScript exceptions.

## Collaboration

This project grew from using the hardware and encountering the decisions a beginner still has to make. The next step is to help develop that experience with the people building the equipment: product work, integration, testing and potentially a role on the team. A focused pilot can establish what is useful, reliable and ready to scale.
