# GitHub handoff

The repository has local commits only — no remote is configured and the assistant will never push.
When you want it on GitHub, run:

    cd ~/Desktop/Programiranje/Colophon
    git remote add origin git@github.com:<you>/colophon.git   # or the https:// URL
    git push -u origin main

If you want the assistant to push for you, either add the remote yourself first, or give it a
fine-grained personal access token scoped to that single repo with Contents:Read+Write.
