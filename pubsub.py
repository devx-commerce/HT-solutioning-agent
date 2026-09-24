"""Publishing build-work — the expensive half of a sweep, split from the
cheap half (classification) so Cloud Run's own concurrency/instance caps
can bound how many Agent Engine sessions run at once.

Why this exists now rather than later: ~100 emails/week isn't much on
average, but "not spread uniformly" means a single sweep can surface a
burst well above that average on a given day. Sequential-in-one-request
(the pre-Pub/Sub design) doesn't absorb a burst — it just makes that one
/sweep call slower. Unbounded parallel calls into Agent Engine would trade
that for the opposite risk: a burst spiking cost or hitting quota all at
once. A queue plus a capped consumer is the actual control for "how many
agent instances run concurrently," independent of how many are queued.
"""

from __future__ import annotations

import json
import os

from google.cloud import pubsub_v1

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
TOPIC = os.environ.get("BUILD_WORK_TOPIC", "solutioning-agent-build-work")

_publisher: pubsub_v1.PublisherClient | None = None


def _client() -> pubsub_v1.PublisherClient:
    global _publisher
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
    return _publisher


def publish_build_task(**payload) -> None:
    topic_path = _client().topic_path(PROJECT, TOPIC)
    _client().publish(topic_path, json.dumps(payload).encode("utf-8")).result()
