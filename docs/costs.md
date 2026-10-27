# What it costs to run

[Product overview](../README.md) · [Local AI](ai-models.md) · [Roadmap](roadmap.md)

**The current local AI runtime has no external inference bill.** It uses pretrained weights on an existing, owner-operated Mac. This removes a per-request LLM provider charge; it does not remove equipment, energy or development costs.

Status: **27 October 2026**. Purchase prices, measured wall power and the owner's electricity tariff have not yet been supplied. No total cost or commercial unit economics is claimed.

## Cost breakdown

| Cost item | Current basis | Amount / treatment |
|---|---|---|
| External language/vision inference | Gemma runs in local Ollama | **€0 per request** in this deployment |
| External embedding requests | Granite runs in local Ollama | **€0 per request** in this deployment |
| Base model weights | Installed pretrained artifacts | No metered inference service; upstream license obligations remain |
| AI/application host | Existing Intel MacBook Pro, 16 GiB | Purchase price and depreciation **not supplied** |
| VGrow box and controller | Existing physical installation | Purchase price **not supplied** |
| GrowCam C4 | Connected reference camera | Purchase price / bundle allocation **not supplied** |
| Logitech C920 and USB adapter | Connected detail camera | Purchase prices **not supplied** |
| Pump, humidifier and other accessories | Existing installation | Purchase prices / bundle allocation **not supplied**; avoid counting bundled parts twice |
| Host electricity | Always-on operation, including app and AI | Wall-power measurement and tariff **not supplied** |
| Grow equipment electricity | Light, fans, pump and humidifier | Separate from the AI host; power readings and duty cycles **not supplied** |
| Water, nutrients, growing medium, replacements | Physical operating supplies | Usage and prices **not supplied** |
| Network, remote access, backups and optional hosting | Depends on deployment and service plans | Existing household costs versus incremental costs **not established** |
| Development tools, subscriptions and engineering time | Separate from runtime inference | Costs and hours **not supplied**, not included in €0/request |
| Support, maintenance and commercial delivery | Future product operation | Not yet costed |

The Mac also hosts the application. Charging its entire electricity bill to AI would overstate incremental model cost, while treating an already-owned computer as costless would understate ownership cost. Measure both idle baseline and representative workload before allocating them.

## Electricity: an explicit example, not a measurement

For a constant average wall draw:

`monthly kWh = average watts × 24 × 30 ÷ 1,000`

`monthly electricity cost = monthly kWh × electricity price per kWh`

The table below assumes **30 days, continuous operation and €0.30/kWh** solely to illustrate the calculation. None of these wattages is a measured value for this installation, and €0.30 is not a tariff quote.

| Assumed average whole-host draw | Energy in 30 days | Illustrative monthly cost |
|---|---:|---:|
| 20 W | 14.4 kWh | €4.32 |
| 40 W | 28.8 kWh | €8.64 |
| 80 W | 57.6 kWh | €17.28 |

For an incremental AI estimate, measure the host baseline and the additional energy during realistic chat and image jobs. A peak power specification, charger rating or model-file size cannot establish monthly energy use.

## Inputs still needed for a real total

Record purchase receipts and which accessories were bundled, the host's measured average wall draw, the actual electricity tariff, equipment duty cycles, backup/service charges, and development/support time. Then separate one-off acquisition, recurring household operation and incremental product costs.

No savings percentage, payback period, profit margin or production hosting price is asserted here. Those need measured usage and a defined commercial deployment.
