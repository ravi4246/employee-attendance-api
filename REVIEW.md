# REVIEW.md

## Starter Code Defect Review

The starter implementation was reviewed against the assignment's problem statement, OpenAPI contract, and data model.

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `health()` | The health endpoint returns success without checking MongoDB availability. | Stop MongoDB and call `GET /health`; it still reports success. | Added a database ping and return a service-unavailable response when MongoDB cannot be reached. |
| 2 | `create_employee()` | Employee fields are not fully validated against the contract. | Submit malformed employee codes, invalid email addresses, invalid dates, or identical shift start/end times. | Added validation for employee code, email, field lengths, dates, and shift times. |
| 3 | `create_employee()` | Checking for an existing employee before inserting is not atomic. | Send concurrent requests with the same `emp_code`. | Enforced a unique MongoDB index and handled duplicate-key errors. |
| 4 | `create_employee()` | `created_at` is created as a naive datetime. | Inspect the stored value and compare its timezone handling with UTC timestamps. | Store a timezone-aware datetime consistently. |
| 5 | `list_employees()` | Invalid pagination returns `400` rather than the contract-required validation response. | Submit `page=0` or `page_size=0`. | Validate query parameters and return `422` for invalid values. |
| 6 | `punch_in()` | Checking for an existing attendance record and then inserting it creates a race condition. | Send simultaneous punch-in requests for the same employee and date. | Retained the unique `(emp_code, date)` index and handle duplicate-key errors as conflicts. |
| 7 | `punch_in()` | The initial implementation does not support the full set of attendance status and derived-field rules. | Try invalid statuses and inspect the derived fields of created records. | Validate allowed statuses and calculate attendance fields according to the contract. |
| 8 | `punch_in()` | Timestamp validation and calendar-date handling need to follow the complete contract, including overnight shifts and second truncation. | Test invalid epoch values and punch-ins around midnight for overnight shifts. | Validate integer epoch milliseconds, truncate to whole seconds, and calculate the IST attendance date using the employee's shift. |
| 9 | `compute_late_minutes()` | Late-arrival calculation has important boundary conditions that must match the strict ten-minute grace period. | For a 09:30 shift, test 09:40:00 and 09:40:01. | Apply the strict threshold and floor elapsed late seconds to whole minutes. |
| 10 | `compute_overtime()` | Overtime must be based on the correct shift-end datetime and must not be counted below the 30-minute threshold. | Test punch-outs just before and at 30 minutes after shift end, including overnight shifts. | Calculate against the correct shift-end date and count whole minutes only when overtime reaches 30 minutes. |
| 11 | `punch_out()` | The starter does not enforce the maximum allowed duration between punch-in and punch-out. | Submit a punch-out more than 24 hours after punch-in. | Reject punch-outs outside the allowed duration. |
| 12 | `punch_out()` | The same missing-record check is duplicated, and concurrent requests require atomic protection. | Inspect the duplicate branch and send simultaneous punch-out requests. | Removed redundant checking and used a conditional atomic update so only one request can close the record. |
| 13 | `punch_out()` | The health and attendance endpoints do not consistently handle MongoDB failures. | Make MongoDB unavailable while requesting an endpoint. | Added appropriate database-error handling where required. |
| 14 | Starter endpoint coverage | Required contract endpoints are missing from the starter implementation. | Compare the starter routes with `openapi.yaml` and inspect Swagger. | Implemented attendance correction, monthly analytics, department summary, late leaderboard, department trend, and explain endpoints. |
| 15 | Analytics endpoints | Aggregation requirements cannot be met by returning simple document lists or calculating all analytics in Python. | Inspect the analytics implementation and test against a large dataset. | Implemented MongoDB aggregation pipelines for analytics and preserved the contract's filtering, date, ranking, and rounding rules. |
| 16 | `/admin/explain/{endpoint}` | The starter does not provide the required explain endpoint. | Call the endpoint listed in the OpenAPI contract. | Added execution-plan reporting using MongoDB `explain("executionStats")` for the relevant query or aggregation. |
| 17 | Attendance corrections | The starter does not implement correction history or enforce the correction rules. | Correct an attendance record and inspect its history, derived fields, and invalid-change responses. | Added validation, derived-field recalculation, and an audit-history entry for each successful correction. |
| 18 | MongoDB indexes | The initial indexes do not cover all query patterns needed by the analytics and explain endpoints. | Run explain plans against representative filters and a large dataset. | Added indexes selected for the relevant employee, date, status, and late-arrival queries. |

## Items Reviewed That Are Not Defects

- **Epoch-millisecond range checks:** The starter's range validation is useful and should be retained; the complete implementation also needs the contract's timezone and truncation behavior.
- **Unique employee-code index:** This is appropriate because `emp_code` is a stable unique identifier.
- **Unique employee/date attendance index:** This is appropriate because an employee should have one attendance record per attendance date.
- **Decimal half-up rounding:** The use of `Decimal` and `ROUND_HALF_UP` is appropriate for the specified rounding rules.
- **Pagination defaults:** A default page of 1 and page size of 20 are appropriate when combined with the required validation and filtering behavior.

## Review Notes

The review was based on the starter implementation and the assignment contract. Before submission, each listed defect and fix should be verified against the final `app/main.py` and relevant tests. A successful happy-path response alone does not establish that boundary cases, concurrent requests, aggregation behavior, or large-dataset query plans are correct.