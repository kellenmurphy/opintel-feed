# Security Policy

## Supported Versions

Only the latest commit on `main` is actively maintained. No versioned releases are published at this time.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

To report a vulnerability, use one of the following:

- **GitHub private vulnerability reporting** — use the [Report a Vulnerability](../../security/advisories/new) link in the Security tab of this repository
- **Email** — contact the maintainer directly at [wfx6yz@virginia.edu](mailto:wfx6yz@virginia.edu)

Include as much detail as possible: steps to reproduce, potential impact, and any suggested mitigations. You can expect an acknowledgment within 5 business days.

## Security Considerations for Deployment

### API Key Protection

This tool requires an Anthropic API key set in a `.env` file. The `.env` file is gitignored and must never be committed.

- Do not hardcode the key in any source file or pass it as a command-line argument
- Rotate the key immediately if you suspect it has been exposed
- Scope the API key to the minimum required permissions in the [Anthropic Console](https://console.anthropic.com)

### RSS Feed Content

This tool fetches and processes content from external RSS feeds. Feed content is passed to the Claude API for summarization but is never executed. However:

- Treat feed content as untrusted input — do not pipe raw feed output to a shell or eval it
- The off-topic guard and heuristic pre-filter provide a layer of content screening, but do not guarantee safety of fetched content
- If a feed URL is compromised or starts serving malicious content, remove it from `config/feeds.json`

### Manual Includes

URLs listed in `config/manual_includes.json` are fetched directly via HTTP. Only add URLs you trust. The tool performs basic HTML-to-text extraction on fetched content; it does not execute scripts.

### Cache Directory

The `cache/` directory stores article titles, URLs, and AI-generated summaries on disk in plaintext JSON. Ensure appropriate filesystem permissions are applied if this tool is run in a shared environment.

### Network Requests

This tool makes outbound HTTPS requests to:

- Configured RSS feed URLs
- The Anthropic API (`api.anthropic.com`)
- URLs listed in `manual_includes.json`

No data is sent to any other external service. If operating in a network-restricted environment, ensure these destinations are reachable and review outbound firewall rules accordingly.

## Out of Scope

The following are not considered security vulnerabilities for this project:

- Rate limiting or cost overruns from the Anthropic API (mitigate with API key spending limits)
- Content quality or accuracy of AI-generated summaries
- Availability or integrity of third-party RSS feeds
