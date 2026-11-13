# Changelog

What changes in behaviour, compatibility and features, release by release. Bug fixes are not
listed one by one (read `git log` or the issues); security fixes are, under "Security", with
their issue.

## 1.0.0 (pre-release: alfa-1, alfa-2, beta-1)

The 1.0.0 release is in pre-release. This list is cumulative: it grows with each pre-release
and is final at the release.

### Compatibility

- Needs the library `python3-activitytrack` 1.0.0~beta-1 or later, and so a server on 1.0
  (sync wire schema 3).
- The data migrate by themselves to the activity model when the store is first opened; the
  to-do is now an activity in the state "Da fare".
- The key `u` (suspend) and the completion sheet are gone: an activity is suspended when
  another one starts.

### Features

- The key `n` opens the creation menu: to-do, work (now, already started, past), travel,
  absence (kind, company, days, optional times).
- Each row shows the symbol, colour and name of its displayed state; absences are listed;
  deleted activities go to the bin and can be restored.
- Travel from work asks whether the work is complete; stop offers the return trip; reopen
  asks to confirm. Priorities are named (none to emergency).

### Behaviour

- What a command moved or removed (an end moved back to a new activity's start, a planned
  segment removed) is shown in the message line, where the result or error of the last
  command shows.
- The notices of the sync stay in the header; the key `r` (sync now) dismisses those read.

Various bug fixes.

Earlier versions: see the release notes of each tag.
