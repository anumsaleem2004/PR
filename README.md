# PR Analysis Agent

AI-powered GitHub pull request analysis and automated code review system.

## Features

- Automated PR analysis using Llama-3 via Ollama
- Merge requirement checks (CI status, reviews, conflicts)
- Security vulnerability detection
- Code quality assessment
- Historical analysis tracking
- Auto-merge capability for approved PRs

## Installation

1. **Prerequisites**:
   - Python 3.9+
   - Django 4.2+
   - GitHub API token
   - Ollama server running Llama-3

2. **Setup**:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   
  settings.py
GITHUB_TOKEN = 'your_github_pat'
OLLAMA_HOST = 'http://localhost:11434'

Run 
python manage.py runserver
