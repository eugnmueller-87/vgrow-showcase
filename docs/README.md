# Documentation

[Repository overview](../README.md) · [Product story](product-story.md) · [Screenshot gallery](gallery.md)

## Product, AI and collaboration

| Guide | Purpose |
|---|---|
| [Workflows](workflows.md) | Onboarding, care, local AI, camera review and record completion |
| [AI deployment](ai-models.md) | Exact local models, roles, verified host and contribution boundaries |
| [Roadmap](roadmap.md) | Delivered software, live setup gaps, validation and future milestones |
| [Costs](costs.md) | No external inference bill, equipment and energy costs, explicitly unknown inputs |
| [VIVOSUN context](vivosun-context.md) | Connect the contribution to their published direction, with clear evidence boundaries |

## Use and run the cockpit

| Guide | Purpose |
|---|---|
| [Getting started](getting-started.md) | Demo, first hardware connection, Mac service, remote access, optional AI |
| [Operating reference / Deutsch](operations.md) | Detailed configuration, feeding records, controllers, port assignments, fault behavior |
| [Durst-Index / thirst index](durst-index.md) | How much water the pot is missing, the watering plan by stage, the need check before every scheduled slot |
| [USB camera](usb-camera.md) | C920 installation, macOS permission, viewer controls and capture lifecycle |
| [Plant record lifecycle](record-lifecycle.md) | Starting, finishing, preserving and exporting records; model implications |
| [Community desk](community-hub.md) | Public sources, saved metadata, image validation and account windows |

## Understand and maintain the implementation

| Guide | Purpose |
|---|---|
| [Architecture](architecture.md) | Components, controller ownership, data storage and failure paths |
| [Development](development.md) | Local setup, offline verification and demo-only browser checks |
| [Cockpit redesign](cockpit-redesign.md) | Routing, responsive layout, accessibility and feature-preservation decisions |
| [Upstream protocol provenance](../vgrow/vivosun/UPSTREAM.md) | Vendored VIVOSUN integration and license |
| [Knowledge-card sources](../kartei/) | Structured profiles, references, images and article data |

## Design history and evaluations

These files preserve design discussions and measured results. Dates and source revisions matter: a proposal or
historical limit in these documents is not a statement that the current code implements it. Use the operating
reference and component source for present behavior.

| Area | Documents |
|---|---|
| Climate autopilot | [Design](autopilot-entwurf.md), [replay utility](autopilot-replay.py) |
| Hank | [Original specification](hank-v1-spec.md) |
| Model evaluation | [Measured Mac results](hank-v1-messung.md), [evaluation cases](hank-v1-evalset.json) |
| Box/plant workspace | [Implemented lifecycle](record-lifecycle.md) |

For example, the original autopilot design discusses command quotas. Those actuator quotas have since been
removed. Likewise, the redesign's original external community landing page has been replaced by the native
metadata feed described in the community guide.
