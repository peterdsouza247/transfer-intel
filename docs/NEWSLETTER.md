# Email capture and the daily digest

TI-010 and TI-011. This is the highest-value hour of setup in the whole
project. Traffic during a transfer window is spiky and almost entirely
non-returning; the digest is the only thing that converts a visitor into a
reader. Every day the window is open without a capture form is subscribers you
cannot go back and get.

## Pick a provider

| Service | Free tier | Verdict |
|---|---|---|
| **Kit** (formerly ConvertKit) | 10,000 subscribers | **Recommended.** The ceiling is what matters during a window that could spike hard. Heavier branding on the free tier and clumsier API ergonomics, both survivable |
| Buttondown | 100 subscribers, then paid | Better API, markdown native, ideal for programmatic sends. The ceiling is the problem: 100 is one good Reddit thread |
| MailerLite | 1,000 subscribers, 12,000 sends/month | Reasonable middle ground if you dislike both of the above |

Both `kit` and `buttondown` are implemented in `run_digest.py`. Switching is a
one-line config change plus a new API key.

## Setup, about fifteen minutes

1. Create the account and a form. Copy the form's **hosted endpoint URL**, the
   one that looks like `https://app.kit.com/forms/1234567/subscriptions`.
2. Turn on **double opt-in** in the provider settings. This is not optional:
   without it a single bot submission run starts poisoning your deliverability
   from day one.
3. Brand the confirmation email. It is the first thing a subscriber sees and
   the default templates all look like a phishing attempt.
4. Put the endpoint in `data.json`:

   ```json
   "newsletter": {
     "provider": "kit",
     "action": "https://app.kit.com/forms/1234567/subscriptions"
   }
   ```

5. Add the API key as a repository secret named `NEWSLETTER_API_KEY`, under
   **Settings, Secrets and variables, Actions**.
6. Rebuild: `python scripts/render_site.py --data data.json --template index.html --out .`

Until `action` is set, **no capture form renders anywhere**. That is on
purpose. A form that posts nowhere collects addresses into a void and burns
the one chance you get to ask a visitor for their email.

## What gets rendered

One form, directly under the site header and above the section nav. No modal,
no interstitial, no second ask further down the page.

It was three placements originally: below the hero, at the end of the
credibility index, and above the footer. On desktop those were separated by
real content. On mobile, where every section stacks into one scroll, it meant
meeting the same offer three times on the way down, which reads as nagging
rather than as an offer. A reader who has declined once does not need asking
again before the footer.

The form is real HTML rendered at build time, so it works with JavaScript
disabled and a crawler can see the offer. The page script re-renders the same
form from the same config on load; both produce identical markup.

Submitting redirects to `/thanks/`, which is also the analytics conversion
goal. See `docs/ANALYTICS.md`.

## Preferences (TI-011)

The form collects two optional fields, both defaulted to "everything", folded
away behind a "Only want certain clubs?" disclosure that is closed by default.

That fold matters more now than it did. A signup form that demands decisions
before it will take an address is a signup form people close, and this is the
only form on the page: it has to ask for one thing well. The preferences are
there for the minority who want them and invisible to everyone else.

- **Clubs**, a multi-select stored as provider tags
- **Minimum credibility**, stored as a custom field: any, 40+, 60+, 80+, or
  confirmed only

Segments are generated, not looped. With 20 clubs and 5 thresholds the naive
product is 100 editions per day. Instead the generator produces one
unsegmented edition plus one per club that actually has news, and leaves the
threshold cut to the provider's send-time filter. A club with nothing to
report gets no edition rather than a quiet one.

### Instant alerts

Capped at two per subscriber per day, hard. Qualifying events are a deal
crossing 80 credibility, or a completion above 40 million. When more than two
qualify, the largest fees survive.

The cap is not negotiable. An alert tier that fires six times on deadline day
trains people to mute it, and then it is not an alert tier.

## The daily job

`.github/workflows/digest.yml`, 06:00 UTC, which is early morning UK time.

Read tomorrow's email today before you ever send one:

```bash
python scripts/run_digest.py --data data.json --out build/digest --segments
```

That writes every edition to disk and touches no network. Use it for the
first week. A digest that goes out wrong cannot be recalled; the failure mode
of reading it first is that you spent two minutes.

Manual send from the Actions tab: **Run workflow**, tick **send**.

### The quiet day edition

When nothing clears the reporting bar, the digest sends two lines saying so.

Resist the urge to skip it or pad it. A short honest email is the clearest
demonstration available that the site distinguishes signal from noise, and it
is the one thing competitors will not send because it looks like failure. It
is not failure. It is the product.

### Never sending twice

`logs/digest-sent.json` records the dates already sent, and the job refuses to
send again for a date in that file. A duplicate send is the fastest way to
lose a subscriber you spent the whole window earning, and a retry after a
failed step is exactly when it happens.

Override deliberately with `--force`, and only when you know why.

## State files

| File | What it is |
|---|---|
| `logs/digest-state.json` | yesterday's snapshot of every deal's status, cred and fee. The digest diffs against this |
| `logs/digest-sent.json` | dates already sent |

Both are committed directly by the workflow rather than opened as a pull
request. Neither affects what the site displays, and a digest that waits for
review is a digest that sends yesterday's news tomorrow.

Diffing snapshots rather than reading the day's patch means the digest still
works if a run was skipped, and a manual edit to `data.json` shows up in the
email like any other change.
