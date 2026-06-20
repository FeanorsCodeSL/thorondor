# Remove Comments from Code

## Goal
Remove all comment-like text from code, Dockerfiles, shell scripts, and config files
(Python, PowerShell, bash, Dockerfile, YAML, TOML, `.env`, `.properties`, `.tcss`).
Documentation stays in `.md` files only. After this change, the code is the only
artifact that describes the implementation.

## Scope
- In scope: `.py`, `Dockerfile`, `.sh`, `.ps1`, `.yml`, `.yaml`, `.toml`,
  `.env*`, `.example`, `.llamacpp`, `.properties`, `.tcss`, `.txt` (requirements).
- Out of scope: `.md` (documentation), `uv.lock` (auto-generated), `.gitignore`,
  `LICENSE`, `THIRD-PARTY-NOTICES.md`, `coverage.xml`, `.coverage`, `__pycache__/`,
  `searxng/` (opaque AGPL dependency), `models/` (weights),
  `tests/golden/*.json` (test data, JSON has no comments), `thorondor.egg-info/`.

## Rules per format
- **Python**: remove `#` line comments (full-line and trailing); remove docstrings
  (`"""..."""` and `'''...'''` only when they are the first statement of a module,
  class, or function). Use `ast` for docstring ranges and `tokenize` for comment
  columns. Also strip `# noqa`, `# type: ignore`, and similar pragmas.
- **Dockerfile**: strip full-line `#` comments. Strip trailing `#` comments on
  instruction lines (e.g. `RUN foo  # bar`). No string-awareness needed
  (Dockerfile has no inline strings that can contain `#`).
- **Bash (`.sh`)**: strip `#` line comments, but be string-aware
  (e.g. `echo "# not a comment"` is left alone).
- **PowerShell (`.ps1`)**: strip `#` line comments and `<# ... #>` block comments.
  Be string-aware.
- **YAML (`.yml`, `.yaml`)**: strip `#` comments, string-aware. Be careful with
  flow scalars (`{a: 1}`), block scalars (`|`, `>`), and quoted strings.
- **TOML (`pyproject.toml`)**: strip `#` comments, string-aware.
- **`.env`, `.properties`, `requirements.txt`**: strip `#` line comments. These
  formats have no string literals, so this is simple line-based stripping.
- **`.tcss`** (Textual CSS): strip `/* ... */` block comments. `#` is an ID
  selector / hex digit, never a comment.

## Approach
Write a single Python script that:
1. Walks the repo and identifies in-scope files by extension.
2. For each file, applies the format-specific stripper.
3. Writes the result back, but only if the content actually changed (to avoid
   touching mtime on files with no comments).
4. Logs a per-file summary (`changed` / `unchanged`, line delta).

The script lives outside the repo (in the temp dir) so it does not become a
new committed artifact. It is invoked once, then discarded.

## Phases
- **Phase 1 — Build the stripper script.** Single Python file that handles
  every in-scope format. Keep it small and testable; no abstractions for
  one-time use.
- **Phase 2 — Dry run.** Run with a `--dry-run` flag and capture the list of
  files that would change, plus per-file line-count deltas. Review before
  writing.
- **Phase 3 — Apply.** Run the stripper for real, in place.
- **Phase 4 — Verify.** Run `python -m pytest ...` and `ruff check`. Both
  must still pass.
- **Phase 5 — Summary.** Report changed file count, total lines removed, and
  any test/lint findings to the user. Do not commit.

## Build & run
- **Containerized:** no
- **Strip command:** `python "%TEMP%/opencode/strip_comments.py" --root .`
- **Test command:** `python -m pytest semantic-chunking-service\tests orchestrator\tests thorondor_cli\tests -v`
- **Lint command:** `.venv\Scripts\ruff.exe check orchestrator semantic-chunking-service ssrf-proxy thorondor_cli scripts`

## Verification
- [ ] All in-scope files have no remaining `#` comments (Python, shell, YAML,
      TOML, env, properties) and no `"""..."""` / `'''...'''` docstrings.
- [ ] No `.tcss` `/* ... */` comments remain.
- [ ] All previously-passing tests still pass.
- [ ] `ruff check` produces no new violations.
- [ ] `python -m compileall` still passes for all packages.
- [ ] `docker compose --env-file .env -f docker-compose.yml config` still
      validates (catches accidental YAML corruption).
- [ ] Diff is intentional: only comment-related lines removed; no code changes.
