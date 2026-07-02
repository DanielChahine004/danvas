# Contributing to danvas

Thanks for your interest. A quick but important note on licensing before you
open a pull request.

## Licensing of contributions (please read)

danvas is **dual-licensed**: AGPL-3.0-or-later for everyone, and paid commercial
licences for companies that don't want the AGPL copyleft (see the README's
Licence section). For that to work, the Maintainer must hold the right to license
*all* of the code — including your contributions — under both.

So **every contribution is accepted under the [Contributor Licence Agreement
(CLA.md)](CLA.md)**, which grants the Maintainer the right to relicense your
contribution commercially. You don't sign anything separately — you agree to the
CLA by signing off your commits:

```bash
git commit -s -m "your message"
```

`-s` appends a `Signed-off-by: Your Name <your@email>` line. By adding it you
certify that you wrote the contribution (or have the right to submit it) and that
you agree to the terms in [CLA.md](CLA.md). Use a real name and email. If you're
contributing for a company, make sure you're authorised to bind it.

Pull requests whose commits aren't signed off can't be merged — it's the record
that you accepted the CLA.

## Practical bits

- Match the style of the surrounding code.
- Run the tests before opening a PR: `pytest -q` (from the repo root).
- If you change the Python↔browser wire protocol, update `danvas/_protocol.py`
  and re-run `python scripts/gen_protocol.py` (a test enforces this).
- If you change anything under `danvas/frontend/src/`, rebuild the bundle
  (`npm run build` in `danvas/frontend/`) so the shipped `dist/` stays in sync.

## API conventions (frozen)

These conventions are load-bearing — users learn them once and every new API
must follow them. A PR that adds surface area is expected to comply:

1. **Factory argument order.** A factory whose panel *renders a value* takes
   that value first (`image(src)`, `table(data)`, `markdown(text)`,
   `toggle(options)`, `custom(html)`, `react(source)`, `webview(url)`,
   `show(value)`); every other factory takes `name=` first. Don't add a factory
   that mixes the two.
2. **Typed placement kwargs.** Every factory ends in
   `**place: Unpack[Place]` — never a bare `**kw` that swallows placement and
   constructor kwargs together (it kills editor autocomplete and hides typos).
   Constructor options get explicit named parameters.
3. **`name` is identity, `label` is caption.** `name` is the `canvas.<name>` /
   `canvas["name"]` handle and the replace-in-place key; `label` is only the
   card title. New components keep both, with `label` defaulting to `name`.
   A name that shadows a Canvas attribute warns at insert (except a component's
   own default name); avoid adding Canvas methods likely to collide with
   common panel names.
4. **`queue=` names two different things** — on a panel/`insert()` it is
   browser-delivery backpressure (`"fifo"`/`"latest"` per viewer); on a
   `dedicated=True` handler it is dispatch backpressure on that handler's own
   thread. This overlap is grandfathered; don't introduce a third meaning.
5. **Per-viewer scoping is always `roles=` / `client_id=`** with precedence
   `shared < role < client`, on every axis (visibility, content, layout, view).
   New per-viewer features must reuse this exact vocabulary, be filtered on
   egress (`Bridge.broadcast(roles=…)`), and be authorized on ingress
   (`Bridge._may_see` / `_may_operate`).
6. **Handlers accept `async def` everywhere.** Any new `on_*` registration must
   route through `BaseComponent._dispatch_callbacks` (or handle coroutine
   results itself, like `_dispatch_request`) so coroutine handlers keep working.
7. **Wire vocabulary lives in `danvas/_protocol.py` only.** New message types,
   binary codes, or flag keys are declared there first (see the protocol note
   above).

## Notes

- This sign-off flow is the lightweight version. If the project grows enough to
  need automated CLA tracking, a CLA-check bot (e.g. CLA Assistant) is the next
  step — the `Signed-off-by` history remains the underlying record either way.
