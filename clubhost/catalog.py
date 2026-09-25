from html import escape

CATALOG = {
    "club-site": {
        "id": "club-site", "name": "Club website", "icon": "01",
        "description": "A home for your club, its mission, and its next meetup.",
        "versions": ["v1", "v2"],
    },
    "event-page": {
        "id": "event-page", "name": "Event page", "icon": "02",
        "description": "A dedicated page for your next workshop or campus event.",
        "versions": ["v1", "v2"],
    },
}


def page(name: str, template: str, version: str, content=None, registration_path='', portal_url='') -> str:
    from .content import default_content
    content = content or default_content(name)
    title = escape(content['title'])
    subtitle = escape(content['tagline'])
    accent = "#a8edb0" if version == "v1" else "#b8b0ff"
    description = escape(content['description']).replace('\n', '<br>')
    link = ''
    if content.get('link_url'):
        link = f'<a class="cta" href="{escape(content["link_url"], quote=True)}" rel="noopener noreferrer">' \
               f'{escape(content.get("link_label") or "Learn more")} &rarr;</a>'
    contact = ''
    if content.get('contact_email'):
        contact = f'<p>Contact: <a href="mailto:{escape(content["contact_email"], quote=True)}">' \
                  f'{escape(content["contact_email"])}</a></p>'
    event = ''
    if template == 'event-page':
        if content.get('event_date'):
            event += f'<p>When: <time datetime="{escape(content["event_date"], quote=True)}">' \
                     f'{escape(content["event_date"])}</time></p>'
        if content.get('location'):
            event += f'<p>Where: {escape(content["location"])}</p>'
        if registration_path and content.get('registration_open'):
            event += f'''<section id="register"><h2>Join us</h2>
<p>Request a place below. When the event is full, new requests join the waitlist.
The club organizer can confirm your status. No confirmation email is sent automatically.</p>
<form id="registration-form" data-endpoint="{escape(registration_path, quote=True)}">
<label for="name">Your name</label><input id="name" name="name" required minlength="2" maxlength="100" autocomplete="name">
<label for="email">Email address</label><input id="email" name="email" type="email" required maxlength="254" autocomplete="email">
<div class="honeypot" aria-hidden="true"><label>Website<input name="website" tabindex="-1" autocomplete="off"></label></div>
<p class="fine">Your name and email will be shared with the club organizer to manage this event.</p>
<button class="cta">Request a place</button><p id="registration-message" role="status" aria-live="polite"></p></form></section>'''
        elif portal_url:
            event += f'<a class="cta" href="{escape(portal_url, quote=True)}#register">Event details and registration &rarr;</a>'
        elif not content.get('registration_open'):
            event += '<p>Registration is currently closed.</p>'
    script = '<script src="/static/public.js" defer></script>' if registration_path else ''
    return f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{margin:0;background:#14241f;color:#f7faf5;font:18px system-ui;padding:7vw;max-width:1000px}}
small{{color:{accent}}}h1{{font-size:clamp(40px,8vw,100px);letter-spacing:-.06em;margin:24px 0}}
p{{color:#c3cfc8;max-width:700px;line-height:1.6;overflow-wrap:anywhere}}a{{color:{accent}}}
.cta{{display:inline-block;background:{accent};color:#14241f;border:0;border-radius:6px;padding:14px 24px;
font:600 16px system-ui;text-decoration:none;margin:15px 0;cursor:pointer}}.cta:disabled{{opacity:.6}}
label{{display:block;margin-top:16px;font-size:14px}}input{{display:block;width:min(90%,480px);padding:12px;
margin-top:8px;border:1px solid #819688;border-radius:5px;font:16px system-ui}}
section{{margin-top:50px;padding-top:24px;border-top:1px solid #3b5146}}.fine{{font-size:13px}}
.honeypot{{position:absolute;left:-10000px}}footer{{margin-top:70px;font-size:13px}}</style>
<small>ON CAMPUS / {escape(version)}</small><h1>{title}</h1><p>{subtitle}</p>
<p>{description}</p>{link}{event}{contact}<footer>Hosted with ClubHost</footer>{script}</html>'''
