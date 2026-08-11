# Manual test — everything built so far (Days 1–9)

> **Who this is for:** you, with a browser, before Day 10 starts.
> **How long:** ~50 minutes if nothing is broken. The two long waits are free-model latency (35–90s per beat) and the memory consolidation that runs when a story ends.
> **What it is not:** a re-run of the automated suite. `make test` already proves 815 backend + 102 frontend behaviours. Everything below is deliberately a thing **a test cannot judge** — does it feel right, does the same character come back, does the companion actually remember.

Work top to bottom. Each part depends on the one before it — Part 7 is not testable until Part 3 has produced a real story and Part 6 has consolidated it.

Tick as you go. Anything that fails, write the actual text you saw next to it — "the greeting was cold" is a bug report; "it didn't work" is not.

---

## Part 0 — Before you touch the browser

### 0.1 Decide the point of this run

The single biggest unverified claim in the product right now is **semantic recall quality**. Everything has only ever run on `local-hash` embeddings, which are *lexical* — "the right memory" currently means "the memory sharing the most words". Day 10 builds on numbers tuned against that signal.

- [ ] **Set a real embedding key** (`OPENAI_API_KEY` in `.env`) before you start. Text embeddings are pennies; this is the cheapest key in the stack and it is the one that makes today's test mean something.
- [ ] If you deliberately choose *not* to, write it here → `ran on local-hash: ☐` — and treat every ranking judgement below as unproven rather than passed.

### 0.2 Switch off the slow model, or accept the wait

Free routes are 35–90s per beat, which makes the story loop miserable to sit through and makes "does this feel warm" hard to judge.

- [ ] For this run, set `AI_PROVIDER_ORDER=anthropic,openrouter` in `.env` if you have a paid key. You are judging *warmth and pacing* today, and you cannot judge pacing at 90 seconds a beat.
- [ ] Otherwise accept it and don't mark pacing as a fail.

### 0.3 Start everything

Four shells:

```bash
make up          # Postgres (pgvector) + Redis
make migrate     # should say "No migrations to apply"
make run         # API on http://localhost:8000  (ASGI/uvicorn — required for SSE)
make worker      # Celery — stories AND memory consolidation depend on this
make front       # PWA on http://localhost:5173
```

- [ ] `make worker` is **actually running**. If it isn't, Part 6 and Part 7 will silently produce nothing and you will conclude memory is broken when it is just not running.
- [ ] http://localhost:8000/api/health/ returns `{"status": "ok"}`.
- [ ] Open http://localhost:5173/ — you should see **"The Primer"**, "Your story is waking up", a **★** button, and a footer status reading **`ok`** with a green dot.
  - Footer says `offline` → the API isn't up. Footer says `waking up` → still loading; give it a second.

### 0.4 Open a log window you can actually read

Keep the `make run` shell visible. Several checks below are "what did the server say", and the log lines are named things you can search for. You will use these in Part 7:

| Log line | Means |
|---|---|
| `companion.greeting … kind=returning` | The day-two greeting was generated and passed every check |
| `companion.greeting … kind=first_meeting reason=no_memories` | This child has no stored memories yet — expected on day one |
| `companion.greeting_untraceable` | **New (Day 9 fix).** The model named a memory that isn't in the bundle, or named none — degraded to the first-meeting hello on purpose |
| `companion.greeting_refused` | The output guard refused the greeting or its caption — degraded on purpose |
| `companion.greeting_generation_failed` | Provider or assembly blew up — degraded on purpose |
| `memory.retrieve … stored=N tokens=N/800` | What retrieval put in the bundle |

---

## Part 1 — The parent path (Days 1–2)

### 1.1 The adult gate

- [ ] From the child home, click **"Grown-ups"** in the footer.
- [ ] You land on **"Grown-ups only"** with a multiplication question ("What is 7 × 8?").
- [ ] **Answer it wrong on purpose.** You should see *"Not quite — here's a new one."* and **a different sum** — not the same one again. (A child who mashes the last correct answer must not get in.)
- [ ] Answer correctly → you continue through.

### 1.2 Signup

- [ ] You are asked to sign in or create an account. Create one: any email, a real-ish password.
- [ ] **Check the gate is genuinely in front of everything:** open a fresh tab straight to http://localhost:5173/parent/login. You should be bounced to the gate, not shown the login form. A child must not be able to reach a form at all.

### 1.3 Consent before anything else

- [ ] Onboarding opens on **"Before we start"** — plain language, no legalese wall.
- [ ] Read it as a parent would. It should say what is kept, that it is never sold and never used to train anyone else's models, no advertising, that you can read and delete everything, and that you set the intensity ceiling.
- [ ] The **"I agree — continue"** button is **disabled** until you tick the attestation box.
- [ ] ⚠️ **This is the one nobody can rescue later:** you must not have been asked a single thing about your child before this screen. If any child field appeared before consent, stop and flag it.

### 1.4 The child profile

Fill it in for real — these seeds drive everything downstream.

- [ ] **First name:** `Rina` (or your own child's — use something you'll recognise in a story)
- [ ] **Birthday:** something that makes them **5**
- [ ] **What does Rina love?** `dragons, the sea`
- [ ] **What is Rina a little scared of?** `the dark`
- [ ] **Anything to avoid entirely?** `hospitals`
- [ ] **How adventurous should stories get?** — drag the slider. The hint text under it must **change wording** as you drag (it is the read-out; the number alone means nothing to a parent). Leave it at 2.
- [ ] Save.

**Note the child's UUID now — you need it in Parts 6 and 7.** The easiest way: the next screen's "Hand over the tablet" button navigates to `/meet/<uuid>`. Click it and read the UUID out of the address bar. Write it here:

```
CHILD_1 = ________________________________________
```

---

## Part 2 — The companion, and the imprint (Day 3)

### 2.1 Choosing a friend

- [ ] You're on **"Who should meet Rina?"**.
- [ ] Pick an archetype, type a name (`Sparkle`), pick a colour, pick a personality.
- [ ] Save. The handover screen says **"Someone is waiting to meet Rina"** and warns their friend takes a moment to wake up.

### 2.2 The imprint — this is a *child's* screen now

- [ ] Click **"Hand over the tablet"** → `/meet/<CHILD_1>`.
- [ ] **Wait for the art.** First render calls Cloudflare; give it up to a minute. You should get a portrait, not a placeholder.
- [ ] ⚠️ **Judge the drawing as a parent, not as a developer.** Is this a thing a five-year-old would want to be friends with? Free `flux-1-schnell` drifts — you're judging "good enough for dev", not perfect.
- [ ] Two round buttons: **☀** (hear the greeting) and **★** (begin).
- [ ] **☀ is the coloured/accent one and ★ is pale.** This is the Day-6 voice-as-gate: the child is nudged to *listen* before they're nudged to *go*.
- [ ] Press **☀**. The companion speaks. ⚠️ **Judge the voice.** Warm? Right pace for a five-year-old? Not a call-centre robot? If not, change `ELEVENLABS_VOICE_ID` and re-run — this is the single highest-leverage thirty seconds in the whole test.
- [ ] After it has spoken, **★ becomes the accent colour** and ☀ goes pale. The gate has opened.
- [ ] There is **no back button, no menu, no way to a parent form** anywhere on this screen.

---

## Part 3 — The first story (Days 4–5)

Press **★**. You're at `/story/<CHILD_1>`.

### 3.1 The opening

- [ ] While it generates you see a **companion thinking** — never a spinner, never a progress bar, never an error.
- [ ] Narration arrives **first**, then the illustration swaps in underneath. (Words before pictures is deliberate: the story starts the moment there are words.)
- [ ] ⚠️ **Read the narration out loud.** Would a five-year-old follow it? Is it two or three sentences, not a paragraph? Does it sound like a friend, not a narrator?
- [ ] Does it use what you seeded — dragons, the sea? It should feel like *Rina's* story, not a generic one.

### 3.2 The choice loop — the thing M1 rests on

- [ ] You get **choice cards** with pictures and short labels.
- [ ] ⚠️ **Before you tap, say out loud what you expect to happen.** Then tap.
- [ ] **The next beat must be about the thing you chose.** This is "consequence fidelity" and it is the one that makes a child feel they are steering rather than being read to. If you pick the river and get a beat about a forest, that's a P0 — write down both the label you tapped and the narration you got.
- [ ] Tap **☀ replay** on a beat. It re-speaks without re-generating.
- [ ] Tap the screen background mid-story. **The story must not restart.**

### 3.3 The learning beat

- [ ] Somewhere you'll get a **counting beat** — a question and a number pad.
- [ ] Answer **wrong on purpose.** ⚠️ You should get warmth and a gentle scaffold — never "incorrect", never a score, never a red X. A child is playing, not being tested.
- [ ] Then answer right. It should feel like the story moving on, not like passing a test.

### 3.4 Do something memorable — this is the setup for Part 7

**Deliberately do two things you will recognise tomorrow:**

- [ ] **Name something.** If the story offers a chance to name a creature or a place, take it. (This is what makes the "you gave me my name" reciprocity possible.)
- [ ] **Make a distinctive choice** — the odd one out, the kind one, the brave one. Write both down:

```
I named ____________________________
I chose ____________________________
```

### 3.5 The warm close

- [ ] End the session (play to the close, or take the ending option).
- [ ] You get **"What next?"** with two options: go somewhere new, or **all done for today**.
- [ ] Choose **all done**. ⚠️ It should feel like a goodnight, not a logout.

---

## Part 4 — Character consistency (Day 6)

- [ ] Scroll back through the beats you just played. **Is it the same character in every illustration?**
- [ ] Same colours, same archetype, same broad shape? Some drift is expected on the free image model. A *different animal* is not drift, it's a bug.
- [ ] Compare against the portrait on `/meet/<CHILD_1>` — same friend?

For a harder look without playing another story — this is the tool built for exactly this question:

```bash
make render-consistency CHILD=<CHILD_1>   # a 6-beat illustrated sequence to eyeball
make smoke-media                          # writes a contact sheet, prints its path
```

- [ ] Open the printed contact-sheet path and look at every scene in one grid. ⚠️ **Drift is obvious at a glance there and invisible one beat at a time** — which is why this check belongs here and not in your memory of Part 3.

---

## Part 5 — Safety, from the child's side (Day 7)

You cannot usefully red-team by typing at a story — the automated suite already drives 179 attacks through the real path. What you *can* check is the two things only a human notices.

- [ ] **Nothing in the whole session showed an error, a stack trace, a status code, or a spinner.** Scroll back through everything you played. A child must never see a failure.
- [ ] **The avoid-topic held.** You seeded `hospitals`. It should not have appeared. (One session is weak evidence, but a violation here is decisive.)
- [ ] **The fear was handled gently, not used as a scare.** You seeded `the dark`. If darkness appeared, was it something the companion helped with — or something used to frighten?

Optional, if you want to see the machinery:

```bash
make redteam        # the injection / ceiling / unsafe-bait suite
```

- [ ] Visit http://localhost:8000/admin/ and look at **Safety incidents**. Anything recorded from your session should be explicable. An empty table after a clean session is correct.

---

## Part 6 — What the companion wrote down (Day 8)

**Memory consolidation runs on the worker when a session ends.** Give it a minute after the warm close.

```bash
make memories CHILD=<CHILD_1>
```

- [ ] Rows came back. If it's empty, `make worker` wasn't running — restart it and re-run the session.
- [ ] ⚠️ **Read the memories as sentences.** Each one should read like something a friend would remember: *"Rina named the little dragon Sparkle."* Not `route=river`, not a log line, not a UUID.
- [ ] **The thing you named in 3.4 is in there.**
- [ ] **The choice you made in 3.4 is in there.**
- [ ] **There is one row per moment, not one per beat.** Twenty tellings of one moment must not be twenty rows. If you see near-identical rows differing only in scenery, that's the dedupe failing.
- [ ] Salience looks sane — the thing you'd expect a child to care about most is scored highest.

```bash
make memories CHILD=<CHILD_1> MEM_ARGS='--like "dragon"'
```

- [ ] ⚠️ **This is the semantic-quality check the test suite cannot make.** The dragon memories should rank above the unrelated ones. With a real embedding key this is meaningful; on `local-hash` it only proves word overlap.

---

## Part 7 — Day two: the whole business (Day 9)

**This is what the product is.** Everything above is scaffolding for this screen.

### 7.1 Come back

- [ ] Go to http://localhost:5173/ and press **★**. (The star routes the first child to `/welcome/<uuid>`.)
- [ ] Or go straight to `/welcome/<CHILD_1>`.

### 7.2 What you should see

- [ ] Heading reads **"Welcome back!"** — not the child's name.
- [ ] Under it, a greeting that **names Rina in the first few words**.
- [ ] It **refers to one specific thing you actually did** in Part 3 — the thing you named, or the choice you made.
- [ ] Under that, in italics, the **`remembered` caption** — the memory the companion used, so an adult can check the claim.
- [ ] ⚠️ **The judgement that matters:** does this land? Read it out loud. Does it sound like a friend who missed you, or like a CRM email? Two or three sentences, ending by opening today's adventure — not a paragraph of reminiscence, not a question the child has to answer.
- [ ] Press **☀**. It is spoken in the companion's voice. Same warmth check as 2.2.
- [ ] Press **★** → today's story begins.

### 7.3 If you get the first-meeting hello instead — read this before filing a bug

A returning child *can* legitimately get "Hi Rina! I'm Sparkle…" now. The Day-9 fix made the greeting refuse to ship a promise it can't keep. **Check the `make run` log to find out which happened:**

| What the log says | What it means | Is it a bug? |
|---|---|---|
| `kind=first_meeting reason=no_memories` | Nothing was consolidated. Go back to Part 6. | Not the greeting — the worker |
| `companion.greeting_untraceable` | The model named a memory that isn't in the bundle, or named none. Degraded on purpose. | **Only if it happens every time** — then the prompt needs work, or the retrieval bundle is too thin |
| `companion.greeting_refused` | The output guard refused the greeting or the caption. | No — that's the guard doing its job. But look at *what* it refused |
| `companion.greeting_generation_failed` | Provider or assembly failed. | Yes — check the traceback above it |

- [ ] Which one did you get? → `________________________`
- [ ] ⚠️ If it was `untraceable`, **re-run the greeting three or four times** (reload `/welcome/<CHILD_1>`). Occasional is the guard working. Every time means the model can't quote its own bundle and the returning greeting is effectively switched off — that's a Day-10 P0.

### 7.4 The bundle behind the greeting

```bash
make memories CHILD=<CHILD_1> MEM_ARGS='--recall "coming back for another story"'
```

- [ ] You get the whole bundle with `sim` / `sal` / `rec` broken out per item.
- [ ] The companion, the fear, the loved things and the recent salient events are all present — the rule-includes ride in every bundle.
- [ ] ⚠️ **Is the top-ranked memory the one you'd have chosen?** This is the "why did it mention *that*" question, and this command is the only place it gets answered. With a real embedding key, this is the tuning signal for `WEIGHTS`, `RECENT_SALIENCE_FLOOR` and `MEMORY_RETRIEVAL_CANDIDATES`.
- [ ] Token count is well under 800.

### 7.5 The second child — no bleed

This is the failure a parent would notice first and forgive last.

- [ ] Back to `/parent` → **"Add a child"**. Create **Omar**, age 5, loves `diggers`, companion `Rusty`, a different colour.
- [ ] Note his UUID from the `/meet/<uuid>` URL → `CHILD_2 = ______________________`
- [ ] Play Omar a short story (Part 3, abbreviated). Name something **completely different** — a tower, a moth, anything that could never appear in Rina's story.
- [ ] End it. Wait for consolidation.
- [ ] Go to `/welcome/<CHILD_2>`.

Now the checks that matter:

- [ ] Omar's greeting names **Omar**, and **Rusty**, and **Omar's** memory.
- [ ] ⚠️ **Rina's name, Rina's companion, and Rina's memory appear nowhere in it.**
- [ ] Go back to `/welcome/<CHILD_1>`. Rina's greeting is **still hers** — no trace of Omar's tower.
- [ ] The two greetings are **different sentences**, not one template with the name swapped.

### 7.6 The brand-new child — the free hello

- [ ] Add a third child, **Ada**, and a companion. **Do not play a story.**
- [ ] Go to `/welcome/<ADA_UUID>`.
- [ ] Heading is **her name**, not "Welcome back!".
- [ ] The text is the first-meeting hello — *"Hi Ada! I'm Pip. I've been waiting to meet you."*
- [ ] There is **no memory caption** under it.
- [ ] ⚠️ Check the `make run` log: there should be **no model call at all** for this greeting (`reason=no_memories`). A brand-new child must cost nothing.

---

## Part 8 — Sign-off

| # | Area | Pass? | If not, what you saw |
|---|---|---|---|
| 1 | Adult gate + consent before any child data | ☐ | |
| 2 | Child profile, seeds honoured | ☐ | |
| 3 | Companion art — same friend, good enough | ☐ | |
| 4 | **Voice — warm, right pace** | ☐ | |
| 5 | Story opens with words, then picture, no spinner | ☐ | |
| 6 | **Consequence fidelity — the beat is about what I tapped** | ☐ | |
| 7 | Wrong answer met with warmth, never a score | ☐ | |
| 8 | Warm close feels like a goodnight | ☐ | |
| 9 | No error, code or spinner reached the child, all session | ☐ | |
| 10 | Memories read like sentences, one per moment | ☐ | |
| 11 | **Day two: greeted by name, about a real thing I did** | ☐ | |
| 12 | **The greeting lands emotionally** | ☐ | |
| 13 | Two children, zero bleed | ☐ | |
| 14 | New child gets the free hello, no model call | ☐ | |
| 15 | `--recall` ranking is defensible with a real embedding key | ☐ | |

**Verdict:** ☐ Ship Day 10 · ☐ Fix first: ______________________________

---

## What this test still cannot tell you

Be honest about the gaps rather than treating a green table as proof:

- **One session is not retention.** Day two feeling good once is not the same as day nine feeling good. The thing that decays — repeated greetings starting to sound the same — is invisible in a single sitting.
- **A real child is not you.** You know what the companion is about to say. Nobody in this test is five.
- **Recall bookkeeping (P2-1)** stamps every memory *retrieved*, not the one that was *said*, and feeds its own recency signal — so what gets recalled keeps getting recalled. You will not see this in one session; you will see it in three weeks as the same memory every time. Decide it before Day 10 builds on `recall_count`.
- **HNSW at scale (P2-3)** cannot be observed at this data size. It has already been sighted once as a flaky test.
