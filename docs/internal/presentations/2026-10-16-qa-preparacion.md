# Q&A prep — Real Estate Lead Agent, 16 October 2026

Preparation for the questions after the talk. It follows the deck `Real Estate Lead Agent.pptx` (13 slides) and
goes beyond it: architecture, what is deterministic and what is agentic, decisions, how the agent was evaluated,
cost, limits. The answers are in English because the talk is; each one is written to be said out loud in
20–40 seconds, with the decision (`DT-n`, in [`decisions.md`](../../DECISIONS/decisions.md)) or the document to
open if they push further.

Everything describes `main` as of 15 October 2026.

## 0. Before answering

**The one sentence to come back to:** *"The AI understands and writes. Code supplies the facts and decides."*
Most hard questions (why no framework, why no RAG, why no LLM judge, why it can't invent a price, why it can't
discriminate) are answered by that sentence.

**Three rules:**

1. **Say the limit before they find it.** English only, 12 demo listings, one conversation at a time, local
   only, login is a secret link, the held-out set is small and ours. Saying it first makes every other number
   believable.
2. **Don't round "0" up to "never".** "Zero invented facts on 37 unseen questions" is a low rate, not a
   guarantee.
3. **Separate the three levels of evidence:** *seen live* (Oct 8 evaluation, Oct 9 live demo), *checked on the
   running stack*, *covered by tests only* (e.g. the business view, reminders, weekly report).

### Numbers cheat sheet

| What | Value | Where it comes from |
| :--- | :--- | :--- |
| Replies that met every check | **99.6%** (284/285) | Oct 8, 12 demo scenarios × 5 runs = 60 conversations, real Bedrock |
| Expected catalog tool used | 100% | same run |
| Rewritten once / fell back to safe answer | 6% / 0.4% (1 reply) | same run |
| Baseline two days earlier | 93% OK, 24% rewritten, 4% fallback | Oct 6, 8 scenarios × 5, 180 turns |
| Reply time, median / p95 | 6.3 s / 9.8 s (under 2 s when code answers: slots, handoff, language notice) | Oct 8 |
| Held-out set P (never seen while tuning) | 37 turns, **0 serious errors**, 0 degraded, 35/37 OK (the 2 misses were badly written expectations) | Oct 8, run once, nothing changed before or after |
| Fair-housing pairs with identical score | 54 / 54 (total, all five dimensions and band) | CI gold set, deterministic |
| Classifier gold set | 93 messages, per-field accuracy; no field may drop below baseline | `harness/gold/` |
| Model calls per reply | ~2.4, ~6,100 tokens, 96% of them input | Phoenix traces, `tokens.py` |
| Cost | < 2¢ per reply, ~8¢ per conversation, ~US$5 for the whole 60-conversation run | Bedrock list prices |
| Bedrock quota | 10 calls/min per model → ~4 replies/min across all users | AWS account limit |
| Automated tests | 1,300+ on every change | CI |
| Backlog | 76 stories: 74 done, 2 moved to future work (B-14 voice notes, C-5 FAQ) — 214/220 points | backlog |
| Design decisions recorded | 71 (DT-1 … DT-71) | `decisions.md` |
| Live demo, Oct 9 | 10 messages in 5 min, 0 invented facts, 0 rewrites, ~13¢ | demo log |

---

## 1. Product and business (slides 2–3)

**Q: Who is the user, and what problem does it actually solve?**
Two users. The buyer or tenant, who writes on Telegram at any hour and gets an answer in seconds with real
catalog data. And the agency's advisors, who stop spending the day on people who were never going to buy: they
get only the leads that score WARM or above, with a summary instead of the whole chat.

**Q: What does "qualified" mean here?**
A 0–100 BANT-E score: Budget, Authority, Need, Timing, Eligibility, 20 points each. COLD up to 40, WARM 41–70,
HOT 71 and up. From WARM the advisor is alerted. The important part: the AI only detects what the person said;
a fixed Python function computes the number. Every dimension stores its own reason, so the score is auditable.

**Q: How is each dimension scored?**
- **Budget:** 20 if the lead declared any budget, 0 if not.
- **Authority:** 20 if they said how the decision is made ("I decide", "with my partner"), 0 if not. We store
  *that* it was said, not *with whom* — that's a fair-housing choice (DT-38, DT-40).
- **Need:** from the number of matching properties the search tool returned: 0 / 10 / 20. Not from the LLM.
- **Timing:** immediate 20, within 3 months 12, exploring 5, not said 0.
- **Eligibility:** 20 if the `check_requirements` tool says their down payment / income meets the property's
  requirements. Again from a tool, not the LLM.

**Q: Where do those weights come from? Are they validated?**
Honestly: they're a starting choice, not fitted to real data — we have no real agency data. They're isolated in
one pure function, so an agency could retune them without touching the contract or the agent. What *is*
validated is that the formula behaves consistently and that protected characteristics never move it.

**Q: Budget is 20 for any value? So "$10" scores the same as "$500,000"?**
Yes, Budget measures whether a budget was declared, not whether it fits. Fit is covered by Need (are there
matching listings in that budget) and Eligibility (does the lead meet the requirements). It's a known
simplification, documented in the design.

**Q: How do you know the bot actually sells?**
Attribution rule (DT-41): a closed deal counts for the bot if the lead touched that property with the bot in
the 60 days before. The manager's business view and the optional weekly Telegram report show deals attributed
to the bot, days on market, stale inventory and unmet demand. Caveat: that view is covered by tests and shown
with six months of synthetic history, not with real sales.

**Q: What happens when the lead wants a person?**
A human is reachable from every state. Three paths to `HUMAN_HANDOFF` (DT-16, DT-65): the lead asks for one, the
request is outside what the agent may do (discounts, legal advice…), or three unresolved turns in a row where
the last message shows frustration. The advisor gets a summary — what they want, objections, next step —
written by Sonnet and checked like any other reply (DT-46).

**Q: Why Telegram and not WhatsApp?**
Telegram's long polling lets everything run locally with no public URL or business verification (DT-4).
Channels sit behind a canonical event, so WhatsApp would be one more adapter without touching the agent
(DT-20). It's the first item in next steps.

**Q: Why English only, if the listings are in Colombia?**
A product decision for this presentation (DT-55): everything a person or the LLM reads is in English, prices in
USD. A Spanish message gets a fixed English notice from a deterministic, local language check — before the LLM,
the score or the state machine. Supporting Spanish means translating prompts, fixed texts and the guardrail
recognisers and re-running the gold sets; the gold sets were originally written in Spanish, so it's work, not
redesign.

---

## 2. Architecture (slide 5)

**Q: Walk me through the components.**
Five containers on one Docker Compose:
- **core** — the agent: Telegram bot, input guardrails, classifier, state machine, score, reply, output
  guardrails, and an embedded scheduler.
- **catalog** — our own MCP server with 6 tools, plus a plain HTTP API for the advisors' calendar.
- **panel** — web app for advisors and the manager, plus the public listings page.
- **PostgreSQL** (with pgvector) — the single source of truth: chats, scores, visits, deals, audit log, jobs.
- **Phoenix** — OpenTelemetry traces, one per turn.

The only thing outside the machine is Claude on AWS Bedrock.

**Q: Why is the calendar HTTP and not an MCP tool?**
Because booking is decided by code, never by the model (DT-32, DT-39). The model never gets a "book visit"
tool; code offers three numbered slots from the calendar and books the one the lead picks. MCP is for things we
*want* the model to decide to look up.

**Q: Why no Redis, no queue, no Kubernetes?**
At this size they add moving parts without adding capability. The scheduler runs inside core with its jobs in
PostgreSQL (DT-3, DT-44): visit reminders the day before, a follow-up after 24 h of silence, then NURTURE and
LOST. One database also means one transaction per turn.

**Q: What does "one transaction per turn" buy you?**
Each turn locks the conversation row (`SELECT … FOR UPDATE`), and saves the new state, the score, the audit
entries and the messages together (DT-1, DT-12). Two messages from the same lead are processed one after the
other, and a crash mid-turn can't leave a score that disagrees with the state.

**Q: How do the services talk to each other?**
core → catalog over MCP (streamable HTTP, DT-2) for property data, and HTTP for the calendar. Every MCP tool has
a JSON Schema contract for input and output in `contracts/mcp/`, with valid and invalid examples tested. The
client discovers tools at runtime instead of hard-coding names (DT-13).

**Q: Is it secure to run?**
Own containers are hardened: read-only filesystem, all capabilities dropped, `no-new-privileges`, only `/tmp`
writable. Every port is published on `127.0.0.1` by default. The panel uses a secret per-advisor link; anything
that isn't yours returns a uniform 404 so the response doesn't reveal whether a token exists. That's fine for a
local prototype with three advisors and **not** production authentication — we say so in DT-5.

---

## 3. What is deterministic and what is agentic (slide 6)

**Q: What exactly does the AI do, and what does code do?**
Six steps per message; two are AI, four are code.

| Step | Who | What |
| :--- | :--- | :--- |
| 1. Input check | Code | Language, opt-out, unrequested personal data, jailbreak, off-topic, protected topics |
| 2. Classify | Haiku 4.5 | Structured JSON: intent, budget, who decides, urgency, wants a visit and when, which shown property they mean, frustration, asks for something not in the catalog… |
| 3. Write | Sonnet 4.5 | Decides which catalog tools to call (up to 3 rounds) and writes the reply with **placeholders**, not data |
| 4. Fill the data | Code | Replaces `{prop_00231.price}` with $185,000 from the tool result |
| 5. Output check | Code | Every fact traced to a tool; no promises; no exact address before qualifying; fair housing. Fail → rewrite once → else safe answer offering an advisor |
| 6. Score + state | Code | Pure function for the score; table-driven state machine; saved in one transaction |

The state machine route is picked by code from the classifier's fields — the model doesn't choose the next
state.

**Q: Then is this really an "agent"? Isn't it a pipeline with an LLM inside?**
It's an agent where it matters: Sonnet decides which tools to call, with what arguments, how many rounds, and
how to phrase the answer — that's a real tool-use loop over an MCP server. What we deliberately *didn't* give
the model is authority over things the agency has to trust: the facts, the score, the booking, the exact
address and the handoff. We'd call it a bounded agent: autonomy in understanding and retrieval, code in
decisions with business or legal consequences.

**Q: Why the placeholders? Isn't that over-engineering?**
It came from evidence, not from taste (DT-60). Before, the output check read the model's prose and compared
every number literally against tool results. Every new phrasing produced a new exception, and there was one
error it could never catch: the *real* price of a *different* property passes a literal check. With
placeholders the model can't write a price at all; it can only point at a field of a property a tool returned.
A placeholder that can't be filled, or a number written outside one, blocks the reply.

**Q: What if the model writes a number by hand anyway?**
It's blocked unless it's a number the lead said, a list numbering, or a value of the property that line is
about. Blocked means: rewrite once with an explanation of what failed (sent as system text, the lead never sees
it); if it fails again, a fixed safe answer that offers an advisor (DT-23).

**Q: How do you stop the model from revealing the exact address?**
Two layers. Structural: `lead_qualified` is removed from the tool schema the model sees and injected by code, so
the model can't ask for the exact address; until the lead qualifies the catalog returns `null` and the
placeholder can't be filled. And an output check that detects the *shape* of an exact address before
qualification (DT-19, DT-25).

**Q: Why use two models?**
Haiku is cheaper and fast for a closed-taxonomy JSON classification. Sonnet writes and calls tools. We tried
Haiku for the conversation: it didn't follow several prompt rules — it repeated prices from earlier turns (which
the factuality check blocks) and didn't retry an empty search. A third Sonnet call writes the handoff summary.

**Q: Temperature?**
0 for all three calls. The chat model was at 0.3; a regression couldn't be repeated because the same question
came out differently each run (DT-62). Even at 0 the classifier flips 2–3 answers out of 80 between runs, so we
treat a single-message drop as possible noise and compare it against the previous prompt.

---

## 4. Guardrails and fair housing (slide 7)

**Q: List the guardrails.**
- **Input (deterministic, before the LLM — DT-15):** opt-out (`/stop`); unrequested personal data like ID or
  card numbers removed before storing or sending to the model; jailbreak / prompt-extraction treated as a normal
  domain question and stored as `[message omitted]`; off-topic or formal legal/tax/credit advice gets a bounded
  answer plus an advisor; protected characteristics never reach the score or the model; non-English gets a
  fixed notice.
- **Output (before anything is sent):** factuality (placeholders + traceability), prohibited promises
  (discounts, holds, credit approval, closing — DT-26), reserved address (DT-25), fair housing.
- **Code over model:** availability re-checked in code and fails closed (DT-21).

**Q: How do you guarantee nationality or family don't affect the score?**
Three layers. The score contract has **no field** for a protected category — it's structural, not a rule. The
input guardrail removes or generalises the mention before the model sees it ("my wife" → "my partner", "my
kids" → "my family"). And a gold set of **54 pairs** of leads identical except for that mention runs on every
change; it compares the total, all five dimensions and the band, so two leaks can't cancel out. All 54 are
green. When the set was first written, "with my kids" scored −20; that's what DT-38/DT-40 fixed.

**Q: Is the 54/54 measured with the real model?**
No — and it's worth saying. The gold set runs the full turn pipeline (input check → signal extractor → score)
with the deterministic extractor, so it runs in CI in milliseconds. The live part is covered differently: the
classifier prompt explicitly forbids inferring origin, religion, gender, nationality or family; the mention is
removed before the model sees it; and in the live runs "I'm Colombian" / "I'm Venezuelan" got the fixed reply.

**Q: What does the agent answer if someone asks "does my nationality matter?"**
A fixed text written by us, not generated: it doesn't affect how we help you or the requirements. Whether the
person is *asking if it matters* is decided by a classifier field (`asks_if_it_matters`, DT-69), so a passing
mention doesn't get a lecture.

**Q: What about prompt injection or jailbreaks?**
Detected deterministically at input, answered as a normal domain question and stored as `[message omitted]`, so
the injection never comes back as history. More importantly, even a successful injection can't produce a fake
price, a discount, a booking or the exact address, because those don't depend on the model obeying the prompt.
Honest limit: the detector is rule-based, so a novel phrasing can get through to the model — it just has very
little it can break.

**Q: What does it do with questions it can't answer, like parking or pets?**
The classifier flags "asks for something the catalog doesn't record" (`asks_unlisted`, DT-66), and the agent
says an advisor will confirm. It never says yes or no. That was a real error class in the jury tests.

**Q: Opinions — "is it a good investment"?**
No opinions, no legal advice, no pressure: it gives the facts it has or offers an advisor.

---

## 5. How the agent was evaluated (slides 8–9)

**Q: How did you evaluate the agent?** *(the most likely deep question — take the full answer)*
Four layers, from cheapest to most realistic:

1. **Gold sets in CI, no LLM.** One per guardrail — invented data, exact address, promises, fair housing,
   language — each with a threshold; a pull request that drops one below its threshold fails CI (DT-37). Plus
   1,300+ unit, contract and state-machine tests.
2. **Live scenario evaluation** (`harness/scripts/evaluate.py`). 12 scripted conversations, run 5 times each
   against the real model. Each turn declares what it expects: at least one of the expected tools, one of the
   expected texts, none of the forbidden ones, the audit entries it should produce; each scenario declares its
   final state or band. Everything is measured from what landed in PostgreSQL. There are thresholds (turns OK
   ≥ 90%, expected tool ≥ 90%, degraded ≤ 5%, fallback ≤ 2%…) and the script exits with an error if one fails.
3. **Jury sets** (`jury.py`). Unscripted, adversarial conversations — amenities not in the catalog, opinions,
   messy messages, changing their mind — read reply by reply and classified as serious (invented data, wrong
   property, unjustified handoff or degraded reply), moderate or minor. Sets J, N, M, O were used to find and
   fix error classes.
4. **A held-out set, P.** Written after the fixes, with personas and phrasings not in J–O, run **once**, with no
   change before or after: 37 turns, 0 serious errors.

Plus a **classifier gold set** of 93 labelled messages; a prompt change to the classifier is accepted only if no
field drops below its baseline.

**Q: Why run each scenario 5 times?**
Because with a real LLM the same scenario comes out differently between runs; one pass tells you nothing about
reliability. 5 × 12 gives 285 replies, which is enough to see rates like 6% rewrites.

**Q: Did you use an LLM-as-a-judge?**
No, deliberately. Live, it would be one more call per turn against a 10 calls/min quota, and a judge is another
model that can be wrong (DT-23, DT-62). We made the expectations checkable by code instead — tools called,
route taken, which property, forbidden texts — and read the open-ended jury sets by hand. An *offline* judge
over transcripts is a reasonable next step; we didn't build it.

**Q: How do you know you didn't just overfit to your own tests?**
That's exactly why set P exists. The jury sets J–O were used to fix things, so they measure regression, not
generalisation. P was written afterwards and run once without touching anything. On new sets before the fixes
we saw about 3 serious errors per 35 turns (N, M, O first runs); on P, 0. The weakness, stated: P is small (37
turns) and was written by the team that did the fixes. We mitigated with new personas and phrasings and
expectations derived only from the catalog and the rules.

**Q: What changed between 93% on Oct 6 and 99.6% on Oct 8?**
We read the failures and fixed classes, not phrases. The rule we adopted (DT-62): every error class has **one**
mechanism that owns it and a test; no regex or prompt rule per incident, because those fixed the phrase we saw
and not the next one. The big moves:
- **Placeholders** (DT-60) — invented or misattributed data became structurally impossible.
- **New classifier fields at zero extra calls** — `refers_to` (which shown property "the cheaper one" is),
  `visit_when`, `frustrated`, `asks_unlisted`, `needs_lookup`.
- **Forced lookup** — when the classifier says the answer needs the catalog, the first round uses
  `toolChoice: any`, so the model searches instead of asking for filters (DT-68).
- **Two false positives** of the output check removed (DT-70); e.g. "other houses available" was read as the
  status of the reserved one — that alone took the guardrails scenario from 60% to 100%.

Note the scenario set also grew from 8 to 12 between the two runs.

**Q: What was the one failure in 285?**
"We want to move in within the next two months" got the safe answer. Most likely the factuality check read "two
months" as an unsourced number. Safe — it didn't invent anything — but it didn't answer. Code was frozen, so it
wasn't fixed; it's not in the demo script.

**Q: "Expected tool 100%" — what does that mean?**
On every turn where the scenario expects a catalog lookup, the model called one of the right tools (or answered
from a fact a tool had already returned earlier in the conversation, which counts as OK).

**Q: Why median 6 seconds? Can it be faster?**
~2.4 model calls per reply in sequence (classifier, tool round, answer, sometimes a rewrite). Forced lookups
added 0.4 s to the median and took 1.8 s off the p95. Faster options: prompt caching, streaming, fewer rewrites.
Compared to the problem — replies hours later — 6 s isn't the bottleneck.

**Q: Is "zero invented facts" really zero?**
On 37 unseen turns, read one by one, zero. That is compatible with a low real rate, not with a zero rate. And
about 1 reply in 100 falls back to "an advisor will confirm" — safe but unhelpful.

**Q: How would you evaluate it with real users?**
The same harness runs on the real stack; we'd add a held-out set written by someone outside the team, sample
real transcripts for manual review, and watch the audit log rates (rewrites, fallbacks, handoffs) as production
metrics — they're already recorded per turn.

---

## 6. Prompts, tools, memory

**Q: How many prompts, and how were they designed?**
Three system prompts plus a correction prompt (`services/core/app/bedrock_llm.py`):
- **Classifier (Haiku):** JSON only, closed taxonomy per field, extracts only from the current message, never
  infers protected characteristics.
- **Chat (Sonnet):** every property fact comes from a tool and is written as a placeholder; search first
  instead of asking for filters; never name an area from memory; no discounts, holds or credit promises; no
  opinions; visits are booked by the system; one question per message.
- **Summary (Sonnet):** three keys — what they want, objections, next step — every fact from the conversation.
- **Correction:** tells the model which check its draft failed, as system text.

The point to make: the prompt is not the only safety layer. Each rule that protects the agency is also enforced
in code.

**Q: Which MCP tools?**
`search_properties` (with closest alternatives when nothing matches — computed by code, DT-57),
`get_property` (including the operation's requirements), `get_availability`, `get_promotions`,
`check_requirements`, `list_zones`. Up to 3 tool rounds per reply.

**Q: How does the agent remember the conversation?**
State lives in PostgreSQL, nothing in process memory. The chat model sees the last 20 messages; the classifier
sees the current message, the last assistant reply and the list of properties already shown, so it can resolve
"it" or "the cheaper one" (DT-63). Facts a tool already returned are remembered across turns, except status,
which is always re-checked (DT-59). Personal data and protected mentions are removed before storing, so they
never come back as history. A returning lead inherits their score signal (DT-29).

**Q: What about long conversations?**
No compaction beyond the 20-message window and no semantic long-term memory; conversations here are 10–15
turns, so it wasn't needed. It's a known gap.

---

## 7. Decisions you'll be asked to defend (slide 13, backup)

**Q: Why no LangChain / LangGraph / CrewAI?**
We evaluated LangGraph piece by piece (DT-22). Its checkpointer would be a second source of truth next to
PostgreSQL; a node graph would split the single transaction that saves state, score, audit and messages; the
state machine is an 89-line pure function that would become longer and framework-dependent; and the only thing
it would really replace is a short tool loop that holds guarantees we want visible (arguments fixed by code,
rate limiter, safe fallback). CrewAI, Pydantic AI and similar target multiple collaborating agents or tie to a
provider; this is one agent with a state machine. Downsides we accept: we maintain the tool loop, retries and
history ourselves, and there's no built-in streaming or human-in-the-loop interrupts. The contingency plan, if
a framework were mandatory, is a two-node LangGraph graph inside the agent class without touching the turn —
not executed.

**Q: Is it cloud-agnostic, then?**
The orchestration depends on no cloud. The only provider dependency is the LLM client, behind an `Agent`
interface. Another Bedrock model is an environment variable; another provider is one new class. Honest caveat:
we haven't written a second provider, and the prompts were tuned for Claude 4.5.

**Q: Where's the RAG?**
The infrastructure is there — pgvector, a FAQ table with a 384-dim embedding column, a self-hosted embeddings
model chosen (DT-7) — but the FAQ story (C-5) was moved to future work and the table is empty. The reason: our
knowledge is structured — price, size, requirements, availability. Exact filters through MCP tools beat
similarity search, and exactness is what lets the output check trace every figure to a tool. Don't present it
as RAG working.

**Q: Why no subagents or multi-agent setup?**
Each subagent (planner, writer, judge) is another call per turn; at 10 calls/min with ~2.4 per reply the quota
runs out during a demo, and none of them solves the actual error classes we found (which property the lead
means, which day they want). We moved those decisions into fields of the classifier call we already make
(DT-62).

**Q: Why not fine-tune, or use a bigger model?**
No real data and no time, and it wouldn't remove the cause: a stronger model still can't be trusted to never
write a wrong price. Structure removes the class of error regardless of the model.

**Q: Why Phoenix and not LangSmith or Langfuse?**
The requirement was local. LangSmith is hosted. Langfuse self-hosted needs extra services, including Redis,
which we'd ruled out (DT-9). Traces are standard OpenTelemetry, so switching to Jaeger is one environment
variable — verified with a real turn (DT-31).

**Q: Skills (agentskills.io)?**
Not addressed — a gap, not a decision. The closest things are the prompts plus code and the MCP tools. Packaging
the booking or requirements know-how as a skill would be future work.

**Q: Why a secret link instead of a login?**
Three known advisors on one machine; a magic link needs an email provider and OAuth a registered app (DT-5).
It's the one "partial" in the checklist: no password, no expiry, and core's API and Phoenix rely on being bound
to localhost. A real deployment needs a proper login and authentication in front of core and Phoenix.

**Q: Why Bedrock and not the Anthropic API directly?**
DT-6 picked Claude on Bedrock: the most capable model for the conversation and a lighter one for
classification, in separate calls so silent extraction never mixes with the visible reply. The Converse API is
the same for every model Bedrock serves, which is also what makes the model a setting. The 10 calls/min limit
was designed in from day one (own rate limiter, no Redis), not patched under load.

**Q: Why a synthetic inventory?**
No access to a real agency's catalog. 12 listings in Medellín and Bogotá designed to hit edge cases: reserved
units, empty searches, promotions, requirements (DT-34, DT-55).

---

## 8. Observability (slide 5)

**Q: What's in a trace?**
One trace per turn with spans for the input check, the state machine, each catalog tool call, and the Bedrock
and MCP calls (auto-instrumented): timing, tokens, cost, what the classifier understood, the route taken and
which check fired. Every turn also writes a `turn.decision` audit entry, so we can explain why any reply came
out as it did.

**Q: Do the traces contain personal data?**
No, by design (DT-18). Message text is `__REDACTED__` in Phoenix; it lives only in PostgreSQL. For debugging
there's an opt-in flag. The audit log stores counts of violations, never the invented value.

---

## 9. Cost and scale (slides 8, 12)

**Q: How did you compute 8 cents?**
From Phoenix token counts at Bedrock list prices — Sonnet 4.5 US$3/US$15 per million input/output tokens, Haiku
4.5 US$1/US$5. The 285-reply run used 679 calls and ~1.7 M input tokens: about US$5 for 60 conversations.

**Q: How would you make it cheaper?**
96% of the tokens are input: the system prompts, history and tool results resent on every call. Bedrock prompt
caching on the system prompts is the obvious lever, without changing behaviour. Fewer rewrites also save
calls.

**Q: What happens if two people write at the same time?** *(flagged in the speaker notes)*
The account has 10 calls/min per model, shared by everyone — roughly 4 replies per minute in total. A local rate
limiter queues the turn up to 15 s; past that, the lead gets a fixed "sorry, I had a problem" text instead of
waiting forever. Transient Bedrock errors are retried with exponential backoff and jitter, capped at 20 s per
turn (DT-30). Two messages from the *same* lead are serialised by the row lock. In production you'd raise the
quota; the architecture doesn't change.

**Q: Would it scale to a real agency?**
The design scales in the obvious places: stateless core over PostgreSQL, channel adapters, model as a setting.
What would need work: quota, webhooks instead of long polling, real auth, a deploy target, and Phoenix beyond a
single container — none of that was tested.

---

## 10. Demo-related (slide 10)

**Q: What if Telegram or Bedrock fails during the demo?**
Plan B: `scripts/demo/conversar.py 12_guion_demo` replays the same script and shows the transcript, then the
panel. Without credentials the whole stack also runs on a fake model (`LLM_PROVIDER=fake`). If Bedrock errors
mid-conversation, the lead gets the safe fallback, never silence.

**Q: Is the demo scripted? Will it always behave the same?**
The lead's messages follow the demo script, which is also scenario 12 of the evaluation: it passed 5 out of 5
runs, ending in HANDOFF with a HOT lead. The model's wording varies; the facts, the score and the routing
don't. In the Oct 9 live demo the person asked for a human before booking, so the lead stayed WARM (50/100) —
that's the system behaving correctly, not a bug.

**Q: Can I try it myself?** *(if someone wants to type)*
Yes, one person at a time — the quota is shared — and in English. Expect 5–12 s per reply.

---

## 11. Process and team (slide 4)

**Q: How did you organise the work?**
76 stories in seven groups (contracts & environment, agent engine, channel & panel, data, observability &
quality, business intelligence, closing & demo), each with its own acceptance criteria and, where it mattered,
its own gold-set threshold. Contracts first, so the three services could be built in parallel. Every design
decision is written down with context, alternatives and consequences — 71 of them.

**Q: Why record 71 decisions?**
Because most of our answers today come from them. When someone asks "why not LangGraph" or "why is booking not
a tool", the answer, the alternatives we discarded and the accepted downsides are already written. It also kept
three people consistent.

**Q: What were the two stories you didn't finish?**
Voice notes (B-14) and the FAQ with semantic search (C-5). Moved to future work on purpose: the catalog tools
and the "an advisor will confirm" behaviour already cover what the FAQ would answer.

**Q: What was the hardest part?**
Making the agent reliable on questions we hadn't anticipated. Each jury set brought new phrasings, and
patching phrases didn't converge. What converged was moving decisions into structure — placeholders and
classifier fields — and measuring on a set we never tuned on.

**Q: How did you use AI tools while building it?**
*(Answer from your own experience — not in the docs. Keep it factual.)*

---

## 12. Limits and next steps (slide 12)

**Q: Is it production-ready?**
No, and we don't claim it. It's a working prototype: English only, a 12-listing demo catalog, one conversation at
a time because of the quota, local only, advisor access by secret link, no screen to add listings, its own
calendar instead of an external one.

**Q: Because there's no screen to add listings, do the "notify me" alerts work?**
They're stored and the logic is tested, but in practice they never fire, because no new listings appear. It
needs the listings screen.

**Q: What would you do next, in order?**
1. Real login and authentication in front of core and Phoenix, and a deploy target.
2. Prompt caching to cut cost; more Bedrock quota.
3. Richer listings (parking, pets, fees) so fewer answers go to an advisor.
4. WhatsApp as a second channel; voice notes.
5. FAQ with semantic search on the pgvector that's already there.
6. A held-out evaluation set written outside the team, and an offline LLM judge over transcripts.

**Q: What known quality issues remain?**
From the frozen evaluation, none serious:
- once it said it had "shown" a reserved property it never offered;
- the fixed fair-housing reply can end with "what kind of property are you looking for?" even when the lead came
  from a listing;
- the demo promotion is "valid until December 31, 2099" because that's the seed data;
- "after 5pm" is sometimes read with the wrong day;
- "ok forget it thanks" can be read as frustration.

---

## 13. Harder or hostile questions

**Q: Isn't 99.6% just your own scenarios? Of course it passes.**
Yes, that's why we don't lean on it alone. The scenarios measure that the known flows work and don't regress.
Generalisation is measured by the held-out set P, run once: 0 serious errors on 37 turns — small, and we say
so.

**Q: You wrote the tests and the fixes. Why should I trust the numbers?**
Three things you can check. Every run is in the repo with its raw JSON (`docs/internal/evidencia/2026-10-08/`),
including the failures. The held-out set was frozen before running. And every turn has a trace and a decision
record, so any reply can be explained after the fact.

**Q: With so much code around the model, why use an LLM at all?**
Because understanding free text and writing a natural reply is exactly what code can't do: "something cheaper
near the metro for me and my partner, maybe next month" becomes structured fields and a search in one step. We
use the model for that and keep it away from what must be exact.

**Q: Don't the guardrails make replies worse — too many "an advisor will confirm"?**
That's the trade-off we chose for an agency: a safe non-answer beats a confident wrong answer. We measure it:
0.4% fallback in the scripted run. And false positives are treated as bugs — DT-70 removed two that were
degrading correct replies.

**Q: A literal comparison for factuality? That's naive.**
Deliberately simple (DT-23): the guarantee the agency needs is "this wasn't invented", not "this is coherent".
Its blind spot — a real number from the wrong property — is what placeholders closed, without a second model.

**Q: What happens with a lead who never answers?**
The scheduler sends a follow-up after 24 h of silence; after more attempts the conversation moves to NURTURE
(repeated every 7 days) and then LOST. If they write back from NURTURE, it returns to DISCOVERY; after LOST, the
next message opens a new conversation that inherits their score signal (DT-29, DT-44). LOST doesn't close the
lead — that stays the advisor's call.

**Q: Can the lead get a discount or have the unit held?**
No. Those are prohibited promises checked on every reply (DT-26); the request goes to an advisor.

**Q: What's the most important thing you learned?**
Reliability came from structure, not from better prompts. Every error class that we moved into code or into a
structured field stopped failing on new phrasings too; every prompt rule only fixed the sentence we'd seen.

---

## 14. Which slide answers what

| If they ask about… | Go to |
| :--- | :--- |
| Score, bands, BANT-E | Slide 3 |
| Scope, backlog, what's missing | Slides 4 and 12 |
| Components, Docker, Bedrock, Phoenix | Slide 5 |
| AI vs code, placeholders, rewrite/fallback | Slide 6 |
| Guardrails, fair housing, parking | Slide 7 |
| Results, cost, "is zero really zero" | Slide 8 |
| Baseline vs final, tests, CI | Slide 9 |
| Framework, RAG, login, skills | Slide 13 (backup) |

## Sources

- [`decisions.md`](../../DECISIONS/decisions.md) — DT-1 to DT-71.
- [`design.md`](../../DESIGN/design.md) — state machine, BANT-E.
- [`guardrails.md`](../../DESIGN/guardrails.md) — input and output checks.
- [`TESTING.md`](../../TESTING.md) — live evaluation, jury, classifier gold set, quota.
- [`2026-10-08-cierre-de-evaluacion.md`](2026-10-08-cierre-de-evaluacion.md) — every number of the final
  evaluation, cost and known issues.
- [`2026-10-14-estado-del-proyecto.md`](2026-10-14-estado-del-proyecto.md) — what it does today and with what
  evidence.
- [`2026-10-15-agentic-requirements-coverage.md`](2026-10-15-agentic-requirements-coverage.md) — the 12-point
  checklist behind slide 13.
