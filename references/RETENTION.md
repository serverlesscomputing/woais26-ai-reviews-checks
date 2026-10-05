# Data retention, ZDR, and model posture — reference

Detail behind Step 0 of `SKILL.md`. Read this when your plan is anything other
than Pro/Max with the training toggle off, or when you need to justify the setup
to a chair or a co-reviewer.

ACM's peer-review policy is the binding rule, and the clause that matters is:

> **Confidentiality of Submissions, Authors, and Reviews** — "For single and
> double anonymous publication venues, submissions may not be disclosed outside
> authorized reviewers […]. **This includes the uploading of confidential
> submissions, technical approaches described by authors in their submissions, or
> any information about the authors into any system managed by a third party,
> including LLMs, that does not promise to maintain the confidentiality of that
> information by reviewers**, since the storage, indexing, learning, and
> utilization of such submissions may violate the author's right to
> confidentiality."
> — [ACM Peer Review Policy](https://www.acm.org/publications/policies/peer-review)

The test is not "is it AI" — it is "does this endpoint promise confidentiality".

## 1. Retention by plan

Run the probe first — it reads your plan from the stored credential and prints
the path that applies to you. The table is the reference.

| Plan you are on | Trains on your content? | Retention | Verdict for reviewing |
|---|---|---|---|
| **Free / Pro / Max**, Model Improvement **OFF** | No | **30 days** | **Usable.** No-training + 30-day delete meets ACM's confidentiality test. Never ZDR-eligible |
| **Free / Pro / Max**, Model Improvement **ON** | **Yes** | **up to 5 years**, de-identified, in training pipelines | **Do not use.** Fails the ACM clause |
| **Commercial API key** (`ANTHROPIC_API_KEY`) | No, under Commercial Terms | inputs+outputs deleted **within 30 days**; **ZDR** available on request | **Best** short of a local model |
| **Claude Code on Claude for Enterprise** | No | 30 days; separate ZDR offering, qualified accounts only | Best for an org |
| **Team / Enterprise** chat or Cowork | No | 30 days | OK, but these interfaces are **not** ZDR-eligible |
| **Bedrock / Vertex / Foundry** | n/a — cloud provider is the processor | per that provider | Check that provider's docs |

Applies on **every** plan, ZDR included:
- content flagged as a Usage Policy violation: inputs/outputs **up to 2 years**,
  trust-and-safety scores **up to 7 years**
- `/feedback`, `/bug`, `/share` upload the conversation (paper text included) and
  it is kept **5 years**
- local Claude Code transcripts live in **plaintext** under `~/.claude/projects/`
  for **30 days** by default

Source: [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention) ·
[Claude Code data usage](https://code.claude.com/docs/en/data-usage) ·
[org retention](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data) ·
[consumer retention](https://privacy.claude.com/en/articles/10023548-how-long-do-you-store-my-data)

## 2. If you are on Claude Pro or Max

A consumer plan **is** usable for peer review, but only in a specific
configuration, and zero data retention is **never** available on Free, Pro or Max.
Do all five:

1. **Model Improvement = OFF.** This is the whole ballgame: off means no training
   and a 30-day retention period; on means up to **5 years** in training
   pipelines. It covers Claude Code from this account, not just the web app.
   Turning it on applies only to new and resumed chats, so switching it off now
   protects this batch.

   **You can check and change it without leaving the agent** — `/privacy-settings`
   views and updates it, and is available to Pro and Max subscribers only. The
   browser equivalent is
   [claude.ai/settings/data-privacy-controls](https://claude.ai/settings/data-privacy-controls)
   ("Model Improvement" / "Help improve Claude"). Note the date you checked.
2. **Harden the Claude Code defaults.** Two of these default to *on specifically
   for Pro/Max sign-ins*, where a ZDR org would never see them:

   ```bash
   export DISABLE_ERROR_REPORTING=1                  # on by default for Pro/Max
   export CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1      # ZDR orgs never see this; you do
   export DISABLE_FEEDBACK_COMMAND=1                 # /feedback = 5-year retention
   export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 # all of the above at once
   ```

3. **Isolate the review session** so transcripts can be deleted wholesale:

   ```bash
   alias claude-review='CLAUDE_CONFIG_DIR=$HOME/.claude-review claude'
   # then: { "cleanupPeriodDays": 7 } in $HOME/.claude-review/settings.json
   ```

4. **Avoid the surfaces that store server-side**: cloud sessions (they always use
   your subscription credential and keep the transcript), Remote Control,
   Artifacts, and `/feedback`.
5. **Delete the review conversations in claude.ai once decisions are out.** This
   is the strongest lever a consumer plan gives you: a deleted chat leaves your
   history immediately, is purged from backend storage within 30 days, **and is
   excluded from future model training**.

**If 30-day retention is not good enough for you**, a consumer plan cannot get
there. The three real options are a Console API key with a ZDR arrangement,
Claude Code on Claude for Enterprise (separate ZDR offering, qualified accounts),
or a local model (section 5). A local model is the only one a solo reviewer can set up
today with no procurement.

**Careful with the API-key route:** setting `ANTHROPIC_API_KEY` while signed in to
Pro/Max makes Claude Code use the key **silently**, billing the Console org
instead of your plan. That is *better* for confidentiality, but confirm it is
deliberate — `/status` marks the credential that is not in use, and
`unset ANTHROPIC_API_KEY` falls back to the subscription. The probe warns when it
sees both.

## 3. Is ZDR in effect? Checking from inside the agent

```bash
uv run "$PC" zdr              # verdict + evidence + how strong the evidence is
uv run "$PC" zdr --offline    # policy facts only, no network
```

**On Pro or Max the answer is settled by policy, not by probing: you are not on
ZDR and cannot be.** Anthropic's retention docs list "Claude consumer products:
Claude Free, Pro, and Max plans, including when customers on those plans use
Claude's web, desktop, or mobile apps or Claude Code" among the things ZDR does
**not** cover. No command, env var or API call changes that answer, so don't go
looking for one. What you *can* check in-agent is the thing that actually varies:
`/privacy-settings` for the training toggle, `/status` for which credential is
live.

**On a commercial credential there is no API that reports ZDR status.**
`/v1/organizations/me` tells you which organization a key belongs to, not its
retention configuration. So `zdr` reports graded evidence instead:

| Evidence | Strength | What it tells you |
|---|---|---|
| **Covered Model gate** — call `claude-fable-5-1` with a 2-token prompt | strongest available | `400 "must have data retention enabled"` ⇒ retention is **off** for this org/workspace, consistent with ZDR. `200` ⇒ retention is **on**, so not ZDR |
| **Transcript-share survey** | inference | "Organizations with zero data retention … never see this follow-up." If you were ever asked "Can Anthropic look at your session transcript?", that session was not ZDR |
| **Error-reporting default** | weak inference | Claude Code enables it only when "your organization doesn't have a zero data retention or HIPAA agreement" |
| **Features disabled under ZDR** | observable | Claude Code ZDR on Enterprise blocks cloud sessions, Claude Tag, Artifacts, `/feedback`, `/bug`, `/share` and Remote Control at the backend. If one of them works, that org is not ZDR — don't test this with a submission in context |
| **Admin API** | none | no public endpoint exposes it |

**The authoritative answer is your contract or your Anthropic account team.** ZDR
is a per-organization arrangement granted after an eligibility review and
audit-logged at enablement, not a flag you can read back. For a review audit trail
record the organization id, the date, and who confirmed it — not a probe result.

Two traps even when you *are* ZDR: it covers only requests authenticating into the
ZDR org (a personal login or another org's key is not covered), and it does **not**
block non-eligible features — using the Files API, Batch API, MCP connector, code
execution or API-side Agent Skills steps outside the arrangement for that data,
silently and with no warning.

### Running this skill in a different coding agent

`zdr` names the host from the env markers each agent sets (`CLAUDECODE`,
`CURSOR_AGENT`, `COPILOT_AGENT`, `CODEX_*`, `GEMINI_CLI`, …) and refuses to give a
verdict when that host is not Claude Code, or when several agents' markers are
present at once and it cannot tell which is in control. Everything in this file
is Anthropic's documentation; it does not describe another vendor's agent.

Two separate questions, both of which must be answered:

1. **The host agent** — what does it store server-side (transcripts, telemetry,
   cloud sync, feedback uploads) and for how long, and where does it keep local
   transcripts?
2. **The model provider behind it** — many agents are bring-your-own-key or
   multi-provider, so the host and the provider can have entirely different
   terms. If the host routes to Anthropic with your own API key, the figures in
   section 1 apply to the inference leg only, not to what the host stores.

Ask the user to confirm these five, offering to web search current docs:

```
a) Which model provider and model is this session actually using?
b) Does that provider train on prompts or outputs by default, and where is the opt-out?
c) How long are prompts and outputs retained? Is a zero-retention mode available
   on this plan, and is it on?
d) What does the host agent store server-side, and for how long?
e) Where does the host keep local session transcripts, and for how long?

  search: "<agent> data retention policy prompts training opt-out"
  search: "<agent> zero data retention enterprise"
  search: "<provider> API data retention training policy commercial terms"
```

Until those are answered the ACM test is unmet, because you cannot say the
endpoint promises confidentiality. A local model (section 5) sidesteps all of it.

## 4. Verify, and pin the model

```bash
# Which credential is actually in use? (the probe prints this too)
#   in Claude Code:  /status
echo "${ANTHROPIC_API_KEY:+API key is set and will WIN over your subscription}"

# Org retention test - API key only, not meaningful on a subscription.
# Fable/Mythos are Covered Models and require 30-day retention, so a 400 proves
# the org is zero-retention. Sends the word "hi" - never paper text.
curl -s https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H 'anthropic-version: 2023-06-01' -H 'content-type: application/json' \
  -d '{"model":"claude-fable-5-1","max_tokens":8,
       "messages":[{"role":"user","content":"hi"}]}'
# 400 "must have data retention enabled" -> ZERO retention. Best case.
# 200 OK                                 -> 30-day retention is on. Acceptable.
```

**Pin the model: `/model opus-5` (or `sonnet-5`), never `best`.** `best` resolves
to the latest **Fable** where available, and Fable 5 / 5.1 and Mythos 5 / 5.1 are
**Covered Models**: prompts and completions are "retained for at least 30 days and
then automatically deleted" wherever they are offered, Claude applications
included. For a **ZDR org** this is a hard block — the model is unavailable unless
Anthropic expressly authorizes it. On **Pro/Max** you never had ZDR to lose, so the
practical gap is small, but "at least 30 days" is looser than your 30-day default,
so pin it anyway. Opus, Sonnet and Haiku are not Covered Models.

**Do not use these API surfaces for submissions** — they are not ZDR-eligible, and
ZDR does *not* block them; using one silently steps outside your arrangement:
Files API (`/v1/files`), Batch API (`/v1/messages/batches`), MCP connector / MCP
tunnels, code execution & programmatic tool calling, API-side Agent Skills
(`/v1/skills`), Claude Managed Agents, Console/playground.
**Fine to use:** `/v1/messages` with inline PDF, prompt caching, citations,
thinking, 1M context, local Claude Code skills (just files on your disk).

## 5. Running against a local model

If you would rather not make a policy argument at all, keep it on the host:

```bash
brew install ollama && brew services start ollama
export OLLAMA_HOST=127.0.0.1:11434        # loopback only
ollama pull qwen3:14b                      # or gpt-oss:20b, mistral-small
ollama run qwen3:14b "summarize the claims in: $(cat out/woais26/papers/7/text.txt)"

# purpose-built alternative, fine-tuned on 79k conference reviews:
#   Llama-OpenReviewer-8B - https://arxiv.org/abs/2412.11948
```

Nothing leaves the machine, so ACM's third-party clause does not apply. Trade-off:
weaker at long-context reasoning than Opus/Sonnet. A good split is **local model
for anything touching the raw PDF, hosted model for your own redacted prose**.
