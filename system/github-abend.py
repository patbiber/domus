#!/usr/bin/env python3
"""Lädt die Commits des Tages gesammelt zu GitHub hoch – einmal am Abend, zu einer zufälligen Zeit zwischen 20:00 und 22:00.

Wunsch von Patrick (21.10.2026): tagsüber wird nur lokal committet; der Timer github-abend.timer startet dieses Skript
täglich um 20:00 mit zufälliger Verzögerung (bis 21:45). Die noch nicht hochgeladenen Commits bekommen Zeitstempel
(Autor und Committer) im Abendfenster: verteilt auf die Minuten vor dem Hochladen, in der ursprünglichen Reihenfolge,
nie vor 20:00 und nie in der Zukunft. Nur lokale, noch nie hochgeladene Commits werden angefasst.

Repos:
  ~/domus                        Branch main → origin main
  ~/domus/biber-solar-test/site  Branch freigegeben → origin main (nur von Patrick freigegebene Stände; der lokale
                                 main mit der Vorschau wird danach auf den neuen Stand umgehängt)
Ist auf GitHub etwas Neues (z. B. eine Änderung direkt auf github.com), wird es zuerst übernommen (rebase); bei einem
Konflikt bleibt alles lokal und Patrick bekommt eine Mail.
Aufruf: github-abend.py [--jetzt] [--trocken]   (--jetzt: auch ausserhalb 20–22 Uhr, --trocken: nichts verändern)
"""
import os
import random
import subprocess
import sys
from datetime import datetime, timedelta

HOME = os.path.expanduser("~")
BIBER_SSH = f"ssh -i {HOME}/.ssh/github_biber_solar -o IdentitiesOnly=yes -o BatchMode=yes"
REPOS = [
    {"name": "domus", "pfad": f"{HOME}/domus", "branch": "main", "vorschau": None, "env": {}},
    {"name": "biber-solar", "pfad": f"{HOME}/domus/biber-solar-test/site", "branch": "freigegeben", "vorschau": "main",
     "env": {"GIT_SSH_COMMAND": BIBER_SSH}},
]
TROCKEN = "--trocken" in sys.argv


def git(repo, *args, pruefen=True, env=None):
    e = {**os.environ, **repo["env"], **(env or {})}
    r = subprocess.run(["git", "-C", repo["pfad"], *args], capture_output=True, text=True, env=e)
    if pruefen and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def zeiten(anzahl, jetzt):
    """Zeitpunkte im Abendfenster vor «jetzt», aufsteigend, mit unregelmässigen Abständen."""
    start = jetzt.replace(hour=20, minute=0, second=0, microsecond=0)
    if jetzt < start:                                    # Aufruf mit --jetzt ausserhalb des Fensters
        start = jetzt - timedelta(minutes=5 * anzahl + 5)
    verfuegbar = max((jetzt - start).total_seconds() - 60, 60)
    abstand = min(9 * 60, verfuegbar / anzahl)
    t, aus = jetzt - timedelta(seconds=30), []
    for _ in range(anzahl):
        aus.append(t)
        t -= timedelta(seconds=abstand * random.uniform(0.55, 1.0))
        t = max(t, start)
    return sorted(aus)


def umdatieren(repo, basis, branch, jetzt):
    """Commits basis..branch mit neuen Zeitstempeln nachbauen (gleicher Inhalt, gleiche Nachricht, gleicher Autor).
    Arbeitsverzeichnis und Index bleiben unberührt: die Bäume sind identisch, nur die Commit-Objekte sind neu."""
    commits = git(repo, "rev-list", "--reverse", "--topo-order", f"{basis}..{branch}").split()
    if not commits:
        return 0
    plan = dict(zip(commits, zeiten(len(commits), jetzt)))
    if TROCKEN:
        for c, z in plan.items():
            print(f"  {repo['name']} {c[:8]} → {z:%H:%M:%S}")
        return len(commits)
    neu = {}
    for c in commits:
        z = plan[c].strftime("%Y-%m-%dT%H:%M:%S%z")
        name, mail_, = git(repo, "show", "-s", "--format=%an%x00%ae", c).split("\0")
        cname, cmail = git(repo, "show", "-s", "--format=%cn%x00%ce", c).split("\0")
        nachricht = subprocess.run(["git", "-C", repo["pfad"], "show", "-s", "--format=%B", c], capture_output=True).stdout
        eltern = []
        for p in git(repo, "show", "-s", "--format=%P", c).split():
            eltern += ["-p", neu.get(p, p)]
        env = {**os.environ, **repo["env"], "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": mail_, "GIT_AUTHOR_DATE": z,
               "GIT_COMMITTER_NAME": cname, "GIT_COMMITTER_EMAIL": cmail, "GIT_COMMITTER_DATE": z}
        r = subprocess.run(["git", "-C", repo["pfad"], "commit-tree", f"{c}^{{tree}}", *eltern], input=nachricht,
                           capture_output=True, env=env, check=True)
        neu[c] = r.stdout.decode().strip()
    git(repo, "update-ref", f"refs/heads/{branch}", neu[commits[-1]], commits[-1])
    return len(commits)


def bearbeiten(repo, jetzt):
    b, alt_kopf = repo["branch"], None
    git(repo, "fetch", "-q", "origin", "main")
    if repo["vorschau"]:
        alt_kopf = git(repo, "rev-parse", b)
        if git(repo, "branch", "--show-current") != repo["vorschau"]:
            raise RuntimeError(f"nicht auf {repo['vorschau']} – bitte prüfen")
    # Neues von GitHub übernehmen
    if subprocess.run(["git", "-C", repo["pfad"], "merge-base", "--is-ancestor", "origin/main", b]).returncode != 0:
        if TROCKEN:
            print(f"  {repo['name']}: GitHub hat Neues, würde rebasen")
        else:
            if repo["vorschau"]:
                raise RuntimeError("GitHub hat Neues, das lokal fehlt – bitte von Hand zusammenführen")
            r = subprocess.run(["git", "-C", repo["pfad"], "rebase", "-q", "origin/main"], capture_output=True, text=True)
            if r.returncode != 0:
                subprocess.run(["git", "-C", repo["pfad"], "rebase", "--abort"], capture_output=True)
                raise RuntimeError(f"Konflikt beim Übernehmen von GitHub: {r.stderr.strip()[:300]}")
    n = umdatieren(repo, "origin/main", b, jetzt)
    if n == 0:
        return f"{repo['name']}: nichts hochzuladen"
    if TROCKEN:
        return f"{repo['name']}: {n} Commit(s) würden hochgeladen"
    git(repo, "push", "-q", "origin", f"{b}:main")
    if repo["vorschau"]:                                # Vorschau-Commits auf den umdatierten Stand umhängen
        git(repo, "rebase", "-q", "--onto", b, alt_kopf, repo["vorschau"])
    return f"{repo['name']}: {n} Commit(s) hochgeladen"


def mail(betreff, text):
    subprocess.run(["mail", "-s", betreff, "root"], input=text, text=True)


def main():
    jetzt = datetime.now().astimezone()
    if "--jetzt" not in sys.argv and not (20 <= jetzt.hour < 22):
        print("ausserhalb von 20–22 Uhr, nichts zu tun")
        return
    fehler = []
    for repo in REPOS:
        try:
            print(bearbeiten(repo, jetzt), flush=True)
        except Exception as e:                            # ein Repo darf das andere nicht blockieren
            fehler.append(f"{repo['name']}: {e}")
            print("FEHLER", fehler[-1], flush=True)
    if fehler and not TROCKEN:
        mail("domus: Abend-Upload zu GitHub fehlgeschlagen",
             "Diese Repos wurden heute nicht hochgeladen (die Commits bleiben lokal und gehen morgen Abend mit):\n\n"
             + "\n".join(fehler) + "\n\nProtokoll: journalctl --user -u github-abend")


if __name__ == "__main__":
    main()
