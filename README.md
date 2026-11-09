# ConferenceOps AI

Find the right conference talk, accessibly, on a platform with an AI operations team behind it:
Elasticsearch search, Dash0 observability with Agent0, Radar's AI SRE, and Gitar-reviewed delivery to Kubernetes.

> Built at the WeMakeDevs Cloud Native Hackathon, Salt Lake City, November 9, 2026. Work in progress: this README
> grows with every merged pull request.

## Status

Being built today. Every change after the first commit lands through a pull request that Gitar reviews. The
**Build log** issue records each one, including the pull requests Gitar blocked.

## What was prepared before the event

The hackathon rules allow ideas, notes, sketches and research before the start. Before November 9 we prepared:

- a written plan, a task list, and a sketch of the fictional demo event's content (notes);
- research: reading the sponsors' documentation and trying how their tools behave, in throwaway places;
- accounts, trials, API keys and installed tools: Gitar, Dash0, Radar, Azure, the Angular CLI, k6.

No project code or configuration was written before the start.

## AI use (disclosure)

- The builder writes the code with **Claude Code** (Anthropic) as the AI coding assistant; Codex is available as
  an alternative.
- **Gitar** reviews every pull request, can push fixes, approves, and auto-merges low- and medium-risk changes.
- **Agent0** (Dash0) investigates production telemetry and can open fix pull requests.
- **Radar's** AI investigations run through Claude Code in a read-only mode.
- The product itself uses **OpenAI** models: reasoning, embeddings and dictation.

## How this repository started

This first configuration commit went straight to `main`: this README, `AGENTS.md`, the Gitar configuration, CI and
the Build log. Branch protection was switched on right after it. From then on, every change needs a pull request,
green checks and Gitar's approval.

## License

Apache-2.0
