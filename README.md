# woais26-ai-reviews-checks

Quick PDF validation for submissions to **WoAIS2 2026 — Second International
Workshop on AI and Serverless** (co-located with the 27th ACM/IFIP International
Middleware Conference, Tarragona, Spain, 14–18 Dec 2026) — plus an optional
[Agent Skill](https://agentskills.io) layer on top.

Built for the case where **one reviewer or chair has a lot of papers and a short
window**: Submissions 1 Oct 2026 · notification 12 Oct 2026 · camera-ready 16 Oct 2026, at least three PC reviews per paper, **doubly-anonymous**.

## Two separable things, and you may only want the first

**1. `scripts/paper_checks.py` — a plain Python script that validates submission
PDFs against the workshop requirements. No AI involved at all.** No model, no API
key, no account, no network. It parses the PDFs with PyMuPDF and applies ordinary
rules and regexes: ≤6 pages of technical content, ACM SIGPLAN at 10pt, and the
doubly-anonymous rules, plus page and font measurement, hidden-text detection and near-duplicate
detection. Point it at a zip of submissions and read the report.

If that is all you want, you need [section 1](#1-install-the-prerequisites) and
[section 2](#2-try-it-now-on-generated-test-pdfs) — nothing else in this README.

**2. An optional [Agent Skill](https://agentskills.io) wrapped around it**, for
when you *do* want an AI coding agent helping you review. `SKILL.md` tells the
agent how to run the checks, what the CFP requires, what it may and may not do
with a confidential submission, and when to stop and ask you.

The `check` subcommand is the no-AI part. The other two, `probe` and `zdr`, exist
only to answer "may I legally show these papers to a model at all" — ignore them
if you never will.

**Want to see it work before reading any of this?** Jump to
[section 2](#2-try-it-now-on-generated-test-pdfs) — it generates its own test
papers, so you need nothing but `uv`.

- **CFP:** <https://www.serverlesscomputing.org/woais2/cfp/index.html>
- **Submission system:** HotCRP, <https://woais26.hotcrp.com/>
- **Governing policy:** [ACM Peer Review Policy](https://www.acm.org/publications/policies/peer-review) (the LLM/confidentiality clause) and [ACM Policy on Authorship, Peer Review, Readership, and Conference Publication](https://www.acm.org/publications/policies/roles-and-responsibilities) (reviewer duties, bulk-downloads)

## 1. Install the prerequisites

Two things, and only one of them is a real install:

```bash
# uv - runs the scripts and installs their dependencies for you
curl -LsSf https://astral.sh/uv/install.sh | sh

# check
uv --version          # any recent version
python3 --version     # 3.10 or newer
```

Nothing else — **no AI, no API key, no account, no network.**
`scripts/paper_checks.py check` is ordinary Python: it reads the PDFs and applies
the workshop requirements as rules. It runs on a laptop with the network off.

Both scripts carry a [PEP 723](https://peps.python.org/pep-0723/)
inline dependency block, so `uv run` fetches PyMuPDF into a throwaway
environment on first use. There is no `pip install`, no `requirements.txt` and no
virtualenv to activate or clean up.

**How to run anything in this repo:** `uv run <script> [args]`, from the repo
root — or just execute it, since the scripts carry a
`#!/usr/bin/env -S uv run --script` shebang:

```bash
./scripts/paper_checks.py ./papers        # equivalent to `uv run scripts/...`
```

Under a bare `python3` with nothing installed, `--version`, `--help`, `probe` and
`zdr` still work; only reading PDFs needs PyMuPDF, and that path says so plainly
instead of failing at import.

```bash
uv run scripts/paper_checks.py --version           # version, commit, tag, sha256
uv run scripts/paper_checks.py --help              # the three subcommands
uv run scripts/paper_checks.py probe --help
uv run scripts/paper_checks.py check --help
uv run scripts/paper_checks.py zdr --help
```

Network access is optional. The only commands that reach the network are
`check --hotcrp` (fetches submissions), `zdr --live` (sends a two-token probe,
never paper text), and `--version` when this copy is not a git checkout — it
then asks the GitHub API which commit `main` is at.

### Which version is this?

```bash
uv run scripts/paper_checks.py --version      # or -v
```

```
paper_checks.py 1.0.0   venue: woais26
  file       : .../woais26-ai-reviews-checks/scripts/paper_checks.py
  sha256     : 566d5c87d2b4...
  git        : commit 95e039ea0e384f8fc0b413be38608237c6915dd8
  tag        : v1.0.0
  describe   : v1.0.0
  committed  : 2026-10-06
  repo       : https://github.com/serverlesscomputing/woais26-ai-reviews-checks
```

From a git checkout it reports the commit, the tag on that commit, and
`describe` (suffixed `-dirty` if you have uncommitted edits). The commit is
only reported when the surrounding repository actually tracks this file in
`HEAD`, so a copy dropped into an unrelated repo cannot claim that repo's
commit as its own. When there is no checkout — a bare copy, or `uv run` from a
raw URL — it reports the published `main` commit and tag from the GitHub API
instead, clearly labelled as `published`.

The `sha256` is the honest identifier in every mode: it pins the exact bytes
that ran, which matters most when the script was fetched from a URL rather
than checked out.

## 2. Try it now, on generated test PDFs

No real submissions needed. `make_test_pdfs.py` builds synthetic papers that
deliberately trip every check — including one carrying a hidden white-text prompt
injection — so you can see the output and judge the heuristics before trusting
them on anything that matters.

```bash
# HTTPS, no login required while the repo is public
git clone https://github.com/serverlesscomputing/woais26-ai-reviews-checks.git
cd woais26-ai-reviews-checks

uv run scripts/make_test_pdfs.py                      # -> .tmp/testpdfs/
uv run scripts/paper_checks.py .tmp/testpdfs --out .tmp/out
```

Validating PDFs is the default action, so there is no subcommand and no venue
flag: this copy only ever checks WoAIS2 submissions. Targets can be PDFs, a
papers zip, directories, or any mix:

```bash
uv run scripts/paper_checks.py woais26-paper7.pdf
uv run scripts/paper_checks.py *.pdf
uv run scripts/paper_checks.py woais26-papers.zip --out out --redact
uv run scripts/paper_checks.py a.pdf b.zip ./more
```

Expected output from the fixtures:

```
venue   : WoAIS2 2026 (Second Intl. Workshop on AI and Serverless)
source  : dir .tmp/testpdfs (3 pdf)
papers  : 3

  + paper      1  OK     7pp  woais26-paper1.pdf
 !! paper      2  BLOCK  9pp  woais26-paper2-jane-real.pdf
  . paper      3  NOTE   4pp  woais26-paper3.pdf

wrote <repo>/.tmp/out/summary.md, summary.csv, gate.json

!! BLOCKED (hidden LLM instructions): 2
   Do not put these PDFs in front of a model. Tell the chairs.
```

Exit code `2` — the gate fired. The three fixtures are built to differ:

| Fixture | Built to be | Verdict |
|---|---|---|
| `woais26-paper1.pdf` | conforming — anonymous, 10pt, 6 pages of technical content with references on page 7 | `OK` |
| `woais26-paper2-jane-real.pdf` | hostile and non-conforming — hidden white-text injection plus 2pt text, PDF metadata naming the author, acknowledgments, funding text, an identifying file name, a de-anonymizing link, and 8 technical pages | `BLOCK` |
| `woais26-paper3.pdf` | a 4-page position paper | `NOTE` — short formats are legitimate here |

Output is detailed by default: each paper is followed by every finding behind
its verdict, then a `CHECK SUMMARY` table rolling the whole batch up per check —
worst severity, how many papers hit it, the full severity breakdown, and which
papers. Two switches:

```bash
uv run scripts/paper_checks.py ./papers -q          # one line per paper, no detail
uv run scripts/paper_checks.py ./papers --show-ok   # include the checks that passed
```

Safety findings always print their evidence, whatever the severity: `safety.hidden_text`
shows the concealed text **verbatim**, pipe-separated span by span, so you can see for
yourself whether it is a concealed message or just chart tick labels. The per-paper
files under `--out` carry the same information in full, and `papers/<id>/hidden_text.txt`
holds the untruncated extraction.

Then read what it produced:

```bash
cat .tmp/out/summary.md                 # worst-first triage, STOP list, duplicates
cat .tmp/out/papers/2/report.md         # every finding against the hostile paper
cat .tmp/out/papers/2/hidden_text.txt   # the injection itself, as evidence
column -s, -t .tmp/out/summary.csv      # paper x check matrix
```

Paper 2 is the one worth studying. It comes back with nine findings, and the
ordering is the point: `safety.prompt_injection` is a `BLOCK` because the
instructions are in **hidden** text, while `safety.injection_like_visible_text` on
the same paper is only a `NOTE` — at a workshop on agent harnesses, injection
strings in visible body text are usually the paper's own subject matter.

Everything lands in `.tmp/`, which is gitignored. `rm -rf .tmp` when done. These
are fixtures — never put real submissions in that directory.

## 3. What this skill is

Everything above needed no AI. This section is about the optional second
layer: a step-by-step guide an agent follows, wrapped around the same
`scripts/paper_checks.py`:

1. **Step 0 — data-retention and model posture.** Before any paper is shown to a
   model: which plan you are on, what its retention actually is, how to verify it,
   and which API surfaces to avoid. `probe` reports the credential Claude Code
   will actually use and the hygiene items still outstanding; `zdr` answers
   whether zero data retention is in effect and how strong the evidence is.
   Full detail, including the Pro/Max checklist and a local-model (Ollama) setup,
   is in [`references/RETENTION.md`](references/RETENTION.md).
2. **Steps 1–2 — ingest and triage.** PDF text extraction from a HotCRP zip
   (preferred, fully offline) or the HotCRP REST API, then a worst-first triage
   table across the whole batch.
3. **Step 3 — hidden-prompt gate.** Blocks papers carrying LLM-directed
   instructions in invisible text before they reach a model.
4. **Steps 4–8 — per-paper CFP checklist, the allowed/forbidden line for model
   use, submitting in HotCRP, batch-consistency checks, and cleanup.**

### What it does not do

It does **not** score papers, decide accept/reject, judge novelty or significance,
or write your review. That split is deliberate — see Step 5 of [`SKILL.md`](SKILL.md)
for the evidence behind it. Every check is a mechanical heuristic on PDF internals
and is reported as a pointer for a human, never a verdict.

## 4. Install the skill into your agent

The repository root **is** the skill folder, so installing is "put this directory
where your agent looks for skills, under its own name". The directory must stay
named `woais26-ai-reviews-checks` — the Agent Skills spec requires the `name` in
`SKILL.md` to match the parent directory.

> **No login needed, as long as the repo is public.** Every URL below is HTTPS,
> so `git clone`, `curl` and the tarball/zip links all work anonymously — no SSH
> key, no token, no `gh auth login`. If the repo is **private**, all of them fail
> instead: HTTPS clone prompts for a username and the `archive/…` download links
> return 404. In that case either make it public, or authenticate once with
> `gh auth login` (which configures a git credential helper) or a
> [personal access token](https://github.com/settings/tokens) as the password.

### Fastest: clone straight into the skills directory

```bash
# Claude Code (personal, available in every project)
git clone https://github.com/serverlesscomputing/woais26-ai-reviews-checks.git \
  ~/.claude/skills/woais26-ai-reviews-checks

# Cursor / Codex / Gemini CLI / Copilot (personal)
git clone https://github.com/serverlesscomputing/woais26-ai-reviews-checks.git \
  ~/.agents/skills/woais26-ai-reviews-checks
```

### Install once, cover every agent

There is **no single directory that every agent reads** — Claude Code does not read
`.agents/skills/`, and Cursor, Codex and Gemini CLI do not read `.claude/skills/`
as a primary location. One copy plus a symlink covers both families:

```bash
mkdir -p ~/.agents/skills ~/.claude/skills
git clone https://github.com/serverlesscomputing/woais26-ai-reviews-checks.git \
  ~/.agents/skills/woais26-ai-reviews-checks
ln -s ~/.agents/skills/woais26-ai-reviews-checks \
      ~/.claude/skills/woais26-ai-reviews-checks
```

Or let the bundled installer do it:

```bash
git clone https://github.com/serverlesscomputing/woais26-ai-reviews-checks.git
cd woais26-ai-reviews-checks
./install.sh            # ~/.agents/skills + symlink into ~/.claude/skills
./install.sh --list     # show what it would do and which targets exist
./install.sh --project  # install into ./.claude/skills and ./.agents/skills instead
./install.sh --copy     # real copies everywhere instead of symlinks
```

### Download and copy, without git

```bash
# 1. tarball, extracted straight into place (no git, no clone dir)
mkdir -p ~/.claude/skills/woais26-ai-reviews-checks
curl -L https://github.com/serverlesscomputing/woais26-ai-reviews-checks/archive/refs/heads/main.tar.gz \
  | tar -xz --strip-components=1 -C ~/.claude/skills/woais26-ai-reviews-checks

# 2. zip
curl -LO https://github.com/serverlesscomputing/woais26-ai-reviews-checks/archive/refs/heads/main.zip
unzip -q main.zip && mv woais26-ai-reviews-checks-main ~/.claude/skills/woais26-ai-reviews-checks

# 3. GitHub CLI
gh repo clone serverlesscomputing/woais26-ai-reviews-checks ~/.claude/skills/woais26-ai-reviews-checks

# 4. a tagged release (reproducible — pin a version)
curl -L https://github.com/serverlesscomputing/woais26-ai-reviews-checks/archive/refs/tags/v1.0.0.tar.gz \
  | tar -xz --strip-components=1 -C ~/.claude/skills/woais26-ai-reviews-checks
```

The tarball form (1) is the best general answer: one command, no git required, no
leftover clone directory, and it lands with the correct directory name.

### Where each agent looks

Paths below were checked against each vendor's own documentation.

| Agent | Project scope | Personal scope | Docs |
|---|---|---|---|
| **Claude Code** | `.claude/skills/` (also nested dirs and `--add-dir`) | `~/.claude/skills/` | [docs](https://code.claude.com/docs/en/skills) |
| **Claude apps + API** | — (upload a zip; see below) | Settings → Capabilities → Skills | [docs](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) |
| **GitHub Copilot** (CLI, VS Code, JetBrains, cloud agent, code review) | `.github/skills/`, `.claude/skills/`, `.agents/skills/` | `~/.copilot/skills/`, `~/.agents/skills/` | [docs](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills) |
| **Cursor** | `.agents/skills/`, `.cursor/skills/` (legacy: `.claude/skills/`, `.codex/skills/`) | `~/.agents/skills/`, `~/.cursor/skills/` | [docs](https://cursor.com/docs/context/skills) |
| **Codex / ChatGPT** | `.agents/skills/` (cwd up to repo root) | `~/.agents/skills/`; system `/etc/codex/skills` | [docs](https://learn.chatgpt.com/docs/build-skills) |
| **Gemini CLI** | `.agents/skills/` (preferred), `.gemini/skills/` | `~/.agents/skills/`, `~/.gemini/skills/` | [docs](https://geminicli.com/docs/cli/skills/) |
| **VS Code** | via Copilot, above | via Copilot, above | [docs](https://code.visualstudio.com/docs/copilot/customization/agent-skills) |

Also implements the standard — follow each project's own install docs, then drop
this directory in the location they name:
[OpenCode](https://opencode.ai/docs/skills/) ·
[Goose](https://block.github.io/goose/docs/guides/context-engineering/using-skills/) ·
[Amp](https://ampcode.com/manual#agent-skills) ·
[OpenHands](https://docs.openhands.dev/overview/skills) ·
[Roo Code](https://docs.roocode.com/features/skills) ·
[Kiro](https://kiro.dev/docs/skills/) ·
[Factory](https://docs.factory.ai/cli/configuration/skills) ·
[Junie](https://junie.jetbrains.com/docs/agent-skills.html) ·
[Letta](https://docs.letta.com/letta-code/skills/) ·
[Augment](https://docs.augmentcode.com/cli/skills) ·
[Tabnine](https://docs.tabnine.com/main/getting-started/tabnine-cli/features/agent-skills) ·
[Firebender](https://docs.firebender.com/multi-agent/skills) ·
[Emdash](https://docs.emdash.sh/skills) ·
[Mux](https://mux.coder.com/agent-skills) ·
[OpenClaw](https://docs.openclaw.ai/tools/skills) ·
[full client list](https://agentskills.io/clients)

### Claude apps (web and desktop)

These take an uploaded zip rather than a directory:

```bash
cd .. && zip -r woais26-ai-reviews-checks.zip woais26-ai-reviews-checks \
  -x '.git/*' -x '*/__pycache__/*'
```

Then Settings → Capabilities → Skills → upload. Note that the Claude apps have no
shell, so `scripts/paper_checks.py` will not run there — the checklist and policy
guidance work, the automation does not. For the full workflow use a terminal agent.

### Project scope, to share with co-reviewers or the PC

```bash
mkdir -p .claude/skills .agents/skills
git submodule add https://github.com/serverlesscomputing/woais26-ai-reviews-checks.git \
  .claude/skills/woais26-ai-reviews-checks
```

Commit it, and everyone who clones the review repo gets the same checks. Use
`.agents/skills/` instead (or as well) for Cursor, Codex and Gemini CLI.

## Verify the install

```bash
# does the agent see it?
#   Claude Code:  /skills        Cursor: /skills        Copilot CLI: /skills
# validate against the spec:
uvx --from git+https://github.com/agentskills/agentskills#subdirectory=skills-ref \
    skills-ref validate ~/.claude/skills/woais26-ai-reviews-checks
```

Then just ask your agent in plain language, e.g.
*"run the woais26 review checks on this zip of submissions"* — the skill is
selected from its `description`, so you don't need to name it. In Claude Code you
can also invoke it directly as `/woais26-ai-reviews-checks`.

## What it looks like in use

Once installed, invoke the skill by name and hand it your papers. In Claude Code
that is `/woais26-ai-reviews-checks`; in other agents, just say what you want —
the skill is selected from its `description`.

> **The transcript below is illustrative.** Paper numbers, titles, counts and
> figures are invented for documentation, and no real submission is described.
> It shows the *shape* of a session: the agent runs the script, verifies the
> findings by hand, and stops where the skill tells it to stop.

<details>
<summary><code>/woais26-ai-reviews-checks check the two PDFs in ./papers</code></summary>

```text
> /woais26-ai-reviews-checks check the two PDFs in ./papers

I'll start with Step 0 - the retention/posture gate - before any paper touches
a model.

  uv run scripts/paper_checks.py probe
  uv run scripts/paper_checks.py zdr --training-off
  uv run scripts/paper_checks.py ./papers --out out/woais26 --redact

Done. Mechanical checks complete on both papers - no BLOCKs, no FAILs (exit 0).

Step 0 - posture: settled, Case A

Claude Code on an Anthropic subscription, training toggle confirmed OFF ->
ACM-compliant (no training + 30-day deletion). Not ZDR - ZDR is never available
on Free/Pro/Max, and I am not claiming it. Audit trail for your review notes:

  2026-10-06 - Claude MAX, subscription OAuth, no API key.
  "Help improve our AI models" = OFF, verified via /privacy-settings.
  Retention: 30 days, no training (Anthropic consumer terms).
  ZDR: not available on consumer plans; not claimed.
  Reviewer: <your name>

Step 3 - hidden-prompt gate: both PASS

chars_hidden 0 for both; no tiny (<4pt), near-white or off-page spans; no 3 Tr
invisible render mode; no optional-content layers; injection lists empty in
hidden and visible text. No hidden_text.txt written - there was nothing to
write. Both are safe to put in front of a model.

Step 4 - findings, each confirmed by hand

              paper 07                        paper 23
  title       Scheduling for Bursty FaaS      Cost Models for Edge Inference
  pages       8 (6 technical + refs p7-8)     5 (4 technical + refs p5)
  font        10.0pt  ok                      9.0pt  wrong template
  geometry    US Letter, 2-col  ok            US Letter, 2-col  ok
  anonymity   clean                           clean
  worst       NOTE                            WARN

Anonymity - both clean. Title pages say "Anonymous Author(s)"; PDF metadata
carries no author field (title only, which is benign); filenames neutral; no
ORCIDs, funding or acknowledgments sections; no extended-version
self-reference - the arXiv hits are bibliography entries. The only e-mail is
ACM's own permissions@acm.org boilerplate, and the "University"/"Inc." hits are
citations rather than affiliations.

Two things worth your attention, both verified rather than taken from the
heuristic:

1. Paper 23 is on the wrong ACM template. The script hedged that 9pt could be a
   scaling artifact - it is not. Geometry is exactly 612x792pt unscaled and body
   text is 9pt on every page, which is acmart sigconf, not the CFP-required
   SIGPLAN 10pt. Its ACM Reference Format block also carries main-conference
   boilerplate rather than the workshop's. Reads as a template mix-up, not
   page-stretching: at 5 pages it is well under the limit and gained nothing.
   Fixable at camera-ready.
2. Paper 23's topical fit is worth a scope conversation. Zero occurrences of
   serverless, FaaS, agent, LLM, inference-serving, container or Kubernetes.
   Paper 07 by contrast is squarely on-territory. The CFP is deliberately wide
   and explicitly welcomes "anything else that may be interesting to the
   workshop audience", so whether that breadth covers it is a chair call - I am
   only reporting the keyword facts.

Paper 23 is legitimately a short format (5pp, within the extended-abstract
allowance) - judge it as one, not as a thin full paper. No duplicate pairs.

Where I stop

Per Step 5 I have not assessed quality, novelty, significance or contribution,
have not listed strengths or weaknesses, and have not proposed scores or
questions for the authors - those are yours. What I can do next, all inside the
allowed set:

  - a claim inventory: what is claimed vs. what is actually evidenced
  - a setup table: baselines, workloads, scale, hardware, metrics, repetitions,
    error bars
  - an internal-inconsistency and technical-error pass with page anchors
  - citation verification, flagging anything I cannot resolve rather than
    guessing
  - a plain-language explanation of either method

redacted.txt is written for both if you want a de-identified polish pass on your
own review draft later.
```

</details>

Three things in that transcript are the skill working as intended, not incidental:

- **Step 0 runs first and produces an attestation**, not a vague assurance. It
  states what posture was verified and explicitly declines to call it ZDR.
- **The hidden-prompt gate is reported even when it passes**, with the signal
  counts, so "no injection" is an observation rather than an absence.
- **It stops before judgement.** No score, no strengths/weaknesses, no questions
  for authors — and it says so, then offers only the things Step 5 permits.

The template and scope observations are the useful pattern: the script *hedges*
("9pt could be a scaling artifact"), and the agent's job is to settle the hedge
by looking, then report what it found rather than passing the hedge along.


### Going deeper: claim inventory and related-work positioning

Both of these are explicitly on the allowed list in Step 5 — they map claims to
evidence and verify facts, without judging quality, novelty or significance.

> **Invented again.** Paper 62 and every number, figure and section reference
> attributed to it are fabricated for documentation. The *external* systems and
> papers named as uncited alternatives are real public work, because that is what
> makes the example usable — the gap the agent is looking for only means
> something if the alternatives are ones you could actually go and read.

<details>
<summary><code>build a claim inventory: what is claimed vs. what is actually evidenced. Are the claims supported by the data shown? Watch for cherry-picked periods or unexplained baselines</code></summary>

```text
Let me verify the figure data directly rather than eyeballing it - several
claims are endpoint readings off noisy series.

I extracted the vector data from Figures 1b and 1d and re-ran the regressions.
The extraction validates itself: it reproduces the paper's own quoted values
exactly (1.21 ms @ 1,340 B; 0.61 ms @ iter 2; 0.644 ms @ iter 36), so what
follows is the plotted data, not my reading of a picture.

Claim inventory - claimed vs. evidenced

  #   claim (anchor)                        what backs it            verdict
  --  ------------------------------------  -----------------------  ----------------------------
  1   Turn = 12.4 s wall / 3.1 ms CPU       two disjoint             composite, never observed.
      (abstract, S4.1)                      experiments: n=50 live   Disclosed in the Fig 1a
                                            calls for wall, 3,600    caption, asserted as fact
                                            mocked iterations for    in the abstract
                                            CPU
  2   CPU = 0.51 + 0.072*KB, R2=0.94        OLS on 36 binned         reproduced exactly from the
      (S3.3)                                medians                  figure data: 0.508 + 0.0719,
                                                                     R2=0.941  OK
  3   dropping the iter-1 outlier "does     assertion                verified: 0.0719 -> 0.0761
      not move the slope materially"                                 (+5.8%), inside their own CI
      (S3.3)                                                         OK
  4   in-memory store rises 7.5x (S3.3)     endpoints 0.086 ->       endpoints accurate but
                                            0.644                    endpoint-sensitive - below
  5   on-disk store shows "periodic         Fig 1d                   not supported by the plotted
      spikes ... batched flushes" (S3.3)                             data - below
  6   mismatch is worst for short turns     linear CPU + latency     derived. Rests on an
      (S4.3)                                assumed output-bound     assumption never tested here
                                            [11, 19]                 - below
  7   44:1 at 180k tokens (S4.2, Fig 1c)    fit extrapolated ~2      extrapolated, and the
                                            orders past the data,    fixed-latency premise
                                            latency held at 3.90 s   conflicts with the paper's
                                                                     own mechanism
  8   memory, not CPU, is what binds        -                        never measured - below
      (S4.1, S5.1, S6)

Where claims outrun the data shown

#4 - the 7.5x is a max-over-near-min reading. Iteration 36 (0.644 ms) is the
series maximum; its neighbours are 0.471 and 0.478, and iteration 33 is a second
spike at 0.639. Iteration 2 (0.086) is the second-lowest point. Comparing local
means instead - iters 32-35 mean 0.498 vs iters 2-5 mean 0.098 - gives about 5x,
not 7.5x. The endpoints are reported honestly and the qualitative claim holds;
the specific multiplier is the most favourable pair of points on a visibly noisy
series.

#5 - "periodic" is contradicted by the figure. Extracted spikes sit at
iterations 1, 2, 3, 4, then 21, 27, 33, 36; everything else is ~0. The four
largest are contiguous at the start, which reads as initialisation rather than
batching, and the rest are irregular (gaps of 6, 6, 3). Evenly spaced spikes
would support a batched-flush mechanism; these do not. The stated range also
fails to bracket the data at both ends.

#6/#7 - the load-bearing assumption is never tested, and conflicts with its own
citation. The whole short-turns-worst result depends on model latency not growing
with input length. S3.1 asserts this from [11, 19] and then sweeps only the
output cap, log-uniformly - input context length is never varied, though the same
harness could vary it directly. Figure 1c then holds latency fixed out to 180k
tokens. Prefill parallelises but is not free; it is linear in input tokens and at
that scale is a well-documented multi-second cost. Citation [19] exists precisely
because prefill is large enough to warrant separating from decode, so it is cited
for a stronger claim than it makes. At a realistic 14 s rather than 3.90 s the
ratio would be nearer 160:1 than 44:1. S4.3 names this exact falsifier without
testing it.

#1 - the headline is assembled, and from an experimenter-chosen distribution.
The 3.90 s median is the median over a synthetic log-uniform output-cap sweep,
not the output-length distribution of the agent they actually built. Tool-calling
steps typically emit short outputs, so the real per-step latency is probably well
below it. The number driving the headline is a design choice, not a measurement
of the system described.

#8 - the resource claimed to bind is the one never measured. Three sections turn
on memory being the binding constraint, and the paper's agenda item is "size
concurrency against state, not CPU". Measured conversation state at the last step
is 22 KB; the cost model assumes a 1 GB invocation. The gap between them is
runtime and framework footprint - never measured anywhere. CPU is characterised
exhaustively; the resource the argument rests on is not.

Is the setup described well enough to judge?

Specified, and unusually completely for 6 pages: framework and checkpoint
library versions, model id, region, API mode, date, host hardware and OS, Python
version, the 36 x 100 x 3 design, payload growth, the transport-mocking mechanism
and its guard assertion, outlier handling, and both binned and unbinned
regression statistics.

Not specified, roughly in order of how much it matters:
  - agent memory footprint - central to the argument, above
  - input-length sensitivity of model latency - the untested assumption
  - which CPU-time API was used; blocks exact reproduction of sub-ms figures
  - machine state: idle? on mains? thermal behaviour? core pinning? At 0.5-2 ms
    per measurement on a laptop this is material
  - the real agent's output-length distribution, as against the synthetic sweep
  - justification for the assumed memory size and the token-spend range
  - artifact: "written to be released", but no anonymised link (the CFP suggests
    anonymous.4open.science)

Cherry-picked periods and unexplained baselines

Period. All latency comes from a single date, single region, n=50, with no
time-of-day recorded and no repetition across days or hours. On-demand latency is
load-dependent. Nothing suggests selection among periods - there appears to be
only one - but there is no evidence of stability across periods either, and the
headline rests on it.

Baselines. This is a characterisation paper and the comparisons it reasons about
are not measured: no function platform (stated plainly three times, so all
platform conclusions are projections), no container, no VM, no long-lived
process - yet S4.1 still calls the long-lived-process comparison "stark". The one
controlled comparison that exists is a three-configuration store ladder which the
paper itself says a stateless substrate could not use, with the remote round-trip
a real deployment would pay omitted entirely.

On the whole the paper is markedly transparent about its limits: S6 lists five
biases in both directions, the Fig 1a caption admits the turn is assembled, and
the unflattering unbinned R2 is published alongside the flattering binned one.
The gaps above are mostly between what the prose asserts and what the figures
establish, not between the paper and its own caveats.

Two claims I would treat as unsupported by the material shown: the batched-flush
mechanism (#5) and fixed latency out to 180k tokens (#7). One number reproduces
to three decimals (#2). One recommendation rests on an unmeasured quantity (#8).

Whether any of that is decisive - novelty, significance, contribution, score - is
yours, not mine.
```

</details>

<details>
<summary><code>review related work. Does it position against the obvious academic and open-source alternatives, scaled to a 6-page paper? Web-search and compare against the paper's related-work section</code></summary>

```text
Related work is S5, about one column, 22 refs, organised as "what transfers"
against Table 1's eight rows. For 6 pages that structure is economical, and the
academic serverless lineage is well covered - I verified each cited system exists
and is described accurately.

What is absent: the entire production and open-source durable-execution
ecosystem. Greps return zero for Temporal, Restate, DBOS, Inngest, Cloudflare
Workers, Durable Objects, Step Functions, Knative and response streaming. Among
agent frameworks only the one they used appears; no Ray, no Dapr.

Two of those are direct counterexamples to claims the paper makes:

  paper 62 claims                        shipping system
  -------------------------------------  -----------------------------------------
  Table 1 row 4: no inbound channel      Cloudflare Durable Objects WebSocket
  mid-invocation - "None known"          Hibernation keeps the connection open
                                         while the object hibernates; Restate
                                         handlers suspend on an external promise
                                         or human approval and are reinvoked when
                                         it arrives
  S4.1: "under per-millisecond charging  Cloudflare Workers bills CPU time, not
  the platform meters the 12.4 s and     wall-clock - time spent awaiting an
  delivers the 3.1 ms"                   upstream API is explicitly not billed. It
                                         would meter the 3.1 ms

That second row matters because it is the title claim. "The meter runs while you
block" is true of the platform they model and false of at least one shipping
competitor. The paper does make this move once, for concurrency - citing a
managed platform's higher per-instance limit as "an existence proof that the
constraint is a design choice" - but applies no equivalent caveat to billing.

For a 6-page paper I would not expect a survey. I would expect one sentence
scoping the billing claim to the platforms where it holds. That is a positioning
gap, not a length problem.

Where I stop

Everything above is claim-to-evidence mapping, arithmetic verification, setup
tabulation and factual citation checking. I have not judged novelty,
significance, quality or contribution, listed strengths or weaknesses, proposed a
score, or drafted author-facing text - including on the related-work gaps.
Whether an uncited alternative is a material omission or a reasonable 6-page
elision is your call.

Two things to verify yourself before any of this reaches author-visible text: the
billing behaviour I cite (I read vendor docs, not a contract), and whether the
figure extraction matches what you see when you open the PDF.
```

</details>

What makes those two useful rather than decorative:

- **The extraction validates itself.** Before trusting re-derived numbers, the
  agent checks that its extraction reproduces values the paper itself quotes. If
  it cannot, the extraction is wrong and nothing built on it counts.
- **It separates "wrong" from "fragile".** `#2` reproduces to three decimals;
  `#4` is arithmetically correct but picked from the most favourable pair of
  points on a noisy series. Those need different sentences in a review, and
  conflating them is unfair to authors.
- **A missing citation is reported as a positioning gap, with the counterexample
  named** — not as a verdict on novelty. The useful output is "this claim needs
  one scoping sentence", which an author can act on.
- **It still refuses the judgement call.** No score, no strengths/weaknesses, and
  it says which of its own findings you should re-verify before any of it reaches
  the authors.


## Real use: your own submissions

```bash
export PC="$HOME/.claude/skills/woais26-ai-reviews-checks/scripts/paper_checks.py"

# Step 0 - always first
uv run "$PC" probe
uv run "$PC" zdr          # is zero data retention in effect? (Pro/Max: never)

# Steps 1-2 - from a HotCRP zip (preferred: offline, no API token)
uv run "$PC" \
    ~/Downloads/woais26-papers.zip --out out/woais26 --redact

# or straight from HotCRP
export HOTCRP_TOKEN=hct_...     # Account -> Developer, scopes submeta:read document:read
uv run "$PC" check \
    --hotcrp https://woais26.hotcrp.com --query "re:me" --out out/woais26
```

Outputs:

```
out/woais26/summary.md    worst-first triage, STOP list, duplicate pairs, batch patterns
out/woais26/summary.csv   one row per paper, one column per check
out/woais26/gate.json     blocked / failed ids, duplicate pairs
out/woais26/papers/<id>/  report.md  checks.json  text.txt  hidden_text.txt  redacted.txt
```

Exit codes: `0` clean · `1` format/compliance failures · `2` at least one BLOCK.

## What the script checks

| Group | Checks |
|---|---|
| Safety | Hidden text (near-white, <4.5pt, off-page, `3 Tr` invisible render mode, optional-content layers) and 21 LLM-directed instruction patterns. Hits in **hidden** text are a `BLOCK`; the same strings in **visible** text are only a `NOTE`, because this venue publishes papers *about* prompt injection and agent guardrails |
| Anonymity (double-blind) | PDF metadata leaks, identifying file name, e-mails, ORCIDs, acknowledgments, funding text, de-anonymizing links (vs. `anonymous.4open.science`), first-person self-reference, references to an extended version |
| Format | ≤6 pages of technical content (figures **and appendices** count, bibliography does not), SIGPLAN 10pt, US Letter, column count, short-format detection (≤5pp abstracts, posters, position papers) || Batch | Cross-paper shingle-Jaccard overlap for possible duplicate or simultaneous submissions; reference-count outliers; a batch-wide table of the most common issues |
| Hygiene | `--redact` writes a de-identified `redacted.txt`, which is what makes a hosted-model polish pass fit ACM's carve-out for third-party tools |

## Privacy and data retention

Step 0 of [`SKILL.md`](SKILL.md) is the reason this skill exists. ACM's peer-review
policy does not ban AI; it bans uploading submissions to a third-party system "that
does not promise to maintain the confidentiality of that information". So the skill
documents, per plan, what retention actually is, how to verify it with a single
command, which API surfaces silently void a zero-retention agreement, and how to
run the whole thing against a local model instead.

`paper_checks.py` makes **no network calls at all** unless you pass `--hotcrp`
(fetches submissions) or `probe --live` (sends the two-token word "hi", never paper
text). Extracted text stays in your `--out` directory. Nothing is uploaded anywhere.

## Updating

How you update depends on how you installed. In every case the skill is just a
directory, so updating means replacing its contents.

| How you installed | How to update |
|---|---|
| `git clone` into a skills dir | `git -C ~/.claude/skills/woais26-ai-reviews-checks pull --ff-only` |
| one clone + symlinks (the "install once" recipe) | pull in the clone; every agent pointing at it follows |
| `install.sh` with `--copy` | pull in the source clone, then re-run `./install.sh --copy` |
| tarball or zip | re-extract over the same directory (see below) |
| `git submodule` in a project | `git submodule update --remote ~/.claude/skills/woais26-ai-reviews-checks` then commit the bump |
| `uv run <raw URL>` | nothing to do — the script is fetched fresh on every run |

### git clone

```bash
git -C ~/.claude/skills/woais26-ai-reviews-checks pull --ff-only
```

`--ff-only` matters. This repo is published as a **single squashed commit that
gets amended and force-pushed**, so its history is rewritten rather than appended
to. A plain `git pull` will then either refuse or try to merge two unrelated
histories. When `--ff-only` fails, take the published version wholesale:

```bash
git -C ~/.claude/skills/woais26-ai-reviews-checks fetch origin
git -C ~/.claude/skills/woais26-ai-reviews-checks reset --hard origin/main
```

That discards local edits. If you have your own changes, commit them on a branch
first, or keep your copy and merge by hand.

### tarball or zip

Re-extracting is the whole update. Delete first so removed files don't linger:

```bash
D=~/.claude/skills/woais26-ai-reviews-checks
rm -rf "$D" && mkdir -p "$D"
curl -L https://github.com/serverlesscomputing/woais26-ai-reviews-checks/archive/refs/heads/main.tar.gz \
  | tar -xz --strip-components=1 -C "$D"
```

### Which version am I running?

```bash
git -C ~/.claude/skills/woais26-ai-reviews-checks log -1 --format='%h %ad %s' --date=short
git -C ~/.claude/skills/woais26-ai-reviews-checks describe --tags            # e.g. v1.0.0
```

Compare against the published tip without fetching anything into your clone:

```bash
git ls-remote https://github.com/serverlesscomputing/woais26-ai-reviews-checks.git main
```

Different hashes mean an update is available. If you pinned a tag when
installing, you are deliberately frozen there and `pull` will not move you —
re-extract from the newer tag, or switch to `main`.

### After updating

Start a new agent session so the skill is re-read; a running session keeps the
copy it loaded. Then re-run the self-check from
[section 2](#2-try-it-now-on-generated-test-pdfs) — the fixtures are regenerated
by the same commit, so if they still land on their expected verdicts the update
is sound.

```bash
uv run scripts/make_test_pdfs.py
uv run scripts/paper_checks.py .tmp/testpdfs
```

## Uninstalling

```bash
rm -rf ~/.claude/skills/woais26-ai-reviews-checks
rm -rf ~/.agents/skills/woais26-ai-reviews-checks      # and any other location you installed into
```

Or, from a clone of this repo, `./install.sh --uninstall` removes the copies and
symlinks it created. Nothing is stored outside those directories, so that is the
whole removal.

## Provenance

Every page limit, font size, anonymity rule, deadline and evaluation criterion in
`SKILL.md` is transcribed from the CFP linked above, read on 2026-10-04. CFPs get
amended — if a date or limit here disagrees with the CFP, the CFP wins; please open
an issue.

## Related

- [`middleware26industrial-reviews-checks`](https://github.com/aslom/middleware26industrial-reviews-checks) — the same workflow for the Middleware 2026 Industrial Track, which differs on anonymity, template, page accounting and track requirements
- [Agent Skills specification](https://agentskills.io/specification) · [agentskills/agentskills](https://github.com/agentskills/agentskills)
- [HotCRP REST API](https://hotcrp.com/devel/api/)
- [PhantomLint](https://github.com/tobycmurray/phantom-lint) — OCR-vs-extracted-text diff for hidden prompts, a good second opinion on any `BLOCK`

## License

MIT. The skill text quotes short passages from the CFP and
from ACM policy for identification and compliance purposes; those remain the
property of their publishers.
