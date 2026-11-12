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

Ship the read tools, `preview_return`, `lookup_treatment`, and `start_mock_batch` first.

### 2. Generative desk

A desk column on the batch, and a question on the home page. Grok calls the same operations as the MCP server and answers with blocks. Django renders the blocks. The model does not send HTML.

Blocks: prose, line and chain, flags, VAT3 boxes, the 12-month comparison, a line edit, and sign-off. An edit posts to the existing save. A change stays an override. The sign-off block is the name field and the flag tick. The agent can draft the note. It cannot sign.

A read is instant. A machine step keeps the five-second animation and defaults to the mock. Each turn is stored on the batch so the trail shows what the agent suggested and what a person saved.

The step pages stay. Build this after the shared tool module exists, so the desk and the MCP server call one path.
