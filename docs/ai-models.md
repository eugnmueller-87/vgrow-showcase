# AI on our own hardware

[Product overview](../README.md) · [Workflows](workflows.md) · [Costs](costs.md) · [Roadmap](roadmap.md)

VGrow Cockpit runs its language, vision and semantic-search models on the owner's computer. The current installation uses Ollama on the same always-on Mac as the application. Questions and camera images do not need an external AI inference service.

**Our work is the application and its AI integration:** the evidence pipeline, retrieval, model orchestration, source handling, answer validation, interface and device workflows. We use pretrained model weights. We have not trained a foundation model from scratch or established a custom fine-tuned model in this installation.

![Local AI roles and the separate device-control path](images/ai-runtime.svg)

## Which model does which job?

Verified against the running installation on **27 October 2026**:

| Product function | Engine | What it contributes |
|---|---|---|
| Hank's language layer | `gemma4:12b`, approximately 11.9 billion parameters, `Q4_K_M` | Drafts explanations using supplied facts and retrieved reference material |
| Camera interpretation | The same `gemma4:12b` model | Describes visible features in a selected camera image; observations retain source and uncertainty |
| Semantic reference search | `granite-embedding:278m`, approximately 278 million parameters, `F16` | Represents text for similarity search; it does not write answers or control hardware |
| Current-state answers, event reports and calculations | Application code and stored observations | Produces factual answers without asking a language model to invent measurements |
| Climate rules, light timing, pump timing and demand checks | Deterministic application controllers | Evaluate configured conditions, submit eligible commands and record reported outcomes |
| Wiki, news and public community browsing | Structured content, feeds and caches | Works without a model call; Hank can separately help explain available knowledge |

Both installed model artifacts report an Apache 2.0 license. Google describes Gemma 4 12B as a multimodal open-weight model; IBM's Granite embedding model supplies multilingual text representations. Those upstream models remain their creators' work. See [Google's model announcement](https://blog.google/innovation-and-ai/technology/developers-tools/introducing-gemma-4-12b/), the [Ollama Gemma artifact license](https://ollama.com/library/gemma4%3A12b/blobs/a59e1ee2d202), and the [Granite model listing](https://ollama.com/library/granite-embedding).

## The actual host

| Verified property | Current installation |
|---|---|
| Host | Intel MacBook Pro, model `MacBookPro15,2` |
| Processor | Intel Core i7-8559U, 2.70 GHz |
| Physical memory | 16 GiB |
| Model service | Ollama, reached through the host's loopback interface |
| Main model artifact | Approximately 8.02 GB on disk |
| Embedding artifact | Approximately 563 MB on disk |
| Model scheduling | Chat and image interpretation share a model queue |

Artifact sizes are storage sizes, not measured RAM requirements. The existing benchmark records CPU inference on this Intel host. Cold starts, available memory and image size affect response time; local inference is not a promise of instant responses. An unloaded model can still be installed and available to load.

## From evidence to an answer

The application first identifies the kind of question and gathers relevant current facts or reference passages. Many operational questions can be answered directly from records. When language generation is useful, the local model receives the selected context, and the application checks the result before displaying it. Search can fall back when semantic retrieval is unavailable.

Camera analysis is descriptive assistance. GrowCam supplies the measuring-strip perspective; the C920 supplies detail. A user estimate, model observation and calibrated physical measurement are different records. Automatic stage-onset recognition and validated ruler measurement remain on the [roadmap](roadmap.md).

**The language model does not issue actuator commands.** Timing and device decisions continue in application controllers independently of whether Ollama is responding. AI availability does not determine the pump's stop deadline.

## What “local” and “no inference bill” mean

The verified runtime has no paid external LLM endpoint and needs no external LLM provider API key. The external AI provider charge for these local requests is **€0 per request**. Hardware, electricity, maintenance and development effort still have costs; the [cost breakdown](costs.md) separates them.

Device communication still uses the manufacturer's cloud, and community/news features contact their public sources. Local AI therefore describes the model execution boundary, not a completely offline product. Model downloads also require access to their distribution source.

This overview discloses model identities, roles and deployment facts. It does not distribute application source, private prompts, retrieval policies, model weights or controller internals.
