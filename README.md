# Employee Attendance Management API

## Overview

This project is a FastAPI-based employee attendance management system using MongoDB. It provides employee management, attendance punch-in and punch-out, attendance corrections, monthly analytics, department summaries, late-arrival leaderboards, department attendance trends, and query execution plan analysis.

## Technology Stack

- Python
- FastAPI
- MongoDB 6.0 or newer
- PyMongo
- Uvicorn

## Prerequisites

- Python installed
- MongoDB Community Server 6.0 or newer, or a MongoDB Atlas connection
- Git (for repository submission)

## Setup

### 1. Create and activate a virtual environment

On Windows:

```bash
python -m venv .venv
.venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Create a `.env` file using `.env.example` as a reference, if provided.

Configure:

```env
MONGO_URI=mongodb://127.0.0.1:27017
MONGO_DB=attendance_db
```

Use your own MongoDB connection details if you use MongoDB Atlas. Do not commit `.env` or credentials to GitHub.

### 4. Load sample data

If sample data is required, run:

```bash
python sample_seed.py
```

### 5. Start the API

From the directory containing the `app` package, run:

```bash
uvicorn app.main:app --port 8000 --reload
```

If using a different working directory, adjust the command so Python can locate the `app` package.

## API Documentation

After starting the server, open:

- Swagger UI: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

## Main Features

- Employee creation and listing
- Attendance punch-in and punch-out
- Attendance record listing and manual corrections
- Monthly employee attendance analytics
- Department attendance summaries
- Late-arrival leaderboard
- Department attendance trends
- MongoDB query execution plan analysis

## Configuration and Security

- Configure MongoDB through environment variables.
- Keep `.env`, credentials, and local virtual environments out of the public repository.
- MongoDB indexes should be initialized by the application as required by the assignment.

## Submission

The public repository should include `app/main.py`, `requirements.txt`, `README.md`, `REVIEW.md`, and `DECISIONS.md`. Do not upload `.env`, secrets, or a Dockerfile.

## Notes

Refer to the assignment's problem statement, OpenAPI specification, and data model for the complete business rules and API contract.