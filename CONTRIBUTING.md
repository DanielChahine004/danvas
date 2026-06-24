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

## Notes

- This sign-off flow is the lightweight version. If the project grows enough to
  need automated CLA tracking, a CLA-check bot (e.g. CLA Assistant) is the next
  step — the `Signed-off-by` history remains the underlying record either way.
