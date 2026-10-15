# DECISIONS.md

## 1. Indexes

I created a unique index on `employees.emp_code` to prevent duplicate employee codes and a unique compound index on `attendance_logs.emp_code` and `date` to allow only one attendance record per employee per day. Additional indexes support department filtering, employee sorting, date-based attendance queries, status filtering, late-arrival queries, and employee punch-in history. I avoided indexing every field because extra indexes consume storage and can slow down inserts and updates.

## 2. Punch-in race

Two simultaneous punch-in requests may both find no existing record. The unique compound index provides the final safeguard: only one insert can succeed. The duplicate-key error is converted to HTTP 409 Conflict.

## 3. Leaderboard ties

The leaderboard uses competition ranking. Employees with equal total late minutes receive the same rank. If a tie crosses the requested limit, all employees tied at that rank are included.

## 4. Department headcount

The department summary starts from the employee collection and looks up attendance records. This allows employees without attendance logs to remain included in headcount if they joined by the end of the selected month.

## 5. Scaling to 100× data

I would first test representative workloads and inspect MongoDB execution plans, including index usage and documents examined. I would measure query latency and identify bottlenecks before changing the aggregation pipelines or indexes. I would also test concurrent requests and realistic data volumes, review memory usage for large aggregations, and use pagination where appropriate. Any optimization would be validated with correctness tests and performance measurements before deployment.
