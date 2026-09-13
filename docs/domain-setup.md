# Setting up voicesofdissent.org (your part)

This is the **human-only** part of putting the archive on its own domain: register
`voicesofdissent.org`, put it on Cloudflare, and delegate DNS. Once the zone is
**Active** and you've handed me the three things in §5, I take it from there
(Worker + R2 binding + content rebuild/upload). Everything here is done in a web
browser + your registrar; none of it needs the command line.

Estimated time: ~20 min of clicking, then a wait (minutes to a few hours) for DNS
to propagate.

---

## 1. Register the domain

Two options:

- **Cloudflare Registrar (recommended).** If you register the domain *through*
  Cloudflare, steps 2–3 are automatic (no nameserver juggling) and it's at-cost
  pricing. Dashboard → **Domain Registration → Register Domains** → search
  `voicesofdissent.org` → buy. Then skip to §4.
- **Any other registrar** (Namecheap, Porkbun, Google/Squarespace, etc.). Buy
  `voicesofdissent.org` there, then do §2–§3 to move DNS to Cloudflare.

## 2. Add the site to Cloudflare (create the zone)

Only needed if you registered *elsewhere*.

1. Log in to the Cloudflare dashboard → **Add a site** (top bar or Websites →
   Add a site).
2. Enter `voicesofdissent.org`. Choose the **Free** plan (fine for this).
3. Cloudflare scans for existing DNS records — there won't be meaningful ones for a
   fresh domain; just continue.
4. Cloudflare shows you **two nameservers**, e.g.
   `xxx.ns.cloudflare.com` and `yyy.ns.cloudflare.com`. Copy both.

## 3. Point the registrar's nameservers at Cloudflare

At your registrar's control panel, find **Nameservers** (sometimes under "DNS" or
"Domain settings"):

1. Switch from the registrar's default nameservers to **Custom**.
2. Paste the two Cloudflare nameservers from §2.4. Remove any others.
3. Save.

This delegates all DNS for the domain to Cloudflare. Propagation is usually minutes
but can take a few hours. Cloudflare emails you when the zone goes **Active**.

## 4. Confirm the zone is ready

In the Cloudflare dashboard for `voicesofdissent.org`:

1. **Overview** → status should read **Active** (green). If it still says "Pending
   nameserver update," give it time / re-check the registrar nameservers.
2. **SSL/TLS → Overview** → set encryption mode to **Full** (or **Full (strict)**).
   This makes `https://voicesofdissent.org` serve with a valid cert.
3. You do **not** need to add any DNS records by hand — when I attach the Worker as
   a *custom domain*, Cloudflare creates the proxied DNS record automatically. (If
   you'd rather pre-create it, a proxied `AAAA @ → 100::` works, but it's optional.)

## 5. Hand off to me

Once the zone is **Active**, give me these three things and I'll do the rest
(create the Worker, bind the R2 bucket, rebuild the site with the new URL prefix,
and deploy):

1. **Account ID** — Cloudflare dashboard → your account → the right-hand sidebar on
   most pages shows **Account ID** (also under Workers & Pages → Overview). A 32-char
   hex string.
2. **Zone ID** — the `voicesofdissent.org` **Overview** page, right sidebar, **Zone
   ID**. Another 32-char hex string.
3. **A scoped API token** — dashboard → **My Profile → API Tokens → Create Token →
   Create Custom Token**. Grant *least privilege*:

   | Type | Resource | Permission |
   |------|----------|------------|
   | Account | Workers Scripts | Edit |
   | Account | Workers R2 Storage | Edit |
   | Zone | Workers Routes | Edit |
   | Zone | DNS | Edit |
   | Zone | Zone | Read |

   - **Account Resources:** include your account.
   - **Zone Resources:** limit to **Specific zone → voicesofdissent.org** (not "all
     zones").
   - Create, copy the token **once** (it's shown only at creation), and send it to me.

   Security notes: this token can manage Workers/DNS **for this one zone only** — it
   can't touch dangerouspress or your other sites. You can **roll or delete** it from
   the same API Tokens page after the migration, and re-issue if we need it again.

Also confirm I may install `wrangler` (Cloudflare's CLI, via `npx`/npm) in this repo —
I'll add a `wrangler.toml` and deploy the Worker from there.

## 6. What I do after handoff (for reference — not your task)

1. Author a Cloudflare **Worker** (adapting the dangerouspress model) that serves the
   archive from the R2 bucket and appends `index.html` for directory requests (so bare
   URLs like `voicesofdissent.org/` and `/masses/` work — the current site 404s on
   those).
2. Attach `voicesofdissent.org` (and `www`) to the Worker as a **custom domain** →
   Cloudflare auto-creates the DNS record.
3. **Rebuild** the whole site with `--prefix https://voicesofdissent.org` so IIIF
   manifests, the search index, covers, and deep-links point at the new host (the URLs
   are baked in absolutely — see `RUNBOOK.md` "Future: move to its own domain").
4. `aws s3 sync` the rebuilt content to the R2 bucket path the Worker reads.
5. Verify: homepage, a gallery, a reader page (crisp image), a search result, a
   deep-link, and a bare directory URL all load on `voicesofdissent.org`.
6. Optionally 301-redirect the old `pages.dangerouspress.org/progressive-magazines/*`
   URLs to the new domain.
