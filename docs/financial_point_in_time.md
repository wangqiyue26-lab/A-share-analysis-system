# Financial data: point-in-time policy

Financial factors must not use a report merely because its reporting period had ended. A value becomes eligible only after its public disclosure timestamp.

The normalized financial-fact schema therefore stores:

- `report_period`: accounting period end.
- `announcement_date`: date the report became public.
- `source_update_date`: provider-exposed revision/update time when available.
- `statement`, `item`, `value`: long-form statement fact.
- `source`: provenance.

`point_in_time(..., as_of=...)` filters out announcements after the requested date and, by default, also filters revisions whose update timestamp is later than that date.

## Important limitation

A public endpoint may expose only the latest revised value rather than every historical version. An update timestamp lets the system avoid using that revision before it existed, but it cannot reconstruct a superseded value that the source no longer exposes. For rigorous historical research, the system should persist daily financial snapshots from now on and can later add a professional point-in-time source.

Phase 2B therefore builds and tests the disclosure-safe data layer first. Fundamental values are not added to the production composite score until the provider coverage and historical revision behavior have been validated.
