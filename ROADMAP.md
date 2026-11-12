# Roadmap

Local Irish VAT working paper. SQLite, Django, Grok when a live call is asked for. Nothing here files with Revenue.

## Done

- Upload up to 10 PDFs, or a 50-line mock batch with no files.
- Review and edit the extraction. Accept is blocked while an invoice has an error.
- VAT classification with a chain: supply, good or service, place of supply, rate, who accounts. Live call or labelled mock.
- Deductibility review with its own chain. The model’s answer can be overridden. T2 uses the recoverable VAT. Petrol, entertainment, hotels, and most food stay out unless you override them.
- Sign-off with a typed name, the open flags, and every override, then the VAT3 return.
- Boxes T1–T4, E1, E2, ES1, ES2, PA1. Payment or refund. Comparison with the last 12 months. Run history and an audit trail.
- Five-second step animation. Save stays instant.
- Generative desk at `/batches/<id>/desk/`. A question returns blocks the templates render: prose, a line and its chain, flags, VAT3 boxes, the 12-month comparison, a line edit, and a sign-off draft. Answer from the file makes no API call. Ask Grok uses the same blocks. The sign-off name is typed by a person. Machine steps from the desk stay on the mock.

## Next

### 1. MCP server (`vat-desk`)

A local stdio server so an agent can use the same steps as the pages. Machine steps default to the mock.

Read:

- `list_batches`, `get_batch`, `list_lines`, `get_return`, `list_audit`
- `lookup_treatment` for a labelled training row
- `preview_return` for proposed overrides, without saving

Write, with the same gates as the pages:

- `start_mock_batch`, `read_invoices` (up to 10 local PDFs)
- `update_lines`, `accept_extraction`
- `update_classification`, `assess_deductibility`
- `update_deductibility`, `accept_deductibility`
- `sign_batch` only with a person’s name, and an explicit tick when flags are open

Also expose the batch JSON and the training catalogue as resources, and a `review_batch` prompt that stops before sign-off.

Ship the read tools, `preview_return`, `lookup_treatment`, and `start_mock_batch` first. The desk already calls `items/agent_tools.py`. The MCP server should wrap that module rather than a second copy.
