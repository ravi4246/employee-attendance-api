# DECISIONS.md

## 1. Indexes

I created a unique index on `employees.emp_code` to prevent duplicate employee codes, and a unique compound index on `attendance_logs.emp_code` and `date` to allow only one attendance record per employee per day. I added department and employee-code indexes for employee filtering and sorting, plus date, status, and late-minute indexes for attendance queries and analytics. I considered adding indexes to every field but rejected that because extra indexes consume storage and slow down writes.

## 2. Punch-in race

Two identical punch-in requests may both check for an existing record and find none. The unique compound index is the final safeguard: only one insert can succeed. The other request receives a duplicate-key error, which the API should convert to HTTP 409 Conflict.

## 3. Ties

The leaderboard uses competition ranking, so equal late-minute totals receive the same rank. If a tie crosses the requested limit, all employees tied at the cutoff are returned rather than arbitrarily excluding someone.

## 4. Headcount

The department summary starts from employees and looks up their attendance records. Therefore, employees with no attendance logs are still counted if they joined by the end of the selected month.

## 5. Scaling to 100x data

I would test query plans and workload first, then optimize aggregation pipelines and indexes based on measured bottlenecks.