# Local Forgejo for php_engine

A private, GitHub-like server on this machine: agent branches are pushed
here, reviewed as pull requests and merged; `main` still goes to GitHub as
before. Set up on 2026-10-23 with Forgejo 17.0.0 (released 2026-10-15; the
LTS line is 15.x).

| Item | Value |
|---|---|
| URL | `http://127.0.0.1:3000` (bound to localhost only) |
| Container | `forgejo`, image `codeberg.org/forgejo/forgejo:17.0.0`, restart `unless-stopped` |
| Data | Docker volume `forgejo-data` (repositories, users, SQLite database) |
| Accounts | self-registration disabled; accounts are created on the command line |
| Footprint | 206 MB image, about 100 MiB RAM when idle |

## 1. Start the server (done once)

```bash
docker run -d --name forgejo --restart unless-stopped \
  -p 127.0.0.1:3000:3000 \
  -e USER_UID=1000 -e USER_GID=1000 \
  -e FORGEJO__security__INSTALL_LOCK=true \
  -e FORGEJO__server__ROOT_URL=http://127.0.0.1:3000/ \
  -e FORGEJO__service__DISABLE_REGISTRATION=true \
  -v forgejo-data:/data \
  codeberg.org/forgejo/forgejo:17.0.0
curl -s http://127.0.0.1:3000/api/v1/version   # {"version":"17.0.0+gitea-1.22.0"}
```

`INSTALL_LOCK=true` skips the web installer, so the server starts on the
default SQLite database. `DISABLE_REGISTRATION=true` makes the sign-up page
say "Registration is disabled".

## 2. Create your admin account (you, not an agent)

Prints a generated password once; Forgejo asks for a new one at first login.

```bash
docker exec -u git forgejo forgejo admin user create \
  --username admin --email admin@example.com \
  --random-password --admin
```

## 3. Create the repository

Log in at `http://127.0.0.1:3000`, change the password, and create an empty
private repository `php_engine`: no README, `.gitignore` or license.

## 4. Add the remote and push (you, in your own terminal)

Git asks for the Forgejo username and password. Agents cannot do this step:
the deny rules forbid `git push` for every agent.

```bash
git -C /home/user/github/php_engine remote add local http://127.0.0.1:3000/admin/php_engine.git
git -C /home/user/github/php_engine push local main
```

## Daily use with agents

1. Each agent works in its own worktree and branch, for example
   `git worktree add ../php-engine-claude -b claude/<task>`.
2. You push the agent branch: `git push local claude/<task>`.
3. Open a pull request in Forgejo (`claude/<task>` into `main`), review the
   diff, merge.
4. Update your checkout and GitHub: `git pull local main`, then
   `git push origin main`.
5. Remove the worktree and branch: `git worktree remove ../php-engine-claude`,
   `git branch -d claude/<task>`.

## Maintenance

- Status: `docker ps --filter name=^forgejo$`
- Stop / start: `docker stop forgejo` / `docker start forgejo`
- Upgrade: read the release notes first (major versions can need manual
  steps), then `docker rm -f forgejo` and rerun step 1 with the new tag; the
  `forgejo-data` volume keeps all data.
- Backup (not tested yet): copy the volume, for example
  `docker run --rm -v forgejo-data:/data -v "$PWD":/backup debian:bookworm tar czf /backup/forgejo-data.tgz -C /data .`
- Remove completely: `docker rm -f forgejo && docker volume rm forgejo-data`
  (deletes every repository and account on it).
