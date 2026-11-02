"""Small development client for exercising the public FastAPI contract."""

from __future__ import annotations

import hmac
import os
import time

import httpx
import streamlit as st

API_URL = os.getenv("DEV_ASSISTANT_API_URL", "http://127.0.0.1:8001").rstrip("/")
API_TOKEN = os.getenv("DEV_ASSISTANT_API_ACCESS_TOKEN", "")
DEMO_PASSWORD = os.getenv("DEV_ASSISTANT_DEMO_ACCESS_PASSWORD", "")
IS_LOCAL_API = API_URL.startswith(("http://127.0.0.1", "http://localhost"))
API_HEADERS = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}

st.set_page_config(page_title="AI GitHub Repository Assistant", page_icon="⌘", layout="wide")

if not IS_LOCAL_API:
    if not API_TOKEN or not DEMO_PASSWORD:
        st.error("The hosted demo is not configured with its access credentials.")
        st.stop()
    if not st.session_state.get("demo_authenticated"):
        st.title("AI GitHub Repository Assistant")
        st.caption("This demo is password protected to prevent unauthorized API usage.")
        password = st.text_input("Demo password", type="password")
        if st.button("Unlock demo", type="primary"):
            st.session_state["demo_authenticated"] = hmac.compare_digest(
                password.encode("utf-8"), DEMO_PASSWORD.encode("utf-8")
            )
            if not st.session_state["demo_authenticated"]:
                st.error("That password did not match.")
            else:
                st.rerun()
        st.stop()

st.title("AI GitHub Repository Assistant")
st.caption("Connect a repository, index a pinned snapshot, and ask evidence-backed questions.")

with st.sidebar:
    st.subheader("Repository")
    repository_id = st.text_input("Owner/repository", placeholder="octocat/Hello-World")
    ref = st.text_input("Branch or commit", value="HEAD")
    if st.button("Connect and index", type="primary", use_container_width=True):
        try:
            response = httpx.post(
                f"{API_URL}/api/v1/repositories/connect",
                headers=API_HEADERS,
                json={"repository_id": repository_id.strip(), "ref": ref.strip() or "HEAD"},
                timeout=15,
            )
            response.raise_for_status()
            st.session_state["index_job_id"] = response.json()["job_id"]
            st.session_state["repository_id"] = repository_id.strip()
            st.session_state.pop("commit_sha", None)
        except httpx.HTTPError as exc:
            st.error(f"Could not start indexing: {exc}")

    job_id = st.session_state.get("index_job_id")
    if job_id:
        try:
            job_response = httpx.get(
                f"{API_URL}/api/v1/repositories/jobs/{job_id}",
                headers=API_HEADERS,
                timeout=10,
            )
            job_response.raise_for_status()
            job = job_response.json()
            if job["status"] == "succeeded":
                result = job["result"]
                st.session_state["commit_sha"] = result["commit_sha"]
                st.success(
                    f"Indexed {result['file_count']} files, {result['chunk_count']} code sections, "
                    f"{result['dependency_count']} imports, and {result['call_count']} cross-file calls."
                )
                st.code(result["commit_sha"][:12])
            elif job["status"] == "failed":
                st.error(job["error"])
            else:
                st.info(f"Indexing: {job['status']}")
                time.sleep(1)
                st.rerun()
        except httpx.HTTPError as exc:
            st.error(f"Could not read indexing status: {exc}")

st.subheader("Search repository code")
search_query = st.text_input("Search query", placeholder="checkout retry handling")
if st.button(
    "Search code",
    disabled=not st.session_state.get("commit_sha") or not search_query.strip(),
):
    try:
        response = httpx.post(
            f"{API_URL}/api/v1/assistant/search",
            headers=API_HEADERS,
            json={
                "repository_id": st.session_state["repository_id"],
                "commit_sha": st.session_state["commit_sha"],
                "query": search_query,
                "top_k": 8,
            },
            timeout=60,
        )
        response.raise_for_status()
        results = response.json()["results"]
        if not results:
            st.info("No matching code was found in this indexed snapshot.")
        for result in results:
            st.markdown(
                f"**{result['path']}:{result['start_line']}-{result['end_line']}** · "
                f"{result['symbol_kind']} `{result['symbol_name']}` · score {result['score']:.3f}"
            )
            st.code(result["snippet"], language=result["language"])
    except httpx.HTTPError as exc:
        st.error(f"Code search failed: {exc}")

st.subheader("Ask about your repository")
question = st.text_area(
    "Question",
    placeholder="Where is authentication implemented? Explain the login flow across files.",
    height=100,
)
path = st.text_input("Limit to a file (optional)", placeholder="src/auth/service.py")

if st.button("Ask", disabled=not st.session_state.get("repository_id")):
    repo_id = st.session_state["repository_id"]
    commit_sha = st.session_state.get("commit_sha")
    asks_for_latest_activity = any(
        word in question.lower() for word in ("pr", "pull request", "issue", "commit")
    ) and any(
        word in question.lower() for word in ("latest", "last", "newest", "most recent", "when")
    )
    if not commit_sha and not asks_for_latest_activity:
        st.warning("Index this repository first to ask source-code questions.")
    else:
        with st.spinner("Gathering repository evidence…"):
            try:
                response = httpx.post(
                    f"{API_URL}/api/v1/assistant/ask",
                    headers=API_HEADERS,
                    json={
                        "repository_id": repo_id,
                        "commit_sha": commit_sha or "activity-only",
                        "question": question,
                        "path": path.strip() or None,
                    },
                    timeout=120,
                )
                response.raise_for_status()
                answer = response.json()
                st.markdown(answer["answer"])
                for citation in answer["citations"]:
                    st.markdown(
                        f"[{citation['citation_id']} · {citation['path']}:{citation['start_line']}-{citation['end_line']}]"
                        f"(https://github.com/{citation['repository_id']}/blob/{citation['commit_sha']}/"
                        f"{citation['path']}#L{citation['start_line']}-L{citation['end_line']})"
                    )
            except httpx.HTTPError as exc:
                st.error(f"The assistant request failed: {exc}")

st.divider()
st.subheader("Review a pull request")
st.caption("Fetches the pull request diff with read-only GitHub access. It does not post a review.")
pull_request_number = st.number_input("Pull request number", min_value=1, step=1)
if st.button("Review pull request", disabled=not st.session_state.get("repository_id")):
    with st.spinner("Fetching and reviewing the pull request diff…"):
        try:
            response = httpx.post(
                f"{API_URL}/api/v1/assistant/review",
                headers=API_HEADERS,
                json={
                    "repository_id": st.session_state["repository_id"],
                    "pull_request_number": int(pull_request_number),
                },
                timeout=120,
            )
            response.raise_for_status()
            review = response.json()
            st.markdown(f"### PR #{review['pull_request_number']}: {review['title']}")
            if review["url"]:
                st.markdown(f"[Open pull request]({review['url']})")
            st.markdown(review["answer"])
            for citation in review["citations"]:
                st.markdown(
                    f"[{citation['citation_id']} · {citation['path']}:{citation['start_line']}-{citation['end_line']}]"
                    f"(https://github.com/{citation['repository_id']}/blob/{citation['commit_sha']}/"
                    f"{citation['path']}#L{citation['start_line']}-L{citation['end_line']})"
                )
        except httpx.HTTPError as exc:
            detail = exc.response.text if exc.response is not None else str(exc)
            st.error(f"Pull request review failed: {detail}")

st.divider()
st.subheader("Investigate a GitHub issue")
st.caption(
    "Issue reports and comments are treated as untrusted context and compared with indexed code."
)
issue_number = st.number_input("Issue number", min_value=1, step=1)
if st.button(
    "Investigate issue",
    disabled=not st.session_state.get("repository_id") or not st.session_state.get("commit_sha"),
):
    with st.spinner("Reading the issue and tracing relevant code…"):
        try:
            response = httpx.post(
                f"{API_URL}/api/v1/assistant/investigate-issue",
                headers=API_HEADERS,
                json={
                    "repository_id": st.session_state["repository_id"],
                    "commit_sha": st.session_state["commit_sha"],
                    "issue_number": int(issue_number),
                },
                timeout=120,
            )
            response.raise_for_status()
            investigation = response.json()
            st.markdown(f"### Issue #{investigation['issue_number']}: {investigation['title']}")
            if investigation["url"]:
                st.markdown(f"[Open issue]({investigation['url']})")
            if investigation["context_truncated"]:
                st.warning("The issue report or comments were truncated to fit the analysis limit.")
            st.markdown(investigation["answer"])
            for citation in investigation["citations"]:
                st.markdown(
                    f"[{citation['citation_id']} · {citation['path']}:{citation['start_line']}-{citation['end_line']}]"
                    f"(https://github.com/{citation['repository_id']}/blob/{citation['commit_sha']}/"
                    f"{citation['path']}#L{citation['start_line']}-L{citation['end_line']})"
                )
        except httpx.HTTPError as exc:
            detail = exc.response.text if exc.response is not None else str(exc)
            st.error(f"Issue investigation failed: {detail}")
