# Why we moved our backups to object storage

By Priya Nair, platform engineer

Last quarter our nightly database backups grew past the size of the disk that held them. We needed a place to keep thirty days of history without buying more machines.

Object storage gave us versioning, lifecycle rules and cheap long-term retention. The move took two days, most of which went into testing restores rather than writing copy jobs.

## What we learned

A backup you have never restored is only a hope. We now rehearse a full restore every month and time it.
