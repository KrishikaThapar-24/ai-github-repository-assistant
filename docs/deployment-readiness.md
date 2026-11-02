# Deployment readiness

The current application is a working local demo. It is not yet deployed or validated on a hosted runtime.

## $0 host limitations

Render currently lists its free web service at 0.1 CPU and 512 MB RAM. Free services use an ephemeral filesystem, so local Chroma/SQLite indexes and the Hugging Face model cache disappear after service restarts or redeploys. The service can also sleep when idle. This app loads sentence-transformers, PyTorch, Chroma, and the API in the backend; its memory use has not been validated against 512 MB, so a free instance may restart or fail while loading the model. A free deployment should be treated as an experiment, not a reliable public demo.

Hugging Face Spaces' current docs say Docker and Gradio Spaces require a paid plan for personal accounts; its free static Spaces cannot run this Python backend.

## Protecting model credentials

Production configuration requires all of these values in the host's secret manager:

- `DEV_ASSISTANT_ENVIRONMENT=production`
- `DEV_ASSISTANT_API_ACCESS_TOKEN`
- `DEV_ASSISTANT_DEMO_ACCESS_PASSWORD`
- The provider key selected by `DEV_ASSISTANT_LLM_PROVIDER` (for example, `DEV_ASSISTANT_OPENAI_API_KEY`)

Keep the API token and provider key on the backend. Keep the demo password private. The password gate limits who can use the shared demo, but this project does not yet implement per-user quotas or a hard model-spend limit. Do not share the public demo password broadly while using a personal API key.

## Current gaps before a public launch

- Add durable storage for repository indexes and job state.
- Add request quotas and per-user access control.
- Measure cold-start memory and indexing time on the chosen host.
- Configure and verify separate frontend and API services.
- Test the Chrome extension against the deployed API, including the configured extension ID, host permission, and session-only bearer token.

Sources: [Render free service limitations](https://render.com/docs/free), [Render compute plans](https://render.com/docs/blueprint-spec), and [Hugging Face Spaces overview](https://huggingface.co/docs/hub/spaces-overview).
