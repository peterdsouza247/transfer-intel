"""Open Graph images, drawn rather than screenshotted.

Social platforms and most chat apps will not run JavaScript to build a
preview, so a link to this site currently unfurls as a bare URL. That is a
share that does not get clicked.

Images are generated at build time, one per page that is worth sharing: the
site card, one per club, and one per deal. They are small flat PNGs with no
photography, which is deliberate. Player photographs are almost universally
agency-owned, and a transfer site is exactly the kind of place that gets a
licensing letter for using them.

1200x630 is the size every platform crops from safely.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630

BG = (11, 14, 20)
PANEL = (19, 24, 36)
BORDER = (35, 44, 66)
TEXT = (232, 236, 244)
MUTED = (138, 148, 171)
ACCENT = (79, 156, 255)
GREEN = (61, 220, 132)
AMBER = (255, 180, 84)
RED = (255, 93, 93)

# DejaVu ships with Pillow's test fonts on most Linux images and with the
# system on GitHub runners. Falling back to the bitmap default keeps the build
# green rather than failing a daily run over a missing font.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]


def _font(size: int, bold: bool = False):
    for path in FONT_CANDIDATES:
        p = Path(path)
        if not p.exists():
            continue
        if bold and "Bold" not in path:
            continue
        try:
            return ImageFont.truetype(str(p), size)
        except OSError:
            continue
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def cred_colour(cred: int) -> tuple[int, int, int]:
    if cred >= 75:
        return GREEN
    if cred >= 50:
        return ACCENT
    if cred >= 25:
        return AMBER
    return RED


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _base(subtitle: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Masthead
    d.text((72, 62), "Transfer", font=_font(42, True), fill=TEXT)
    d.text((72 + d.textlength("Transfer", font=_font(42, True)), 62),
           "Intel", font=_font(42, True), fill=ACCENT)
    d.text((74, 116), subtitle, font=_font(22), fill=MUTED)

    # Accent rule under the masthead
    d.rectangle([72, 158, 1128, 160], fill=BORDER)
    return img, d


def _footer(d: ImageDraw.ImageDraw, updated: str) -> None:
    d.rectangle([72, H - 92, 1128, H - 90], fill=BORDER)
    d.text((72, H - 72), "credibility, not clickbait", font=_font(20), fill=MUTED)
    if updated:
        label = f"Updated {updated}"
        d.text((1128 - d.textlength(label, font=_font(20)), H - 72),
               label, font=_font(20), fill=MUTED)


def site_card(out: Path, window: str, updated: str, sections: list[tuple[str, str]]) -> Path:
    """The default card, used for the home page and anything without its own.

    The three rows are the site's own sections, each carrying a real number.
    Naming what the site does is more use to someone deciding whether to click
    than three abstract statistics, and attaching a figure to each keeps the
    reason-to-click that a bare navigation list would throw away.

    Rows with nothing to report are dropped by the caller rather than rendered
    as a zero. A share card advertising "0 completed" argues against itself.
    """
    img, d = _base(window)

    title = _font(54, True)
    d.text((72, 200), "Premier League transfer", font=title, fill=TEXT)
    d.text((72, 262), "rumours, scored 0 to 100", font=title, fill=TEXT)

    y = 356
    for label, value in sections[:3]:
        d.rounded_rectangle([72, y, 1128, y + 62], 10, fill=PANEL, outline=BORDER)
        d.text((96, y + 17), label, font=_font(26, True), fill=TEXT)
        if value:
            w = d.textlength(value, font=_font(24))
            d.text((1104 - w, y + 19), value, font=_font(24), fill=ACCENT)
        y += 74

    _footer(d, updated)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out


def deal_card(out: Path, deal: dict, updated: str) -> Path:
    """One per tracked deal. The credibility score is the hero."""
    img, d = _base(f"{deal['from']} to {deal['to']}")

    name = _font(64, True)
    lines = _wrap(d, deal["p"], name, 760)
    y = 214
    for line in lines[:2]:
        d.text((72, y), line, font=name, fill=TEXT)
        y += 74

    meta = f"{deal.get('pos', '')} · {deal.get('age', '')} · {deal['from']} to {deal['to']}"
    d.text((72, y + 6), meta.strip(" ·"), font=_font(26), fill=MUTED)

    fee = deal.get("fee")
    if fee:
        d.text((72, y + 48), f"£{fee:g}m", font=_font(34, True), fill=TEXT)

    # The score dial
    cred = int(deal.get("cred", 0))
    colour = cred_colour(cred)
    cx, cy, r = 980, 320, 118
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=BORDER, width=14)
    # Arc proportional to the score, drawn from twelve o'clock.
    if cred > 0:
        d.arc([cx - r, cy - r, cx + r, cy + r], -90, -90 + int(360 * cred / 100),
              fill=colour, width=14)
    score = _font(64, True)
    d.text((cx - d.textlength(str(cred), font=score) / 2, cy - 46),
           str(cred), font=score, fill=colour)
    label = deal.get("status", "").upper()
    d.text((cx - d.textlength(label, font=_font(20, True)) / 2, cy + 30),
           label, font=_font(20, True), fill=MUTED)

    _footer(d, updated)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out


def club_card(out: Path, club: str, summary: dict, updated: str) -> Path:
    img, d = _base("Club dashboard")

    d.text((72, 206), club, font=_font(72, True), fill=TEXT)

    # Only rows that have something to say. A dashboard card showing four
    # zeros reads as a broken page, not a quiet window.
    rows = [(k, v) for k, v in summary.items() if v not in (0, "0", "£0m", "", None)]
    if not rows:
        rows = [("Tracked this window", summary.get("Tracked incoming", 0))]

    y = 320
    for label, value in rows[:4]:
        d.text((72, y), label, font=_font(24), fill=MUTED)
        d.text((560, y), str(value), font=_font(24, True), fill=TEXT)
        d.rectangle([72, y + 40, 1128, y + 41], fill=BORDER)
        y += 62

    _footer(d, updated)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG", optimize=True)
    return out
