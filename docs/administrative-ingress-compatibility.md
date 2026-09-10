# Feishu administrative ingress compatibility

The gateway can optionally forward Feishu long-connection message events to
the Administrative M6 durable ingress. The feature is disabled by default;
set `ADMINISTRATIVE_INGRESS_BASE_URL` to enable it. The target path is
`/v1/intake/feishu/events`.

## Field mapping

The official Lark SDK event provides the following fields without needing to
retain the provider message body:

| Durable-ingress field | Long-connection source | Handling |
|---|---|---|
| `source_event_id` | `header.event_id` | Stable delivery identity; message id is only a fallback for existing chat dispatch. |
| `tenant_ref` | `header.tenant_key`, then `event.sender.tenant_key` | Required; missing tenant metadata fails closed for the handoff. |
| `message_id` | `event.message.message_id` | Required. |
| `thread_ref` | `root_id`, then `thread_id`, then `parent_id`, then `message_id` | Reconstructed as the ingress envelope's `root_id`/`parent_id` fields for the current admin adapter. |
| `sender_external_subject` | `event.sender.sender_id.open_id` | Provider-native identity only; no displayed sender is added. |
| `occurred_at` / `sequence` | `event.message.create_time`, then `header.create_time` | Numeric epoch value is forwarded unchanged as a string; the admin adapter normalizes it. |
| provider verification | `header.token` | Required for this compatibility path; the value is used only as an opaque authentication field and is never logged. |

The reconstructed JSON deliberately omits `event.message.content`. The
Administrative boundary stores only the verified receipt and metadata outbox
payload, then performs canonical message retrieval asynchronously. The
gateway does not call the canonical fetcher, model pipeline, identity
resolver, or admission path.

## Delivery and failure behavior

The forwarder uses a bounded retry budget for network errors and retryable
HTTP responses. Retries reuse the same source event id and exact metadata
payload, so the Administrative receipt boundary can deduplicate an ambiguous
delivery. A missing provider token, non-retryable rejection, or exhausted
retry budget fails closed and is logged with a safe error code only. Such a
handoff failure does not fail or change the existing Feishu text dispatch to
the control plane.

The current long-connection path cannot recreate optional callback signature
headers (`X-Lark-Request-Timestamp`, nonce, signature) because the SDK
callback is not carrying the original HTTP request. Therefore this adapter
requires the Administrative Feishu verifier to use its configured callback
token for this handoff; deployments requiring the optional signed callback
headers need a separate provider-authenticated ingress path.

## Evidence boundary

Unit tests prove field extraction, omission of message content, bounded retry,
and safe request construction. Provider staging evidence is still required
for the actual long-connection payload shape (`header.token` presence,
tenant/thread/timestamp population), network reachability, Administrative
receipt/outbox persistence, duplicate delivery, canonical fetch, and the
subsequent human-confirmed M6 path. No staging claim is made by the gateway
unit tests.
