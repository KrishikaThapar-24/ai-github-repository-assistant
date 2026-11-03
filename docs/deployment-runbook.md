# Demo deployment runbook

This runbook reflects the current demo deployment: Render API, Streamlit Community Cloud UI, and an unpacked Chrome extension. It does not make the current local-disk index suitable for company-scale or multi-user use.

## Render API

1. Create a Render Web Service from the GitHub repository, using the repository root and its `Dockerfile`. Set `/health` as the HTTP health-check path.
2. The container binds to Render's `PORT` on `0.0.0.0` and runs one Uvicorn worker. Keep one instance because job status and indexes are local to a process.
3. Set these environment variables in the Render service:
   - `DEV_ASSISTANT_ENVIRONMENT=production`
   - `DEV_ASSISTANT_LLM_PROVIDER=openai` (or `anthropic`)
   - `DEV_ASSISTANT_INDEXING_MODE=background` (the default; the UI and extension poll job status)
   - `DEV_ASSISTANT_API_ACCESS_TOKEN` (long random secret)
   - `DEV_ASSISTANT_DEMO_ACCESS_PASSWORD` (a different random secret)
   - The provider key, for example `DEV_ASSISTANT_OPENAI_API_KEY`
   - `DEV_ASSISTANT_CHROME_EXTENSION_ID` after loading the unpacked extension and reading its ID from `chrome://extensions`
   - Leave `DEV_ASSISTANT_GITHUB_TOKEN` unset for a public demo that accepts arbitrary repositories. A server-side token could expose private repositories belonging to its owner. Add one only for a trusted/private deployment and grant read-only contents, pull requests, and issues access.
4. Add secrets through Render's Environment page. Never put secrets in source control, Docker build arguments, or image layers.
5. Verify `/health`, then `/api/v1/status` with the bearer token. `/health` is public and reveals only process health. Redeploy after setting the extension ID so FastAPI's CORS allowlist includes `chrome-extension://<extension-id>`.

The API stores Chroma, SQLite, and job state locally. Render's filesystem is ephemeral, so indexes disappear after a restart, redeploy, or free-service spin-down; re-index after that. Render Free has 512 MB RAM and 0.1 CPU, which may not be enough for the local Hugging Face/PyTorch model plus Chroma. Free services sleep after 15 minutes without inbound traffic. Treat Free as a best-effort demo and test indexing in the live service. [Render free service limits](https://render.com/docs/free)

## Streamlit Community Cloud UI

1. Deploy `apps/web/streamlit_app.py` from the repository root and select `requirements.txt`.
2. Add these TOML values in the Streamlit app's Secrets setting. The UI reads them through `st.secrets`; local runs can use environment variables.

   ```toml
   DEV_ASSISTANT_API_URL = "https://your-service.onrender.com"
   DEV_ASSISTANT_API_ACCESS_TOKEN = "your-api-token"
   DEV_ASSISTANT_DEMO_ACCESS_PASSWORD = "your-demo-password"
   ```

   Replace the placeholders in the hosting console; do not commit real values.
3. Open the public app and use the demo password. Try a code question, latest-PR question, PR review, and issue investigation.

Streamlit Community Cloud may sleep when idle. Render may also discard its local index, so users may need to re-index. Keep the demo password private; per-user quotas and a hard provider-spend cap are not implemented.

## Chrome extension

1. Open `chrome://extensions`, enable Developer mode, and load the `apps/extension` directory using **Load unpacked**.
2. Copy the extension ID into Render's `DEV_ASSISTANT_CHROME_EXTENSION_ID` setting and redeploy the API.
3. Open the extension's Connection settings. Set the API origin to the Render URL (origin only, no path), enter the Render bearer token, save, and grant the requested host permission. The token is stored for the current browser session.
4. Open a GitHub repository and click the extension icon. Public repositories need no GitHub token. Private repositories require a narrowly scoped read-only GitHub token on Render.

## Limits before a production launch

Use durable managed storage for vector, lexical, dependency, and job data; add per-user identity, quotas, and audit logs; use durable background workers; then load test on representative large repositories. The current ingestor limits archives to 64 MiB, indexable files to 10,000, each file to 1 MiB, and aggregate source to 128 MiB.
