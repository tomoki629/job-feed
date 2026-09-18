# Job feed

A daily snapshot of current job postings, collected by GitHub Actions from public sources
(ReliefWeb jobs API, unjobs.org, jobs.ilo.org, UNDP careers, UN Careers) into `feed.json`.
Every record keeps the posting date and the closing date as published by the source.
Closed postings and postings older than 45 days are dropped; junior grades are filtered out.

The automated job scan reads `feed.json` and ranks the openings against the owner's profile.
