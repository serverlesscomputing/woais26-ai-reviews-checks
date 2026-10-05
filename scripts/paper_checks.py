#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pymupdf>=1.24"]
# ///
"""
paper_checks.py - mechanical pre-review checks for WoAIS2 2026 and Middleware 2026
Industrial Track submissions.

Two subcommands:

  probe    Report the data-retention / model posture of this machine BEFORE any
           submission is shown to a model. Run this first, every session.

  check    Extract text from submission PDFs and run format, anonymity,
           hidden-prompt and batch-duplicate checks. No network needed in the
           preferred (--zip) mode.

Design rules:
  * Nothing here judges a paper. Every check is mechanical and every result is
    a pointer for a human, not a verdict. Page/font/affiliation checks are
    heuristics on PDF internals and MUST be eyeballed before you act on them.
  * The hidden-text gate runs before anything else, because WoAIS2 and the
    Middleware Industrial Track both solicit papers about LLM agents and
    guardrails - so injection strings in *visible* body text are usually the
    paper's legitimate subject matter, while the same string in *hidden* text
    is an attack. The two are reported separately.
  * Zip mode is preferred: no API token, no network, nothing leaves the host.

Usage:
  uv run paper_checks.py paper15.pdf
  uv run paper_checks.py woais26-papers.zip --out out --redact
  uv run paper_checks.py ./downloads
  uv run paper_checks.py check --hotcrp https://woais26.hotcrp.com --query "re:me"
  uv run paper_checks.py probe
  uv run paper_checks.py zdr

Exit codes: 0 clean | 1 warnings only | 2 at least one BLOCK | 3 tool error
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# PyMuPDF is only needed to read PDFs. Import it softly so --version, --help,
# probe and zdr still work under a bare `python3` with nothing installed.
try:
    import pymupdf  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover
    try:
        import fitz as pymupdf  # older name
    except ImportError:
        pymupdf = None


def require_pymupdf() -> None:
    if pymupdf is None:
        sys.exit("error: reading PDFs needs PyMuPDF, which is not installed.\n"
                 "       Run this script with uv so its inline dependency block "
                 "installs it:\n"
                 "         uv run paper_checks.py <targets>\n"
                 "       or, since the shebang does that for you:\n"
                 "         ./paper_checks.py <targets>")

# --------------------------------------------------------------------------
# venue profiles - transcribed from the two calls for papers
# --------------------------------------------------------------------------

# Venue-specific by design: this copy only checks woais26 submissions.
VENUE_KEY = 'woais26'
VENUE = {
    "label": "WoAIS2 2026 (Second Intl. Workshop on AI and Serverless)",
    "cfp": "https://www.serverlesscomputing.org/woais2/cfp/index.html",
    "hotcrp": "https://woais26.hotcrp.com/",
    "template": "ACM SIGPLAN (acmart sigplan)",
    "body_pt": 10.0,
    "tech_page_limit": 6,          # "at most six (6) pages of technical content"
    "refs_excluded": True,          # "excluding any number of additional pages for references"
    "appendix_counts": True,        # limit includes "text, figures, and appendices"
    "short_formats_ok": True,       # extended abstracts <=5pp, posters, position papers
    "blind": "double",              # doubly-anonymous
    "reviewers_min": 3,
    "title_suffix": None,
    "industry_author_required": False,
}

ACM_POLICY_URLS = {
    # The LLM/confidentiality clause lives in the Peer Review Policy, not in
    # Roles and Responsibilities - verified against a PDF printout of both.
    "peer_review": "https://www.acm.org/publications/policies/peer-review",
    "roles": "https://www.acm.org/publications/policies/roles-and-responsibilities",
    "all_policies": "https://www.acm.org/publications/policies",
    "plagiarism": "https://www.acm.org/publications/policies/plagiarism-overview",
    "authorship": "https://www.acm.org/publications/policies/new-acm-policy-on-authorship",
}

# --------------------------------------------------------------------------
# pattern tables
# --------------------------------------------------------------------------

# Imperative strings aimed at an LLM reviewer. Hits in hidden text => BLOCK.
# Hits in visible text => NOTE only (both these venues publish papers *about*
# prompt injection and agent guardrails).
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(the\s+)?(previous|prior|above|preceding)\s+instructions?",
    r"disregard\s+(all\s+)?(the\s+)?(previous|prior|above)\s+(instructions?|prompts?)",
    r"forget\s+(all\s+)?(previous|prior)\s+instructions?",
    r"give\s+a\s+positive\s+review",
    r"positive\s+review\s+only",
    r"do\s+not\s+highlight\s+any\s+negativ",
    r"(recommend|give)\s+(strong\s+)?accept",
    r"accept\s+this\s+(paper|submission)",
    r"(highest|maximum|top|perfect)\s+(possible\s+)?(score|rating|grade)",
    r"score\s+of\s+(10|9|5)\b",
    r"as\s+an\s+ai\s+(language\s+)?model",
    r"you\s+are\s+a[n]?\s+(helpful\s+)?(ai\s+|language\s+)?(model|assistant|reviewer)",
    r"<\s*system\s*>|\[\s*INST\s*\]|###\s*instruction|<\|im_start\|>",
    r"new\s+instructions?\s*:",
    r"system\s+prompt\s*:",
    r"(include|insert|use)\s+the\s+(exact\s+)?(phrase|word|term|sentence)",
    r"do\s+not\s+mention\s+(this|these|the\s+following)",
    r"when\s+(reviewing|asked)\s+this\s+paper",
    r"(reviewer|llm|language\s+model)s?\s*,?\s*please\b",
    r"only\s+(list|mention)\s+(the\s+)?strengths",
]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Addresses the ACM template itself prints - "Request permissions from
# permissions@acm.org" sits on page 1 of every acmart paper, so it is not an
# anonymity leak.
BOILERPLATE_EMAIL_RE = re.compile(r"@acm\.org$|^permissions@|@ifip\.org$", re.I)
ORCID_RE = re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b")
URL_RE = re.compile(r"(?:https?://|www\.)[^\s)>\]},;\"']+", re.I)

# URLs that keep anonymity (do not flag these)
ANON_HOST_RE = re.compile(
    r"anonymous\.4open\.science|anonymous\.github|osf\.io/\?view_only|"
    r"figshare\.com/s/|zenodo\.org/record/\d+\?token=", re.I)

# URLs that typically carry an identity
DEANON_HOST_RE = re.compile(
    r"github\.com/|gitlab\.com/|bitbucket\.org/|huggingface\.co/|"
    r"linkedin\.com|twitter\.com|x\.com/|researchgate\.net|scholar\.google|"
    r"orcid\.org|dropbox\.com|drive\.google\.com|sites\.google\.com|"
    r"\.github\.io|people\.|~[a-z]{2,}/|homepage|personal", re.I)

ACK_HEAD_RE = re.compile(
    r"^\s*(\d+\.?\s*)?(acknowledg(e)?ments?|acknowledgement|thanks|funding"
    r"|financial\s+support)\b", re.I | re.M)

FUNDING_RE = re.compile(
    r"\b(grant\s*(no\.?|number|#)|award\s*(no\.?|number)|NSF\b|DFG\b|ERC\b"
    r"|EPSRC\b|Horizon\s*20\d\d|Horizon\s*Europe|MICINN\b|AEI/|FEDER\b"
    r"|funded\s+by|supported\s+(in\s+part\s+)?by\s+(the\s+)?(grant|NSF|DFG|EU|European))",
    re.I)

SELF_REF_RE = re.compile(
    r"\b(our\s+(previous|prior|earlier|own)\s+(work|paper|system|study|approach)"
    r"|in\s+our\s+(previous|prior|earlier)\s+(work|paper)"
    r"|we\s+(previously|earlier)\s+(showed|presented|published|proposed|reported)"
    r"|as\s+we\s+(showed|reported)\s+in"
    r"|extends?\s+our\s+)", re.I)

EXT_VERSION_RE = re.compile(
    r"\b((?:an?|the)\s+(?:extended|full|longer)\s+version\s+of\s+(?:this|our|the\s+present)"
    r"|(?:extended|full)\s+version\s+(?:is\s+)?available"
    r"|our\s+(?:technical\s+report|extended\s+version|preprint)"
    r"|(?:see|cf\.)\s+our\s+(?:technical\s+report|preprint|arxiv))", re.I)

REFS_HEAD_RE = re.compile(
    r"^\s*(\d+\.?\s*)?(references|bibliography|references\s+and\s+notes)\s*$", re.I)
APPENDIX_HEAD_RE = re.compile(
    r"^\s*(\d+\.?\s*)?(appendix|appendices|supplementary\s+material)\b", re.I)

ACADEMIC_RE = re.compile(
    r"\b(universit\w*|univ\.|college|school\s+of|institute\s+of\s+technology"
    r"|polytech\w*|politecnico|escuela|hochschule|ecole|école|academy|faculty"
    r"|dept\.|department\s+of|cnrs|inria|loria|cyfronet|csic|max\s*planck"
    r"|\.edu\b|\.ac\.[a-z]{2}\b)", re.I)
INDUSTRY_RE = re.compile(
    r"(\bInc\.|\bInc\b(?=\s*[,.]|$)|\bLtd\.?\b|\bLLC\b|\bGmbH\b|\bS\.A\b"
    r"|\bCorp\.?\b|\bCorporation\b|\bCompany\b|\bCo\.,|\bLabs?\b"
    r"|\bTechnologies\b|\bResearch\b|\bIBM\b|\bMicrosoft\b|\bGoogle\b"
    r"|\bAmazon\b|\bAWS\b|\bMeta\b|\bApple\b|\bNVIDIA\b|\bIntel\b"
    r"|\bHuawei\b|\bAlibaba\b|\bTencent\b|\bBaidu\b|\bSamsung\b"
    r"|\bSiemens\b|\bEricsson\b|\bNokia\b|\bBell\s*Labs\b|\bTelef[oó]nica\b"
    r"|\bRed\s*Hat\b|\bVMware\b|\bOracle\b|\bSAP\b|\bSalesforce\b"
    r"|\bAdobe\b|\bUber\b|\bNetflix\b|\bLinkedIn\b|\bByteDance\b"
    r"|\bZettaScale\b|\bRTX\b|\bBBN\b|\bCSIRO\b|\bData61\b|\bBosch\b"
    r"|\bZTE\b|\bFujitsu\b|\bHitachi\b|\bNEC\b|\bCisco\b|\bDell\b"
    r"|\bHPE\b)")

NDA_RE = re.compile(r"\bnon[\- ]?disclosure\s+agreement|\bNDA\b", re.I)

# Markers of the real-world deployment evidence the Industrial Track asks for.
DEPLOY_EVIDENCE_RE = re.compile(
    r"\b(in\s+production|production\s+(system|cluster|deployment|workload|traffic)"
    r"|deployed\s+(at|in|on|across)|real[\-\s]world\s+(workload|deployment|trace)"
    r"|customer(s|\s+workload)|live\s+traffic|incident|postmortem|post[\-\s]mortem"
    r"|over\s+\d+\s+(months?|years?|weeks?)|\d+\s+(nodes|servers|hosts|clusters|regions|tenants)"
    r"|p9[59](\.\d+)?\b|SLO\b|SLA\b|on[\-\s]call)", re.I)

SEV_ORDER = {"BLOCK": 0, "FAIL": 1, "WARN": 2, "NOTE": 3, "OK": 4}

# --------------------------------------------------------------------------
# data model
# --------------------------------------------------------------------------


@dataclass
class Finding:
    sev: str          # BLOCK | FAIL | WARN | NOTE | OK
    check: str        # short id, e.g. "format.page_limit"
    message: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {"severity": self.sev, "check": self.check,
                "message": self.message, "detail": self.detail}


@dataclass
class Paper:
    paper_id: str
    source_name: str
    path: Path
    findings: list[Finding] = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    text: str = ""
    hidden_text: str = ""
    hidden_display: str = ""

    def add(self, sev: str, check: str, message: str, detail: str = "") -> None:
        self.findings.append(Finding(sev, check, message, detail))

    @property
    def worst(self) -> str:
        if not self.findings:
            return "OK"
        return min((f.sev for f in self.findings), key=lambda s: SEV_ORDER[s])


# --------------------------------------------------------------------------
# version reporting
# --------------------------------------------------------------------------

SCRIPT_VERSION = "1.0.0"
REPO = "serverlesscomputing/woais26-ai-reviews-checks"


def _git_version(f: Path) -> str | None:
    """Describe the checkout this file belongs to, if it belongs to one.

    Being *inside* a work tree is not enough: a copy dropped into an unrelated
    repository would otherwise report that repository's commit as its own. The
    repo must actually track this file.
    """
    d = f.parent

    def git(*args) -> str | None:
        try:
            r = subprocess.run(("git", "-C", str(d)) + args,
                               capture_output=True, text=True, timeout=8)
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            return None

    if git("rev-parse", "--is-inside-work-tree") != "true":
        return None
    rel = git("ls-files", "--full-name", "--error-unmatch", str(f))
    if not rel:
        return None          # inside a repo, but not a file that repo tracks
    sha = git("rev-parse", "HEAD")
    if not sha:
        return None          # a repo with no commits cannot version anything
    if git("cat-file", "-e", f"HEAD:{rel}") is None:
        return None          # staged but never committed here
    described = git("describe", "--tags", "--always", "--dirty") or sha[:12]
    tag = git("describe", "--tags", "--exact-match") or "(no tag on this commit)"
    when = git("log", "-1", "--format=%cs") or "?"
    dirty = " (uncommitted changes present)" if git("status", "--porcelain") else ""
    return (f"commit {sha}\n"
            f"  tag        : {tag}\n"
            f"  describe   : {described}{dirty}\n"
            f"  committed  : {when}")


def _published_version(timeout: float = 5.0) -> str:
    """Ask GitHub what the published tip and tags are."""
    import urllib.request

    def get(path):
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}{path}",
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "paper_checks"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    try:
        head = get("/commits/main")
        sha = head["sha"]
        line = f"commit {sha}\n  committed  : {head['commit']['committer']['date'][:10]}"
        try:
            tags = get("/tags")
            here = [t["name"] for t in tags if t["commit"]["sha"] == sha]
            newest = tags[0]["name"] if tags else None
            if here:
                line += f"\n  tag        : {', '.join(here)}"
            elif newest:
                line += f"\n  tag        : (main is past {newest})"
        except Exception:
            pass
        return line
    except Exception as exc:
        return f"could not reach github.com ({exc.__class__.__name__})"


def version_info() -> str:
    out = [f"paper_checks.py {SCRIPT_VERSION}   venue: {VENUE_KEY}"]
    src = globals().get("__file__")
    if src:
        p = Path(src).resolve()
        out.append(f"  file       : {p}")
        try:
            out.append("  sha256     : "
                       + hashlib.sha256(p.read_bytes()).hexdigest())
        except Exception:
            pass
        local = _git_version(p)
        if local:
            out.append(f"  git        : {local}")
        else:
            out.append("  git        : not a git checkout - this copy carries no "
                       "commit id of its own")
            out.append(f"  published  : {_published_version()}")
    else:
        out.append("  file       : (read from stdin, no path)")
        out.append(f"  published  : {_published_version()}")
    out.append(f"  repo       : https://github.com/{REPO}")
    return "\n".join(out)


# --------------------------------------------------------------------------
# which agent is running this skill?
# --------------------------------------------------------------------------

# Documented environment markers each agent sets in the shells it spawns.
HOST_MARKERS = [
    ("Claude Code", "anthropic",
     ["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_CHILD_SESSION"]),
    ("Cursor", "unknown",
     ["CURSOR_AGENT", "CURSOR_TRACE_ID"]),
    ("GitHub Copilot", "unknown",
     ["COPILOT_AGENT"]),
    ("Codex / ChatGPT", "openai",
     ["CODEX_SESSION_ID", "CODEX_THREAD_ID", "CODEX_VERSION", "CODEX_SANDBOX",
      "CODEX_CI"]),
    ("Gemini CLI", "google",
     ["GEMINI_CLI"]),
    ("OpenCode", "unknown", ["OPENCODE", "OPENCODE_BIN"]),
    ("Goose", "unknown", ["GOOSE_PROVIDER", "GOOSE_MODEL"]),
    ("Amp", "unknown", ["AMP_API_KEY", "AMP_URL"]),
]


def detect_host() -> dict:
    """Name the agent running this script, from the env vars it sets."""
    found = []
    for name, vendor, keys in HOST_MARKERS:
        hit = [k for k in keys if os.environ.get(k)]
        if hit:
            found.append({"host": name, "vendor": vendor, "evidence": hit})
    generic = os.environ.get("AI_AGENT")
    if len(found) == 1:
        only = found[0]
        only.update({"others": [], "ambiguous": False, "ai_agent": generic})
        return only
    if found:
        # Several agents' markers are present, so we cannot tell which one is
        # driving this shell. Never resolve that by list order - say so.
        return {"host": " + ".join(f["host"] for f in found),
                "vendor": "unknown", "ambiguous": True,
                "evidence": [k for f in found for k in f["evidence"]],
                "others": [f["host"] for f in found[1:]], "ai_agent": generic}
    return {"host": None, "vendor": None, "evidence": [], "others": [],
            "ambiguous": False, "ai_agent": generic}


ASK_USER_BLOCK = """
========================================================================
  STOP - this skill is not running in Claude Code
========================================================================

  detected host : {host}
  evidence      : {evidence}

  Everything this skill says about retention, training and zero data
  retention is sourced from ANTHROPIC's documentation. It describes
  Anthropic's API and Claude plans. It does NOT describe this host, and the
  probe cannot verify this host's behaviour.

  Two separate questions, and both matter:
    1. the HOST  - {host} - what does the agent itself store, and for how
       long? (transcripts, telemetry, cloud sync, feedback paths)
    2. the MODEL PROVIDER behind it - many agents are bring-your-own-key or
       multi-provider, so the host and the provider can have completely
       different retention terms. If this host is routing to Anthropic with
       your own API key, the Anthropic figures in references/RETENTION.md do
       apply to the inference leg - but not to whatever the host stores.

  AGENT: do not guess, and do not apply the Anthropic numbers. Stop here,
  tell the user what was detected, and ask them to confirm - offering to web
  search their provider's current docs - these five questions:

    a) Which model provider and model is this session actually using?
    b) Does that provider train on prompts or outputs by default, and is
       there an opt-out? Where is it?
    c) How long are prompts and outputs retained, and is a zero-retention or
       no-logging mode available on this plan?
    d) What does {host} itself store server-side - transcripts, telemetry,
       cloud sessions, feedback uploads - and for how long?
    e) Where does {host} keep local session transcripts, and for how long?

  Suggested searches:
    "{host} data retention policy prompts training opt-out"
    "{host} zero data retention enterprise"
    "<model provider> API data retention training policy"

  Until those five are answered, the ACM test is unmet: reviewers "may not
  upload confidential submissions ... into any system managed by a third
  party, including LLMs, that does not promise to maintain the
  confidentiality of that information."

  Safe fallback that needs no answers at all: run against a LOCAL model
  (references/RETENTION.md, section 5). Nothing leaves the host, so the
  third-party clause does not apply.
========================================================================
"""


def host_warning(host: dict) -> str | None:
    """Return the stop-and-ask block unless we are clearly in Claude Code."""
    if host["host"] == "Claude Code" and not host.get("ambiguous"):
        return None
    name = host["host"] or "unrecognised agent"
    ev = ", ".join(host["evidence"]) or "no known agent env markers found"
    if host.get("ai_agent"):
        ev += f"; AI_AGENT={host['ai_agent']}"
    return ASK_USER_BLOCK.format(host=name, evidence=ev)


# --------------------------------------------------------------------------
# step 0: retention / model posture probe
# --------------------------------------------------------------------------

COVERED_MODELS = ["claude-fable-5-1", "claude-fable-5", "claude-mythos-5-1", "claude-mythos-5"]
ZDR_SAFE_MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"]


def config_dir() -> Path:
    """Claude Code keeps credentials/transcripts under CLAUDE_CONFIG_DIR if set."""
    d = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(d) if d else Path.home() / ".claude"


def detect_credential() -> dict:
    """Which credential will Claude Code actually use, and what plan is it?

    Precedence per the Claude Code docs: cloud provider > ANTHROPIC_AUTH_TOKEN >
    ANTHROPIC_API_KEY > apiKeyHelper > CLAUDE_CODE_OAUTH_TOKEN > profile >
    subscription OAuth from /login.
    """
    env = os.environ.get
    info = {"plan": None, "subscription_present": False, "shadowed": False}

    # a subscription login may exist even when something else outranks it
    cred = config_dir() / ".credentials.json"
    if cred.is_file():
        try:
            oauth = json.loads(cred.read_text()).get("claudeAiOauth") or {}
            if oauth:
                info["subscription_present"] = True
                info["plan"] = (oauth.get("subscriptionType") or "").lower() or None
        except Exception:
            pass
    elif sys.platform == "darwin":
        info["keychain"] = True   # macOS stores it in the Keychain, not a file

    if env("CLAUDE_CODE_USE_BEDROCK") or env("CLAUDE_CODE_USE_VERTEX") \
            or env("CLAUDE_CODE_USE_FOUNDRY"):
        info["surface"] = "third-party cloud (Bedrock / Vertex / Foundry)"
        info["kind"] = "cloud"
    elif env("ANTHROPIC_AUTH_TOKEN"):
        info["surface"] = "ANTHROPIC_AUTH_TOKEN (gateway / proxy bearer token)"
        info["kind"] = "commercial"
    elif env("ANTHROPIC_API_KEY"):
        info["surface"] = "ANTHROPIC_API_KEY (Claude Console / Commercial Terms)"
        info["kind"] = "commercial"
        info["shadowed"] = info["subscription_present"]
    elif env("CLAUDE_CODE_OAUTH_TOKEN"):
        info["surface"] = "CLAUDE_CODE_OAUTH_TOKEN (long-lived subscription token)"
        info["kind"] = "subscription"
    elif info["subscription_present"] or info.get("keychain"):
        info["surface"] = "subscription OAuth from /login"
        info["kind"] = "subscription"
    else:
        info["surface"] = "(none detected - run /login)"
        info["kind"] = "unknown"
    return info


CONSUMER_PLANS = {"free", "pro", "max"}
COMMERCIAL_PLANS = {"team", "enterprise"}


def probe(args: argparse.Namespace) -> int:
    host = detect_host()
    warn = host_warning(host)
    if warn:
        print(warn)

    print("=" * 72)
    print("STEP 0  data-retention and model posture")
    print("=" * 72)
    print(f"\n  host              : {host['host'] or 'not recognised'}"
          + ("  <- Anthropic figures below may not apply" if warn else ""))

    cred = detect_credential()
    plan = cred.get("plan")
    kind = cred["kind"]
    # the credential actually in use decides which rules apply, not whichever
    # subscription happens to be stored on the machine
    consumer = kind == "subscription" and plan in CONSUMER_PLANS

    print(f"\n  credential in use : {cred['surface']}")
    if plan and kind == "subscription":
        print(f"  plan              : {plan.upper()}"
              + ("   (consumer plan)" if consumer else "   (commercial plan)"))
    elif plan:
        print(f"  stored login      : {plan.upper()} subscription, NOT in use")
    elif cred.get("keychain"):
        print("  plan              : stored in the macOS Keychain - run /status "
              "in Claude Code to see it")
    print()

    if cred["shadowed"]:
        print("  !! ANTHROPIC_API_KEY is set AND you have a subscription login.")
        print("     The API key WINS, silently, and bills the Console org. That is")
        print("     good for confidentiality (Commercial Terms, ZDR possible) but")
        print("     confirm it is deliberate: /status marks the unused credential.")
        print("     `unset ANTHROPIC_API_KEY` falls back to the subscription.\n")

    # ---- what this credential means ---------------------------------------
    if consumer:
        print("-" * 72)
        print(f"  CLAUDE {plan.upper()} - what actually works for reviewing")
        print("-" * 72)
        print("""
  A consumer plan IS usable for peer review, but only on the strict reading:
  ACM bars uploading submissions to a third party that does not promise
  confidentiality. With Model Improvement OFF a consumer plan is no-training
  plus 30-day deletion, which meets that. With it ON your data goes into
  training pipelines for up to 5 years, which does not.

  Zero data retention is NEVER available on Free / Pro / Max. If you need
  "it was never stored", your options are a Console API key with a ZDR
  arrangement, Claude Code on Claude for Enterprise (separate ZDR offering,
  qualified accounts), or a local model. See 0.4 in SKILL.md.

  FIVE THINGS TO DO:

   1. Check and set the training toggle WITHOUT LEAVING THE AGENT:

        /privacy-settings      <- views and updates it; Pro/Max only

      Or in the browser: claude.ai/settings/data-privacy-controls ->
      "Model Improvement" / "Help improve Claude" = OFF. It covers Claude Code
      from this account too. Turning it ON applies to new and resumed chats
      only, so switching it OFF now protects this batch.
   2. Keep review work in its own config dir so transcripts are isolated and
      can be deleted wholesale:
         alias claude-review='CLAUDE_CONFIG_DIR=$HOME/.claude-review claude'
   3. When decisions are out, DELETE the review conversations in claude.ai.
      Deleted chats leave your history immediately, are purged from backend
      storage within 30 days, and are excluded from future model training.
      This is the strongest lever a consumer plan gives you.
""")
    elif kind == "commercial":
        print("  Commercial Terms apply: no training on your content, inputs and")
        print("  outputs deleted within 30 days, and ZDR available on request.")
        print("  This is the best posture short of a local model.\n")
    elif kind == "cloud":
        print("  Your cloud provider is the data processor. Anthropic's retention")
        print("  docs do not govern this path - check that provider's docs.\n")

    # ---- hardening checks --------------------------------------------------
    print("-" * 72)
    print("  local hygiene (current state -> what it should be)")
    print("-" * 72)
    rows: list[tuple[str, str, str]] = []

    settings = config_dir() / "settings.json"
    cleanup = "(settings.json not found)"
    if settings.is_file():
        try:
            cleanup = str(json.loads(settings.read_text())
                          .get("cleanupPeriodDays", "(unset -> 30 days)"))
        except Exception as exc:
            cleanup = f"(unreadable: {exc})"
    rows.append(("cleanupPeriodDays", cleanup,
                 "set low (e.g. 7). Transcripts sit in PLAINTEXT under "
                 f"{config_dir()}/projects/"))

    proj = config_dir() / "projects"
    n_tx = sum(1 for _ in proj.rglob("*.jsonl")) if proj.is_dir() else 0
    rows.append(("transcripts on disk", f"{n_tx} .jsonl file(s) in {proj}",
                 "delete once decisions are out"))

    rows.append(("DISABLE_FEEDBACK_COMMAND",
                 os.environ.get("DISABLE_FEEDBACK_COMMAND") or "(unset)",
                 "=1. /feedback, /bug and /share upload the conversation, "
                 "paper text included, kept 5 years"))

    # error reporting defaults ON only for Pro/Max sign-ins
    er = os.environ.get("DISABLE_ERROR_REPORTING")
    if consumer and plan in ("pro", "max"):
        er_note = ("=1. Error reporting defaults to ON *specifically* for Pro/Max "
                   "sign-ins on direct Claude API. It redacts known secrets and "
                   "paths, but it is third-party egress you do not need here")
    else:
        er_note = "=1 for good measure (off by default on your credential)"
    rows.append(("DISABLE_ERROR_REPORTING", er or "(unset)", er_note))

    fs = os.environ.get("CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY")
    if consumer:
        fs_note = ("=1. ZDR orgs never see the 'can Anthropic look at your "
                   "transcript?' follow-up. Consumer plans DO, and saying yes "
                   "uploads the transcript for up to 6 months")
    else:
        fs_note = "=1 to suppress the transcript-share follow-up"
    rows.append(("CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY", fs or "(unset)", fs_note))

    rows.append(("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
                 os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")
                 or "(unset)",
                 "=1 turns off telemetry, error reports, /feedback and surveys "
                 "together"))

    rows.append(("CLAUDE_CONFIG_DIR", os.environ.get("CLAUDE_CONFIG_DIR")
                 or "(unset -> ~/.claude)",
                 "point review sessions at their own dir to isolate transcripts"))

    for name, value, verdict in rows:
        done = value not in ("(unset)", "(unset -> 30 days)",
                             "(settings.json not found)")
        flag = " ok " if done else "TODO"
        print(f"  [{flag}] {name}\n         now : {value}\n         want: {verdict}\n")

    print("  Copy-paste hardening for this review directory:\n")
    print("    export DISABLE_FEEDBACK_COMMAND=1")
    print("    export DISABLE_ERROR_REPORTING=1")
    print("    export CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1")
    print("    export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1")
    print(f"    # and in {config_dir()}/settings.json:  "
          '{ "cleanupPeriodDays": 7 }')
    print("\n  Also avoid, while submissions are in context: cloud sessions "
          "(they always\n  use subscription credentials and store the transcript "
          "server-side),\n  Remote Control, Artifacts, and /feedback.\n")

    # ---- model choice -------------------------------------------------------
    print("-" * 72)
    print("  model choice")
    print("-" * 72)
    model_env = os.environ.get("ANTHROPIC_MODEL", "(not pinned)")
    print(f"  ANTHROPIC_MODEL   : {model_env}")
    if consumer:
        print("""
  Fable and Mythos are Covered Models: prompts and completions are "retained
  for at least 30 days and then automatically deleted" wherever they are
  offered, Claude applications included. On a consumer plan with Model
  Improvement off your default is already 30 days, so the gap is small - but
  "at least 30 days" is looser than the consumer default, so pin the model:

    /model opus-5        (or sonnet-5)

  Do not leave it on `best`, which resolves to the latest Fable where
  available. The hard ZDR block on Covered Models is an org-level concern and
  does not apply to you - you never had ZDR to lose.
""")
    else:
        print("""
  Fable 5 / 5.1 and Mythos 5 / 5.1 are Covered Models: they REQUIRE 30-day
  retention and are not available under ZDR unless Anthropic expressly
  authorizes it. If your org is ZDR, calling one returns 400. Pin the model:

    /model opus-5        (or sonnet-5)

  Never leave it on `best`, which resolves to the latest Fable where available.
""")

    # ---- local model --------------------------------------------------------
    ollama = shutil.which("ollama")
    if ollama:
        try:
            out = subprocess.run([ollama, "list"], capture_output=True, text=True,
                                 timeout=10).stdout.strip().splitlines()[1:]
            models = ", ".join(l.split()[0] for l in out) or "(none pulled)"
        except Exception:
            models = "(ollama present, `ollama list` failed)"
        print(f"  local models      : {models}")
        print("  Nothing leaves the host, so ACM's third-party clause does not")
        print("  apply at all. On a consumer plan this is the only way to get")
        print("  true zero retention. OLLAMA_HOST=127.0.0.1 keeps it loopback.\n")
    else:
        print("  local models      : ollama not installed")
        print("  `brew install ollama && ollama pull qwen3:14b` gives a path that")
        print("  needs no policy argument. On Free/Pro/Max it is the only way to")
        print("  get true zero retention.\n")

    # ---- live retention probe ----------------------------------------------
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    print("-" * 72)
    if args.live and key:
        print("live probe: does this org have 30-day retention enabled?")
        print("-" * 72)
        print(_covered_model_probe(key))
    elif args.live:
        print("--live needs an ANTHROPIC_API_KEY.")
        print("-" * 72)
        print("  The retention probe is an org/workspace test and does not apply "
              "to a\n  subscription credential. On a consumer plan there is "
              "nothing to query:\n  retention follows the Model Improvement "
              "setting, which is only visible\n  in the web UI. Check it at "
              "claude.ai/settings/data-privacy-controls and\n  note the date you "
              "checked in your review notes.")
    else:
        print("To test org retention for real (needs an API key), re-run with "
              "--live, or:")
        print(_covered_model_probe_curl())

    print("\nPolicy references:")
    for k, v in ACM_POLICY_URLS.items():
        print(f"  {k:12s} {v}")
    print("  retention   https://platform.claude.com/docs/en/manage-claude/"
          "api-and-data-retention")
    print("  consumer    https://privacy.claude.com/en/articles/"
          "10023548-how-long-do-you-store-my-data")
    print("  claude code https://code.claude.com/docs/en/data-usage")
    return 0


def _covered_model_probe_curl() -> str:
    return (
        "\n  # A Covered Model (Fable/Mythos) REQUIRES 30-day retention, so this\n"
        "  # call is a retention test. Sends only the word 'hi' - no paper text.\n"
        "  curl -s https://api.anthropic.com/v1/messages \\\n"
        "    -H \"x-api-key: $ANTHROPIC_API_KEY\" \\\n"
        "    -H 'anthropic-version: 2023-06-01' -H 'content-type: application/json' \\\n"
        "    -d '{\"model\":\"claude-fable-5-1\",\"max_tokens\":8,"
        "\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}'\n\n"
        "  400 invalid_request_error 'must have data retention enabled'\n"
        "        -> your org/workspace is ZERO data retention. Best case.\n"
        "  200 OK\n"
        "        -> your org has 30-day retention on. Acceptable under ACM policy\n"
        "           (no training + 30-day delete), but prefer Opus/Sonnet so you\n"
        "           are not routing submissions to a retention-required model.\n")


def _covered_model_probe(key: str) -> str:
    import urllib.error
    import urllib.request
    body = json.dumps({"model": "claude-fable-5-1", "max_tokens": 8,
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
        return ("  200 OK -> this org/workspace has 30-DAY RETENTION enabled.\n"
                "  Acceptable (no training, 30-day delete) but NOT zero-retention.\n"
                "  Pin /model to opus-5 or sonnet-5 for review work.")
    except urllib.error.HTTPError as exc:
        txt = exc.read().decode(errors="replace")
        if exc.code == 400 and "data retention" in txt:
            return ("  400 'must have data retention enabled' -> this org is "
                    "ZERO DATA RETENTION.\n  Best case. Keep off Fable/Mythos "
                    "and off Files/Batch/MCP-connector APIs.")
        return f"  HTTP {exc.code}: {txt[:400]}"
    except Exception as exc:
        return f"  probe failed: {exc}"


# --------------------------------------------------------------------------
# zdr: can this credential be zero-data-retention, and how do we know?
# --------------------------------------------------------------------------

def zdr(args: argparse.Namespace) -> int:
    """Answer 'am I on zero data retention?' with the evidence and its strength.

    There is no public API that reports ZDR status. So this reports what is
    knowable: a policy fact for consumer plans, and for commercial credentials
    an inference from the Covered Model gate plus the documented side effects.
    """
    host = detect_host()
    warn = host_warning(host)
    if warn:
        print(warn)

    cred = detect_credential()
    plan, kind = cred.get("plan"), cred["kind"]
    consumer = kind == "subscription" and plan in CONSUMER_PLANS

    print("=" * 72)
    print("ZDR CHECK  is zero data retention in effect?")
    print("=" * 72)
    print(f"\n  host              : {host['host'] or 'not recognised'}")
    if warn:
        print("  NOTE: the verdict below reasons about Anthropic plans only. "
              "Answer the\n        five questions above before relying on it.")
    print(f"\n  credential in use : {cred['surface']}")
    if plan and kind == "subscription":
        print(f"  plan              : {plan.upper()}")
    elif plan:
        print(f"  stored login      : {plan.upper()} subscription, NOT in use")
    print()

    # ---- consumer plans: settled by policy, not by probing -----------------
    if consumer:
        print("-" * 72)
        print(f"  VERDICT: NOT ZDR - and not obtainable on {plan.upper()}")
        print("  evidence strength: DEFINITIVE (policy, no probe needed)")
        print("-" * 72)
        print("""
  Anthropic's retention docs list "Claude consumer products: Claude Free, Pro,
  and Max plans, including when customers on those plans use Claude's web,
  desktop, or mobile apps or Claude Code" under what ZDR does NOT cover. So
  there is nothing to detect: a consumer plan is never ZDR, and no command,
  env var or API call will change that answer.

  What you have instead, and how to check it from inside the agent:

    /privacy-settings     view and update the model-training toggle (Pro/Max)
    /status               confirm which credential is actually in use

  With the training toggle OFF you get: no training on your content, 30-day
  retention, and deletion of a conversation purges it from backend storage
  within 30 days and excludes it from future training. That is the ceiling on
  this plan, and it does satisfy ACM's confidentiality clause.

  To actually get ZDR you need one of:
    * a Claude Console API key on an org with a ZDR arrangement (sales)
    * Claude Code on Claude for Enterprise, separate ZDR offering, qualified
      accounts, enabled by your account team and audit-logged
    * a local model - the only zero-retention option with no procurement
""")
        _acm_verdict(plan, args, host)
        _zdr_refs()
        return 0

    if kind == "cloud":
        print("-" * 72)
        print("  VERDICT: NOT APPLICABLE - your cloud provider is the processor")
        print("  evidence strength: DEFINITIVE (policy)")
        print("-" * 72)
        print("\n  On Amazon Bedrock and Google Cloud's Agent Platform the cloud")
        print("  provider is the data processor, so Anthropic's ZDR arrangement is")
        print("  not what governs you. Check that provider's retention docs and")
        print("  your agreement with them.\n")
        _zdr_refs()
        return 0

    # ---- commercial: probe what can be probed ------------------------------
    print("-" * 72)
    print("  commercial credential - gathering evidence")
    print("-" * 72)
    findings: list[tuple[str, str, str]] = []

    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if key and not args.offline:
        verdict = _covered_model_gate(key)
        findings.append(("Covered Model gate", verdict[0], verdict[1]))
    else:
        findings.append(("Covered Model gate", "NOT CHECKED",
                         "needs ANTHROPIC_API_KEY and no --offline. This is the "
                         "only live test available."))

    findings.append((
        "error reporting default", "INFERENCE",
        "Claude Code enables error reporting only when, among other things, "
        "'your organization doesn't have a zero data retention or HIPAA "
        "agreement'. So error reporting being active implies NOT ZDR. It is "
        "off by default on a commercial key anyway, so this is weak evidence."))

    findings.append((
        "transcript-share survey", "INFERENCE",
        "'Organizations with zero data retention ... never see this follow-up.' "
        "If you have ever been asked 'Can Anthropic look at your session "
        "transcript?', that session was not ZDR."))

    findings.append((
        "features disabled under ZDR", "OBSERVABLE",
        "Under Claude Code ZDR on Claude for Enterprise these are blocked at the "
        "backend: cloud sessions, Claude Tag, Artifacts, /feedback /bug /share, "
        "and Remote Control. If any of them works, that organization is not ZDR. "
        "Do not test this with a submission in context."))

    findings.append((
        "Admin API", "NONE",
        "There is no public endpoint that reports ZDR status. "
        "/v1/organizations/me returns which org a credential belongs to, not its "
        "retention configuration."))

    for name, strength, detail in findings:
        print(f"\n  [{strength}] {name}")
        for line in _wrap(detail, 68):
            print(f"      {line}")

    print("\n" + "-" * 72)
    print("  AUTHORITATIVE ANSWER: your contract, or your Anthropic account team.")
    print("-" * 72)
    print("""
  ZDR is a per-organization arrangement, granted after an eligibility review and
  audit-logged at enablement - not a flag you can read back. Probes can tell you
  'retention is enabled' with confidence, but they cannot prove a ZDR agreement
  exists. For a peer-review audit trail, record the organization id, the date,
  and who confirmed it - not a probe result.

  Two traps even when you ARE ZDR:
    * ZDR applies only to requests authenticating into the ZDR org. A personal
      login or another org's key is not covered.
    * ZDR does not block non-eligible features. Using the Files API, Batch API,
      MCP connector, code execution or API-side Agent Skills steps outside the
      arrangement for that data, silently.
""")
    _acm_verdict(plan, args, host)
    _zdr_refs()
    return 0


def _acm_verdict(plan: str | None, args: argparse.Namespace, host: dict) -> None:
    """The question that actually matters: may I review on this setup?"""
    import datetime
    today = datetime.date.today().isoformat()
    consumer = (plan or "") in CONSUMER_PLANS
    unknown_host = host["host"] != "Claude Code"

    print("\n" + "=" * 72)
    print("  ACM COMPLIANCE VERDICT")
    print("=" * 72)

    if unknown_host:
        print("""
  UNDETERMINED - this is not a Claude Code session, so neither this script nor
  Anthropic's documentation can answer it. Answer the five questions at the top
  of this output first, or run against a local model.
""")
        return

    if consumer:
        if args.training_off:
            print(f"""
  COMPLIANT, on the reading that matters.

  ACM bars uploading submissions to a third party "that does not promise to
  maintain the confidentiality of that information". A consumer plan with
  training off is no-training plus 30-day deletion. That is such a promise.

  What you have confirmed is the BEST POSTURE THIS PLAN OFFERS - not zero
  retention. Do not describe it as ZDR; ZDR is not available on {(plan or '').upper()}
  and is not being claimed.

  Copy this into your review notes as the audit trail:

    {today} - Claude {(plan or '').upper()}, subscription OAuth{', no API key' if True else ''}.
    "Help improve our AI models" = OFF, verified via /privacy-settings.
    Retention: 30 days, no training (Anthropic consumer terms).
    ZDR: not available on consumer plans; not claimed.
    Reviewer: <your name>
""")
        else:
            print(f"""
  CONDITIONAL - one thing left to confirm, and it is the thing that decides it.

  On {(plan or 'a consumer').upper()} the training toggle is the whole question:

    OFF -> no training + 30-day retention  -> COMPLIANT
    ON  -> up to 5 years in training pipelines -> NOT COMPLIANT

  The script cannot read it. Confirm it yourself, then re-run with the flag so
  the attestation is complete:

    /privacy-settings                      <- in Claude Code (Pro/Max only)
    https://claude.ai/settings/data-privacy-controls

    uv run <this script> zdr --training-off
""")
        return

    print("""
  COMPLIANT. Commercial Terms: no training on your content, inputs and outputs
  deleted within 30 days, ZDR available on request. Record the organization id
  and the date you confirmed the arrangement, not a probe result.
""")


def _wrap(text: str, width: int) -> list[str]:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


def _covered_model_gate(key: str) -> tuple[str, str]:
    """Covered Models require 30-day retention, so the gate is a retention test."""
    import urllib.error
    import urllib.request
    body = json.dumps({"model": "claude-fable-5-1", "max_tokens": 8,
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
        return ("NOT ZDR",
                "A Covered Model answered 200, so this org or workspace has "
                "30-day retention ENABLED. Strong evidence against ZDR for this "
                "workspace. Acceptable under ACM policy (no training, 30-day "
                "delete), but it is not zero retention.")
    except urllib.error.HTTPError as exc:
        txt = exc.read().decode(errors="replace")
        if exc.code == 400 and "data retention" in txt:
            return ("LIKELY ZDR",
                    "A Covered Model was refused with 'must have data retention "
                    "enabled', so retention is NOT enabled for this org or "
                    "workspace - consistent with ZDR. Strong, but still an "
                    "inference: confirm against your contract.")
        return (f"INCONCLUSIVE", f"HTTP {exc.code}: {txt[:200]}")
    except Exception as exc:
        return ("INCONCLUSIVE", f"probe failed: {exc}")


def _zdr_refs() -> None:
    print("References:")
    print("  ZDR scope and feature eligibility")
    print("    https://platform.claude.com/docs/en/manage-claude/"
          "api-and-data-retention")
    print("  Claude Code ZDR (Enterprise), incl. features disabled under it")
    print("    https://code.claude.com/docs/en/zero-data-retention")
    print("  Covered Models")
    print("    https://support.claude.com/en/articles/15425695-covered-models")
    print("  Claude Code data usage and retention by plan")
    print("    https://code.claude.com/docs/en/data-usage")
    print("  ACM Peer Review Policy (the LLM / confidentiality clause)")
    print(f"    {ACM_POLICY_URLS['peer_review']}")
    print("  ACM Roles and Responsibilities (reviewer duties, bulk-downloads)")
    print(f"    {ACM_POLICY_URLS['roles']}")


# --------------------------------------------------------------------------
# input collection
# --------------------------------------------------------------------------


def collect_from_zip(zpath: Path, workdir: Path) -> list[tuple[str, Path]]:
    out = []
    with zipfile.ZipFile(zpath) as zf:
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".pdf"):
                continue
            # keep the archive name: it is itself an anonymity check
            safe = re.sub(r"[^A-Za-z0-9._\-]", "_", Path(info.filename).name)
            dest = workdir / safe
            with zf.open(info) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
            out.append((Path(info.filename).name, dest))
    return sorted(out)


def collect_from_dir(d: Path) -> list[tuple[str, Path]]:
    return sorted((p.name, p) for p in d.rglob("*.pdf"))


def collect_from_hotcrp(base: str, query: str, token: str,
                        workdir: Path) -> list[tuple[str, Path]]:
    import urllib.parse
    import urllib.request
    base = base.rstrip("/")

    def get(path: str, binary: bool = False):
        req = urllib.request.Request(base + path,
                                     headers={"Authorization": f"bearer {token}"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read() if binary else json.loads(r.read())

    q = urllib.parse.urlencode({"q": query, "t": "s"})
    data = get(f"/api/search?{q}")
    ids = [str(r["pid"] if isinstance(r, dict) else r)
           for r in (data.get("ids") or data.get("papers") or [])]
    if not ids:
        ids = [str(i) for i in data.get("ids", [])]
    out = []
    for pid in ids:
        pdf = get(f"/api/document?p={pid}&dt=submission", binary=True)
        dest = workdir / f"paper{pid}.pdf"
        dest.write_bytes(pdf)
        out.append((f"paper{pid}.pdf", dest))
    return out


def paper_id_from_name(name: str) -> str:
    """HotCRP names downloads like 'woais26-paper17.pdf' or 'paper17.pdf', so the
    number that matters follows 'paper'/'submission' - not the first digit run,
    which is usually the venue year."""
    stem = Path(name).stem
    m = re.search(r"(?:paper|submission|sub|p)[-_ ]?(\d+)", stem, re.I)
    if m:
        return m.group(1)
    nums = re.findall(r"\d+", stem)
    if nums:
        return nums[-1]
    return re.sub(r"[^A-Za-z0-9]+", "-", stem)[:40] or "unknown"


def unique_ids(items: list[tuple[str, Path]]) -> list[tuple[str, str, Path]]:
    """(paper_id, source_name, path) with collisions disambiguated, so two
    submissions never overwrite each other's output directory."""
    seen: dict[str, int] = {}
    out = []
    for name, path in items:
        base = paper_id_from_name(name)
        seen[base] = seen.get(base, 0) + 1
        pid = base if seen[base] == 1 else f"{base}-{seen[base]}"
        out.append((pid, name, path))
    return out


# --------------------------------------------------------------------------
# PDF inspection
# --------------------------------------------------------------------------


def _is_near_white(color_int: int, thresh: int = 235) -> bool:
    r, g, b = (color_int >> 16) & 255, (color_int >> 8) & 255, color_int & 255
    return r >= thresh and g >= thresh and b >= thresh


def inspect_pdf(paper: Paper, venue: dict) -> None:
    try:
        doc = pymupdf.open(paper.path)
    except Exception as exc:
        paper.add("FAIL", "pdf.open", "PDF cannot be opened", str(exc))
        return

    f = paper.facts
    f["pages"] = doc.page_count
    f["encrypted"] = bool(doc.is_encrypted)
    f["metadata"] = {k: (v or "") for k, v in (doc.metadata or {}).items()}

    visible_chunks: list[str] = []
    hidden_chunks: list[str] = []
    size_weight: Counter = Counter()
    page_texts: list[str] = []
    refs_page = None
    refs_y_frac = None
    appendix_pages: list[int] = []
    col_starts: Counter = Counter()
    tiny_spans = 0
    white_spans = 0
    offpage_spans = 0

    dom_guess = None
    for pno in range(doc.page_count):
        page = doc[pno]
        rect = page.rect
        ptext = page.get_text("text")
        page_texts.append(ptext)
        try:
            d = page.get_text("dict")
        except Exception:
            d = {"blocks": []}
        for block in d.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    t = span.get("text", "")
                    if not t.strip():
                        continue
                    size = float(span.get("size", 0.0))
                    color = int(span.get("color", 0))
                    bbox = span.get("bbox", (0, 0, 0, 0))
                    off = (bbox[2] < rect.x0 - 1 or bbox[0] > rect.x1 + 1
                           or bbox[3] < rect.y0 - 1 or bbox[1] > rect.y1 + 1)
                    tiny = size < 4.0
                    white = _is_near_white(color)
                    if off:
                        offpage_spans += 1
                    if tiny:
                        tiny_spans += 1
                    if white:
                        white_spans += 1
                    if off or tiny or white:
                        hidden_chunks.append(t)
                    else:
                        visible_chunks.append(t)
                        size_weight[round(size * 2) / 2] += len(t.strip())
                        col_starts[round(bbox[0] / 10) * 10] += 1

        # headings on this page
        for raw_line in ptext.splitlines():
            s = raw_line.strip()
            if refs_page is None and REFS_HEAD_RE.match(s):
                refs_page = pno + 1
            elif refs_page is not None and APPENDIX_HEAD_RE.match(s) \
                    and (pno + 1) >= refs_page:
                appendix_pages.append(pno + 1)

    paper.text = "\n".join(page_texts)
    paper.hidden_text = "\n".join(hidden_chunks)
    # A pipe-separated rendering reads at a glance: a concealed message shows as
    # prose, whereas figure labels and axis ticks show as short tokens.
    paper.hidden_display = " | ".join(c.strip() for c in hidden_chunks if c.strip())
    f["chars_visible"] = sum(len(c) for c in visible_chunks)
    f["chars_hidden"] = sum(len(c) for c in hidden_chunks)

    # ---- hidden-text gate -------------------------------------------------
    contents_flags = []
    for pno in range(doc.page_count):
        try:
            raw = doc[pno].read_contents()
        except Exception:
            continue
        if re.search(rb"\b3\s+Tr\b", raw):
            contents_flags.append(f"page {pno+1}: text render mode 3 (invisible)")
    try:
        ocgs = doc.get_ocgs() or {}
    except Exception:
        ocgs = {}
    if ocgs:
        contents_flags.append(f"{len(ocgs)} optional-content group(s) (layers) present")

    f["hidden_signals"] = {
        "tiny_spans_lt_4pt": tiny_spans,
        "near_white_spans": white_spans,
        "offpage_spans": offpage_spans,
        "content_stream_flags": contents_flags,
    }

    if paper.facts["chars_visible"] == 0:
        paper.add("FAIL", "pdf.text",
                  "No extractable text (image-only or odd encoding)",
                  "Check the PDF by hand; none of the text checks below ran.")

    hidden_hits = _injection_hits(paper.hidden_text)

    if tiny_spans or white_spans or offpage_spans or contents_flags:
        shown = paper.hidden_display[:1200]
        if len(paper.hidden_display) > 1200:
            shown += f" ... [+{len(paper.hidden_display) - 1200} more chars]"
        detail = ("hidden text, verbatim:\n  " + (shown or "(none extractable)")
                  + "\n\nsignals: "
                  + json.dumps(f["hidden_signals"], separators=(", ", "=")))
        # Small or white text is overwhelmingly figure labels, axis ticks and
        # chart annotations. Only call it a WARN when there is enough hidden
        # *prose* to be a concealed message; otherwise note it and move on.
        words = re.findall(r"[A-Za-z]{3,}", paper.hidden_text)
        prose_like = len(paper.hidden_text) >= 200 and len(words) >= 15
        sev = "WARN" if (prose_like or contents_flags or hidden_hits) else "NOTE"
        msg = ("Hidden/invisible text present "
               f"(tiny={tiny_spans}, white={white_spans}, offpage={offpage_spans})")
        if hidden_hits:
            msg += " - and it contains LLM-directed instructions, see below"
        elif sev == "NOTE":
            msg += " - short and label-like, most likely figure text"
        paper.add(sev, "safety.hidden_text", msg, detail)

    visible_hits = _injection_hits(paper.text)
    visible_only = [h for h in visible_hits if h not in hidden_hits]
    f["injection"] = {"in_hidden_text": hidden_hits, "in_visible_text": visible_only}

    if hidden_hits:
        paper.add("BLOCK", "safety.prompt_injection",
                  "LLM-directed instructions found in HIDDEN text - treat as an "
                  "attack on the review process",
                  "hidden text, verbatim:\n  " + paper.hidden_display[:1200] +
                  "\n\nmatches: " + "; ".join(hidden_hits[:12]) +
                  "\n\nDo NOT put this PDF in front of any model. Report to the "
                  "track chairs, review by hand. See ACM policy: "
                  + ACM_POLICY_URLS["peer_review"])
    if visible_only:
        paper.add("NOTE", "safety.injection_like_visible_text",
                  "Injection-style strings in VISIBLE body text - most likely the "
                  "paper's own subject matter at these venues",
                  "matches: " + "; ".join(visible_only[:12]))

    # ---- metadata ----------------------------------------------------------
    meta = f["metadata"]
    # `title` is the paper's own title and `subject` holds the ACM CCS
    # concepts - acmart writes both automatically and neither identifies the
    # authors. Only `author`, or an e-mail/ORCID hiding in any field, leaks.
    leaky = {k: v for k, v in meta.items() if k == "author" and v.strip()}
    for k, v in meta.items():
        if v and (EMAIL_RE.search(v) or ORCID_RE.search(v)):
            leaky[k] = v
    f["metadata_nonempty"] = leaky
    f["metadata_benign"] = {k: v for k, v in meta.items()
                            if k in ("title", "subject", "keywords") and v.strip()
                            and k not in leaky}

    # ---- body font size ----------------------------------------------------
    if size_weight:
        dom_guess = size_weight.most_common(1)[0][0]
    f["dominant_body_pt"] = dom_guess
    f["font_size_histogram"] = dict(size_weight.most_common(8))
    want = venue["body_pt"]
    if dom_guess is None:
        paper.add("WARN", "format.font_size", "Could not measure body font size")
    elif abs(dom_guess - want) > 0.35:
        paper.add("WARN", "format.font_size",
                  f"Dominant body text measures {dom_guess}pt, "
                  f"{venue['template']} requires {want}pt",
                  "PDF-reported sizes can be off when the PDF is scaled. Confirm "
                  "by hand before declining a paper on this.")
    else:
        paper.add("OK", "format.font_size",
                  f"Body text ~{dom_guess}pt, matches the required {want}pt")

    # ---- page budget -------------------------------------------------------
    f["refs_start_page"] = refs_page
    f["appendix_pages_after_refs"] = appendix_pages
    tech = _technical_pages(doc, refs_page, appendix_pages)
    f["technical_pages_estimate"] = tech
    limit = venue["tech_page_limit"]
    if refs_page is None:
        paper.add("WARN", "format.page_limit",
                  f"No 'References' heading found; {doc.page_count} total pages, "
                  f"limit is {limit} pages of technical content",
                  "Count the pages by hand.")
    elif tech > limit:
        paper.add("FAIL", "format.page_limit",
                  f"~{tech} pages of technical content (limit {limit}); "
                  f"technical text runs onto page {refs_page} above the "
                  f"references heading"
                  + (f", appendix after references on page(s) "
                     f"{appendix_pages}" if appendix_pages else ""),
                  "Heuristic - verify by opening the PDF. Both calls count "
                  "figures and appendices toward the limit and exclude only the "
                  "bibliography.")
    else:
        paper.add("OK", "format.page_limit",
                  f"~{tech} technical page(s) within the {limit}-page limit "
                  f"(references start page {refs_page}, {doc.page_count} total)")
    if appendix_pages:
        paper.add("NOTE", "format.appendix_after_refs",
                  f"Appendix material after the references on page(s) {appendix_pages}",
                  "Both calls include appendices in the page limit, so this "
                  "counts as technical content.")

    # ---- geometry / columns ------------------------------------------------
    r0 = doc[0].rect
    f["page_size_pt"] = [round(r0.width, 1), round(r0.height, 1)]
    if abs(r0.width - 612) > 6 or abs(r0.height - 792) > 6:
        paper.add("WARN", "format.page_size",
                  f"Page is {round(r0.width)}x{round(r0.height)}pt, acmart is "
                  f"US Letter 612x792pt",
                  "A4 (595x842) usually means the ACM template was not used.")
    clusters = _column_clusters(col_starts, r0.width)
    f["column_clusters"] = clusters
    if clusters and clusters != 2:
        paper.add("NOTE", "format.columns",
                  f"Text appears to use {clusters} column(s); "
                  f"{venue['template']} is two-column",
                  "Advisory only - title blocks and wide figures skew this.")

    f["reference_count_estimate"] = _count_refs(paper.text, refs_page, page_texts)
    doc.close()


def _injection_hits(text: str) -> list[str]:
    if not text:
        return []
    low = re.sub(r"\s+", " ", text.lower())
    hits = []
    for pat in INJECTION_PATTERNS:
        m = re.search(pat, low)
        if m:
            s = max(0, m.start() - 40)
            hits.append(f"/{pat}/ -> ...{low[s:m.end()+60]}...")
    return hits


def _technical_pages(doc, refs_page: int | None, appendix_pages: list[int]) -> int:
    """Pages of technical content: everything except pages that are only
    bibliography. Appendices count as technical per both calls."""
    if refs_page is None:
        return doc.page_count
    tech = refs_page - 1
    # does the references page also carry technical content above the heading?
    try:
        page = doc[refs_page - 1]
        h = page.rect.height
        for raw in page.get_text("dict").get("blocks", []):
            if raw.get("type") != 0:
                continue
            txt = " ".join(s.get("text", "")
                           for l in raw.get("lines", []) for s in l.get("spans", []))
            if REFS_HEAD_RE.match(txt.strip()) and raw["bbox"][1] > h * 0.18:
                tech += 1
                break
    except Exception:
        tech += 1
    return tech + len(set(appendix_pages))


def _column_clusters(col_starts: Counter, width: float) -> int | None:
    if not col_starts:
        return None
    main = [x for x, n in col_starts.items() if n >= max(col_starts.values()) * 0.25]
    main.sort()
    clusters, last = 0, -1e9
    for x in main:
        if x - last > width * 0.25:
            clusters += 1
            last = x
    return clusters


def _count_refs(text: str, refs_page: int | None, page_texts: list[str]) -> int | None:
    if refs_page is None:
        return None
    tail = "\n".join(page_texts[refs_page - 1:])
    n = len(re.findall(r"^\s*\[\s*\d+\s*\]", tail, re.M))
    if n:
        return n
    return len(re.findall(r"^\s*\d+\.\s+[A-Z]", tail, re.M)) or None


# --------------------------------------------------------------------------
# venue-specific text checks
# --------------------------------------------------------------------------


def front_matter(paper: Paper) -> str:
    """The author block: page-1 text above the Abstract heading.

    An earlier version clipped the top 40% of page 1, which swept in the
    abstract and the start of the introduction - so company names discussed in
    the prose ("AWS Lambda", "NVIDIA GPUs") read as affiliations.
    """
    try:
        doc = pymupdf.open(paper.path)
        page = doc[0]
        text = page.get_text("text")
        doc.close()
    except Exception:
        return "\n".join(paper.text.splitlines()[:25])

    out = []
    for line in text.splitlines():
        if re.match(r"^\s*(abstract|ABSTRACT|\d+\.?\s+introduction)\b",
                    line.strip(), re.I):
            break
        out.append(line)
        if len(out) > 40:          # a sane ceiling if no Abstract is found
            break
    return "\n".join(out)


def check_double_blind(paper: Paper) -> None:
    """WoAIS2: doubly-anonymous. The CFP says papers that reveal identity are
    'subject to immediate rejection', so every hit here is reviewer-visible."""
    t = paper.text
    f = paper.facts

    leaky = f.get("metadata_nonempty") or {}
    if leaky:
        paper.add("FAIL", "anon.pdf_metadata",
                  "PDF metadata carries identifying fields",
                  json.dumps(leaky, indent=2) +
                  "\nThe CFP requires names/affiliations absent from the paper; "
                  "metadata is the most-missed leak.")
    else:
        paper.add("OK", "anon.pdf_metadata", "PDF metadata has no author/title fields")

    name = paper.source_name
    if re.search(r"[A-Za-z]{3,}", re.sub(r"(?i)paper|submission|woais|middleware|"
                                         r"industry|track|final|camera|ready|draft|"
                                         r"\.pdf", "", name)):
        paper.add("WARN", "anon.filename",
                  f"Submission file name may identify the authors: {name!r}",
                  "CFP: \"The paper's file name must not identify the authors\".")
    else:
        paper.add("OK", "anon.filename", f"File name looks neutral: {name!r}")

    emails = sorted({e for e in EMAIL_RE.findall(t)
                     if not BOILERPLATE_EMAIL_RE.search(e)})
    if emails:
        paper.add("FAIL", "anon.emails", f"{len(emails)} e-mail address(es) in the text",
                  ", ".join(emails[:10]))
    orcids = sorted(set(ORCID_RE.findall(t)))
    if orcids:
        paper.add("FAIL", "anon.orcid", "ORCID identifier(s) present", ", ".join(orcids))

    acks = [m.group(0).strip() for m in ACK_HEAD_RE.finditer(t)]
    if acks:
        paper.add("FAIL", "anon.acknowledgments",
                  "Acknowledgments/thanks section present",
                  "CFP: no acknowledgment of people, projects, groups or funding in "
                  "the submitted version. Found: " + "; ".join(acks[:5]))
    funds = sorted({m.group(0).strip() for m in FUNDING_RE.finditer(t)})
    if funds:
        paper.add("FAIL", "anon.funding", "Funding/grant text present",
                  "; ".join(funds[:8]) +
                  "\nCFP: \"Funding sources must not be acknowledged anywhere\".")

    urls = URL_RE.findall(t)
    anon_ok = sorted({u for u in urls if ANON_HOST_RE.search(u)})
    # A link is only an anonymity risk if it plausibly belongs to the authors.
    # Papers cite third-party project repos constantly (containerd, openwhisk,
    # langchain, docs.github.com); those are citations, not leaks. Use the
    # surrounding wording to tell the two apart.
    owned, cited = [], []
    for u in sorted({u for u in urls
                     if DEANON_HOST_RE.search(u) and not ANON_HOST_RE.search(u)}):
        i = t.find(u)
        ctx = t[max(0, i - 140):i + len(u) + 60].lower() if i >= 0 else ""
        mine = re.search(r"\b(our|we\s+(?:release|provide|publish|open[- ]source|"
                         r"make\s+available|share)|artifact(s)?\s+(?:is|are|at)"
                         r"|replication\s+package|available\s+at)\b", ctx)
        personal = re.search(r"~[a-z]{2,}/|people\.|/u/|\.github\.io/~", u, re.I)
        (owned if (mine or personal) else cited).append(u)
    if owned:
        paper.add("WARN", "anon.links",
                  f"{len(owned)} link(s) the authors appear to present as their "
                  f"own",
                  "\n".join(owned[:10]) +
                  "\nCFP suggests https://anonymous.4open.science for artifacts.")
    if cited:
        paper.add("NOTE", "anon.links_cited",
                  f"{len(cited)} third-party link(s), read as citations rather "
                  f"than author artifacts",
                  "\n".join(cited[:10]) +
                  "\nSpot-check that none is actually the authors' own repo.")
    if anon_ok:
        paper.add("OK", "anon.links",
                  f"Uses anonymized artifact link(s): {', '.join(anon_ok[:3])}")

    selfref = sorted({m.group(0).strip() for m in SELF_REF_RE.finditer(t)})
    if selfref:
        paper.add("WARN", "anon.self_reference",
                  "First-person self-reference to prior work",
                  "; ".join(selfref[:8]) +
                  "\nCFP: refer to your own past work in the third person.")
    ext = sorted({m.group(0).strip() for m in EXT_VERSION_RE.finditer(t)})
    if ext:
        paper.add("WARN", "anon.extended_version",
                  "Mentions an extended version / tech report / arXiv posting",
                  "; ".join(ext[:8]) +
                  "\nCFP: extended versions and downloadable-version URLs must not "
                  "be referenced.")

    fm = front_matter(paper)
    aff = [l.strip() for l in fm.splitlines()
           if ACADEMIC_RE.search(l) or INDUSTRY_RE.search(l)]
    if aff:
        paper.add("FAIL", "anon.affiliation_on_title_page",
                  "Affiliation-looking lines on the title page",
                  "\n".join(aff[:6]))


def check_single_blind_industry(paper: Paper) -> None:
    """Middleware Industrial Track: single-blind, names REQUIRED, title suffix
    required, at least one industry author, no NDA."""
    t = paper.text
    fm = front_matter(paper)

    # title suffix
    head = " ".join(fm.split())[:400]
    if "(industry track)" in head.lower():
        paper.add("OK", "format.title_suffix",
                  "Title carries the required \"(Industry Track)\" suffix")
    else:
        paper.add("FAIL", "format.title_suffix",
                  "Title does not appear to end with \"(Industry Track)\"",
                  "CFP: \"Authors are required to add '(Industry Track)' at the end "
                  "of their paper title.\" Title region read as:\n" + head[:300])

    # names/affiliations must be present
    emails = sorted(set(EMAIL_RE.findall(fm)))
    aff_lines = [l.strip() for l in fm.splitlines()
                 if ACADEMIC_RE.search(l) or INDUSTRY_RE.search(l)]
    paper.facts["front_matter"] = fm[:1200]
    paper.facts["affiliation_lines"] = aff_lines[:10]
    if not aff_lines and not emails:
        paper.add("FAIL", "format.authors_present",
                  "No author affiliations or e-mails found on the title page",
                  "Reviewing is single-blind; the CFP requires author names and "
                  "affiliations. Could also be an anonymized submission by mistake.")
    else:
        paper.add("OK", "format.authors_present",
                  f"{len(aff_lines)} affiliation-looking line(s) on the title page")

    # industry author
    industry = [l for l in aff_lines if INDUSTRY_RE.search(l)]
    academic = [l for l in aff_lines if ACADEMIC_RE.search(l)
                and not INDUSTRY_RE.search(l)]
    paper.facts["industry_affiliations"] = industry[:10]
    paper.facts["academic_affiliations"] = academic[:10]
    if industry:
        paper.add("OK", "scope.industry_author",
                  f"Industry-looking affiliation present: {industry[0][:80]!r}",
                  "Confirm by eye - the CFP requires at least one author from "
                  "industry.")
    elif academic:
        paper.add("WARN", "scope.industry_author",
                  "All detected affiliations look academic - the CFP requires at "
                  "least one author from industry",
                  "\n".join(academic[:6]) +
                  "\nHeuristic. A research lab name may not match the pattern list; "
                  "confirm before acting.")
    else:
        paper.add("WARN", "scope.industry_author",
                  "Could not classify any affiliation; check the industry-author "
                  "requirement by hand")

    if NDA_RE.search(t):
        paper.add("WARN", "policy.nda",
                  "Mentions a non-disclosure agreement",
                  "CFP: \"Papers accompanied by nondisclosure agreement forms will "
                  "not be considered.\" Check whether this is a submission condition "
                  "or just discussion.")

    ev = sorted({m.group(0).strip() for m in DEPLOY_EVIDENCE_RE.finditer(t)})
    paper.facts["deployment_evidence_markers"] = ev[:25]
    if len(ev) >= 4:
        paper.add("OK", "scope.deployment_evidence",
                  f"{len(ev)} marker(s) of real-world deployment/measurement",
                  "; ".join(ev[:12]))
    else:
        paper.add("NOTE", "scope.deployment_evidence",
                  f"Only {len(ev)} marker(s) of production deployment or measurement",
                  "; ".join(ev) +
                  "\nThe Industrial Track exists to \"emphasize the practical issues, "
                  "observations, and measurements of 'real-world' systems\". Judge "
                  "this yourself - a low count is a prompt to look, not a verdict.")


def check_common(paper: Paper, venue: dict) -> None:
    if paper.facts.get("encrypted"):
        paper.add("WARN", "pdf.encrypted", "PDF is encrypted/password-protected")
    n = paper.facts.get("reference_count_estimate")
    if n is not None and n < 8:
        paper.add("NOTE", "quality.references",
                  f"Only ~{n} reference(s) detected",
                  "Both calls ask for appropriate comparison to related work.")
    if venue["short_formats_ok"]:
        pages = paper.facts.get("pages", 0)
        if pages and pages <= 5:
            paper.add("NOTE", "format.short_submission",
                      f"{pages}-page submission - WoAIS2 explicitly accepts extended "
                      "abstracts (<=5pp), posters and position papers",
                      "Judge it as a short format, not as a thin full paper.")


def write_redacted(paper: Paper, out_dir: Path) -> Path:
    """De-identified text, per the ACM clause allowing third-party tools once
    'parts that would identify the submission, author identities, reviewer
    identity, or other confidential content' are removed."""
    t = paper.text
    t = EMAIL_RE.sub("[EMAIL-REDACTED]", t)
    t = ORCID_RE.sub("[ORCID-REDACTED]", t)
    t = URL_RE.sub(lambda m: m.group(0) if ANON_HOST_RE.search(m.group(0))
                   else "[URL-REDACTED]", t)
    lines = t.splitlines()
    # drop the front matter block (title/authors/affiliations)
    body_start = 0
    for i, l in enumerate(lines[:60]):
        if re.match(r"^\s*(abstract|1\.?\s+introduction)\b", l.strip(), re.I):
            body_start = i
            break
    out = out_dir / "redacted.txt"
    out.write_text("[front matter removed]\n" + "\n".join(lines[body_start:]),
                   encoding="utf-8")
    return out


# --------------------------------------------------------------------------
# batch-level checks
# --------------------------------------------------------------------------


def shingles(text: str, k: int = 8) -> set[int]:
    words = re.findall(r"[a-z]{2,}", text.lower())
    return {hash(" ".join(words[i:i + k])) for i in range(max(0, len(words) - k + 1))}


def duplicate_pairs(papers: list[Paper], thresh: float = 0.30) -> list[tuple]:
    sig = {p.paper_id: shingles(p.text) for p in papers if len(p.text) > 2000}
    out = []
    ids = sorted(sig)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            inter = len(sig[a] & sig[b])
            if not inter:
                continue
            j = inter / len(sig[a] | sig[b])
            if j >= thresh:
                out.append((a, b, round(j, 3)))
    return sorted(out, key=lambda r: -r[2])


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def paper_report(paper: Paper, venue: dict) -> str:
    L = [f"# Paper {paper.paper_id} - mechanical checks",
         "",
         f"* venue: **{venue['label']}**",
         f"* file: `{paper.source_name}`",
         f"* worst finding: **{paper.worst}**",
         "",
         "> Every line below is a heuristic on the PDF. Confirm before acting. "
         "None of it is a review.",
         ""]
    for sev in ("BLOCK", "FAIL", "WARN", "NOTE", "OK"):
        group = [f for f in paper.findings if f.sev == sev]
        if not group:
            continue
        L.append(f"## {sev}")
        for f in group:
            L.append(f"* **{f.check}** - {f.message}")
            if f.detail:
                L.append("")
                L.append("  ```")
                for line in f.detail.splitlines()[:40]:
                    L.append("  " + line)
                L.append("  ```")
        L.append("")
    L += ["## Measured facts", "", "```json",
          json.dumps({k: v for k, v in paper.facts.items()
                      if k not in ("front_matter",)}, indent=2, default=str),
          "```", ""]
    return "\n".join(L)


def summary_report(papers: list[Paper], venue: dict, dups: list[tuple]) -> str:
    L = [f"# {venue['label']} - batch pre-review summary", "",
         f"* papers: **{len(papers)}**",
         f"* CFP: {venue['cfp']}",
         f"* HotCRP: {venue['hotcrp']}",
         f"* reviewing: {venue['blind']}-blind, at least "
         f"{venue['reviewers_min']} PC reviews per paper",
         f"* ACM policy: {ACM_POLICY_URLS['peer_review']}",
         "", "## Triage order (worst first)", "",
         "| paper | worst | pages | tech pp | body pt | blocks/fails | file |",
         "|---|---|---|---|---|---|---|"]
    for p in sorted(papers, key=lambda q: (SEV_ORDER[q.worst], q.paper_id)):
        bad = "; ".join(f.check for f in p.findings if f.sev in ("BLOCK", "FAIL")) or "-"
        L.append(f"| {p.paper_id} | **{p.worst}** | {p.facts.get('pages','?')} | "
                 f"{p.facts.get('technical_pages_estimate','?')} | "
                 f"{p.facts.get('dominant_body_pt','?')} | {bad} | "
                 f"`{p.source_name}` |")
    L.append("")

    blocked = [p for p in papers if p.worst == "BLOCK"]
    if blocked:
        L += ["## STOP - do not show these to any model", ""]
        for p in blocked:
            L.append(f"* paper {p.paper_id} (`{p.source_name}`): hidden "
                     f"LLM-directed instructions. Report to the track chairs and "
                     f"review by hand.")
        L.append("")

    if dups:
        L += ["## Possible duplicate / overlapping submissions", "",
              "| a | b | shingle Jaccard |", "|---|---|---|"]
        for a, b, j in dups:
            L.append(f"| {a} | {b} | {j} |")
        L += ["", "Overlap is not proof. Both calls prohibit simultaneous "
              "submission of the same work; take it to the chairs, not to the "
              "authors.", ""]

    counts: Counter = Counter()
    for p in papers:
        for f in p.findings:
            if f.sev in ("BLOCK", "FAIL", "WARN"):
                counts[f.check] += 1
    if counts:
        L += ["## Most common issues across the batch", "",
              "| check | papers |", "|---|---|"]
        for k, v in counts.most_common():
            L.append(f"| {k} | {v} |")
        L.append("")
    return "\n".join(L)


def write_csv(papers: list[Paper], path: Path) -> None:
    checks = sorted({f.check for p in papers for f in p.findings})
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["paper_id", "file", "worst", "pages", "technical_pages",
                    "body_pt", "refs_start_page"] + checks)
        for p in sorted(papers, key=lambda q: q.paper_id):
            by = {f.check: f.sev for f in p.findings}
            w.writerow([p.paper_id, p.source_name, p.worst,
                        p.facts.get("pages", ""),
                        p.facts.get("technical_pages_estimate", ""),
                        p.facts.get("dominant_body_pt", ""),
                        p.facts.get("refs_start_page", "")]
                       + [by.get(c, "") for c in checks])


# --------------------------------------------------------------------------
# stdout reporting
# --------------------------------------------------------------------------

SEV_FLAG = {"BLOCK": "!!", "FAIL": " X", "WARN": " ~", "NOTE": " .", "OK": " +"}


def print_paper(paper: Paper, show_ok: bool = False, quiet: bool = False) -> None:
    """One block per paper: the headline, then every finding behind it."""
    flag = SEV_FLAG[paper.worst]
    print(f" {flag} paper {paper.paper_id:>6}  {paper.worst:<5}  "
          f"{paper.facts.get('pages','?')}pp  {paper.source_name}")
    if quiet:
        return

    # Checks whose whole purpose is to put evidence in front of a human are
    # shown even when they pass, and always with their detail.
    ALWAYS = ("safety.", "scope.deployment_evidence", "scope.industry_author")

    def keep(x):
        return show_ok or x.sev != "OK" or x.check.startswith(ALWAYS)

    for f in sorted((x for x in paper.findings if keep(x)),
                    key=lambda x: SEV_ORDER[x.sev]):
        print(f"        {SEV_FLAG[f.sev]} {f.check:<34} {f.message}")
        verbose = (f.sev in ("BLOCK", "FAIL") or f.check.startswith(ALWAYS))
        if verbose and f.detail:
            limit = 12 if f.check.startswith(ALWAYS) else 4
            for line in f.detail.splitlines()[:limit]:
                if line.strip():
                    print(f"              {line.strip()[:160]}")

    facts = paper.facts
    bits = []
    if facts.get("technical_pages_estimate") is not None:
        bits.append(f"technical pp ~{facts['technical_pages_estimate']}")
    if facts.get("refs_start_page"):
        bits.append(f"refs p{facts['refs_start_page']}")
    if facts.get("dominant_body_pt"):
        bits.append(f"body {facts['dominant_body_pt']}pt")
    if facts.get("reference_count_estimate"):
        bits.append(f"{facts['reference_count_estimate']} refs")
    if bits:
        print("           (" + ", ".join(bits) + ")")
    print()


def print_check_summary(papers: list[Paper], dups: list[tuple]) -> None:
    """Per-check roll-up across the batch, so patterns are visible at a glance."""
    print("=" * 72)
    print("CHECK SUMMARY")
    print("=" * 72)
    print()

    worst = Counter(p.worst for p in papers)
    line = "  ".join(f"{SEV_FLAG[s].strip()} {s} {worst[s]}"
                     for s in ("BLOCK", "FAIL", "WARN", "NOTE", "OK") if worst[s])
    print(f"  {len(papers)} paper(s):  {line}")
    print()

    by_check = defaultdict(Counter)
    who = defaultdict(list)
    for p in papers:
        for f in p.findings:
            by_check[f.check][f.sev] += 1
            who[(f.check, f.sev)].append(p.paper_id)

    def rank(item):
        chk, sevs = item
        return (min(SEV_ORDER[s] for s in sevs), -sum(sevs.values()), chk)

    print(f"  {'check':<34} {'worst':<6} {'n':>3}  {'breakdown':<22} papers")
    print("  " + "-" * 34 + " " + "-" * 6 + " " + "-" * 3 + "  "
          + "-" * 22 + " " + "-" * 20)
    for chk, sevs in sorted(by_check.items(), key=rank):
        sev = min(sevs, key=lambda s: SEV_ORDER[s])
        ids = who[(chk, sev)]            # papers at the worst severity only
        shown = ",".join(ids[:6]) + ("..." if len(ids) > 6 else "")
        breakdown = ", ".join(f"{s} {sevs[s]}" for s in
                              ("BLOCK", "FAIL", "WARN", "NOTE", "OK") if sevs[s])
        print(f"  {chk:<34} {sev:<6} {len(ids):>3}  {breakdown:<22} {shown}")

    if dups:
        print()
        print("  possible duplicate/overlapping pairs:")
        for a, b, j in dups[:8]:
            print(f"    {a} ~ {b}   jaccard {j}")

    print()
    print("  Every line is a heuristic on PDF internals. Open the PDF before "
          "acting on any of it.")


# --------------------------------------------------------------------------
# check command
# --------------------------------------------------------------------------


def check(args: argparse.Namespace) -> int:
    require_pymupdf()
    venue = VENUE
    out_root = Path(args.out or f"out/{VENUE_KEY}").resolve()
    (out_root / "papers").mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="paperchecks-"))
    try:
        items: list[tuple[str, Path]] = []
        descs: list[str] = []

        for raw in args.targets:
            t = Path(raw).expanduser()
            if not t.exists():
                sys.exit(f"error: no such file or directory: {raw}")
            if t.is_dir():
                found = collect_from_dir(t)
                descs.append(f"dir {raw} ({len(found)} pdf)")
            elif t.suffix.lower() == ".zip":
                found = collect_from_zip(t, tmp)
                descs.append(f"zip {raw} ({len(found)} pdf)")
            elif t.suffix.lower() == ".pdf":
                found = [(t.name, t)]
                descs.append(f"pdf {raw}")
            else:
                sys.exit(f"error: {raw} is not a .pdf, a .zip or a directory")
            items.extend(found)

        if args.hotcrp:
            tok = os.environ.get(args.token_env or "HOTCRP_TOKEN", "")
            if not tok:
                sys.exit(f"error: set ${args.token_env or 'HOTCRP_TOKEN'} to a "
                         "HotCRP bearer token (Account > Developer), or just "
                         "pass the papers zip instead.")
            found = collect_from_hotcrp(args.hotcrp, args.query, tok, tmp)
            items.extend(found)
            descs.append(f"hotcrp {args.hotcrp} q={args.query!r} "
                         f"({len(found)} pdf)")

        if not items:
            sys.exit("error: nothing to check. Pass one or more PDFs, a papers "
                     "zip, or a directory - or --hotcrp to fetch from HotCRP.")

        src_desc = "; ".join(descs)
        print(f"venue   : {venue['label']}")
        print(f"source  : {src_desc}")
        print(f"papers  : {len(items)}")
        print(f"out     : {out_root}\n")

        papers: list[Paper] = []
        for pid, name, path in unique_ids(items):
            paper = Paper(paper_id=pid, source_name=name, path=path)
            inspect_pdf(paper, venue)
            if paper.text or paper.facts.get("pages"):
                if venue["blind"] == "double":
                    check_double_blind(paper)
                else:
                    check_single_blind_industry(paper)
                check_common(paper, venue)
            papers.append(paper)

            pdir = out_root / "papers" / pid
            pdir.mkdir(parents=True, exist_ok=True)
            (pdir / "text.txt").write_text(paper.text, encoding="utf-8")
            if paper.hidden_text.strip():
                (pdir / "hidden_text.txt").write_text(paper.hidden_text,
                                                      encoding="utf-8")
            (pdir / "checks.json").write_text(json.dumps(
                {"paper_id": pid, "file": name, "venue": VENUE_KEY,
                 "worst": paper.worst,
                 "findings": [f.as_dict() for f in paper.findings],
                 "facts": paper.facts}, indent=2, default=str), encoding="utf-8")
            (pdir / "report.md").write_text(paper_report(paper, venue),
                                            encoding="utf-8")
            if args.redact and paper.worst != "BLOCK":
                write_redacted(paper, pdir)

            print_paper(paper, show_ok=args.show_ok, quiet=args.quiet)

        dups = [] if args.no_similarity else duplicate_pairs(papers)
        (out_root / "summary.md").write_text(summary_report(papers, venue, dups),
                                             encoding="utf-8")
        write_csv(papers, out_root / "summary.csv")
        gate = {"venue": VENUE_KEY, "papers": len(papers),
                "blocked": [p.paper_id for p in papers if p.worst == "BLOCK"],
                "failed": [p.paper_id for p in papers if p.worst == "FAIL"],
                "duplicate_pairs": dups}
        (out_root / "gate.json").write_text(json.dumps(gate, indent=2),
                                            encoding="utf-8")

        if not args.quiet:
            print_check_summary(papers, dups)

        print(f"\nwrote {out_root}/summary.md, summary.csv, gate.json")
        if gate["blocked"]:
            print(f"\n!! BLOCKED (hidden LLM instructions): "
                  f"{', '.join(gate['blocked'])}")
            print("   Do not put these PDFs in front of a model. Tell the chairs.")
            return 2
        if gate["failed"]:
            print(f"\n X format/anonymity failures: {', '.join(gate['failed'])}")
            return 1
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------


CHECK_EPILOG = """examples:
  paper_checks.py paper15.pdf                      one PDF
  paper_checks.py *.pdf                            several
  paper_checks.py woais26-papers.zip                 a HotCRP papers zip
  paper_checks.py ./downloads --redact             a directory
  paper_checks.py a.pdf b.zip ./more               any mix
  paper_checks.py --hotcrp https://woais26.hotcrp.com --query re:me     fetch from HotCRP

  paper_checks.py --version                        version, commit, sha256

other commands (only relevant if you involve an AI agent):
  paper_checks.py probe                            credential, plan, hygiene
  paper_checks.py zdr [--training-off]             retention + ACM verdict
"""


def _add_check_args(q: argparse.ArgumentParser) -> None:
    q.add_argument("targets", nargs="*", metavar="TARGET",
                   help="any mix of PDF files, papers zips and directories")
    q.add_argument("--hotcrp", metavar="URL",
                   help="also fetch submissions from this HotCRP site "
                        "(needs a bearer token)")
    q.add_argument("--query", default="re:me",
                   help="HotCRP search for --hotcrp (default: re:me)")
    q.add_argument("--token-env", default="HOTCRP_TOKEN",
                   help="env var holding the HotCRP bearer token")
    q.add_argument("--out", metavar="DIR",
                   help="output directory (default out/woais26)")
    q.add_argument("--redact", action="store_true",
                   help="also write redacted.txt per paper (de-identified text, "
                        "for ACM-compliant use of third-party tools)")
    q.add_argument("--no-similarity", action="store_true",
                   help="skip cross-paper duplicate detection")
    q.add_argument("--quiet", "-q", action="store_true",
                   help="one line per paper: no per-finding detail and no "
                        "check summary")
    q.add_argument("--show-ok", action="store_true",
                   help="also print the checks that passed")
    q.set_defaults(func=check)


def main() -> int:
    desc = ("Quick PDF validation of WoAIS2 2026 (Second Intl. Workshop on AI and Serverless) submissions against the call for "
            "papers. No AI, no network and no account needed. Validating PDFs is "
            "the default action, so TARGETs can be given with no command.")
    verbs = {"probe", "zdr", "check"}
    argv = sys.argv[1:]

    if any(a in ("-v", "-V", "--version") for a in argv):
        print(version_info())
        return 0

    if argv and argv[0] in verbs:
        ap = argparse.ArgumentParser(prog="paper_checks.py", description=desc,
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=CHECK_EPILOG)
        sub = ap.add_subparsers(dest="cmd")

        pp = sub.add_parser("probe", help="report retention/model posture, only "
                                         "relevant if you involve an AI agent")
        pp.add_argument("--live", action="store_true",
                        help="send a 2-token probe to a Covered Model to test "
                             "whether this org has 30-day retention enabled")
        pp.set_defaults(func=probe)

        zp = sub.add_parser("zdr", help="is zero data retention in effect for "
                                        "this credential, and how do we know?")
        zp.add_argument("--offline", action="store_true",
                        help="skip the one live probe; report policy facts only")
        zp.add_argument("--training-off", action="store_true",
                        help="assert that you have verified the consumer "
                             "training toggle is OFF (via /privacy-settings or "
                             "the web settings page), so the attestation is "
                             "complete")
        zp.set_defaults(func=zdr)

        _add_check_args(sub.add_parser(
            "check", help="validate submission PDFs (this is the default, so "
                          "the word `check` is optional)"))
        args = ap.parse_args(argv)
    else:
        # No verb given: validating PDFs is the default action.
        ap = argparse.ArgumentParser(prog="paper_checks.py", description=desc,
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=CHECK_EPILOG)
        _add_check_args(ap)
        args = ap.parse_args(argv)

    if getattr(args, "func", None) is None:
        args.func = check

    try:
        return args.func(args)
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover
        print(f"error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
