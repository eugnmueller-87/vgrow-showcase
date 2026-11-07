# Community desk

The Community page renders public metadata inside the cockpit instead of presenting an outbound-link landing page.
It does not merge provider identities or expose private account feeds. Device control, plant records and automation
remain independent of the community service.

## Public sources

| Source | Retrieved metadata | In-app presentation |
| --- | --- | --- |
| [r/VIVOSUN new RSS](https://www.reddit.com/r/VIVOSUN/new/.rss) | Up to 25 public titles, canonical discussion links, publication times and source thumbnails | Recent discussion cards with original previews |
| [VIVOSUN Community](https://vivosun.com/en-DE/community) | Featured story titles, links, cover images; featured creator video IDs and covers | Story/video cards |
| [YouTube oEmbed](https://www.youtube.com/oembed) | Video titles for the IDs featured by VIVOSUN | Video labels; already known titles are reused |
| [Discord public invite](https://discord.com/invite/DHp7a6jxNk) | Server name and approximate member/presence counts | Public server overview |

On 26 October 2026, the public sources returned 25 discussion posts, 3 featured stories and 10 videos. These are
source contents, not a guaranteed catalogue size. Featured stories/videos retain VIVOSUN's ordering, with no
invented publication times. Discussion times come from RSS. Titles are shown in their original language.
RSS post bodies, comments, usernames, private messages and session credentials are not retained. The service
does not generate growing recommendations or interpret posts as advice.

`vgrow/community.py` exposes authenticated, read-only `GET /api/community`. Its URL set is fixed; the endpoint is
not an arbitrary URL proxy. Each request has an 8-second timeout and a 2 MB response limit. Redirects are refused,
outgoing cookies are disabled, and an identifiable User-Agent is sent. Thumbnail and video URLs are validated
again in the browser. Text is rendered with `textContent`, never executed as provider HTML.

Discussion photos come from RSS media thumbnails or the first supported image in the encoded post HTML.
Only HTTPS images on the exact Reddit media hosts (`preview.redd.it`, `i.redd.it`, `external-preview.redd.it`,
`a.thumbs.redditmedia.com`, `b.thumbs.redditmedia.com`) are accepted; story/video covers stay on
`image.next.vivosun.com`. No individual posts are fetched to find images. Browsers load the source thumbnails
with no referrer and decode candidates before showing them. Every displayed photo must be at least 640 × 360
pixels and meet the actual card width × device pixel ratio on both axes of its 16:9 crop. The browser rechecks
this when the feed or viewport resizes. It does not upscale thumbnails or zoom photos on hover. Missing,
undersized, failed or undecodable images leave compact text cards without placeholder panels. This is a
resolution check, not a claim that an upstream photograph's focus or composition has been independently rated.

Requests share a lock and cache. Sources refresh at most once per 10 minutes; manual refresh has a one-minute
minimum interval. Failures preserve the last successful data and back off (one hour for 403/404/410; ten minutes
otherwise). Each source has independent health, so a Reddit failure does not discard VIVOSUN stories or Discord
counts. Last successful metadata is stored atomically in `STATE_DIR/community/*.json`; include these files in
state backups. A cache-write failure is returned as `storage_error`, while live data remains usable.

The browser fetches only while Community is visible. It keeps filters, search, up to 200 saved metadata cards and
read markers in `localStorage` under `vgrow.community.desk`. Saved cards retain their metadata when the upstream
list changes. They are local to that browser. Feed position is retained when navigating within the cockpit.
Demo mode has clearly labeled fixture cards and makes no external server requests.

## Account windows and embedded videos

VIVOSUN, Discord and Facebook pages open after a click in a top-level companion window. The cockpit keeps one window reference
per service, focuses it when available, and does not reload its URL just to bring it forward. Explicitly choosing
a new story or the VIVOSUN sign-in button navigates that provider window. `opener` is cleared and the initial
window uses `no-referrer`. No browser security policy is disabled. Browser isolation/COOP, a reload of the cockpit,
popup blockers and mobile tab behavior limit reuse; the UI reports window actions, never a verified login.

Reddit is the exception: discussion cards and Reddit launch links use ordinary `<a target="_blank">` browser
navigation, without `window.open`, popup features, a named-window reuse path or a claim of successful access.
This preserves the user's normal tab/context-menu behavior. The public feed remains in the cockpit. A collapsed
“Reddit won't open?” section offers direct Reddit sign-in and official support links. Each discussion has Copy link;
if the browser's clipboard API is unavailable or denied (including HTTP LAN contexts), the exact link is revealed
in a selected, read-only field for manual copying. Link clicks retain the read marker.

This change followed recurring Reddit “blocked by network security” screens on 27 October 2026. The discussion URL
was valid and reachable from a different connection; that does not establish why the user's request was blocked.
The cockpit cannot inspect a cross-origin Reddit page, know whether sign-in succeeded, or clear Reddit's block.
The integration does not retry through alternate Reddit hosts, proxies, credential forwarding or server-side
comment scraping. A successful popup creation was never proof of successful access to the external site.

The native feed works without social sign-in. A private Facebook/Discord feed requires an authorized provider
integration; none is configured by this change. A desktop companion would be a separate installation and would
still have to respect each provider's authentication requirements.

Google documents that third-party, cross-site One Tap frames are unsupported:
[FedCM migration, cross-origin frames](https://developers.google.com/identity/gsi/web/guides/fedcm-migration).
Discord's public guild widget provides a public overview rather than a message feed:
[official guild API documentation](https://github.com/discord/discord-api-docs/blob/main/developers/resources/guild.mdx).
The VIVOSUN server's widget returned 403 during verification, so this implementation uses public invite counts.

The video dialog loads only after Watch here is pressed. It uses `youtube-nocookie.com`, an explicit Close button,
Escape/focus restoration, and removes its iframe when closed or when leaving the page. The iframe sends the
origin referrer required by YouTube and offers an original-source link for sign-in/embedding restrictions:
[YouTube embedding guidance](https://support.google.com/youtube/answer/171780).

## Validation

`tests/test_community.py` covers parsing, URL boundaries, cache coalescing, minimum refresh interval, restart/outage
fallback, partial failures, public counts, metadata-only retention, authentication and offline demo behavior.
`scripts/community_screens.py` exercises source photos, resolution/crop/density checks, failed/unsafe images, saved photos, search,
filters, saving/reload, scroll restoration, window reuse without resetting sign-in, popup denial,
native Reddit links and read markers, clipboard success/denial/unavailability, recovery links,
video-dialog lifecycle, offline display and DE/EN Light/Dark/Auto layouts down
to 320 px. It refuses non-local/non-demo targets, simulates account windows, and blocks video playback.
