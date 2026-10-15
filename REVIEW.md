# REVIEW.md

## Starter Code Defect Review

The starter implementation was reviewed against the assignment's problem statement, OpenAPI contract, and data model. The following issues and fixes should be verified against the original starter code and final implementation.

| # | Area | Defect or concern | How to verify | Fix in final implementation |
|---|---|---|---|---|
| 1 | `health()` | Health status may not reflect MongoDB availability. | Stop MongoDB and call `GET /health`. | Ping MongoDB and return HTTP 503 if unavailable. |
| 2 | `create_employee()` | Employee fields need contract-level validation. | Test malformed employee codes, emails, dates, and shift times. | Add field validation and reject identical shift start and end times. |
| 3 | Employee creation | Duplicate employee checks can race. | Submit simultaneous requests with the same employee code. | Use a unique index and handle duplicate-key errors with HTTP 409. |
| 4 | Employee timestamps | Naive datetimes can cause inconsistent timezone handling. | Inspect stored and returned timestamps. | Store timezone-aware UTC timestamps consistently. |
| 5 | Pagination | Invalid page values must be rejected. | Try `page=0` and `page_size=0`. | Validate query parameters and return HTTP 422. |
| 6 | `punch_in()` | Concurrent requests could create duplicate attendance records without database protection. | Send simultaneous punch-ins for the same employee and date. | Use a unique compound index and handle duplicate-key errors. |
| 7 | Punch-in status | Invalid attendance statuses must not be accepted. | Submit unsupported status values. | Validate allowed punch-in statuses. |
| 8 | Timestamp handling | Epoch-millisecond values and attendance dates require consistent validation. | Test invalid timestamps and punches around midnight for overnight shifts. | Validate timestamps, truncate to whole seconds, and derive the attendance date using the employee's shift. |
| 9 | Late calculation | The ten-minute grace-period boundary must be exact. | For a 09:30 shift, test 09:40:00 and 09:40:01. | Apply the specified strict threshold and whole-minute calculation. |
| 10 | Overtime | Overtime depends on the correct shift-end date and threshold. | Test before, at, and after 30 minutes of overtime, including overnight shifts. | Calculate against the correct shift-end datetime and apply the specified threshold. |
| 11 | `punch_out()` | Punch-out duration must be bounded. | Try punching out more than 24 hours after punch-in. | Reject durations outside the allowed window. |
| 12 | `punch_out()` | Concurrent punch-outs must not overwrite each other. | Send two punch-out requests for the same attendance record. | Use a conditional atomic update and return HTTP 409 if another request wins. |
| 13 | Database errors | Database failures need appropriate API error responses. | Make MongoDB unavailable and call relevant endpoints. | Handle database errors according to the endpoint requirements. |
| 14 | Endpoint coverage | Required contract endpoints may be missing from the starter. | Compare starter routes with `openapi.yaml`. | Implement the required correction, analytics, trend, leaderboard, summary, and explain endpoints. |
| 15 | Analytics | Analytics must follow the specified aggregation and calculation rules. | Compare results against known sample data and boundary cases. | Use MongoDB aggregation pipelines and validate filtering, rounding, and ranking. |
| 16 | Explain endpoint | Query execution statistics are required for supported endpoints. | Call `/admin/explain/{endpoint}` using valid parameters. | Return MongoDB execution-plan information for supported queries and aggregations. |
| 17 | Attendance correction | Corrections must validate changes and preserve audit history. | Correct a record and inspect derived fields and history. | Recalculate derived fields and record successful changes in history. |
| 18 | Indexes | Index coverage must support important query patterns. | Run explain plans against representative queries. | Add indexes based on query patterns and measured execution plans. |

## Items Reviewed That Are Not Defects

- **Epoch-millisecond range checks:** Useful validation that should be retained alongside timezone and truncation rules.
- **Unique employee-code index:** Appropriate for preventing duplicate employee codes.
- **Unique employee/date attendance index:** Appropriate for allowing one attendance record per employee per date.
- **Decimal half-up rounding:** Appropriate where the contract specifies half-up rounding.
- **Pagination defaults:** Page 1 and page size 20 are reasonable defaults when invalid values are rejected.

## Review Notes

These findings describe issues to verify against the original starter implementation. The final implementation should be checked against the contract and tested for validation errors, concurrency, timestamp boundaries, aggregation correctness, and query performance. A successful happy-path response alone does not prove that every requirement is satisfied.
