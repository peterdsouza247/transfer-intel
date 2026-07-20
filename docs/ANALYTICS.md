# Measurement

TI-012. There is no point building an audience you cannot describe, and
"pageviews" describes nothing worth knowing.

## Setup, about five minutes

**Cloudflare Web Analytics** is the base layer. It is free with no traffic
ceiling, sets no cookies, and therefore needs no consent banner. You do not
need Cloudflare to be your DNS provider to use it.

1. Cloudflare dashboard, **Analytics and Logs**, **Web Analytics**, **Add a
   site**. Enter the Pages URL.
2. Copy the token out of the snippet it gives you, the value of
   `data-cf-beacon`.
3. Put it in `data.json`:

   ```json
   "analytics": {
     "cloudflareToken": "your-token-here",
     "goatcounter": "https://transferintel.goatcounter.com/count"
   }
   ```

4. Rebuild. The snippet lands in every generated page, because it is injected
   into the shared head block rather than pasted per page.

GoatCounter is already configured and stays as a second opinion. Two providers
disagreeing by 10 percent is normal and worth knowing; one provider silently
breaking is not something you can detect with one provider.

## Events

Six, all fired through one helper that fails silently. Analytics must never
break the page.

| Event | Fires when | Why you care |
|---|---|---|
| `newsletter_submit` | any capture form is submitted, tagged with placement | tells you which of the three placements actually converts |
| `newsletter_confirmed` | the `/thanks/` page loads | the real conversion. The gap between this and `newsletter_submit` is your form abandonment rate |
| `deal_opened` | a deal page link is clicked, tagged with the slug | which rumours people care about, which is not always the ones with the biggest fees |
| `club_opened` | a club dashboard opens | tells you which fanbases found you |
| `index_filtered` | someone sorts or filters the credibility index | engagement with the actual product rather than the headline |
| `outbound_source` | a link to a source is clicked | proves the site sends traffic to journalists, which is the argument for them linking back |

There is also `render_error`, fired if a page section throws. It should always
be zero. If it is not, something is broken for real users and you want to know
before they tell you.

## The six numbers that demonstrate value

Raw pageviews persuade nobody. When you need to show this site is working, to
a journalist, a sponsor, or yourself, these are the numbers:

1. **Email subscribers, total and weekly growth rate.** The headline. Everything
   else is a leading indicator of this.
2. **Returning visitor percentage.** Above 25 percent during a window is a
   genuinely strong signal. It means people came back on purpose.
3. **Median time on deal detail pages.** Depth beats breadth. A thousand
   visitors who bounce are worth less than a hundred who read.
4. **Referral sources**, especially organic inbound from forums, Reddit, or
   journalists. This is the one that compounds.
5. **Pages per session.** Whether the credibility index actually leads
   anywhere or people read one deal and leave.
6. **Digest open rate and click-through**, from the email provider.

Numbers 1 and 6 come from the newsletter provider. Two to five come from
Cloudflare. None require engineering help to read, which was the point.

## Page weight

The whole analytics block, both providers plus the event helper, is under 5KB
and there is a test asserting it. If that test fails, something has been added
that should not have been.

## Consent banners

Not required for this setup, and worth keeping it that way. Cloudflare Web
Analytics and GoatCounter are both cookieless and neither fingerprints. The
moment you add anything that does, you owe your readers a banner, and banners
cost more traffic than the extra data is worth at this scale.
