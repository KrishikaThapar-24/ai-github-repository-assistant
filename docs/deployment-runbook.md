# Demo deployment runbook

This runbook targets a small, password-gated demo. It does not make the current
single-process/local-disk index suitable for company-scale or multi-user use.

## Services

- API: Google Cloud Run, built from the repository `Dockerfile`.
- UI: Streamlit Community Cloud, using `apps/web/streamlit_app.py` and the root
  `requirements.txt`.
- Chrome extension: load `apps/extension` unpacked for local testing; configure
  its API origin and bearer token in the extension options page.

Vercel is a good fit for the planned Next.js frontend. It is not the API target
for this Python service: the API uses Chroma/SQLite files, long indexing work,
and a process-local job runner. The current Streamlit frontend should stay on
Streamlit hosting until the Next.js migration.

## API on Cloud Run

1. Push the project to GitHub and create/select a Google Cloud project with
   billing enabled. Cloud Run usage may exceed its free quota.
2. Build and deploy the repository root as a Cloud Run service. Start with one
   maximum instance, one worker, at least 2 GiB memory / 1 vCPU, and a 30-minute
   request timeout because the local Hugging Face model and PyTorch add cold
   start time and repository indexing can take several minutes.
3. Set these environment variables in the service configuration:
   - `DEV_ASSISTANT_ENVIRONMENT=production`
   - `DEV_ASSISTANT_LLM_PROVIDER=openai` (or `anthropic`)
   - `DEV_ASSISTANT_INDEXING_MODE=inline` so indexing finishes within the HTTP
     request and works with request-based CPU allocation.
   - `DEV_ASSISTANT_API_ACCESS_TOKEN` (a long random secret)
   - `DEV_ASSISTANT_DEMO_ACCESS_PASSWORD` (a different random secret)
   - The selected provider key, for example `DEV_ASSISTANT_OPENAI_API_KEY`
   - Leave `DEV_ASSISTANT_GITHUB_TOKEN` unset for a public demo that accepts
     arbitrary repository names. A server-side token could expose private repos
     belonging to its owner. Only add one for a trusted/private deployment and
     grant read-only contents, pull request, and issue access.
4. Configure each secret through Secret Manager or the Cloud Run secret
   integration. Do not put secrets in source control, Docker build arguments,
   or image layers.
5. Verify `/health`, then `/api/v1/status` with the bearer token. `/health` is
   intentionally public and reveals only process health.

The API uses local Chroma, SQLite, and in-memory job state. Cloud Run's writable
filesystem and process memory are temporary: an instance replacement loses
repository indexes and active job state. Keep the first demo to one instance and
small public repositories, and re-index after a restart. Do not claim durable
storage or company-scale support until these stores and jobs move to managed
services. Inline indexing holds the request open while it works; the Streamlit
UI waits up to 30 minutes and does not show per-file progress. Do not use the
background indexing mode with Cloud Run's default request-based CPU allocation:
Cloud Run can suspend background threads after returning a response. Instance-
based billing can support background jobs, but charges for instance lifetime
and needs minimum instances for reliable processing, so it is not the $0 demo
configuration. [Cloud Run billing behavior](https://docs.cloud.google.com/run/docs/configuring/billing-settings)

## Streamlit Community Cloud

1. Deploy `apps/web/streamlit_app.py` from the repository root; select
   `requirements.txt` for dependencies.
2. In the Streamlit app's Secrets setting, add TOML values for
   `DEV_ASSISTANT_API_URL` (the HTTPS Cloud Run URL),
   `DEV_ASSISTANT_API_ACCESS_TOKEN`, and
   `DEV_ASSISTANT_DEMO_ACCESS_PASSWORD`. The UI reads these from `st.secrets`;
   local runs can continue using environment variables.
   Example shape (replace placeholders in the hosting console; do not commit
   real values):

   ```toml
   DEV_ASSISTANT_API_URL = "https://your-service.run.app"
   DEV_ASSISTANT_API_ACCESS_TOKEN = "your-api-token"
   DEV_ASSISTANT_DEMO_ACCESS_PASSWORD = "your-demo-password"
   ```
3. Open the public app URL and use the demo password. Index a small repository,
   then check a source question, a latest-PR question, a PR review, and an issue
   investigation. Each AI action may use the configured provider key.

Community Cloud may sleep when idle. The Cloud Run local index may also be
replaced, so demo users may need to re-index. Keep the demo password private;
there are not yet per-user quotas or a hard provider-spend cap.

## Before calling this production-ready

Use durable managed storage for vector, lexical, dependency, and job data;
add per-user identity, quotas and audit logs; add background workers; then load
test indexing and retrieval on representative large repositories. The current
ingestor has a 64 MiB archive cap, 10,000 indexable-file cap, 1 MiB per-file
cap, and 128 MiB aggregate source cap.
