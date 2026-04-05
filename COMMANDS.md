# COMMANDS.md — DITA Converter Tool Quick Reference

---

## Git — Daily Use

```cmd
git add <file>
git add .
git commit -m "type(scope): message"
git push
git pull --no-rebase
git status
git lfs ls-files
```

---

## Git — Fixing Problems

```cmd
git merge --abort
git checkout <file>
git config --global core.editor "notepad"
```

---

## Build & Run

```cmd
py -3.11 build\build.py
py -3.11 build\build.py --sign --cert "C:\Certs\DITAConverter.pfx"

streamlit run ui\app.py

py -3.11 -m pip install -r requirements.txt
py -3.11 -m pip install <package>
```

---

## Project Navigation

```cmd
cd "D:\Projects\ToDita - Claude"
dir
```

---

## Repo Setup (one-time)

```cmd
git lfs install
git config --global core.editor "notepad"
git config --global user.name "Your Name"
git config --global user.email "your@email.com"
```

---

## Conventional Commit Types

| Type | Use for |
|---|---|
| `feat` | New feature |
| `fix` | Bug fix |
| `style` | UI or formatting change, no logic change |
| `chore` | Maintenance, config, gitignore |
| `docs` | Documentation only |
| `build` | Build scripts, packaging |
| `refactor` | Code restructure, no behaviour change |

---

## Branch Workflow

```cmd
REM Start of every session — always confirm you're on develop
git checkout develop
git pull

REM Work, then commit to develop as usual
git add <files>
git commit -m "type(scope): message"
git push

REM When tested and confirmed — merge to main
git checkout main
git merge develop
git push
git checkout develop
```

## Session Reference

| Session | Key deliverable |
|---|---|
| S-01 | Project scaffold |
| S-02 | Extractor module |
| S-03 | Mapper module |
| S-04 | Generator module |
| S-05 | Validator module |
| S-06 | Streamlit UI |
| S-07 | Integration & Streamlit Cloud deploy |
| S-08 | DITA 2.0 + multi-topic + ZIP + image support |
| S-09 | Per-topic type detection + blank @id + ditamap + selective export |
| S-10 | Windows exe (PyInstaller + launcher + IT cert guide) |
| S-11 | Bookmap output + page range input + blank page detection + DTD compliance |
| S-12 | Batch conversion (planned) |
