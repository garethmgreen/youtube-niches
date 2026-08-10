# Higgsfield setup

The [Higgsfield](https://higgsfield.ai) CLI generates images, video, audio, and
3D assets from the terminal. The companion skills in `.agents/skills/` drive it.

## Local setup

```bash
npm i -g @higgsfield/cli   # installs the `higgsfield` / `higgs` / `hf` binary
higgsfield auth login      # opens a browser; sign in to finish
higgsfield account         # verify: prints credits
```

That is the whole setup. Auth must happen on a machine with a browser — see
[Why remote sessions can't authenticate](#why-remote-sessions-cant-authenticate).

## Usage

Every command requires auth, including read-only ones like `model list`.

```bash
higgsfield model list --video            # available video models
higgsfield model get <job_type>          # params a model accepts
higgsfield workflow list                 # prebuilt workflows

higgsfield generate create seedance_2_0 \
  --prompt "cinematic product photo" \
  --image-references ./photo.png \
  --wait
```

Media flags (`--image`, `--video`, `--audio`, `--start-image`, `--end-image`)
take either a UUID or a local file path; paths are uploaded automatically.
`--wait` blocks until the job finishes and prints the result URL.

## Skills

Installed via `npx skills add higgsfield-ai/skills`, tracked in
`skills-lock.json`. Real files live in `.agents/skills/`; `.claude/skills/`
holds symlinks so Claude Code picks them up.

| Skill | Use for |
| --- | --- |
| `higgsfield-youtube-thumbnail` | YouTube thumbnails, Shorts covers |
| `higgsfield-generate` | General image/video/audio/3D generation |
| `higgsfield-video-explainer` | Narrated explainer videos |
| `higgsfield-soul-id` | Train a face/identity model for consistent output |
| `higgsfield-product-photoshoot` | Product and brand photography |
| `higgsfield-brandkit` | Palettes, logos, brandbooks |
| `higgsfield-marketplace-cards` | Marketplace listing images |
| `higgsfield-websites` | Full-stack sites, apps, games |

Re-run `npx skills add higgsfield-ai/skills` to update them.

## Why remote sessions can't authenticate

`higgsfield auth login` uses OAuth 2.0 PKCE with a **loopback redirect**. It
starts a listener on `http://localhost:8766/callback` on the machine running
the CLI, opens a browser, and waits for the authorization code to come back to
that port.

In a headless remote sandbox (Claude Code on the web, CI, a container) this
cannot complete. No browser opens that you can see, and visiting the printed
authorize URL on your own machine does not help: the redirect delivers the code
to `localhost:8766` on *your* machine, where nothing is listening. The PKCE
verifier only exists inside the remote process.

There is no fallback — the CLI has no device-code flow and reads no API-key
environment variable. The browser and the CLI must be on the same host.

### Moving an existing session into a remote environment

`HIGGSFIELD_CREDENTIALS_PATH` points the CLI at a credentials file, so a session
authenticated elsewhere can be reused:

1. Run `higgsfield auth login` locally.
2. Copy `~/.config/higgsfield/credentials.json` to the remote machine.
3. Export `HIGGSFIELD_CREDENTIALS_PATH=/path/to/credentials.json`.

The file must carry the `auth_version` field written by the current flow;
without it the CLI rejects it as "Stored credentials use an older auth flow".

**This file is a live credential.** It grants access to the account and bills
against it. Never commit it — `.gitignore` blocks the usual paths, but that is a
backstop, not a guarantee. Prefer generating locally over shipping tokens into
shared environments.
