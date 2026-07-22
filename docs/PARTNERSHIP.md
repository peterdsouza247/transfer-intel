# Partnership pitch

Written for The Athletic and Tifo, adaptable to Sky Sports, ESPN,
Transfermarkt, or a betting-adjacent data business. Section 7 is the part to
read before sending anything.

---

## 1. The one-line version

TransferIntel scores every Premier League transfer rumour from 0 to 100 for
credibility, using arithmetic that can be audited rather than a model that
guesses, and it can prove which reporters were right.

---

## 2. The problem it solves

A transfer window produces thousands of claims and no accountability. An
outlet reports a done deal, the deal collapses, and by the following week
nobody remembers who said what. Aggregators make this worse: they republish a
tabloid's speculation and a tier 1 scoop in the same font, at the same size,
with the same confidence.

Readers have adapted by disbelieving everything, which is bad for the people
who get it right. The reporters with genuine sourcing are indistinguishable,
at a glance, from the ones inventing it. That is a competitive problem for any
outlet whose actual product is accuracy.

TransferIntel makes the distinction machine-readable.

---

## 3. What exists today

Not a prototype. A live site, running an automated pipeline, in its first
window.

- **Every rumour scored 0 to 100.** The score is computed from the tier of the
  outlets reporting it, how many independently corroborate it, how far the
  deal has progressed, and how long it has been since anyone said anything.
- **Nothing is asserted.** A language model reads an article and reports what
  the article claimed. Every number is arithmetic in code. Replay yesterday's
  articles and you get yesterday's scores, byte for byte.
- **Completion requires proof.** A transfer is only marked complete when a
  tier 1 source uses language that only applies after the fact, or the club
  announces it itself. That phrase is recorded on the record and shown to the
  reader.
- **Silence is measured.** A deal nobody has mentioned for a fortnight decays
  and is flagged as stale, because the absence of news is information.
- **Collapses are kept, with causes.** Failed medicals, valuation gaps,
  hijacks. The site tracks the pivot: a club losing one target and signing
  another within 72 hours is one story, not two.

### The failure that produced the architecture

An early version promoted deals to "Completed" with credibility 100 on no
positive evidence. It had learned that absence of contradicting news meant
confirmation. Six deals sat on the homepage marked complete while their own
summaries said "announcement imminent" and, in one case, "then silence".

The fix was not a patch. It was a new status, a class of evidence called
completion markers, and a validation gate that assumes the rest of the
pipeline is lying and looks for the specific ways that would matter. That gate
fails the build rather than publishing a doubtful claim, and a day with no
update is always an acceptable outcome.

This matters to a publisher because it is the same editorial instinct, written
down as code. The interesting part of the system is not that it scores
rumours. It is that it refuses to.

---

---

## 4. How the score is actually built

Worth reading if you are going to evaluate this, because "we score rumours"
is a claim anyone can make and the arithmetic is the part that separates it
from a vibe.

Four terms, added, then multiplied by one decay factor.

### The base: who said it

Source tier is a property of the outlet, decided once in code, not per story.
It is a standing judgment about track record and it does not move to suit a
particular deal.

| Tier | Who | Base |
|---|---|---|
| 1 | Ornstein, Romano, BBC, Sky, The Athletic, Guardian | **55** |
| 2 | Telegraph, Mail, Football365, FootballTransfers | **35** |
| 3 | Aggregators, foreign press pickup, unattributed paper talk | **15** |

Anything not on the list is tier 3 by default, which is the safe direction to
be wrong in.

### Corroboration: who else said it

**+5 for each additional independent outlet, capped at +15.**

Capped deliberately. Six outlets running the same agency copy is one source
wearing six hats, and an uncapped term would reward syndication rather than
reporting.

### Stage: how far it has got

| Stage | Bonus |
|---|---|
| Interest | 0 |
| In talks | +10 |
| Fee agreed | +25 |
| Medical booked | +35 |
| Reported complete | +45 |

Only tier 1 and 2 sources can move a deal along this ladder. A tabloid saying
"medical booked" does not make it so.

### Decay: how long since anyone said anything

The one multiplicative term, and the one nobody else has.

| Days quiet | Multiplier |
|---|---|
| 0 to 3 | 1.00 |
| 7 | 0.85 |
| 10 | 0.75 |
| 14 | 0.60 |
| 15+ | **0.40**, and flagged Stale |

The cliff at day 15 is deliberate rather than a smoothing mistake. A deal
nobody has mentioned in a fortnight is a different kind of object from one
that went quiet for twelve days, and the step is what makes the Stale badge
mean something.

Decay multiplies rather than subtracts, so a strong rumour and a weak one lose
proportionally and the ranking between them survives a quiet fortnight.

The site shows both numbers: **"base 55, currently 41 after 10 days without
movement."** A reader can see what the evidence was worth and what silence has
done to it.

### Four real records, today

```
Johan Manzambi     tier 1   base +55, corroboration +5, stage +45  = 90
Savinho            tier 1   base +45, stage +10 = 55, decayed to 41 (10 days quiet)
Julian Alvarez     tier 2   base +25 = 25, decayed to 20 (9 days quiet)
Randal Kolo Muani  tier 3   base +5  = 5,  decayed to 4  (10 days quiet)
```

Manzambi is a tier 1 source reporting a completed deal with a second outlet
agreeing. Kolo Muani is one French report nobody has repeated. The gap between
90 and 4 is the product.

Note Savinho's base of 45 rather than 55: a tier 1 outlet that has not
reported in the scoring window is worth less than one that reported this
morning. The score tracks the reporting, not the reputation.

### The status ladder

```
rumour -> talks -> agreed -> medical -> confirmed -> done
                                  |
                             collapsed   (only ever on an explicit report)
```

One rung per run. A deal cannot go from rumour to done because a single
outlet got excited.

**Confirmed** means confirmed pending announcement: a tier 1 source says it is
finished, nobody has announced it. Capped at 90, and it still decays, because
a signing nobody has announced three weeks after it was called done really is
less certain.

**Done** requires a tier 1 source plus a recorded **completion marker**: a
phrase that only makes sense after the fact, matched with hedge detection so
"completes his move" counts and "set to complete his move" does not. A club's
own domain is the strongest marker available. The phrase is stored on the
record and printed on the page, so every completion can be traced to the
sentence that produced it.

**Credibility 100 is only ever possible on a completed transfer.** A
validation gate fails the build otherwise.

### Two guards worth knowing about

**Whiplash cap.** Credibility moves at most 25 points per run unless a
terminal status pins it. A score that swings 70 points overnight is almost
always a parsing fault, not news.

**Volume limit.** More than 15 editorial changes in a day aborts the run and
publishes nothing. A day that busy is far more likely to be a broken feed
than a real news day, and a day with no update is always an acceptable
outcome.

## 5. What is on the roadmap, and why it is the actual pitch

**The source accuracy leaderboard.** Every claim already carries which outlet
made it, when, and at what stage. Every deal eventually resolves. Joining
those two facts produces a measured hit rate per source: of the deals this
outlet called done, how many happened.

Nobody publishes this. Outlets assert their own reliability and readers have
no way to check. A third party measuring it, with the method published and
the underlying records visible, is a different kind of claim.

**Why that is worth something to The Athletic specifically.** David Ornstein
and Fabrizio Romano are the two names most often cited as the standard. If
the data shows what the industry already believes, The Athletic has an
independently produced number instead of a reputation. Self-reported accuracy
is marketing. Externally measured accuracy is evidence.

If the data shows something more complicated, that is worth knowing privately
before a competitor measures it publicly.

Also on the roadmap: contested targets, where three clubs chase one player and
the site tracks who is closest; squad gap analysis, mapping each club's
depth against what they are pursuing; and deal structure extraction,
separating a guaranteed fee from add-ons, which almost nobody reports clearly.

---

## 6. What a partnership could look like

Four shapes, from lightest to heaviest.

**Attribution and links.** Already happening in one direction: the site links
out to source articles. A reciprocal link, or a mention in a transfer
round-up, costs nothing and is worth a great deal at this stage.

**Data licensing.** The dataset, with credibility scores and evidence trails,
delivered as a feed. Useful for a live blog, a deadline day graphic, or a
weekly accuracy segment. Priced as data, not as sponsorship.

**Editorial collaboration.** Tifo's explainer format and a transparent
scoring model fit together unusually well. "How do you tell a real transfer
rumour from a fake one" is a video that writes itself, and the model provides
the spine.

**Sponsorship or acquisition.** The heaviest option and the one with the
problem described below.

---

## 7. The problem to raise before they do

**Funding the scorer compromises the score.** The entire value of a
credibility index is that it is independent. The moment The Athletic pays for
a site that rates The Athletic's reporters, every future score involving them
is worth less, and a competitor will say so.

Do not pretend this is not a problem. Raise it first, and have an answer.

Answers that hold up:

- **License the data, do not fund the operation.** A commercial customer of a
  dataset is not the same as a patron of the methodology.
- **Publish the governance.** The scoring rules are already in code and
  already public. Commit to any partner having no input into source tiering,
  and make tier changes a public changelog entry.
- **Exclude the partner from the leaderboard, or hand it over.** Either
  TransferIntel does not rank its funder, or the ranking method is handed to
  a third party. Both are worse products. Both are better than a compromised
  one.

**The second problem: the dataset is built on their journalism.** Every record
cites reporting that belongs to somebody. The site's licence is explicit that
CC BY covers the compilation, not the underlying articles, and it links out
rather than reproducing. That is a defensible position and it is worth stating
plainly in a first email, because their lawyers will get there eventually and
it is better coming from you.

**The third: they may simply build it.** The method is public. A publisher
with engineers could reimplement it in a quarter. The defence is not secrecy,
it is that a scoreboard operated by a competitor in the same market is worth
less than one operated by a neutral party, and that the accumulated record
from this window cannot be backfilled by anyone starting later.

---

## 8. What is actually being asked for

In order of what would help most:

1. **A conversation**, with someone in product or data rather than editorial.
2. **A link or a mention**, which is the cheapest thing they can give and the
   most valuable thing to receive.
3. **Feedback on the leaderboard method** before it ships, from people who
   understand transfer reporting from the inside.
4. **A data licensing conversation**, if the feed is useful to them.
5. Financial support, last, and structured to preserve independence.

Do not lead with five.

---

## 9. Realistic expectations

The Athletic is owned by The New York Times. Partnerships go through business
development and legal, not through a reporter's inbox, and the process is
slow. A cold approach to a named journalist is more likely to be ignored than
forwarded, and if it is forwarded it arrives without context.

Better routes, roughly in order:

- Tifo, which is smaller, more experimental, and has a format that fits.
- Football data people on the analytics conference circuit, who will
  understand the method immediately and know the right people.
- Smaller outlets first. A credibility index that Sky or ESPN already cites is
  a much easier conversation than one nobody has used.

The strongest possible position is arriving with the leaderboard already
published and a window of data behind it. That is a finished thing with
evidence, not a proposal. It is worth waiting for.

---

## 10. The cold email

Short. One idea. No attachment.

> Subject: I built something that scores transfer rumours, and it can measure
> who gets them right
>
> Hi [name],
>
> I built a site that scores every Premier League transfer rumour 0 to 100 for
> credibility. The score is arithmetic, not a model guessing: source tier,
> independent corroboration, how far the deal has got, and how long it has
> been quiet. A transfer is only marked complete when a tier 1 source uses
> language that only applies after the fact, or the club announces it.
>
> [link]
>
> The next piece is a source accuracy leaderboard: every claim already carries
> who made it and when, and every deal eventually resolves, so the hit rate
> per outlet falls out of data I am already collecting. Nobody publishes that.
>
> Before I ship it, I would value fifteen minutes with someone who knows
> transfer reporting from the inside, mostly to be told what I have got wrong
> about how sourcing actually works.
>
> Peter

Why this works: it leads with the thing built rather than the thing wanted,
it makes one specific and small ask, and it offers them the flattering role of
expert rather than the tiresome role of patron.

---

## 11. The short version, for WhatsApp and Instagram

### WhatsApp, to friends

> Every transfer window you see "DONE DEAL" ten times a day and half of them
> never happen.
>
> So I built a thing that scores every rumour out of 100 for how much you
> should actually believe it.
>
> Ornstein says it, that is worth a lot. Some aggregator says it, that is
> worth almost nothing. Three outlets independently say it, worth more again.
> And if nobody has mentioned it for two weeks, the score drops and it gets
> flagged as stale, because silence usually means it is dying.
>
> It will not tell you your club is signing someone. It will tell you when
> everyone else is lying to you about it.
>
> [link]

### Instagram caption

> **I got tired of "HERE WE GO" meaning nothing.**
>
> So I built a transfer tracker that scores every rumour out of 100.
>
> Not vibes. Arithmetic.
>
> Who reported it (Ornstein 55, aggregator 15). Who else confirmed it (+5
> each). How far it has got (fee agreed +25, medical +35). And how long it has
> been quiet: a fortnight of silence knocks 40% off the score and gets it
> flagged Stale, because silence usually means a deal is dying and every other
> site keeps showing it exactly as it was.
>
> Nothing gets called Completed until a tier 1 source says words that only
> make sense after it has happened, or the club announces it itself. The
> phrase that proved it is printed on the page.
>
> Free, no ads, no login. Link in bio.
>
> #transfernews #premierleague #deadlineday #footballtwitter

### The explainer post, when someone asks how the number works

Carousel or thread, one idea per panel. This is the one that earns trust,
because the answer to "who decides?" is "nobody, here is the arithmetic".

> **1.** Every transfer rumour on the site gets a score out of 100. Here is
> exactly how it is built. No vibes, no editor's hunch.
>
> **2. Who said it.** Ornstein, Romano, BBC, Sky start at 55. Telegraph and
> the mid-tier at 35. Aggregators and paper talk at 15. That ranking is fixed
> in advance and does not change to suit a story.
>
> **3. Who else said it.** +5 for every independent outlet that confirms it,
> capped at +15. Capped because six sites running the same agency copy is one
> source wearing six hats.
>
> **4. How far it has got.** In talks +10. Fee agreed +25. Medical booked +35.
> Reported complete +45. Only reliable sources can move a deal up that ladder:
> a tabloid saying "medical booked" does not make it so.
>
> **5. How long since anyone said anything.** This is the bit nobody else
> does. Quiet for a week, the score drops to 85% of itself. A fortnight, 60%.
> Past that it falls to 40% and gets flagged **Stale**.
>
> Silence is information. A deal nobody has mentioned in two weeks is usually
> dying, and every other site keeps showing it exactly as it was.
>
> **6.** So you see both numbers: *base 55, currently 41 after 10 days without
> movement.* What the evidence was worth, and what silence has done to it.
>
> **7.** And nothing gets called **Completed** until a top-tier source uses
> words that only make sense after it has happened, or the club announces it
> itself. The exact phrase that proved it is printed on the page.
>
> Because the whole point is you should not have to take my word for it either.

### The one-liner, for anywhere

> A transfer tracker that tells you how much to believe each rumour, and shows
> its working.

### If you only post one thing

Screenshot a rumour everyone believed, showing a low score, next to the same
deal collapsing a week later. Caption: "It said 34 out of 100 on the 12th."

Being right in public, with a timestamp, is the only marketing this product
needs.
