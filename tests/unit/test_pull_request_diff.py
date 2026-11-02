from dev_assistant.github.activity import PullRequestReviewContext
from dev_assistant.github.pull_request_diff import pull_request_diff_evidence


def review_context(diff: str) -> PullRequestReviewContext:
    return PullRequestReviewContext(
        number=23,
        title="Harden checkout retries",
        body="",
        url="https://github.com/acme/shop/pull/23",
        base_sha="base12345678",
        head_sha="head12345678",
        diff=diff,
    )


def test_diff_parser_cites_added_lines_at_the_pr_head_commit() -> None:
    diff = """diff --git a/checkout/retry.py b/checkout/retry.py
index 123..456 100644
--- a/checkout/retry.py
+++ b/checkout/retry.py
@@ -8,2 +8,3 @@ def retry_payment():
     attempt = 0
+    if timeout <= 0:
+        raise ValueError("timeout must be positive")
     return attempt
"""

    evidence, truncated = pull_request_diff_evidence("acme/shop", review_context(diff))

    assert not truncated
    assert len(evidence) == 1
    assert evidence[0].path == "checkout/retry.py"
    assert evidence[0].commit_sha == "head12345678"
    assert (evidence[0].start_line, evidence[0].end_line) == (9, 10)
    assert evidence[0].symbol_kind == "pull_request_diff"


def test_deletion_only_hunk_points_to_the_base_commit() -> None:
    diff = """diff --git a/auth/check.py b/auth/check.py
--- a/auth/check.py
+++ b/auth/check.py
@@ -20,2 +20,1 @@ def check_token(token):
-    if not token:
     return validate(token)
"""

    evidence, _ = pull_request_diff_evidence("acme/shop", review_context(diff))

    assert evidence[0].commit_sha == "base12345678"
    assert evidence[0].start_line == 20


def test_diff_parser_skips_unsafe_paths_and_reports_hunk_truncation() -> None:
    diff = """diff --git a/../outside.py b/../outside.py
--- a/../outside.py
+++ b/../outside.py
@@ -1,1 +1,2 @@
-value = 1
+value = 2
+other = 3
diff --git a/second.py b/second.py
--- a/second.py
+++ b/second.py
@@ -1,1 +1,2 @@
-value = 1
+value = 2
+other = 3
diff --git a/third.py b/third.py
--- a/third.py
+++ b/third.py
@@ -1,1 +1,2 @@
-value = 1
+value = 2
+other = 3
"""

    evidence, truncated = pull_request_diff_evidence("acme/shop", review_context(diff), max_hunks=1)

    assert len(evidence) == 1
    assert evidence[0].path == "second.py"
    assert truncated
